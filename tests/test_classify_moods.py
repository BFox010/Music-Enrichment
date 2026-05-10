"""Tests for pipeline.classify_moods centroid algorithm."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.classify_moods import (
    _split_moods,
    classify_track,
    compute_centroids,
    compute_global_stats,
    euclidean,
    load_audit,
    load_claude_results,
    to_vector,
    write_claude_batch,
)


def _features(**kwargs) -> dict:
    """Default audio_features dict; override any field."""
    base = {
        "danceability": 0.5, "energy": 0.5, "valence": 0.5,
        "speechiness": 0.05, "acousticness": 0.3,
        "instrumentalness": 0.0, "liveness": 0.1,
        "tempo": 120.0, "loudness": -10.0,
    }
    base.update(kwargs)
    return base


class TestSplitMoods:
    def test_comma(self) -> None:
        assert _split_moods("Sad, Slow, Moody") == ["Sad", "Slow", "Moody"]

    def test_semicolon(self) -> None:
        assert _split_moods("Sad; Slow") == ["Sad", "Slow"]

    def test_pipe(self) -> None:
        assert _split_moods("Sad|Slow") == ["Sad", "Slow"]

    def test_unknown_dropped(self) -> None:
        assert _split_moods("Sad, Garbage, Slow") == ["Sad", "Slow"]

    def test_case_normalized(self) -> None:
        assert _split_moods("sad, MOODY") == ["Sad", "Moody"]

    def test_empty(self) -> None:
        assert _split_moods("") == []


class TestComputeGlobalStats:
    def test_basic(self) -> None:
        feats = [_features(tempo=100, loudness=-10),
                 _features(tempo=140, loudness=-5),
                 _features(tempo=120, loudness=-15)]
        stats = compute_global_stats(feats)
        assert stats["tempo"]["mean"] == 120.0
        assert stats["loudness"]["mean"] == -10.0
        assert stats["tempo"]["std"] > 0

    def test_empty(self) -> None:
        stats = compute_global_stats([])
        assert stats["tempo"]["mean"] == 0.0
        assert stats["tempo"]["std"] == 1.0

    def test_missing_values_skipped(self) -> None:
        feats = [_features(tempo=100), _features(tempo=None), _features(tempo=140)]
        stats = compute_global_stats(feats)
        assert stats["tempo"]["mean"] == 120.0


class TestToVector:
    def test_length(self) -> None:
        stats = compute_global_stats([_features()])
        v = to_vector(_features(), stats)
        assert len(v) == 9  # 7 linear + 2 scaled

    def test_missing_linear_becomes_zero(self) -> None:
        stats = compute_global_stats([_features()])
        v = to_vector({"tempo": 120.0, "loudness": -10.0}, stats)
        # First 7 (linear) should all be 0
        assert v[:7] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def test_z_normalization(self) -> None:
        feats = [_features(tempo=100), _features(tempo=120), _features(tempo=140)]
        stats = compute_global_stats(feats)
        v_low = to_vector(_features(tempo=100), stats)
        v_high = to_vector(_features(tempo=140), stats)
        # Tempo is the second-to-last element (index -2)
        assert v_low[-2] < 0
        assert v_high[-2] > 0


class TestComputeCentroids:
    def test_simple_two_moods(self) -> None:
        stats = compute_global_stats([_features(tempo=100), _features(tempo=140)])
        training = [
            (["Slow"], _features(tempo=80, energy=0.2)),
            (["Slow"], _features(tempo=80, energy=0.25)),
            (["Fast"], _features(tempo=160, energy=0.85)),
            (["Fast"], _features(tempo=170, energy=0.9)),
        ]
        centroids = compute_centroids(training, stats)
        assert "Slow" in centroids
        assert "Fast" in centroids
        # Slow centroid should have lower energy than Fast centroid
        # energy is the second-element of the linear keys (index 1)
        slow = centroids["Slow"]
        fast = centroids["Fast"]
        assert slow[1] < fast[1]

    def test_multi_tag_track_contributes_to_each(self) -> None:
        stats = compute_global_stats([_features()])
        training = [
            (["Sad", "Slow"], _features(energy=0.2)),
            (["Sad", "Slow"], _features(energy=0.25)),
        ]
        centroids = compute_centroids(training, stats)
        assert "Sad" in centroids
        assert "Slow" in centroids

    def test_unknown_mood_skipped(self) -> None:
        stats = compute_global_stats([_features()])
        training = [(["NotARealMood"], _features())]
        centroids = compute_centroids(training, stats)
        assert centroids == {}


class TestClassifyTrack:
    def test_picks_nearest(self) -> None:
        stats = compute_global_stats([_features(tempo=100), _features(tempo=140)])
        training = [
            (["Slow"], _features(tempo=80, energy=0.2)),
            (["Slow"], _features(tempo=80, energy=0.25)),
            (["Fast"], _features(tempo=160, energy=0.85)),
            (["Fast"], _features(tempo=170, energy=0.9)),
        ]
        centroids = compute_centroids(training, stats)

        # New track with low tempo + low energy should get Slow
        moods, nearest = classify_track(
            _features(tempo=85, energy=0.22), stats, centroids,
            threshold=10.0,  # very permissive
        )
        assert moods[0] == "Slow"
        assert nearest is not None and nearest >= 0

    def test_threshold_filters_out(self) -> None:
        stats = compute_global_stats([_features()])
        training = [(["Slow"], _features(energy=0.2))]
        centroids = compute_centroids(training, stats)
        moods, _ = classify_track(_features(energy=0.9), stats, centroids,
                                  threshold=0.05)
        assert moods == []

    def test_max_assignments(self) -> None:
        stats = compute_global_stats([_features()])
        training = [(m, _features()) for m in (["A"], ["B"], ["C"], ["D"])]
        # All centroids are identical, so distance to track is 0 for all
        # — but the moods aren't in MOOD_CATEGORIES so won't be returned
        # Use canonical moods instead
        training = [
            (["Slow"], _features()),
            (["Sad"], _features()),
            (["Moody"], _features()),
            (["Dark"], _features()),
        ]
        centroids = compute_centroids(training, stats)
        moods, _ = classify_track(_features(), stats, centroids,
                                  threshold=10.0, max_assignments=2)
        assert len(moods) == 2

    def test_empty_features_returns_empty(self) -> None:
        moods, nearest = classify_track({}, {}, {})
        assert moods == []
        assert nearest is None


class TestEuclidean:
    def test_zero(self) -> None:
        assert euclidean([1, 2, 3], [1, 2, 3]) == 0.0

    def test_basic(self) -> None:
        assert abs(euclidean([0, 0], [3, 4]) - 5.0) < 1e-9


class TestAuditAndClaudeIO:
    def test_load_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.csv"
            path.write_text(
                "artist,track,mood_tags\n"
                "Portishead,Roads,\"Sad, Slow, Moody\"\n"
                "Daft Punk,One More Time,\"Dance, Hype\"\n",
                encoding="utf-8",
            )
            rows = load_audit(path)
            assert len(rows) == 2
            assert rows[0]["mood_tags"] == ["Sad", "Slow", "Moody"]

    def test_load_audit_missing(self) -> None:
        assert load_audit(Path("nonexistent.csv")) == []

    def test_audit_alternate_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.csv"
            path.write_text(
                "Artist Name,Track Name,moods\n"
                "Portishead,Roads,Sad;Slow\n",
                encoding="utf-8",
            )
            rows = load_audit(path)
            assert len(rows) == 1
            assert rows[0]["mood_tags"] == ["Sad", "Slow"]

    def test_round_trip_claude_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.jsonl"
            tracks = [{
                "artist": "Portishead", "track": "Roads",
                "artist_normalized": "portishead", "track_normalized": "roads",
                "audio_features": _features(), "lastfm_tags": ["trip-hop"],
            }]
            n = write_claude_batch(tracks, path)
            assert n == 1
            content = path.read_text(encoding="utf-8").strip()
            payload = json.loads(content)
            assert payload["artist"] == "Portishead"
            assert payload["audio_features"]["energy"] == 0.5

    def test_load_claude_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "artist_normalized": "portishead",
                    "track_normalized": "roads",
                    "mood_tags": ["Sad", "Slow"],
                }) + "\n")
            results = load_claude_results(path)
            assert results[("portishead", "roads")] == ["Sad", "Slow"]
