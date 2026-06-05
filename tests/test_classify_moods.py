"""Tests for pipeline.classify_moods centroid algorithm."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.classify_moods import (
    CENTROID_MOOD_GATES,
    CENTROID_MOOD_TREATMENTS,
    CENTROID_SUPPRESSED_MOODS,
    MOODY_TEMPO_MAX,
    SLOW_TEMPO_MAX,
    _split_moods,
    apply_centroid_policy,
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

    def test_suppressed_moods_filtered_from_output(self) -> None:
        """Centroid predictions for CENTROID_SUPPRESSED_MOODS are dropped.

        Audit / Claude-batch paths still emit these moods — only the
        centroid path is gated. See 2026-05-25 spot-check verdict in
        music_enrichment_todo.md.
        """
        stats = compute_global_stats([_features()])
        training = [
            (["Dark"], _features(energy=0.9, valence=0.1)),
            (["Fast"], _features(tempo=170, energy=0.9)),
            (["Heartbreak"], _features(valence=0.2, energy=0.4)),
            (["Heavy Bass"], _features(energy=0.95, acousticness=0.05)),
            (["Sad"], _features(valence=0.1, energy=0.2, tempo=80)),
        ]
        centroids = compute_centroids(training, stats)

        # Pick a feature point near the Sad centroid so Sad is nearest;
        # the suppressed moods are also close enough to be within threshold
        # but should be filtered from output.
        moods, _ = classify_track(
            _features(valence=0.1, energy=0.2, tempo=80),
            stats, centroids, threshold=10.0, max_assignments=5,
        )
        assert "Sad" in moods
        assert "Dark" not in moods
        assert "Fast" not in moods
        assert "Heartbreak" not in moods
        assert "Heavy Bass" not in moods

    def test_suppression_set_is_overridable(self) -> None:
        """Caller-supplied suppressed_moods overrides the module default."""
        stats = compute_global_stats([_features()])
        training = [(["Dark"], _features())]
        centroids = compute_centroids(training, stats)

        # Empty suppression: Dark should now come through
        moods, _ = classify_track(
            _features(), stats, centroids,
            threshold=10.0, suppressed_moods=frozenset(),
        )
        assert "Dark" in moods

    def test_suppressed_moods_constant_matches_spotcheck_verdict(self) -> None:
        """Guards the canonical set so it can't drift without test update.

        Heavy Bass added 2026-06-05 (feature-inadequate, ~10x over-tag in
        Rock/Indie/Metal vs human). See docs/mood_centroid_decisions.md.
        """
        assert CENTROID_SUPPRESSED_MOODS == frozenset(
            {"Dark", "Fast", "Heartbreak", "Heavy Bass"}
        )


class TestCentroidPolicy:
    """Suppress / gate framework — CENTROID_MOOD_TREATMENTS + apply_centroid_policy."""

    def test_treatments_dict_consistent(self) -> None:
        # Every treatment is one of the two known kinds.
        for mood, meta in CENTROID_MOOD_TREATMENTS.items():
            assert meta["treatment"] in ("suppress", "gate")
            assert meta.get("reason")
            assert meta.get("evidence")
        # Suppressed set is exactly the suppress-treatment moods.
        assert CENTROID_SUPPRESSED_MOODS == frozenset(
            m for m, v in CENTROID_MOOD_TREATMENTS.items() if v["treatment"] == "suppress"
        )
        # Gates are exactly the gate-treatment moods.
        gated = {m for m, v in CENTROID_MOOD_TREATMENTS.items() if v["treatment"] == "gate"}
        assert set(CENTROID_MOOD_GATES) == gated
        # A mood is gated XOR suppressed, never both.
        assert CENTROID_SUPPRESSED_MOODS.isdisjoint(set(CENTROID_MOOD_GATES))

    def test_policy_suppresses(self) -> None:
        out = apply_centroid_policy(["Heavy Bass", "Dance"], _features())
        assert out == ["Dance"]

    def test_policy_preserves_order(self) -> None:
        out = apply_centroid_policy(["Dance", "Groove", "Hype"], _features())
        assert out == ["Dance", "Groove", "Hype"]

    def test_slow_gate_emits_when_slow_tempo(self) -> None:
        out = apply_centroid_policy(["Slow"], _features(tempo=SLOW_TEMPO_MAX - 10))
        assert out == ["Slow"]

    def test_slow_gate_drops_fast_tempo(self) -> None:
        out = apply_centroid_policy(["Slow"], _features(tempo=SLOW_TEMPO_MAX + 20))
        assert out == []

    def test_moody_gate_keeps_slow_drops_fast(self) -> None:
        assert apply_centroid_policy(["Moody"], _features(tempo=110)) == ["Moody"]
        # Blinding Lights case: 171 BPM should not be Moody.
        assert apply_centroid_policy(["Moody"], _features(tempo=171)) == []

    def test_gate_missing_tempo_drops(self) -> None:
        af = _features()
        af.pop("tempo")
        assert apply_centroid_policy(["Slow"], af) == []
        assert apply_centroid_policy(["Moody"], af) == []

    def test_gate_runs_before_truncation(self) -> None:
        """A gated-out mood must not consume a max_assignments slot."""
        stats = compute_global_stats([_features()])
        # Four distinct centroids; the nearest happens to be a fast 'Slow'.
        training = [
            (["Slow"], _features(tempo=150, energy=0.5)),   # nearest, but fast → gated out
            (["Dance"], _features(tempo=150, energy=0.51)),
            (["Groove"], _features(tempo=150, energy=0.52)),
            (["Hype"], _features(tempo=150, energy=0.53)),
        ]
        centroids = compute_centroids(training, stats)
        moods, _ = classify_track(
            _features(tempo=150, energy=0.5), stats, centroids,
            threshold=10.0, max_assignments=3,
        )
        # Slow gated out (tempo 150 > 105); slot freed → 3 real moods returned.
        assert "Slow" not in moods
        assert len(moods) == 3


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
