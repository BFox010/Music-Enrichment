"""Tests for pipeline._http.RateLimitedClient (cache load/save only).

Network calls are not exercised here — they need a mock HTTP layer which is
beyond the Phase 4/5 scope.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline._http import RateLimitedClient


class TestRateLimitedClientCache:
    def test_load_missing_cache_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "missing.json"
            c = RateLimitedClient(cache, rate_per_second=1.0)
            assert c.cache == {}

    def test_load_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "c.json"
            cache.write_text(json.dumps({"foo": {"bar": 1}}), encoding="utf-8")
            c = RateLimitedClient(cache, rate_per_second=1.0)
            assert c.cache == {"foo": {"bar": 1}}

    def test_load_corrupt_cache_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "c.json"
            cache.write_text("{not json", encoding="utf-8")
            c = RateLimitedClient(cache, rate_per_second=1.0)
            assert c.cache == {}

    def test_flush_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "c.json"
            c = RateLimitedClient(cache, rate_per_second=1.0)
            c.cache["foo"] = {"value": 42}
            c.flush()
            assert json.loads(cache.read_text(encoding="utf-8")) == {"foo": {"value": 42}}

    def test_flush_writes_atomically(self) -> None:
        # No .tmp file should remain after a successful flush
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "c.json"
            c = RateLimitedClient(cache, rate_per_second=1.0)
            c.cache["k"] = "v"
            c.flush()
            assert not (Path(tmp) / "c.json.tmp").exists()
