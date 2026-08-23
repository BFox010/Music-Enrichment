"""Phase 6 — mood classification.

Two stages, both implemented here:

1. **Centroid (automated):** load owner-provided audit (artist + track +
   mood_tags), join to tracks with audio features to build training data,
   compute the centroid vector for each of the 14 moods, then classify all
   unlabeled tracks by Euclidean distance in normalized feature space.
   Sets ``mood_source: "centroid"``, ``mood_confidence: "medium"``.

   Two guards keep this honest. Each mood has its own calibrated radius, so a
   track is judged against that mood in absolute terms rather than ranked
   against the others. And a mood is only emitted at all if cross-validation
   shows the features can predict it (``pipeline.evaluate_moods``) — for most
   of the 14 they cannot, and those are left blank rather than guessed.

2. **Claude batch (manual):** any track whose nearest centroid is beyond a
   confidence threshold is dumped to ``inputs/claude_mood_batch.jsonl`` for
   the owner to run through Claude.ai. Owner pastes responses back as
   ``inputs/claude_mood_results.jsonl`` and re-runs; that data wins over
   centroid output. Sets ``mood_source: "claude_batch"``,
   ``mood_confidence: "high"``.

Without an audit CSV the script writes ``tracks_with_moods.jsonl`` with no
mood data populated and instructs the owner what to provide.

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
from typing import Iterable

from pipeline.config import (
    INPUT_CLAUDE_MOOD_RESULTS,
    INPUT_EXISTING_AUDIT,
    INPUTS_DIR,
    MOOD_CATEGORIES,
    REPO_ROOT,
    TRACKS_PATH,
    TRACKS_WITH_AUDIO_PATH,
    TRACKS_WITH_MOODS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)

# Audio feature axes used for centroid / classification.
# 0-1 features go through unchanged; SCALED axes are z-normalized using
# global stats (tempo and loudness vary on different scales).
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

# Fallback distance cutoff, used only when a mood has no calibrated threshold
# (too few training rows). Note this value is deliberately *not* the main gate:
# measured across the library, 11 of the 14 centroids sit within 1.6 of ~70% of
# all tracks, so a single global cutoff admits nearly everything. Per-mood
# calibration below is what actually discriminates.
CENTROID_THRESHOLD: float = 1.6

# Percentile of a mood's own training-distance distribution used as its cutoff.
# 0.30 means "accept a track only if it sits closer to this mood's centroid than
# 70% of the tracks the owner actually tagged with it", so each mood judges on
# its own scale instead of competing for a fixed number of slots.
#
# Tuned for precision: measured across 0.20–0.75, tightening the radius raises
# precision and lowers recall roughly monotonically (Fast 0.59 → 0.90). 0.30
# keeps precision high without collapsing recall below ~0.30. A missing tag is
# recoverable from the labeling queue; a wrong one is shown to the reader as
# fact.
CALIBRATION_PERCENTILE: float = 0.30

# Minimum owner-labeled examples before a mood's threshold is calibrated rather
# than falling back to CENTROID_THRESHOLD.
MIN_CALIBRATION_SUPPORT: int = 20

# Output for tracks that need Claude classification
CLAUDE_BATCH_PATH: Path = INPUTS_DIR / "claude_mood_batch.jsonl"


# ── feature normalization ────────────────────────────────────────────────


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
    """Map an ``audio_features`` dict to a normalized fixed-order vector.

    Missing values become 0 (linear keys) or the global mean (scaled keys),
    so they contribute zero to the distance after centring.
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


# ── centroids ────────────────────────────────────────────────────────────


