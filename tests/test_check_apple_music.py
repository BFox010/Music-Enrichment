"""Tests for pipeline.check_apple_music helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from pipeline import check_apple_music as cam
from pipeline.check_apple_music import (
    DEFAULT_INPUT,
    _INPUT_PRIORITY,
    _artwork_from_itunes,
    _best_match,
    _is_stale,
    _upsize_itunes_artwork,
)
from pipeline.config import (
    TRACKS_RESOLVED_PATH,
    TRACKS_WITH_DISCOGS_PATH,
    TRACKS_WITH_GENRE_BACKFILL_PATH,
    TRACKS_WITH_GENRES_PATH,
)


class TestInputPriority:
    """Regression guard: phase 5 must consume the deepest genre-bearing file.

    The manifest declares the chain, but the orchestrator calls each phase with
    no args, so the module's own input preference is what actually runs. If the
    deepest predecessor is not preferred here, the genres those phases produced
    are silently dropped from tracks.jsonl even though they succeed (the bug
    fixed 2026-05-31 was exactly this, for phase 4c)."""

    def test_deepest_file_is_preferred(self) -> None:
        # Phase 4e (identity resolution) is the immediate predecessor. This
        # assertion named 4d until 4e was inserted between them and the priority
        # list was not updated: 4e's row clustering was computed, written, and
        # then read past, leaving duplicate canonical_track_ids in tracks.jsonl.
        assert _INPUT_PRIORITY[0] == TRACKS_RESOLVED_PATH
        assert DEFAULT_INPUT == TRACKS_RESOLVED_PATH

    def test_identity_resolution_outranks_genre_backfill(self) -> None:
        assert _INPUT_PRIORITY.index(TRACKS_RESOLVED_PATH) < \
            _INPUT_PRIORITY.index(TRACKS_WITH_GENRE_BACKFILL_PATH)

    def test_genre_files_outrank_discogs(self) -> None:
        # 4b output still reachable if 4c/4d were skipped, just lower priority.
        assert TRACKS_WITH_DISCOGS_PATH in _INPUT_PRIORITY
        assert _INPUT_PRIORITY.index(TRACKS_WITH_GENRE_BACKFILL_PATH) < \
            _INPUT_PRIORITY.index(TRACKS_WITH_GENRES_PATH) < \
            _INPUT_PRIORITY.index(TRACKS_WITH_DISCOGS_PATH)


class TestBestMatch:
    def test_exact_match_found(self) -> None:
        response = {
            "results": [
                {"artistName": "Some Other Artist", "trackName": "Roads", "trackId": 1},
                {"artistName": "Portishead", "trackName": "Roads", "trackId": 999},
            ]
        }
        match = _best_match(response, "portishead", "roads")
        assert match is not None
        assert match["trackId"] == 999

    def test_no_match_returns_none(self) -> None:
        response = {"results": [{"artistName": "Foo", "trackName": "Bar", "trackId": 1}]}
        assert _best_match(response, "portishead", "roads") is None

    def test_empty_results(self) -> None:
        assert _best_match({"results": []}, "x", "y") is None

    def test_normalization_match(self) -> None:
        # iTunes returns "The Beatles" with leading "the" — should normalize equal
        response = {"results": [{"artistName": "The Beatles", "trackName": "The End", "trackId": 5}]}
        match = _best_match(response, "beatles", "the end")
        assert match is not None
        assert match["trackId"] == 5

    def test_diacritics_match(self) -> None:
        response = {"results": [{"artistName": "Beyoncé", "trackName": "Halo", "trackId": 7}]}
        match = _best_match(response, "beyonce", "halo")
        assert match is not None

    def test_error_response(self) -> None:
        assert _best_match({"_error": "not_found"}, "x", "y") is None

    def test_non_dict_response(self) -> None:
        assert _best_match(None, "x", "y") is None  # type: ignore[arg-type]
        assert _best_match([], "x", "y") is None  # type: ignore[arg-type]

    def test_skips_non_dict_results(self) -> None:
        response = {"results": ["garbage", None, {"artistName": "X", "trackName": "Y", "trackId": 1}]}
        match = _best_match(response, "x", "y")
        assert match is not None
        assert match["trackId"] == 1


class TestIsStale:
    def test_none_is_stale(self) -> None:
        assert _is_stale(None) is True

    def test_invalid_format_is_stale(self) -> None:
        assert _is_stale("not-a-date") is True

    def test_recent_is_fresh(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        assert _is_stale(recent) is False

    def test_old_is_stale(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d")
        assert _is_stale(old) is True


# A real cached artworkUrl100, so the rewrite is tested against the shape the
# iTunes CDN actually hands out.
_ART_BASE = (
    "https://is1-ssl.mzstatic.com/image/thumb/Music124/v4/55/88/c0/"
    "5588c084-e86a-3b40-aa26-66be2d735ec4/20UMGIM50515.rgb.jpg"
)
_ART_100 = f"{_ART_BASE}/100x100bb.jpg"
_ART_600 = f"{_ART_BASE}/600x600bb.jpg"


class TestUpsizeItunesArtwork:
    def test_rewrites_the_size_segment(self) -> None:
        assert _upsize_itunes_artwork(_ART_100) == _ART_600

    def test_only_the_final_segment_is_touched(self) -> None:
        """The path above the size segment ends in ``.rgb.jpg`` — a careless
        rewrite of the first ``.jpg`` or ``100x100`` would break the URL."""
        url = "https://is1-ssl.mzstatic.com/image/thumb/100x100bb/a.jpg/100x100bb.png"
        assert _upsize_itunes_artwork(url) == (
            "https://is1-ssl.mzstatic.com/image/thumb/100x100bb/a.jpg/600x600bb.png"
        )

    def test_unrecognised_shape_is_left_alone(self) -> None:
        for url in (f"{_ART_BASE}/60x60bb.jpg", "https://example.com/cover.jpg"):
            assert _upsize_itunes_artwork(url) == url


class TestArtworkFromItunes:
    def test_upsized_from_artworkurl100(self) -> None:
        assert _artwork_from_itunes({"artworkUrl100": _ART_100}) == _ART_600

    def test_missing_or_blank_is_none(self) -> None:
        assert _artwork_from_itunes({}) is None
        assert _artwork_from_itunes({"artworkUrl100": "  "}) is None
        assert _artwork_from_itunes({"artworkUrl100": None}) is None


class _StubItunesClient:
    """Canned iTunes Search responses keyed by cache key."""

    def __init__(self, by_key: dict[str, dict]) -> None:
        self._by_key = by_key
        self.stats: dict[str, int] = {}

    def get(self, url, params, cache_key):
        return self._by_key.get(cache_key, {"resultCount": 0, "results": []})

    def flush(self) -> None:
        pass

    def warn_if_forced(self, n: int) -> None:
        pass

    def cache_summary(self) -> str:
        return "stub"


class TestCheckRecordsArtwork:
    @staticmethod
    def _run(monkeypatch, tmp_path, tracks, by_key):
        src = tmp_path / "in.jsonl"
        src.write_text("".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8")
        out = tmp_path / "out.jsonl"
        monkeypatch.setattr(cam, "RateLimitedClient", lambda *a, **k: _StubItunesClient(by_key))
        stats = cam.check(input_path=src, output_path=out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
        return stats, {r["track"]: r for r in rows}

    @staticmethod
    def _track(**extra) -> dict:
        return {"artist": "Portishead", "track": "Roads",
                "artist_normalized": "portishead", "track_normalized": "roads", **extra}

    _HIT = {"portishead|roads": {"resultCount": 1, "results": [{
        "artistName": "Portishead", "trackName": "Roads", "trackId": 42,
        "artworkUrl100": _ART_100,
    }]}}

    def test_match_fills_artwork_with_provenance(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(monkeypatch, tmp_path, [self._track()], self._HIT)
        row = rows["Roads"]
        assert row["artwork_url"] == _ART_600
        assert row["artwork_source"] == "itunes_search"
        assert row["artwork_retrieved_at"] == row["apple_music_checked_at"]
        assert stats["artwork"] == 1

    def test_deezer_artwork_from_5a_is_kept(self, monkeypatch, tmp_path) -> None:
        deezer = {"artwork_url": "https://cdn-images.dzcdn.net/images/cover/x/1000x1000.jpg",
                  "artwork_source": "deezer", "artwork_retrieved_at": "2026-01-01"}
        stats, rows = self._run(monkeypatch, tmp_path, [self._track(**deezer)], self._HIT)
        for key, value in deezer.items():
            assert rows["Roads"][key] == value
        assert stats["artwork"] == 0

    def test_no_match_records_no_artwork(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(monkeypatch, tmp_path, [self._track()], {})
        assert rows["Roads"]["apple_music_available"] is False
        assert "artwork_url" not in rows["Roads"]

    def test_failed_lookup_keeps_existing_artwork(self, monkeypatch, tmp_path) -> None:
        deezer = {"artwork_url": "https://cdn-images.dzcdn.net/images/cover/x/1000x1000.jpg",
                  "artwork_source": "deezer"}
        stats, rows = self._run(
            monkeypatch, tmp_path, [self._track(**deezer)],
            {"portishead|roads": {"_error": "max_retries"}},
        )
        assert rows["Roads"]["artwork_url"] == deezer["artwork_url"]
