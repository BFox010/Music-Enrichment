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
        assert out == {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}

    def test_garbage_response(self) -> None:
        # Not even a dict
        out = _extract_lastfm_fields("nope")  # type: ignore[arg-type]
        assert out == {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}

    def test_tag_without_name_skipped(self) -> None:
        response = {
            "track": {
                "toptags": {"tag": [{"name": "ok"}, {"url": "no-name"}, {"name": ""}]},
                "artist": {},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["ok"]


# ── Crash safety: an interrupted run keeps what it already fetched ────────


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