def compute_centroids(
    training: list[tuple[list[str], dict]],
    stats: dict[str, dict[str, float]],
) -> dict[str, list[float]]:
    """``training``: list of ``(mood_tags, audio_features)``.

    Each track contributes to every mood centroid it's tagged with.
    Returns ``{mood: centroid_vector}``. Moods with no training rows are absent.
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
    """Per-mood distance cutoffs derived from the owner's own labels.

    For each mood, measures how far its genuinely-tagged tracks sit from its
    centroid and takes the ``percentile`` of that spread. A tight mood gets a
    tight cutoff and a diffuse one gets a loose cutoff, which a single global
    threshold cannot express.

    Moods with fewer than ``min_support`` examples are omitted; callers fall
    back to ``CENTROID_THRESHOLD`` for those.
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

    A mood is assigned when the track falls inside that mood's own calibrated
    radius — an absolute judgement per mood, not a ranking contest. This
    replaces an earlier "nearest three centroids" rule, which forced three tags
    onto essentially every track regardless of fit.

    ``allowed_moods`` restricts output to moods the features can actually infer
    (see ``pipeline.evaluate_moods``); ``None`` disables gating entirely, which
    is what the evaluation harness wants. ``max_assignments`` is an optional
    safety cap, off by default.
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


# ── audit CSV loading ────────────────────────────────────────────────────


def _split_moods(value: str) -> list[str]:
    """Tolerant splitter for mood-list cells in the audit CSV.

    Accepts comma, semicolon, or pipe-separated values. Drops anything that
    isn't in the canonical 14-category set; logs unknown values for debug.
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
    """Load the audit CSV. Detects column names case-insensitively.

    Expected columns (case-insensitive, any of):
      - artist (or 'Artist Name(s)' or 'Artist Name')
      - track  (or 'Track Name', 'Title', 'Name')
      - mood_tags (or 'moods', 'mood', 'mood_classifiers')
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


# ── claude batch I/O ─────────────────────────────────────────────────────


def write_claude_batch(tracks: list[dict], path: Path = CLAUDE_BATCH_PATH) -> int:
    """Write ambiguous tracks to a JSONL batch for Claude review.

    Each line has only the fields Claude needs to classify: identity, audio
    features, lastfm_tags, discogs_styles, itunes_genre. Owner pastes Claude's
    responses back as ``inputs/claude_mood_results.jsonl`` (same join key +
    mood_tags).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
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


# ── owner-label recovery ─────────────────────────────────────────────────

# Sources that represent a human judgement (directly, or a review the owner
# accepted). Centroid output must never overwrite these.
OWNER_LABEL_SOURCES: frozenset[str] = frozenset({"audit", "claude_batch"})


def _recover_owner_labels(tracks: list[dict]) -> dict[tuple[str, str], dict]:
    """Owner labels already carried on the tracks file, keyed by identity.

    Returns ``{(artist_norm, track_norm): {"mood_tags", "mood_source",
    "mood_confidence"}}`` for every row whose ``mood_source`` is owner-derived.
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


# ── main classifier ──────────────────────────────────────────────────────


