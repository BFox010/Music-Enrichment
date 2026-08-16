"""Tests for pipeline.evaluate_moods — the harness that decides what the
classifier is allowed to say.

The allowlist is generated from measurement rather than maintained by hand,
because the previous hand-kept list had silently inverted: it banned the
best-predicted mood and permitted the worst. These tests guard the mechanism
that keeps it honest.
"""

from __future__ import annotations

from pipeline.classify_moods import compute_global_stats
from pipeline.evaluate_moods import (
    _training_from_tracks,
    cross_validate,
    derive_allowlist,
)


def _features(**kwargs) -> dict:
    base = {
        "danceability": 0.5, "energy": 0.5, "valence": 0.5,
        "speechiness": 0.05, "acousticness": 0.3,
        "instrumentalness": 0.0, "liveness": 0.1,
        "tempo": 120.0, "loudness": -10.0,
    }
    base.update(kwargs)
    return base


class TestDeriveAllowlist:
    def test_gates_on_precision_not_f1(self):
        """A mood with strong recall but poor precision must be withheld.

        F1 would let it through by trading precision for recall. On a dashboard
        those errors are not equivalent: a false tag is a wrong statement shown
        as fact, a missing one is a gap the labeling queue can fill.
        """
        report = {"per_mood": {
            "Fast": {"precision": 0.79, "recall": 0.31, "f1": 0.44},
            "Sunny": {"precision": 0.16, "recall": 0.95, "f1": 0.27},
        }}
        allowed = derive_allowlist(report)
        assert "Fast" in allowed
        assert "Sunny" not in allowed

    def test_threshold_is_adjustable(self):
        report = {"per_mood": {"Dance": {"precision": 0.52, "recall": 0.3, "f1": 0.37}}}
        assert "Dance" in derive_allowlist(report, min_precision=0.45)
        assert "Dance" not in derive_allowlist(report, min_precision=0.60)

    def test_unknown_moods_are_rejected(self):
        report = {"per_mood": {"NotAMood": {"precision": 0.99, "recall": 0.99, "f1": 0.99}}}
        assert derive_allowlist(report) == frozenset()

    def test_empty_report_allows_nothing(self):
        assert derive_allowlist({}) == frozenset()


class TestCrossValidate:
    def test_separable_moods_score_well(self):
        """A mood that really is a function of the features should be learnable
        — otherwise a low score everywhere would prove nothing."""
        training = (
            [(["Fast"], _features(tempo=175, energy=0.9)) for _ in range(30)]
            + [(["Slow"], _features(tempo=65, energy=0.2)) for _ in range(30)]
        )
        stats = compute_global_stats([f for _, f in training])
        report = cross_validate(training, stats, folds=3)
        assert report["per_mood"]["Fast"]["precision"] > 0.8

    def test_is_deterministic(self):
        """A shifting allowlist would make pipeline output non-reproducible."""
        training = (
            [(["Fast"], _features(tempo=170)) for _ in range(20)]
            + [(["Slow"], _features(tempo=70)) for _ in range(20)]
        )
        stats = compute_global_stats([f for _, f in training])
        assert cross_validate(training, stats) == cross_validate(training, stats)

    def test_too_few_rows_is_safe(self):
        report = cross_validate([], {})
        assert report["per_mood"] == {}
        assert derive_allowlist(report) == frozenset()


class TestTrainingSelection:
    def test_only_audit_rows_are_ground_truth(self):
        """claude_batch rows are themselves model output. Scoring against them
        would measure agreement between two classifiers, not fidelity to the
        owner."""
        tracks = [
            {"mood_source": "audit", "mood_tags": ["Fast"], "audio_features": _features()},
            {"mood_source": "claude_batch", "mood_tags": ["Slow"], "audio_features": _features()},
            {"mood_source": "centroid", "mood_tags": ["Moody"], "audio_features": _features()},
        ]
        training = _training_from_tracks(tracks)
        assert len(training) == 1
        assert training[0][0] == ["Fast"]

    def test_rows_without_features_are_skipped(self):
        tracks = [{"mood_source": "audit", "mood_tags": ["Fast"], "audio_features": None}]
        assert _training_from_tracks(tracks) == []
