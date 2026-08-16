"""Mood classifier evaluation — measures what the audio features can actually infer.

The centroid classifier is trained on the owner's own mood audit. That raises a
question the pipeline never asked: *for which moods can audio features reproduce
the owner's judgement at all?*

This module answers it with k-fold cross-validation over the audit-labeled
tracks, reporting per-mood precision / recall / F1, and derives the allowlist of
moods the centroid path is permitted to emit.

The finding that motivated it: the features recover only what is close to a
direct function of tempo or rhythm. At the shipped operating point Fast reaches
precision ~0.79 and Dance/Moody ~0.50, while everything semantic collapses —
Happy ~0.10, Sad ~0.14, Love ~0.16. Even Slow, despite sounding tempo-shaped,
never clears ~0.32 at any calibration setting.

A hand-maintained suppression list had drifted to the point of banning the
best-predicted moods and permitting the worst, so the list is now generated from
measurement instead of memory.

Usage:
    python -m pipeline.evaluate_moods
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import (
    MOOD_CATEGORIES,
    REPO_ROOT,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)

# Report destination. Committed so the allowlist is reviewable in diffs rather
# than being recomputed invisibly at import time.
MOOD_EVAL_PATH: Path = REPO_ROOT / "mood_eval.json"

# A mood is emitted by the centroid path only if its cross-validated *precision*
# clears this bar.
#
# Precision rather than F1, because the two errors are not symmetric on a
# dashboard: a false tag is a wrong statement shown to the reader as fact, while
# a missing tag is an honest gap that the labeling queue can fill later. F1
# would trade the first away to buy the second.
#
# 0.45 sits in a natural gap in the measured spread — Fast 0.79, Dance 0.52,
# Moody 0.50, then a drop to Groove 0.36 and below. See mood_eval.json for the
# current numbers; the pipeline never hard-codes which moods pass.
ALLOWLIST_MIN_PRECISION: float = 0.45

# Folds and seed are fixed so the report is reproducible across runs; a shifting
# allowlist would make pipeline output non-deterministic.
DEFAULT_FOLDS: int = 5
DEFAULT_SEED: int = 7


def _shuffled(rows: list[Any], seed: int) -> list[Any]:
    """Deterministic shuffle — stdlib ``random`` seeded per call.

    Kept local so callers can't accidentally consume shared RNG state and make
    the report drift between runs.
    """
    import random

    out = list(rows)
    random.Random(seed).shuffle(out)
    return out


def cross_validate(
    training: list[tuple[list[str], dict]],
    stats: dict[str, dict[str, float]],
    *,
    folds: int = DEFAULT_FOLDS,
    seed: int = DEFAULT_SEED,
    percentile: float | None = None,
) -> dict[str, Any]:
    """k-fold CV over ``(mood_tags, audio_features)`` rows.

    Evaluates the centroid on held-out owner labels with suppression disabled,
    so the result measures the features' raw ability to recover the owner's
    judgement rather than the effect of any current policy.

    Returns ``{per_mood: {mood: {...}}, macro_f1, exact_match, n}``.
    """
    # Imported here rather than at module scope: classify_moods imports this
    # module for the allowlist, so a top-level import would be circular.
    from pipeline.classify_moods import (
        calibrate_thresholds,
        classify_track,
        compute_centroids,
    )

    rows = _shuffled(training, seed)
    if len(rows) < folds or not rows:
        log.warning("Too few training rows (%d) for %d folds", len(rows), folds)
        return {"per_mood": {}, "macro_f1": 0.0, "exact_match": 0.0, "n": len(rows)}

    buckets = [rows[i::folds] for i in range(folds)]
    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    exact = 0
    n = 0

    for i in range(folds):
        test = buckets[i]
        train = [r for j, b in enumerate(buckets) if j != i for r in b]
        centroids = compute_centroids(train, stats)
        # Calibrate on the training fold only — calibrating on all rows would
        # leak the held-out labels and flatter the score. This must mirror what
        # classify() actually ships, or the report describes a different
        # classifier than the one in production.
        thresholds = (
            calibrate_thresholds(train, stats, centroids, percentile=percentile)
            if percentile is not None
            else calibrate_thresholds(train, stats, centroids)
        )
        for mood_tags, features in test:
            # allowed_moods=None disables gating: we want the unfiltered answer.
            predicted, _ = classify_track(
                features, stats, centroids,
                thresholds=thresholds,
                allowed_moods=None,
            )
            truth, pred = set(mood_tags), set(predicted)
            n += 1
            if truth == pred:
                exact += 1
            for m in pred & truth:
                tp[m] += 1
            for m in pred - truth:
                fp[m] += 1
            for m in truth - pred:
                fn[m] += 1

    per_mood: dict[str, dict[str, float]] = {}
    for mood in MOOD_CATEGORIES:
        support = tp[mood] + fn[mood]
        if not support:
            continue
        precision = tp[mood] / (tp[mood] + fp[mood]) if (tp[mood] + fp[mood]) else 0.0
        recall = tp[mood] / support
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        per_mood[mood] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    macro_f1 = (
        sum(m["f1"] for m in per_mood.values()) / len(per_mood) if per_mood else 0.0
    )
    return {
        "per_mood": per_mood,
        "macro_f1": round(macro_f1, 4),
        "exact_match": round(exact / n, 4) if n else 0.0,
        "n": n,
        "folds": folds,
        "seed": seed,
    }


def derive_allowlist(
    report: dict[str, Any], *, min_precision: float = ALLOWLIST_MIN_PRECISION
) -> frozenset[str]:
    """Moods the centroid path may emit — those clearing ``min_precision``.

    A mood the features cannot infer is better left blank than guessed: an empty
    mood is an honest gap, while a guessed one is presented to the reader as
    fact.
    """
    per_mood = report.get("per_mood") or {}
    return frozenset(
        mood
        for mood, m in per_mood.items()
        if m.get("precision", 0.0) >= min_precision and mood in MOOD_CATEGORIES
    )


def load_report(path: Path = MOOD_EVAL_PATH) -> dict[str, Any]:
    """Read a previously written report; ``{}`` when absent."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        log.warning("Unreadable mood eval report at %s", path)
        return {}


