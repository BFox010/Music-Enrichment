"""Tests for pipeline.classify_moods centroid algorithm."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.classify_moods import (
    _split_moods,
    calibrate_thresholds,
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

    def test_no_quota_by_default(self) -> None:
        """Every mood inside its radius is emitted — no fixed slot count.

        The old rule took the nearest three centroids regardless of fit, which
        forced three tags onto ~88% of the library. Assignment is now an
        absolute per-mood judgement, so a track sitting near four centroids
        gets four tags.
        """
        stats = compute_global_stats([_features()])
        training = [
            (["Slow"], _features()),
            (["Sad"], _features()),
            (["Moody"], _features()),
            (["Dark"], _features()),
        ]
        centroids = compute_centroids(training, stats)
        moods, _ = classify_track(_features(), stats, centroids, threshold=10.0)
        assert len(moods) == 4

    def test_max_assignments_is_an_optional_cap(self) -> None:
        """The cap still works when a caller explicitly asks for one."""
        stats = compute_global_stats([_features()])
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

    def test_allowlist_gates_output(self) -> None:
        """Only moods on the allowlist are emitted by the centroid path.

        The allowlist is derived from cross-validated F1 (pipeline.
        evaluate_moods) rather than hand-maintained, but the gating mechanism
        itself is what this covers.
        """
        stats = compute_global_stats([_features()])
        training = [
            (["Dark"], _features(energy=0.9, valence=0.1)),
            (["Fast"], _features(tempo=170, energy=0.9)),
            (["Sad"], _features(valence=0.1, energy=0.2, tempo=80)),
        ]
        centroids = compute_centroids(training, stats)

        moods, _ = classify_track(
            _features(valence=0.1, energy=0.2, tempo=80),
            stats, centroids, threshold=10.0,
            allowed_moods=frozenset({"Fast", "Sad"}),
        )
        assert "Sad" in moods
        assert "Dark" not in moods

    def test_allowlist_none_disables_gating(self) -> None:
        """``allowed_moods=None`` lets everything through — used by the
        evaluation harness, which must measure the unfiltered classifier."""
        stats = compute_global_stats([_features()])
        training = [(["Dark"], _features())]
        centroids = compute_centroids(training, stats)

        moods, _ = classify_track(
            _features(), stats, centroids, threshold=10.0, allowed_moods=None,
        )
        assert "Dark" in moods


class TestCalibrateThresholds:
    def test_tight_mood_gets_tighter_cutoff(self) -> None:
        """A mood whose examples cluster tightly earns a smaller radius than a
        diffuse one — the thing a single global threshold cannot express."""
        stats = compute_global_stats([_features(energy=e / 10) for e in range(11)])
        training = [(["Slow"], _features(energy=0.50)) for _ in range(30)]
        training += [(["Moody"], _features(energy=e / 10)) for e in range(11)] * 3
        centroids = compute_centroids(training, stats)
        thresholds = calibrate_thresholds(training, stats, centroids, min_support=5)
        assert thresholds["Slow"] < thresholds["Moody"]

    def test_low_support_moods_omitted(self) -> None:
        """Under min_support a mood is left out so callers fall back to the
        global default rather than calibrating on noise."""
        stats = compute_global_stats([_features()])
        training = [(["Slow"], _features())] * 3
        centroids = compute_centroids(training, stats)
        thresholds = calibrate_thresholds(training, stats, centroids, min_support=20)
        assert "Slow" not in thresholds

    def test_empty_training_is_safe(self) -> None:
        assert calibrate_thresholds([], {}, {}) == {}


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


class TestOwnerLabelRecovery:
    """inputs/ is gitignored, so a fresh clone has no audit CSV.

    Without recovery the phase would find no owner labels, fall through to the
    centroid for every row, and destroy every hand-made judgement in the
    library. These labels are the training signal the classifier is built on —
    losing them is unrecoverable, so the guard is load-bearing.
    """

    def _library(self):
        return [
            {"artist": "A", "track": "one", "artist_normalized": "a",
             "track_normalized": "one", "mood_tags": ["Moody", "Dark"],
             "mood_source": "audit", "mood_confidence": "high",
             "audio_features": _features(energy=0.3, valence=0.2)},
            {"artist": "B", "track": "two", "artist_normalized": "b",
             "track_normalized": "two", "mood_tags": ["Fast"],
             "mood_source": "claude_batch", "mood_confidence": "high",
             "audio_features": _features(tempo=170)},
            {"artist": "C", "track": "three", "artist_normalized": "c",
             "track_normalized": "three", "mood_tags": ["Slow"],
             "mood_source": "centroid", "mood_confidence": "medium",
             "audio_features": _features(tempo=80)},
        ]

    def test_recovers_audit_and_claude_labels(self):
        from pipeline.classify_moods import _recover_owner_labels

        recovered = _recover_owner_labels(self._library())
        assert ("a", "one") in recovered
        assert ("b", "two") in recovered

    def test_centroid_rows_are_not_treated_as_owner_labels(self):
        from pipeline.classify_moods import _recover_owner_labels

        recovered = _recover_owner_labels(self._library())
        assert ("c", "three") not in recovered

    def test_recovered_tags_are_copied_not_aliased(self):
        """Mutating the recovered list must not reach back into the track row."""
        from pipeline.classify_moods import _recover_owner_labels

        library = self._library()
        recovered = _recover_owner_labels(library)
        recovered[("a", "one")]["mood_tags"].append("Injected")
        assert library[0]["mood_tags"] == ["Moody", "Dark"]

    def test_rows_without_identity_are_skipped(self):
        from pipeline.classify_moods import _recover_owner_labels

        rows = [{"artist_normalized": "", "track_normalized": "",
                 "mood_tags": ["Fast"], "mood_source": "audit"}]
        assert _recover_owner_labels(rows) == {}


class TestAuditFallbackToCommittedCSV:
    """Phase 6 must find owner labels on a fresh clone.

    ``inputs/`` is gitignored, so ``inputs/existing_audit.csv`` is absent after a
    clone. ``_recover_owner_labels`` covers labels already written into the
    tracks file, but cannot recover a label for a track that never carried one —
    which is exactly what the committed ``mood_audit.csv`` supplies.
    """

    @staticmethod
    def _track(artist: str, track: str, **over) -> dict:
        row = {
            "artist": artist, "track": track,
            "artist_normalized": artist.lower(), "track_normalized": track.lower(),
            "audio_features": _features(),
            "mood_tags": None, "mood_source": None,
        }
        row.update(over)
        return row

    def _run(self, tmp: Path, *, write_root_csv: bool) -> list[dict]:
        import pipeline.classify_moods as cm

        library = [
            self._track("Portishead", "Roads", audio_features=_features(energy=0.2, valence=0.1)),
            self._track("Daft Punk", "One More Time", audio_features=_features(energy=0.9, valence=0.9)),
        ]
        tracks_file = tmp / "tracks_with_availability.jsonl"
        tracks_file.write_text(
            "".join(json.dumps(r) + "\n" for r in library), encoding="utf-8"
        )

        if write_root_csv:
            (tmp / "mood_audit.csv").write_text(
                "artist,track,mood_tags\n"
                "Portishead,Roads,\"Sad, Slow\"\n"
                "Daft Punk,One More Time,\"Dance, Hype\"\n",
                encoding="utf-8",
            )

        out = tmp / "out.jsonl"
        # Redirect the Claude-batch write too: it resolves from INPUTS_DIR at
        # import, so patching REPO_ROOT alone would leave it writing into the
        # real repo. The suite must not touch the working tree.
        original, original_batch = cm.REPO_ROOT, cm.CLAUDE_BATCH_PATH
        cm.REPO_ROOT = tmp
        cm.CLAUDE_BATCH_PATH = tmp / "claude_mood_batch.jsonl"
        try:
            cm.classify(
                audit_path=tmp / "inputs" / "existing_audit.csv",   # deliberately absent
                tracks_path=tracks_file,
                output_path=out,
                claude_results_path=tmp / "nonexistent_claude.jsonl",
                run_log_path=tmp / "run.log",
            )
        finally:
            cm.REPO_ROOT, cm.CLAUDE_BATCH_PATH = original, original_batch
        return [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_committed_csv_is_used_when_inputs_csv_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._run(Path(tmp), write_root_csv=True)
        by = {r["track_normalized"]: r for r in rows}
        assert by["roads"]["mood_source"] == "audit"
        assert by["roads"]["mood_tags"] == ["Sad", "Slow"]
        assert by["one more time"]["mood_source"] == "audit"

    def test_without_the_committed_csv_there_is_no_owner_label(self) -> None:
        """Guards the test above: absent the CSV, nothing supplies 'audit'."""
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._run(Path(tmp), write_root_csv=False)
        assert all(r.get("mood_source") != "audit" for r in rows)
