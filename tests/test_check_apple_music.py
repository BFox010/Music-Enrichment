"""Tests for pipeline.check_apple_music helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from pipeline.check_apple_music import (
    DEFAULT_INPUT,
    _INPUT_PRIORITY,
    _best_match,
    _is_stale,
)
from pipeline.config import (
    TRACKS_RESOLVED_PATH,
    TRACKS_WITH_DISCOGS_PATH,
    TRACKS_WITH_GENRE_BACKFILL_PATH,
    TRACKS_WITH_GENRES_PATH,
    TRACKS_WITH_ISRCS_PATH,
)


class TestInputPriority:
    """Regression guard: phase 5 must consume the deepest genre-bearing file.

    The orchestrator hands each phase its predecessor's output (#98), but a
    standalone run still uses the module's own input preference. If the
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

    def test_isrc_output_sits_between_identity_and_genre_backfill(self) -> None:
        """5a runs after 4d and before 4e, so its output ranks in that gap (#132)."""
        assert _INPUT_PRIORITY.index(TRACKS_RESOLVED_PATH) < \
            _INPUT_PRIORITY.index(TRACKS_WITH_ISRCS_PATH) < \
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


# ── check() end-to-end against a faked iTunes (#109) ──


class _Resp:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.headers: dict = {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.headers: dict = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


HIT = {"results": [{"artistName": "Portishead", "trackName": "Roads", "trackId": 999}]}
ROW = {"artist": "Portishead", "track": "Roads",
       "artist_normalized": "portishead", "track_normalized": "roads"}


def _run(monkeypatch, tmp_path, rows, responses, *, cache=None):
    """Run check() over ``rows`` with a real client whose session is faked."""
    import pipeline.check_apple_music as cam
    from pipeline._http import RateLimitedClient

    monkeypatch.setattr("pipeline._http.time.sleep", lambda _s: None)
    cache_path = tmp_path / "apple.json"
    if cache is not None:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    session = _Session(responses)

    def factory(_path, **kwargs):
        kwargs["rate_per_second"] = 10000.0
        client = RateLimitedClient(cache_path, **kwargs)
        client.session = session
        return client

    monkeypatch.setattr(cam, "RateLimitedClient", factory)
    src, out = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    stats = cam.check(input_path=src, output_path=out)
    written = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
    return stats, written, session


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


class TestCheckErrorsAreUnknown:
    def test_a_transient_error_does_not_become_unavailable(self, monkeypatch, tmp_path) -> None:
        """A 503 used to write available=False with today's date, which the
        freshness gate then protected for 90 days."""
        prior = {**ROW, "apple_music_available": True, "apple_music_id": "999",
                 "apple_music_checked_at": _days_ago(200)}
        stats, rows, _ = _run(monkeypatch, tmp_path, [prior], [_Resp(503)] * 5)
        assert stats["errors"] == 1
        assert rows[0]["apple_music_available"] is True
        assert rows[0]["apple_music_id"] == "999"
        assert rows[0]["apple_music_checked_at"] == prior["apple_music_checked_at"]

    def test_an_unchecked_row_stays_unchecked_on_error(self, monkeypatch, tmp_path) -> None:
        _, rows, _ = _run(monkeypatch, tmp_path, [dict(ROW)], [_Resp(503)] * 5)
        assert "apple_music_available" not in rows[0]
        assert "apple_music_checked_at" not in rows[0]

    def test_an_empty_result_is_a_checked_miss(self, monkeypatch, tmp_path) -> None:
        stats, rows, _ = _run(monkeypatch, tmp_path, [dict(ROW)], [_Resp(200, {"results": []})])
        assert stats["unavailable"] == 1 and stats["errors"] == 0
        assert rows[0]["apple_music_available"] is False
        assert rows[0]["apple_music_checked_at"]
        cached = json.loads((tmp_path / "apple.json").read_text(encoding="utf-8"))
        assert cached["portishead|roads"]["_error"] == "not_found"


class TestStaleRowsReachTheNetwork:
    def test_a_stale_row_refetches_despite_a_cached_success(self, monkeypatch, tmp_path) -> None:
        stale = {**ROW, "apple_music_available": False, "apple_music_id": None,
                 "apple_music_checked_at": _days_ago(200)}
        _, rows, session = _run(
            monkeypatch, tmp_path, [stale], [_Resp(200, HIT)],
            cache={"portishead|roads": {"results": []}},
        )
        assert session.calls == 1
        assert rows[0]["apple_music_available"] is True
        assert rows[0]["apple_music_id"] == "999"

    def test_a_fresh_row_is_skipped(self, monkeypatch, tmp_path) -> None:
        fresh = {**ROW, "apple_music_available": True, "apple_music_checked_at": _days_ago(5)}
        stats, _, session = _run(monkeypatch, tmp_path, [fresh], [])
        assert stats["skipped_fresh"] == 1
        assert session.calls == 0

    def test_an_undated_row_uses_the_cache(self, monkeypatch, tmp_path) -> None:
        """A full run rebuilds rows from scrobbles, so they arrive undated;
        re-asking each of those would re-fetch the whole library every run."""
        _, rows, session = _run(
            monkeypatch, tmp_path, [dict(ROW)], [], cache={"portishead|roads": HIT},
        )
        assert session.calls == 0
        assert rows[0]["apple_music_available"] is True