def classify(
    audit_path: Path = INPUT_EXISTING_AUDIT,
    tracks_path: Path = TRACKS_WITH_AUDIO_PATH,
    output_path: Path = TRACKS_WITH_MOODS_PATH,
    claude_results_path: Path = INPUT_CLAUDE_MOOD_RESULTS,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Classify moods. Falls back to skeleton if tracks_with_audio missing.

    Returns ``{total, classified_centroid, claude_overrides, batched_for_claude,
    no_match}``.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 6: mood classification ===")

    # Resolve input — pick the DEEPEST intermediate so we carry every upstream
    # field forward (Phase 5 availability, Phase 4b discogs_styles, ...). Since
    # Phase 4 now reads the audio branch, tracks_with_availability carries
    # audio_features too, so the centroid still has what it needs.
    # tracks.jsonl is the last resort: the intermediates are gitignored, so a
    # fresh clone has only the canonical file. Re-running the phase against it
    # must still work rather than dead-ending.
    chosen_input = None
    for candidate in (
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

    # inputs/ is gitignored, so a fresh clone has no audit CSV and Phase 6 would
    # train on nothing. Fall back to the committed root copy — same recovery the
    # deepest-intermediate lookup above does for tracks.
    if not audit_path.exists():
        root_audit = REPO_ROOT / "mood_audit.csv"
        if root_audit.exists():
            log.info("%s missing — using committed %s", audit_path, root_audit)
            audit_path = root_audit

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

    # Owner labels already persisted on the tracks file. ``inputs/`` is not
    # committed, so without this a re-run on a fresh clone would find no audit
    # CSV, fall through to the centroid for every row, and silently destroy
    # every hand-made judgement in the library. Labels the owner made are the
    # spine of the classifier — they must survive the loss of their source file.
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

    # Build training data: audit rows joined to tracks-with-features. Only
    # ``audit`` counts as ground truth — claude_batch rows are themselves model
    # output, so training on them would teach the centroid to imitate another
    # classifier rather than the owner.
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

    # Per-mood cutoffs from the owner's own label spread, replacing the old
    # fixed top-3 quota.
    thresholds = calibrate_thresholds(training, stats, centroids) if centroids else {}
    if thresholds:
        log.info(
            "Calibrated thresholds: %s",
            ", ".join(f"{m}={d:.2f}" for m, d in sorted(thresholds.items())),
        )

    # Which moods the centroid may emit at all. Derived from cross-validated
    # F1 (see pipeline.evaluate_moods) so the gate reflects measurement rather
    # than a hand-maintained list. Falls back to "no gating" when no report
    # exists yet, so a fresh clone still classifies.
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

    # Optional Claude verdicts (high-quality)
    claude_index = load_claude_results(claude_results_path)
    log.info("Claude mood overrides loaded: %d", len(claude_index))

    # Audit-direct map (medium confidence — direct from audit, no centroid math needed)
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

    for track in tracks:
        key = (track["artist_normalized"], track["track_normalized"])

        # Priority 1: Claude review (highest confidence)
        if key in claude_index:
            track["mood_tags"] = claude_index[key]
            track["mood_source"] = "claude_batch"
            track["mood_confidence"] = "high"
            stats_out["claude_overrides"] += 1
            continue

        # Priority 2: direct audit hit (also high confidence — owner-labeled)
        if key in audit_index:
            track["mood_tags"] = audit_index[key]
            track["mood_source"] = "audit"
            track["mood_confidence"] = "high"
            stats_out["audit_direct"] += 1
            continue

        # Priority 3: an owner label already on the row, whose source file is
        # no longer present. Falling through to the centroid here would replace
        # a human judgement with a guess.
        if key in recovered:
            label = recovered[key]
            track["mood_tags"] = label["mood_tags"]
            track["mood_source"] = label["mood_source"]
            track["mood_confidence"] = label["mood_confidence"]
            stats_out["owner_preserved"] += 1
            continue

        # Priority 4: centroid classification
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
            # Distance to the nearest centroid, so the dashboard can show fit
            # rather than mere presence.
            track["mood_distance"] = round(nearest, 4) if nearest is not None else None
            stats_out["classified_centroid"] += 1
        else:
            track["mood_tags"] = None
            track["mood_source"] = None
            track["mood_confidence"] = None
            stats_out["no_match"] += 1
            if af:  # only batch tracks that COULD be classified by Claude
                batch_for_claude.append(track)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in tracks:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if batch_for_claude:
        n = write_claude_batch(batch_for_claude, CLAUDE_BATCH_PATH)
        stats_out["batched_for_claude"] = n
        log.info("Wrote %d tracks to %s for Claude review", n, CLAUDE_BATCH_PATH)

    log.info(
        "Phase 6 done: centroid=%d  audit=%d  claude_override=%d  "
        "owner_preserved=%d  no_match=%d  batched=%d  /  %d total",
        stats_out["classified_centroid"], stats_out["audit_direct"],
        stats_out["claude_overrides"], stats_out["owner_preserved"],
        stats_out["no_match"], stats_out["batched_for_claude"],
        stats_out["total"],
    )
    log.info("Wrote → %s", output_path)
    return stats_out


if __name__ == "__main__":
    classify()
    sys.exit(0)
