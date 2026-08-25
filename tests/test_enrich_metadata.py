"""Tests for pipeline.enrich_metadata._extract_lastfm_fields.

Note: tests do NOT hit the Last.fm API. They only exercise pure response parsing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import pipeline.enrich_metadata as mod
from pipeline.enrich_metadata import _extract_lastfm_fields


class TestExtractLastfmFields:
    def test_full_response(self) -> None:
        response = {
            "track": {
                "name": "Roads",
                "mbid": "abc-123",
                "artist": {"name": "Portishead", "mbid": "def-456"},
                "toptags": {
                    "tag": [
                        {"name": "trip hop", "url": "..."},
                        {"name": "90s", "url": "..."},
                        {"name": "melancholic", "url": "..."},
                    ]
                },
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["trip hop", "90s", "melancholic"]
        assert out["musicbrainz_id"] == "abc-123"
        assert out["artist_mbid"] == "def-456"

    def test_single_tag_as_dict(self) -> None:
        # Last.fm sometimes returns a dict instead of a list when there's only one tag
        response = {
            "track": {
                "mbid": "x",
                "artist": {"mbid": "y"},
                "toptags": {"tag": {"name": "rock", "url": "..."}},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["rock"]

    def test_missing_toptags(self) -> None:
        response = {"track": {"mbid": "x", "artist": {"mbid": "y"}}}
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == []
        assert out["musicbrainz_id"] == "x"

    def test_missing_mbids(self) -> None:
        response = {"track": {"toptags": {"tag": []}, "artist": {}}}
        out = _extract_lastfm_fields(response)
        assert out["musicbrainz_id"] is None
        assert out["artist_mbid"] is None

    def test_empty_string_mbid_becomes_none(self) -> None:
        response = {"track": {"mbid": "", "artist": {"mbid": ""}}}
        out = _extract_lastfm_fields(response)
        assert out["musicbrainz_id"] is None
        assert out["artist_mbid"] is None

    def test_error_response(self) -> None:
        response = {"_error": "not_found"}
        out = _extract_lastfm_fields(response)
        assert out == {
            "lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None,
            "lastfm_duration_ms": None, "lastfm_listeners": None, "lastfm_playcount": None,
        }

    def test_garbage_response(self) -> None:
        # Not even a dict
        out = _extract_lastfm_fields("nope")  # type: ignore[arg-type]
        assert out == {
            "lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None,
            "lastfm_duration_ms": None, "lastfm_listeners": None, "lastfm_playcount": None,
        }

    def test_duration_listeners_playcount_captured(self) -> None:
        """#41 — already-fetched fields that were previously discarded."""
        response = {
            "track": {
                "mbid": "abc-123",
                "duration": "245000",
                "listeners": "918234",
                "playcount": "5102934",
                "artist": {"mbid": "def-456"},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_duration_ms"] == 245000
        assert out["lastfm_listeners"] == 918234
        assert out["lastfm_playcount"] == 5102934

    def test_zero_duration_means_unknown_not_zero(self) -> None:
        """Last.fm returns "0" for duration/listeners/playcount when it does
        not know the value, not to mean the value is actually zero."""
        response = {"track": {"duration": "0", "listeners": "0", "playcount": "0"}}
        out = _extract_lastfm_fields(response)
        assert out["lastfm_duration_ms"] is None
        assert out["lastfm_listeners"] is None
        assert out["lastfm_playcount"] is None

    def test_missing_or_unparseable_popularity_fields_are_none(self) -> None:
        response = {"track": {"duration": "not-a-number"}}
        out = _extract_lastfm_fields(response)
        assert out["lastfm_duration_ms"] is None
        assert out["lastfm_listeners"] is None
        assert out["lastfm_playcount"] is None

    def test_tag_without_name_skipped(self) -> None:
        response = {
            "track": {
                "toptags": {"tag": [{"name": "ok"}, {"url": "no-name"}, {"name": ""}]},
                "artist": {},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["ok"]


# ── Crash safety: an interrupted run keeps what it already fetched ──


class _InterruptingClient:
    """A client that serves N responses then raises, as a Ctrl-C would.

    Mirrors enough of RateLimitedClient for the phase: `get` records into a real
    dict, `flush` writes it out. The point of the test is that `flush` runs at
    all when the loop dies part-way through.
    """

    def __init__(self, cache_path: Path, fail_after: int) -> None:
        self.cache_path = cache_path
        self.fail_after = fail_after
        self.cache: dict = {}
        self.stats: dict = {}
        self.calls = 0

    def get(self, _url, _params, cache_key):
        self.calls += 1
        if self.calls > self.fail_after:
            raise KeyboardInterrupt("simulated Ctrl-C")
        self.cache[cache_key] = {"track": {"mbid": f"mbid-{self.calls}"}}
        return self.cache[cache_key]

    def flush(self) -> None:
        self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")

    def warn_if_forced(self, _n_requests: int) -> None:
        pass

    def cache_summary(self) -> str:
        return "cache (fake)"


def _track_row(n: int) -> dict:
    return {
        "artist": f"Artist {n}", "track": f"Track {n}",
        "artist_normalized": f"artist {n}", "track_normalized": f"track {n}",
    }


class TestInterruptedRunPersistsCache:
    def test_flush_runs_when_the_loop_is_interrupted(self, monkeypatch) -> None:
        monkeypatch.setenv("LASTFM_API_KEY", "test-key")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "lastfm.json"
            client = _InterruptingClient(cache_path, fail_after=3)
            monkeypatch.setattr(mod, "RateLimitedClient", lambda *a, **kw: client)

            inp = Path(tmp) / "in.jsonl"
            with open(inp, "w", encoding="utf-8") as fh:
                for n in range(10):
                    fh.write(json.dumps(_track_row(n)) + "\n")

            with pytest.raises(KeyboardInterrupt):
                mod.enrich(input_path=inp, output_path=Path(tmp) / "out.jsonl")

            # Everything fetched before the interrupt survived.
            assert cache_path.exists(), "cache was never flushed"
            assert len(json.loads(cache_path.read_text(encoding="utf-8"))) == 3


# ── #41: duration/listeners/playcount gap-fill at the enrich() level ──


class _FakeClient:
    """Serves one canned response to every request. No network, no retries."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.cache: dict = {}
        self.stats: dict = {}

    def get(self, _url, _params, cache_key):
        self.cache[cache_key] = self.response
        return self.response

    def flush(self) -> None:
        pass

    def warn_if_forced(self, _n_requests: int) -> None:
        pass

    def cache_summary(self) -> str:
        return "cache (fake)"


class TestPopularityGapFill:
    def _run(self, monkeypatch, tracks: list[dict], response: dict) -> list[dict]:
        monkeypatch.setenv("LASTFM_API_KEY", "test-key")
        monkeypatch.setattr(mod, "RateLimitedClient", lambda *a, **kw: _FakeClient(response))
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            with open(inp, "w", encoding="utf-8") as fh:
                for t in tracks:
                    fh.write(json.dumps(t) + "\n")
            mod.enrich(input_path=inp, output_path=out)
            return [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]

    def test_null_duration_is_filled_from_lastfm(self, monkeypatch) -> None:
        track = _track_row(0)
        track["duration_ms"] = None
        response = {"track": {"mbid": "x", "duration": "180000"}}
        written = self._run(monkeypatch, [track], response)
        assert written[0]["duration_ms"] == 180000

    def test_existing_duration_is_never_overwritten(self, monkeypatch) -> None:
        """Exportify/ReccoBeats values must win over Last.fm's gap-fill."""
        track = _track_row(0)
        track["duration_ms"] = 200000
        response = {"track": {"mbid": "x", "duration": "999999"}}
        written = self._run(monkeypatch, [track], response)
        assert written[0]["duration_ms"] == 200000

    def test_listeners_and_playcount_are_stored(self, monkeypatch) -> None:
        track = _track_row(0)
        response = {"track": {"mbid": "x", "listeners": "1000", "playcount": "50000"}}
        written = self._run(monkeypatch, [track], response)
        assert written[0]["lastfm_listeners"] == 1000
        assert written[0]["lastfm_playcount"] == 50000

    def test_no_response_data_leaves_fields_absent(self, monkeypatch) -> None:
        track = _track_row(0)
        response = {"_error": "not_found"}
        written = self._run(monkeypatch, [track], response)
        assert written[0].get("lastfm_listeners") is None
        assert written[0].get("lastfm_playcount") is None
        assert written[0].get("duration_ms") is None


class TestExportMbidSurvivesAFailedLookup:
    """#74: Phase 2 seeds MBIDs from the export, so Phase 4 must never trade a
    known identifier for the blank an error produces.

    ``canonical_track_id`` is ``mbid:`` for most of the library, so nulling one
    re-keys the row and orphans its hand-edited fields at the Phase 8 merge
    (CLAUDE.md invariant 4).
    """

    def _run(self, monkeypatch, tracks: list[dict], response: dict) -> list[dict]:
        monkeypatch.setenv("LASTFM_API_KEY", "test-key")
        monkeypatch.setattr(mod, "RateLimitedClient", lambda *a, **kw: _FakeClient(response))
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            with open(inp, "w", encoding="utf-8") as fh:
                for t in tracks:
                    fh.write(json.dumps(t) + "\n")
            mod.enrich(input_path=inp, output_path=out)
            return [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]

    @staticmethod
    def _seeded() -> dict:
        track = _track_row(0)
        track["musicbrainz_id"] = "mbid-from-export"
        track["artist_mbid"] = "artist-mbid-from-export"
        track["lastfm_tags"] = ["trip-hop"]
        return track

    def test_transient_error_does_not_null_the_mbids(self, monkeypatch) -> None:
        written = self._run(monkeypatch, [self._seeded()], {"_error": "max_retries"})
        assert written[0]["musicbrainz_id"] == "mbid-from-export"
        assert written[0]["artist_mbid"] == "artist-mbid-from-export"

    def test_canonical_track_id_is_unchanged_by_a_failed_lookup(self, monkeypatch) -> None:
        from pipeline.schema import compute_canonical_track_id

        before = compute_canonical_track_id(self._seeded())
        written = self._run(monkeypatch, [self._seeded()], {"_error": "max_retries"})
        assert compute_canonical_track_id(written[0]) == before == "mbid:mbid-from-export"

    def test_transient_error_does_not_wipe_existing_tags(self, monkeypatch) -> None:
        written = self._run(monkeypatch, [self._seeded()], {"_error": "max_retries"})
        assert written[0]["lastfm_tags"] == ["trip-hop"]

    def test_a_successful_lookup_still_overwrites(self, monkeypatch) -> None:
        """The guard must not freeze a row that Last.fm can genuinely improve."""
        response = {"track": {"mbid": "fresh-mbid",
                              "artist": {"mbid": "fresh-artist-mbid"},
                              "toptags": {"tag": [{"name": "downtempo"}]}}}
        written = self._run(monkeypatch, [self._seeded()], response)
        assert written[0]["musicbrainz_id"] == "fresh-mbid"
        assert written[0]["artist_mbid"] == "fresh-artist-mbid"
        assert written[0]["lastfm_tags"] == ["downtempo"]

    def test_a_successful_lookup_may_prune_tags_to_nothing(self, monkeypatch) -> None:
        """tag_filter learning a new noise rule must still be able to clear tags."""
        response = {"track": {"mbid": "fresh-mbid", "toptags": {"tag": []}}}
        written = self._run(monkeypatch, [self._seeded()], response)
        assert written[0]["lastfm_tags"] == []

    def test_a_row_with_no_mbid_is_unaffected(self, monkeypatch) -> None:
        written = self._run(monkeypatch, [_track_row(1)], {"_error": "not_found"})
        assert written[0]["musicbrainz_id"] is None
        assert written[0]["artist_mbid"] is None