def _training_from_tracks(tracks: Iterable[dict]) -> list[tuple[list[str], dict]]:
    """Owner-labeled rows that also carry audio features.

    Only ``mood_source == "audit"`` counts as ground truth — claude_batch rows
    are themselves model output, so scoring against them would measure agreement
    between two classifiers rather than fidelity to the owner.
    """
    return [
        (t["mood_tags"], t["audio_features"])
        for t in tracks
        if t.get("mood_source") == "audit"
        and t.get("audio_features")
        and t.get("mood_tags")
    ]


def evaluate(
    tracks_path: Path = TRACKS_PATH,
    output_path: Path = MOOD_EVAL_PATH,
    run_log_path: Path | None = None,
) -> dict[str, Any]:
    """Cross-validate against the current tracks file and write the report."""
    configure_logging(run_log_path)
    log.info("=== Mood classifier evaluation ===")

    from pipeline.classify_moods import compute_global_stats

    if not tracks_path.exists():
        raise FileNotFoundError(f"No tracks file at {tracks_path}")

    tracks: list[dict] = []
    with open(tracks_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))

    stats = compute_global_stats(
        [t["audio_features"] for t in tracks if t.get("audio_features")]
    )
    training = _training_from_tracks(tracks)
    log.info("Owner-labeled training rows with features: %d", len(training))

    report = cross_validate(training, stats)
    allowed = derive_allowlist(report)
    report["allowlist"] = sorted(allowed)
    report["min_precision"] = ALLOWLIST_MIN_PRECISION
    report["withheld"] = sorted(set(report.get("per_mood", {})) - allowed)

    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    log.info(
        "macro F1=%.3f  exact=%.1f%%  n=%d",
        report["macro_f1"],
        report["exact_match"] * 100,
        report["n"],
    )
    log.info("Centroid may emit : %s", ", ".join(report["allowlist"]) or "(none)")
    log.info("Withheld          : %s", ", ".join(report["withheld"]) or "(none)")
    log.info("Wrote → %s", output_path)
    return report


if __name__ == "__main__":
    evaluate()
    sys.exit(0)
