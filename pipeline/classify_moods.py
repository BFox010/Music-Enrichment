"""Phase 6 — mood classification.

Two stages, both implemented here:

1. **Centroid (automated):** load owner-provided audit (artist + track +
   mood_tags), join to tracks with audio features to build training data,
   compute the centroid vector for each of the 14 moods, then classify all
   unlabeled tracks by Euclidean distance in normalized feature space.
   Sets ``mood_source: "centroid"``, ``mood_confidence: "medium"``.

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
from typing import Callable, Iterable

from pipeline.config import (
    INPUT_CLAUDE_MOOD_RESULTS,
    INPUT_EXISTING_AUDIT,
    INPUTS_DIR,
    MOOD_CATEGORIES,
    REPO_ROOT,
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

# Distance below which a centroid match is "confident" (medium confidence).
# Above this, the track is queued for Claude review.
CENTROID_THRESHOLD: float = 1.6

# ── Centroid reliability policy ──────────────────────────────────────────
# The audio-feature centroid is medium-confidence and systematically misfires
# on some moods. Each such mood gets one of two treatments, declared once here
# (the single source of truth) and enforced by apply_centroid_policy():
#
#   "suppress" — drop the centroid prediction entirely. Used when the feature
#       set CANNOT express the mood, so no threshold can rescue it. Audit- and
#       Claude-batch hits for the mood are still emitted; only the centroid
#       path is dropped.
#   "gate" — emit the centroid prediction only when a per-mood predicate over
#       the raw audio_features holds. Used when the mood IS feature-correlated
#       but the global distance threshold is too loose (e.g. Slow ~ tempo).
#
# Verdict ledger with the quantitative evidence lives in
# docs/mood_centroid_decisions.md; re-derive any time with
# `python scripts/eval_mood_centroids.py`.
CENTROID_MOOD_TREATMENTS: dict[str, dict[str, str]] = {
    "Heavy Bass": {
        "treatment": "suppress",
        "reason": "feature-inadequate: Spotify's 9 features carry no bass descriptor, so "
                  "the centroid degenerates to loud+energetic+low-acousticness and over-tags "
                  "Rock/Indie/Metal ~10x vs human labels.",
        "evidence": "2026-06-05 eval_mood_centroids trigger A",
    },
    "Dark": {
        "treatment": "suppress",
        "reason": "feature-inadequate: centroid hallucinates in the hip-hop-moody cluster.",
        "evidence": "2026-05-25 Group B (revalidated 2026-06-05)",
    },
    "Heartbreak": {
        "treatment": "suppress",
        "reason": "feature-inadequate: 0/5 centroid predictions matched owner judgement.",
        "evidence": "2026-05-25 Group B",
    },
    "Fast": {
        "treatment": "suppress",
        "reason": "Group B verdict (0/5 centroid match). Tempo-correlated, so a future "
                  "tempo>140 gate could recover it — revisit via the eval report.",
        "evidence": "2026-05-25 Group B",
    },
    "Moody": {
        "treatment": "gate",
        "reason": "feature-weak: keys on low valence and ignores tempo, dragging in fast "
                  "intense tracks (centroid avg 138 BPM vs human 128, 100% >105 BPM). "
                  "Gated to tempo < MOODY_TEMPO_MAX.",
        "evidence": "2026-06-05 eval_mood_centroids trigger B",
    },
    "Slow": {
        "treatment": "gate",
        "reason": "feature-correlated (tempo) but threshold-loose: ~30% of centroid-Slow is "
                  ">105 BPM. Gated to tempo < SLOW_TEMPO_MAX.",
        "evidence": "2026-05-25 Group C + 2026-06-05 eval_mood_centroids",
    },
}

# Tempo ceilings (BPM) for gated moods — human-readable thresholds applied to
# the RAW audio_features (not the normalized vector).
SLOW_TEMPO_MAX: float = 105.0
MOODY_TEMPO_MAX: float = 125.0

# Per-mood gate predicates over the raw audio_features dict. A gated mood's
# centroid prediction is emitted only when its predicate returns True. Missing
# tempo → predicate False → mood dropped (conservative: better to under-emit a
# gated guess than emit an ungated one).
CENTROID_MOOD_GATES: dict[str, Callable[[dict], bool]] = {
    "Slow": lambda af: af.get("tempo") is not None and af["tempo"] < SLOW_TEMPO_MAX,
    "Moody": lambda af: af.get("tempo") is not None and af["tempo"] < MOODY_TEMPO_MAX,
}

# Derived from the treatment table so the two can never drift apart. Centroid
# predictions for these moods are dropped outright (see "suppress" above).
CENTROID_SUPPRESSED_MOODS: frozenset[str] = frozenset(
    m for m, meta in CENTROID_MOOD_TREATMENTS.items() if meta["treatment"] == "suppress"
)


def apply_centroid_policy(
    moods: list[str],
    features: dict,
    *,
    suppressed_moods: frozenset[str] = CENTROID_SUPPRESSED_MOODS,
    gates: dict[str, Callable[[dict], bool]] = CENTROID_MOOD_GATES,
) -> list[str]:
    """Filter a centroid-derived mood list through suppression + per-mood gates.

    Single source of truth shared by classify_track() (live classification) and
    the retroactive cleanup migration (scripts/cleanup_centroid_moods.py), so the
    two paths can never diverge. Order within ``moods`` is preserved.

    ``features`` is the raw ``audio_features`` dict (tempo in BPM, etc.).
    """
    kept = [m for m in moods if m not in suppressed_moods]
    return [m for m in kept if m not in gates or gates[m](features)]

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


def classify_track(
    features: dict,
    stats: dict[str, dict[str, float]],
    centroids: dict[str, list[float]],
    *,
    threshold: float = CENTROID_THRESHOLD,
    max_assignments: int = 3,
    suppressed_moods: frozenset[str] = CENTROID_SUPPRESSED_MOODS,
    gates: dict[str, Callable[[dict], bool]] = CENTROID_MOOD_GATES,
) -> tuple[list[str], float | None]:
    """Return (assigned_moods, distance_of_nearest).

    Picks up to ``max_assignments`` moods whose distance is within
    ``threshold`` of the track's normalized vector, then runs them through
    ``apply_centroid_policy`` (suppression + per-mood gates) — see the
    module-level ``CENTROID_MOOD_TREATMENTS`` docstring. Policy is applied
    BEFORE the ``max_assignments`` truncation, so a suppressed/gated-out mood
    frees a slot for the next-nearest valid mood.
    """
    if not features or not centroids:
        return [], None
    vec = to_vector(features, stats)
    distances = [(mood, euclidean(vec, c)) for mood, c in centroids.items()]
    distances.sort(key=lambda x: x[1])
    nearest = distances[0][1] if distances else None
    within = [m for m, d in distances if d <= threshold]
    chosen = apply_centroid_policy(
        within, features, suppressed_moods=suppressed_moods, gates=gates
    )
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
    chosen_input = None
    for candidate in (
        REPO_ROOT / "tracks_with_availability.jsonl",
        tracks_path,
        REPO_ROOT / "tracks_with_metadata.jsonl",
    ):
        if candidate.exists():
            chosen_input = candidate
            break
    if chosen_input is None:
        log.error("No tracks file found — run earlier phases first.")
        raise FileNotFoundError("tracks_with_audio.jsonl or tracks_with_metadata.jsonl")
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

    stats_pool = [t.get("audio_features") or {} for t in tracks if t.get("audio_features")]
    stats = compute_global_stats(stats_pool)
    log.info("Global stats — tempo: mean=%.2f std=%.2f | loudness: mean=%.2f std=%.2f",
             stats["tempo"]["mean"], stats["tempo"]["std"],
             stats["loudness"]["mean"], stats["loudness"]["std"])

    # Build training data: audit rows joined to tracks-with-features
    track_index = {(t["artist_normalized"], t["track_normalized"]): t for t in tracks}
    training: list[tuple[list[str], dict]] = []
    for audit in audit_rows:
        key = (audit["artist_normalized"], audit["track_normalized"])
        track = track_index.get(key)
        if track and track.get("audio_features"):
            training.append((audit["mood_tags"], track["audio_features"]))
    log.info("Training rows (audit ∩ have_features): %d", len(training))

    centroids = compute_centroids(training, stats) if training else {}
    if centroids:
        log.info("Centroids built for moods: %s",
                 ", ".join(sorted(centroids.keys())))
    else:
        log.warning("No centroids computed — audit data missing or empty. "
                    "All tracks will be queued for Claude or left unclassified.")

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

        # Priority 3: centroid classification
        af = track.get("audio_features")
        if not af or not centroids:
            track["mood_tags"] = None
            track["mood_source"] = None
            track["mood_confidence"] = None
            stats_out["no_match"] += 1
            batch_for_claude.append(track)
            continue

        moods, nearest = classify_track(af, stats, centroids)
        if moods:
            track["mood_tags"] = moods
            track["mood_source"] = "centroid"
            track["mood_confidence"] = "medium"
            stats_out["classified_centroid"] += 1
        else:
            track["mood_tags"] = None
            track["mood_source"] = None
            track["mood_confidence"] = None
            stats_out["no_match"] += 1
            if af:  # only batch tracks that COULD be classified by Claude
                batch_for_claude.append(track)

    # Persist
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
        "no_match=%d  batched=%d  /  %d total",
        stats_out["classified_centroid"], stats_out["audit_direct"],
        stats_out["claude_overrides"], stats_out["no_match"],
        stats_out["batched_for_claude"], stats_out["total"],
    )
    log.info("Wrote → %s", output_path)
    return stats_out


if __name__ == "__main__":
    classify()
    sys.exit(0)
