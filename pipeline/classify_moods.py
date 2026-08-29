"""Phase 6 — mood classification. Two stages, both here:

1. **Centroid (automated).** Owner audit (artist + track + mood_tags) joined to
   tracks with audio features → a centroid per mood; unlabeled tracks classified
   by Euclidean distance in normalized feature space.
   → ``mood_source: "centroid"``, ``mood_confidence: "medium"``.

   Two guards: each mood has its own calibrated radius (judged in absolute terms,
   not ranked against the others), and a mood is emitted only if cross-validation
   says the features can predict it (``pipeline.evaluate_moods``). Most of the 14
   cannot be — those stay blank rather than guessed.

2. **Claude batch (manual).** Tracks beyond the confidence threshold go to
   ``inputs/claude_mood_batch.jsonl``; owner runs them through Claude.ai and
   pastes back ``inputs/claude_mood_results.jsonl``, which wins over centroid
   output. → ``mood_source: "claude_batch"``, ``mood_confidence: "high"``.

No audit CSV ⇒ writes ``tracks_with_moods.jsonl`` unpopulated, with instructions.

Usage:
    python -m pipeline.classify_moods
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import (
    INPUT_CLAUDE_MOOD_RESULTS,
    INPUT_EXISTING_AUDIT,
    INPUTS_DIR,
    MOOD_AUDIT_FILENAME,
    MOOD_CATEGORIES,
    REPO_ROOT,
    TRACKS_PATH,
    TRACKS_WITH_AUDIO_PATH,
    TRACKS_WITH_MOODS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track
from pipeline.schema import atomic_open

log = get_logger(__name__)

# Centroid axes. LINEAR are already 0-1; SCALED are z-normalized against global
# stats (tempo and loudness live on their own scales).
LINEAR_KEYS: tuple[str, ...] = (
    "danceability",
    "energy",
    "valence",
    "speechiness",
    "acousticness",
    "instrumentalness",
    "liveness",
)
SCALED_KEYS: tuple[str, ...] = ("tempo", "loudness")
ALL_KEYS: tuple[str, ...] = LINEAR_KEYS + SCALED_KEYS

# Fallback only, for moods with too few training rows to calibrate. NOT the main
# gate: 11 of 14 centroids sit within 1.6 of ~70% of the library, so a single
# global cutoff admits nearly everything. Per-mood calibration is what discriminates.
CENTROID_THRESHOLD: float = 1.6

# Cutoff percentile of a mood's own training-distance distribution: accept only
# tracks closer to the centroid than 70% of the rows the owner tagged with it.
# Each mood judges on its own scale rather than competing for fixed slots.
# Tuned for precision — across 0.20–0.75 a tighter radius raises precision and
# lowers recall near-monotonically. A missing tag is recoverable from the labeling
# queue; a wrong one is shown to the reader as fact.
CALIBRATION_PERCENTILE: float = 0.30

# Below this many owner labels, a mood falls back to CENTROID_THRESHOLD.
MIN_CALIBRATION_SUPPORT: int = 20

CLAUDE_BATCH_PATH: Path = INPUTS_DIR / "claude_mood_batch.jsonl"


# ── feature normalization ──


def compute_global_stats(features_list: Iterable[dict]) -> dict[str, dict[str, float]]:
    """Mean and (population) std for each scaled feature across all tracks."""
    sums: dict[str, list[float]] = {k: [] for k in SCALED_KEYS}
    for feat in features_list:
        if not feat:
            continue
        for k in SCALED_KEYS:
            v = feat.get(k)
            if v is not None:
                sums[k].append(float(v))
    stats: dict[str, dict[str, float]] = {}
    for k, values in sums.items():
        if not values:
            stats[k] = {"mean": 0.0, "std": 1.0}
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(var) if var > 0 else 1.0
        stats[k] = {"mean": mean, "std": std}
    return stats


def to_vector(features: dict, stats: dict[str, dict[str, float]]) -> list[float]:
    """``audio_features`` → normalized fixed-order vector. Missing values become 0
    (linear) or the global mean (scaled), contributing nothing to the distance.
    """
    vec: list[float] = []
    for k in LINEAR_KEYS:
        v = features.get(k)
        vec.append(float(v) if v is not None else 0.0)
    for k in SCALED_KEYS:
        v = features.get(k)
        s = stats.get(k, {"mean": 0.0, "std": 1.0})
        if v is None:
            vec.append(0.0)
        else:
            std = s["std"] or 1.0
            vec.append((float(v) - s["mean"]) / std)
    return vec


# ── centroids ──


def compute_centroids(
    training: list[tuple[list[str], dict]],
    stats: dict[str, dict[str, float]],
) -> dict[str, list[float]]:
    """``training`` is ``[(mood_tags, audio_features)]``; each track feeds every mood
    it carries. Returns ``{mood: vector}`` — moods with no training rows are absent.
    """
    grouped: dict[str, list[list[float]]] = defaultdict(list)
    for mood_tags, features in training:
        if not features or not mood_tags:
            continue
        vec = to_vector(features, stats)
        for mood in mood_tags:
            if mood in MOOD_CATEGORIES:
                grouped[mood].append(vec)

    centroids: dict[str, list[float]] = {}
    for mood, vecs in grouped.items():
        if not vecs:
            continue
        d = len(vecs[0])
        n = len(vecs)
        centroids[mood] = [sum(v[i] for v in vecs) / n for i in range(d)]
    return centroids


def euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile of a sorted-on-the-fly list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def calibrate_thresholds(
    training: list[tuple[list[str], dict]],
    stats: dict[str, dict[str, float]],
    centroids: dict[str, list[float]],
    *,
    percentile: float = CALIBRATION_PERCENTILE,
    min_support: int = MIN_CALIBRATION_SUPPORT,
) -> dict[str, float]:
    """Per-mood cutoffs from the owner's own labels: the ``percentile`` of how far
    each mood's tagged tracks sit from its centroid. A tight mood gets a tight
    cutoff, a diffuse one a loose cutoff — a global threshold can't express that.

    Moods under ``min_support`` are omitted; callers fall back to CENTROID_THRESHOLD.
    """
    per_mood: dict[str, list[float]] = defaultdict(list)
    for mood_tags, features in training:
        if not features or not mood_tags:
            continue
        vec = to_vector(features, stats)
        for mood in mood_tags:
            centroid = centroids.get(mood)
            if centroid is not None:
                per_mood[mood].append(euclidean(vec, centroid))

    return {
        mood: _percentile(distances, percentile)
        for mood, distances in per_mood.items()
        if len(distances) >= min_support
    }


def classify_track(
    features: dict,
    stats: dict[str, dict[str, float]],
    centroids: dict[str, list[float]],
    *,
    thresholds: dict[str, float] | None = None,
    threshold: float = CENTROID_THRESHOLD,
    max_assignments: int | None = None,
    allowed_moods: frozenset[str] | None = None,
) -> tuple[list[str], float | None]:
    """Return (assigned_moods, distance_of_nearest).

    A mood is assigned when the track falls inside that mood's calibrated radius —
    an absolute per-mood judgement, not a ranking contest, so a track may get zero.

    ``allowed_moods=None`` disables gating (what the eval harness wants);
    otherwise it restricts to moods the features can infer (evaluate_moods).
    ``max_assignments`` is an optional safety cap, off by default.
    """
    if not features or not centroids:
        return [], None
    vec = to_vector(features, stats)
    distances = [(mood, euclidean(vec, c)) for mood, c in centroids.items()]
    distances.sort(key=lambda x: x[1])
    nearest = distances[0][1] if distances else None

    limits = thresholds or {}
    chosen = [
        m
        for m, d in distances
        if d <= limits.get(m, threshold)
        and (allowed_moods is None or m in allowed_moods)
    ]
    if max_assignments is not None:
        chosen = chosen[:max_assignments]
    return chosen, nearest


# ── audit CSV loading ──


def _split_moods(value: str) -> list[str]:
    """Split an audit-CSV mood cell on , ; or |. Drops values outside the canonical
    14; logs the unknowns at debug.
    """
    if not value:
        return []
    cleaned = value.replace(";", ",").replace("|", ",")
    raw = [p.strip() for p in cleaned.split(",") if p.strip()]
    out: list[str] = []
    canonical_lower = {m.lower(): m for m in MOOD_CATEGORIES}
    for r in raw:
        canonical = canonical_lower.get(r.lower())
        if canonical:
            out.append(canonical)
        else:
            log.debug("Unknown mood value in audit: %r", r)
    return out


def load_audit(path: Path) -> list[dict]:
    """Load the audit CSV. Column names matched case-insensitively, any of:
    artist | artist name(s) | artist name; track | track name | title | name;
    mood_tags | moods | mood | mood_classifiers.
    """
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    out: list[dict] = []
    for row in rows:
        keys_lower = {k.lower().strip(): k for k in row.keys()}
        artist = (
            row.get(keys_lower.get("artist", ""), "")
            or row.get(keys_lower.get("artist name(s)", ""), "")
            or row.get(keys_lower.get("artist name", ""), "")
        ).strip()
        track = (
            row.get(keys_lower.get("track", ""), "")
            or row.get(keys_lower.get("track name", ""), "")
            or row.get(keys_lower.get("title", ""), "")
            or row.get(keys_lower.get("name", ""), "")
        ).strip()
        moods_raw = (
            row.get(keys_lower.get("mood_tags", ""), "")
            or row.get(keys_lower.get("moods", ""), "")
            or row.get(keys_lower.get("mood", ""), "")
            or row.get(keys_lower.get("mood_classifiers", ""), "")
        ).strip()
        if not artist or not track or not moods_raw:
            continue
        out.append({
            "artist": artist,
            "track": track,
            "artist_normalized": normalize_artist(artist),
            "track_normalized": normalize_track(track),
            "mood_tags": _split_moods(moods_raw),
        })
    return out


# ── claude batch I/O ──


def write_claude_batch(tracks: list[dict], path: Path = CLAUDE_BATCH_PATH) -> int:
    """Write ambiguous tracks to a JSONL batch for Claude review — identity plus
    only the fields needed to classify. Owner pastes verdicts back as
    ``inputs/claude_mood_results.jsonl`` (same join key + mood_tags).
    """
    with atomic_open(path) as fh:
        for t in tracks:
            payload = {
                "artist": t.get("artist"),
                "track": t.get("track"),
                "artist_normalized": t.get("artist_normalized"),
                "track_normalized": t.get("track_normalized"),
                "audio_features": t.get("audio_features"),
                "lastfm_tags": t.get("lastfm_tags") or [],
                "discogs_styles": t.get("discogs_styles") or [],
                "itunes_genre": t.get("itunes_genre"),
                "release_year": t.get("release_year"),
            }
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return len(tracks)


def load_claude_results(path: Path = INPUT_CLAUDE_MOOD_RESULTS) -> dict[tuple[str, str], list[str]]:
    """Load Claude's mood verdicts. Keyed by (artist_norm, track_norm)."""
    if not path.exists():
        return {}
    out: dict[tuple[str, str], list[str]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            artist = row.get("artist_normalized") or normalize_artist(row.get("artist", ""))
            track = row.get("track_normalized") or normalize_track(row.get("track", ""))
            moods_raw = row.get("mood_tags") or row.get("moods") or []
            if isinstance(moods_raw, str):
                moods = _split_moods(moods_raw)
            else:
                moods = [m for m in moods_raw if m in MOOD_CATEGORIES]
            if artist and track and moods:
                out[(artist, track)] = moods
    return out


# ── owner-label recovery ──

# Sources that represent a human judgement (directly, or a review the owner
# accepted). Centroid output must never overwrite these.
OWNER_LABEL_SOURCES: frozenset[str] = frozenset({"audit", "claude_batch"})


def _identity_keys(track: dict) -> list[tuple[str, str]]:
    """Every key this row answers to: its own, then its merged-away aliases.

    Phase 4e collapses credit variants ("Paranoid" into "Paranoid feat Mr
    Hudson"), so a hand-labelled key can survive only as an ``identity_aliases``
    entry. Looking up the row key alone silently drops those labels.
    """
    keys = [(track.get("artist_normalized") or "", track.get("track_normalized") or "")]
    for alias in track.get("identity_aliases") or []:
        if isinstance(alias, (list, tuple)) and len(alias) == 2:
            pair = (alias[0], alias[1])
            if pair not in keys:
                keys.append(pair)
    return keys


def _lookup_by_identity(track: dict, index: dict[tuple[str, str], Any]):
    """First hit for this row in ``index``, own key before aliases."""
    for key in _identity_keys(track):
        if key in index:
            return index[key]
    return None


def _recover_owner_labels(tracks: list[dict]) -> dict[tuple[str, str], dict]:
    """``{(artist_norm, track_norm): {mood_tags, mood_source, mood_confidence}}``
    for every row already carrying an owner-derived ``mood_source``.
    """
    out: dict[tuple[str, str], dict] = {}
    for t in tracks:
        source = t.get("mood_source")
        tags = t.get("mood_tags")
        if source in OWNER_LABEL_SOURCES and tags:
            key = (t.get("artist_normalized") or "", t.get("track_normalized") or "")
            if key != ("", ""):
                out[key] = {
                    "mood_tags": list(tags),
                    "mood_source": source,
                    "mood_confidence": t.get("mood_confidence") or "high",
                }
    return out


# ── main classifier ──


def classify(
    audit_path: Path | None = None,
    tracks_path: Path = TRACKS_WITH_AUDIO_PATH,
    output_path: Path = TRACKS_WITH_MOODS_PATH,
    claude_results_path: Path = INPUT_CLAUDE_MOOD_RESULTS,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Classify moods. Falls back to skeleton if tracks_with_audio missing.

    ``audit_path`` defaults to the canonical, git-tracked ``mood_audit.csv`` at
    the repo root (``MOOD_AUDIT_PATH``) — pass ``audit_path=INPUT_EXISTING_AUDIT``
    explicitly to use the legacy gitignored copy instead; it is never chosen
    silently.

    Returns ``{total, classified_centroid, claude_overrides, batched_for_claude,
    no_match}``.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 6: mood classification ===")

    if audit_path is None:
        # Resolved against the module's REPO_ROOT rather than importing the
        # ready-made MOOD_AUDIT_PATH, so tests that redirect REPO_ROOT to a
        # tmpdir redirect the audit file with it. MOOD_AUDIT_FILENAME keeps the
        # name itself defined once.
        audit_path = REPO_ROOT / MOOD_AUDIT_FILENAME

    # DEEPEST intermediate wins, so every upstream field survives (5b features,
    # 5 availability, 4b discogs_styles, ...). tracks.jsonl is the last resort:
    # intermediates are gitignored, so a fresh clone has only the canonical file.
    chosen_input = None
    for candidate in (
        REPO_ROOT / "tracks_with_features.jsonl",
        REPO_ROOT / "tracks_with_isrcs.jsonl",
        REPO_ROOT / "tracks_with_availability.jsonl",
        REPO_ROOT / "tracks_resolved.jsonl",
        tracks_path,
        REPO_ROOT / "tracks_with_metadata.jsonl",
        TRACKS_PATH,
    ):
        if candidate.exists():
            chosen_input = candidate
            break
    if chosen_input is None:
        log.error("No tracks file found — run earlier phases first.")
        raise FileNotFoundError("tracks_with_audio.jsonl or tracks_with_metadata.jsonl")

    # mood_audit.csv (repo root) is the canonical, git-tracked label file (#66)
    # and the unconditional default above. inputs/existing_audit.csv is a
    # gitignored legacy copy that must never silently win — a forgotten local
    # file would otherwise make classification non-reproducible relative to a
    # fresh clone or CI, with no visible sign of which file actually trained
    # the run. If it's present alongside the canonical file, ignore it loudly;
    # to use it deliberately, pass audit_path=INPUT_EXISTING_AUDIT explicitly.
    if INPUT_EXISTING_AUDIT.exists() and audit_path != INPUT_EXISTING_AUDIT:
        log.warning(
            "%s exists but is not authoritative — ignoring it in favor of %s. "
            "Reconcile any local edits into the committed file; pass "
            "audit_path=INPUT_EXISTING_AUDIT explicitly to use it instead.",
            INPUT_EXISTING_AUDIT, audit_path,
        )

    log.info("Tracks input: %s", chosen_input)
    log.info("Audit input : %s (exists=%s)", audit_path, audit_path.exists())
    log.info("Output      : %s", output_path)

    tracks: list[dict] = []
    with open(chosen_input, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))
    log.info("Loaded %d tracks", len(tracks))

    have_features = sum(1 for t in tracks if t.get("audio_features"))
    log.info("Tracks with audio features: %d", have_features)

    audit_rows = load_audit(audit_path) if audit_path.exists() else []
    log.info("Audit rows loaded: %d", len(audit_rows))

    # Owner labels already on the tracks file. Without this, a fresh clone (no
    # audit CSV) falls through to the centroid on every row and silently destroys
    # every hand-made judgement in the library.
    recovered = _recover_owner_labels(tracks)
    if recovered and not audit_rows:
        log.info(
            "Audit CSV absent — recovered %d owner labels from the tracks file",
            len(recovered),
        )

    stats_pool = [t.get("audio_features") or {} for t in tracks if t.get("audio_features")]
    stats = compute_global_stats(stats_pool)
    log.info("Global stats — tempo: mean=%.2f std=%.2f | loudness: mean=%.2f std=%.2f",
             stats["tempo"]["mean"], stats["tempo"]["std"],
             stats["loudness"]["mean"], stats["loudness"]["std"])

    # Only ``audit`` is ground truth. claude_batch rows are model output; training
    # on them teaches the centroid to imitate another classifier, not the owner.
    track_index = {(t["artist_normalized"], t["track_normalized"]): t for t in tracks}
    training: list[tuple[list[str], dict]] = []
    for audit in audit_rows:
        key = (audit["artist_normalized"], audit["track_normalized"])
        track = track_index.get(key)
        if track and track.get("audio_features"):
            training.append((audit["mood_tags"], track["audio_features"]))
    if not training and recovered:
        for key, label in recovered.items():
            if label["mood_source"] != "audit":
                continue
            track = track_index.get(key)
            if track and track.get("audio_features"):
                training.append((label["mood_tags"], track["audio_features"]))
        log.info("Training rebuilt from persisted owner labels")
    log.info("Training rows (audit ∩ have_features): %d", len(training))

    centroids = compute_centroids(training, stats) if training else {}
    if centroids:
        log.info("Centroids built for moods: %s",
                 ", ".join(sorted(centroids.keys())))
    else:
        log.warning("No centroids computed — audit data missing or empty. "
                    "All tracks will be queued for Claude or left unclassified.")

    thresholds = calibrate_thresholds(training, stats, centroids) if centroids else {}
    if thresholds:
        log.info(
            "Calibrated thresholds: %s",
            ", ".join(f"{m}={d:.2f}" for m, d in sorted(thresholds.items())),
        )

    # Which moods the centroid may emit. Gated on cross-validated *precision*
    # (pipeline.evaluate_moods), so it is measured, not a hand-maintained list.
    # No report yet ⇒ no gating, so a fresh clone still classifies.
    from pipeline.evaluate_moods import cross_validate, derive_allowlist, load_report

    report = load_report()
    if not report.get("per_mood") and training:
        log.info("No mood_eval.json — cross-validating inline to derive allowlist")
        report = cross_validate(training, stats)
    allowed_moods = derive_allowlist(report) if report.get("per_mood") else None
    if allowed_moods is not None:
        withheld = sorted(set(MOOD_CATEGORIES) - allowed_moods)
        log.info("Centroid may emit : %s", ", ".join(sorted(allowed_moods)) or "(none)")
        log.info("Withheld (unlearnable): %s", ", ".join(withheld))
    else:
        log.warning("No evaluation report — centroid gating disabled")

    claude_index = load_claude_results(claude_results_path)
    log.info("Claude mood overrides loaded: %d", len(claude_index))

    audit_index = {(a["artist_normalized"], a["track_normalized"]): a["mood_tags"]
                   for a in audit_rows}

    stats_out = {
        "total": len(tracks),
        "classified_centroid": 0,
        "claude_overrides": 0,
        "audit_direct": 0,
        "owner_preserved": 0,
        "batched_for_claude": 0,
        "no_match": 0,
    }
    batch_for_claude: list[dict] = []

    matched_audit_keys: set[tuple[str, str]] = set()

    for track in tracks:
        key = (track["artist_normalized"], track["track_normalized"])

        # 1. audit hit — the owner's own labelling outranks model output
        # (MOOD_SOURCE_RANK), so it must be checked first.
        audit_hit = None
        for candidate in _identity_keys(track):
            if candidate in audit_index:
                audit_hit = candidate
                break
        if audit_hit is not None:
            track["mood_tags"] = audit_index[audit_hit]
            track["mood_source"] = "audit"
            track["mood_confidence"] = "high"
            matched_audit_keys.add(audit_hit)
            stats_out["audit_direct"] += 1
            continue

        # 2. Claude review
        claude_tags = _lookup_by_identity(track, claude_index)
        if claude_tags is not None:
            track["mood_tags"] = claude_tags
            track["mood_source"] = "claude_batch"
            track["mood_confidence"] = "high"
            stats_out["claude_overrides"] += 1
            continue

        # 3. owner label on the row whose source file is gone. Falling through to
        # the centroid here would replace a human judgement with a guess.
        if key in recovered:
            label = recovered[key]
            track["mood_tags"] = label["mood_tags"]
            track["mood_source"] = label["mood_source"]
            track["mood_confidence"] = label["mood_confidence"]
            stats_out["owner_preserved"] += 1
            continue

        # 4. centroid
        af = track.get("audio_features")
        if not af or not centroids:
            track["mood_tags"] = None
            track["mood_source"] = None
            track["mood_confidence"] = None
            stats_out["no_match"] += 1
            batch_for_claude.append(track)
            continue

        moods, nearest = classify_track(
            af, stats, centroids,
            thresholds=thresholds,
            allowed_moods=allowed_moods,
        )
        if moods:
            track["mood_tags"] = moods
            track["mood_source"] = "centroid"
            track["mood_confidence"] = "medium"
            # Nearest-centroid distance, so the dashboard can show fit, not just presence.
            track["mood_distance"] = round(nearest, 4) if nearest is not None else None
            stats_out["classified_centroid"] += 1
        else:
            track["mood_tags"] = None
            track["mood_source"] = None
            track["mood_confidence"] = None
            stats_out["no_match"] += 1
            if af:  # no features ⇒ Claude can't classify it either
                batch_for_claude.append(track)

    # An audit row that matches nothing is a silent loss of hand-made judgement —
    # the failure mode that let the committed audit and the library drift apart.
    # Name it in the log and on the returned stats so a growing gap is visible.
    unmatched_audit = [k for k in audit_index if k not in matched_audit_keys]
    stats_out["audit_unmatched"] = len(unmatched_audit)
    if unmatched_audit:
        log.warning(
            "%d of %d audit rows matched no track — their labels are not in the "
            "output. First 20: %s",
            len(unmatched_audit), len(audit_index),
            "; ".join(f"{a} — {t}" for a, t in sorted(unmatched_audit)[:20]),
        )

    with atomic_open(output_path) as fh:
        for row in tracks:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if batch_for_claude:
        n = write_claude_batch(batch_for_claude, CLAUDE_BATCH_PATH)
        stats_out["batched_for_claude"] = n
        log.info("Wrote %d tracks to %s for Claude review", n, CLAUDE_BATCH_PATH)

    log.info(
        "Phase 6 done: centroid=%d  audit=%d  claude_override=%d  "
        "owner_preserved=%d  no_match=%d  batched=%d  audit_unmatched=%d  /  %d total",
        stats_out["classified_centroid"], stats_out["audit_direct"],
        stats_out["claude_overrides"], stats_out["owner_preserved"],
        stats_out["no_match"], stats_out["batched_for_claude"],
        stats_out["audit_unmatched"], stats_out["total"],
    )
    log.info("Wrote → %s", output_path)
    return stats_out


if __name__ == "__main__":
    classify()
    sys.exit(0)
