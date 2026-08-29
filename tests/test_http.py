"""Tests for pipeline._http.RateLimitedClient.

Cache load/save, negative-cache expiry, and the force modes. Network calls are
faked at the ``client.session.get`` boundary — nothing here touches a socket.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from pipeline._http import (
    FORCE_ALL,
    FORCE_ERRORS,
    FORCE_OFF,
    RateLimitedClient,
    _error_kind,
    _is_expired,
)

URL = "https://example.test/api"


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

    def test_flush_on_unwritable_path_logs_and_does_not_raise(self) -> None:
        # flush() runs inside `finally` in every phase — it must never mask the
        # exception that got us there.
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            c = RateLimitedClient(blocker / "sub" / "c.json", rate_per_second=1.0)
            c.cache["k"] = "v"
            c.flush()  # must not raise

    def test_rejects_non_positive_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="rate_per_second"):
                RateLimitedClient(Path(tmp) / "c.json", rate_per_second=0)

    def test_rejects_non_positive_flush_every(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="flush_every"):
                RateLimitedClient(Path(tmp) / "c.json", rate_per_second=1.0, flush_every=0)

    def test_rejects_unknown_force_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="force"):
                RateLimitedClient(Path(tmp) / "c.json", rate_per_second=1.0, force="yes")


# ── Entry classification / expiry (pure) ──


class TestErrorKind:
    def test_success_entry_has_no_kind(self) -> None:
        assert _error_kind({"track": {"name": "Roads"}}) is None

    def test_non_dict_entry_has_no_kind(self) -> None:
        assert _error_kind("nope") is None

    def test_not_found_is_its_own_kind(self) -> None:
        assert _error_kind({"_error": "not_found"}) == "not_found"

    def test_max_retries_is_transient(self) -> None:
        assert _error_kind({"_error": "max_retries"}) == "transient"

    def test_invalid_json_is_transient(self) -> None:
        assert _error_kind({"_error": "invalid_json: <html>"}) == "transient"


class TestIsExpired:
    NEG = 100.0
    TRANS = 10.0

    def _expired(self, entry, now: float = 1000.0) -> bool:
        return _is_expired(entry, now, self.NEG, self.TRANS)

    def test_success_never_expires(self) -> None:
        assert not self._expired({"track": {}, "_cached_at": 0})

    def test_fresh_not_found_is_not_expired(self) -> None:
        assert not self._expired({"_error": "not_found", "_cached_at": 950.0})

    def test_aged_not_found_is_expired(self) -> None:
        assert self._expired({"_error": "not_found", "_cached_at": 800.0})

    def test_transient_expires_on_the_shorter_ttl(self) -> None:
        # 20s old: past the 10s transient TTL, well inside the 100s negative one
        entry = {"_error": "max_retries", "_cached_at": 980.0}
        assert self._expired(entry)
        assert not self._expired({"_error": "not_found", "_cached_at": 980.0})

    def test_legacy_entry_without_timestamp_is_expired(self) -> None:
        # Pre-TTL cache files: one free retry pass on upgrade.
        assert self._expired({"_error": "not_found"})

    def test_future_timestamp_is_expired(self) -> None:
        # A skewed clock must not pin an entry forever.
        assert self._expired({"_error": "not_found", "_cached_at": 9999.0})


# ── get() against a faked session ──


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    """Stands in for requests.Session; counts calls and replays queued responses."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.headers: dict = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected network call")
        return self.responses.pop(0)


def _client(tmp: str, *, cache=None, responses=(), **kwargs) -> RateLimitedClient:
    """Build a client with a seeded cache and a faked session.

    ``rate_per_second`` is huge so the rate limiter never sleeps; the TTLs are
    tiny so expiry can be exercised with explicit timestamps rather than waiting.
    """
    path = Path(tmp) / "c.json"
    if cache is not None:
        path.write_text(json.dumps(cache), encoding="utf-8")
    kwargs.setdefault("rate_per_second", 10000.0)
    kwargs.setdefault("negative_ttl", 100.0)
    kwargs.setdefault("transient_ttl", 10.0)
    c = RateLimitedClient(path, **kwargs)
    c.session = _FakeSession(responses)
    return c


OK_BODY = {"track": {"name": "Roads"}}


class TestNegativeCacheExpiry:
    def test_fresh_not_found_is_not_refetched(self) -> None:
        # AC: a not_found entry is not re-fetched within the TTL.
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "not_found", "_cached_at": time.time()}}
            c = _client(tmp, cache=seeded, responses=[])
            assert c.get(URL, {}, "k")["_error"] == "not_found"
            assert c.session.calls == 0
            assert c.stats["hits"] == 1

    def test_aged_not_found_is_refetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "not_found", "_cached_at": time.time() - 500}}
            c = _client(tmp, cache=seeded, responses=[_FakeResponse(200, OK_BODY)])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 1
            assert c.stats["expired_retries"] == 1

    def test_aged_max_retries_is_refetched(self) -> None:
        # AC: an entry cached as max_retries is re-attempted after the TTL.
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "max_retries", "_cached_at": time.time() - 60}}
            c = _client(tmp, cache=seeded, responses=[_FakeResponse(200, OK_BODY)])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 1

    def test_fresh_max_retries_is_not_refetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "max_retries", "_cached_at": time.time()}}
            c = _client(tmp, cache=seeded, responses=[])
            assert c.get(URL, {}, "k")["_error"] == "max_retries"
            assert c.session.calls == 0

    def test_legacy_error_entry_is_refetched(self) -> None:
        # AC: existing cache files load without error after the format change —
        # and their poisoned entries get one retry.
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"k": {"_error": "max_retries"}},
                        responses=[_FakeResponse(200, OK_BODY)])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 1

    def test_legacy_success_entry_is_served_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"k": OK_BODY}, responses=[])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 0

    def test_404_write_carries_a_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, responses=[_FakeResponse(404)])
            out = c.get(URL, {}, "k")
            assert out["_error"] == "not_found"
            assert isinstance(out["_cached_at"], float)

    def test_success_heals_a_cached_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "not_found", "_cached_at": time.time() - 500}}
            c = _client(tmp, cache=seeded, responses=[_FakeResponse(200, OK_BODY)])
            c.get(URL, {}, "k")
            assert c.cache["k"] == OK_BODY
            assert "_error" not in c.cache["k"]


class TestForceModes:
    def test_force_all_refetches_a_fresh_success(self) -> None:
        # AC: --force re-fetches regardless of cache state.
        with tempfile.TemporaryDirectory() as tmp:
            new_body = {"track": {"name": "Glory Box"}}
            c = _client(tmp, cache={"k": OK_BODY}, force=FORCE_ALL,
                        responses=[_FakeResponse(200, new_body)])
            assert c.get(URL, {}, "k") == new_body
            assert c.session.calls == 1
            assert c.stats["forced"] == 1

    def test_force_errors_refetches_a_fresh_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {"k": {"_error": "not_found", "_cached_at": time.time()}}
            c = _client(tmp, cache=seeded, force=FORCE_ERRORS,
                        responses=[_FakeResponse(200, OK_BODY)])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 1

    def test_force_errors_leaves_a_fresh_success_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"k": OK_BODY}, force=FORCE_ERRORS, responses=[])
            assert c.get(URL, {}, "k") == OK_BODY
            assert c.session.calls == 0

    def test_force_does_not_refetch_the_same_key_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"k": OK_BODY}, force=FORCE_ALL,
                        responses=[_FakeResponse(200, OK_BODY)])
            c.get(URL, {}, "k")
            c.get(URL, {}, "k")
            assert c.session.calls == 1

    def test_force_off_is_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"k": OK_BODY}, responses=[])
            assert c.force == FORCE_OFF
            c.get(URL, {}, "k")
            assert c.session.calls == 0


class TestObservability:
    def test_error_counts_split_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = {
                "a": OK_BODY,
                "b": {"_error": "not_found", "_cached_at": 1.0},
                "c": {"_error": "max_retries", "_cached_at": 1.0},
                "d": {"_error": "not_found"},
            }
            c = _client(tmp, cache=seeded)
            assert c.error_counts() == {"not_found": 2, "transient": 1}

    def test_cache_summary_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, cache={"a": OK_BODY, "b": {"_error": "not_found"}})
            summary = c.cache_summary()
            assert "2 entries" in summary
            assert "1 not_found" in summary

    def test_warn_if_forced_is_silent_when_off(self, caplog) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp)
            with caplog.at_level("WARNING"):
                c.warn_if_forced(100)
            assert caplog.records == []

    def test_warn_if_forced_warns_when_forced(self, caplog) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, force=FORCE_ALL)
            with caplog.at_level("WARNING"):
                c.warn_if_forced(100)
            assert any("force=all" in r.getMessage() for r in caplog.records)


class TestFlushBatching:
    def test_entries_persist_once_flush_every_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, flush_every=2,
                        responses=[_FakeResponse(200, OK_BODY) for _ in range(2)])
            c.get(URL, {}, "k1")
            assert not c.cache_path.exists()  # still buffered
            c.get(URL, {}, "k2")
            on_disk = json.loads(c.cache_path.read_text(encoding="utf-8"))
            assert set(on_disk) == {"k1", "k2"}


class TestFlushThresholdGrowth:
    """F-08c: a fixed flush_every rewrites the whole, ever-growing cache dict
    every N new entries, so cumulative bytes written over a large first-time
    run scale roughly with entry_count**2. The threshold must instead grow
    with the cache's current size — the array-doubling trick — so a run with
    thousands of new entries doesn't pay for that quadratic blow-up, while a
    small cache (the common case) behaves exactly as flush_every describes.
    """

    def test_threshold_is_flush_every_for_a_small_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, flush_every=50)
            c.cache = {str(i): OK_BODY for i in range(10)}
            assert c._next_flush_threshold() == 50

    def test_threshold_grows_once_the_cache_is_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(tmp, flush_every=50)
            c.cache = {str(i): OK_BODY for i in range(2000)}
            assert c._next_flush_threshold() == 200  # 10% of 2000, > flush_every

    def test_flush_count_grows_sublinearly_with_entry_count(self) -> None:
        """Direct evidence of the fix: over N new entries, the number of full
        rewrites must land well under the old fixed-cadence count
        (N // flush_every) — logarithmic-ish growth, not linear-in-N."""
        n = 5000
        flush_every = 50
        with tempfile.TemporaryDirectory() as tmp:
            c = _client(
                tmp, flush_every=flush_every,
                responses=[_FakeResponse(200, OK_BODY) for _ in range(n)],
            )
            flush_calls = 0
            original_flush = c.flush

            def counting_flush():
                nonlocal flush_calls
                flush_calls += 1
                original_flush()

            c.flush = counting_flush
            for i in range(n):
                c.get(URL, {}, f"k{i}")

        old_fixed_cadence_flushes = n // flush_every  # 100
        assert 0 < flush_calls < old_fixed_cadence_flushes / 3

    def test_cumulative_bytes_written_are_far_below_fixed_cadence(self) -> None:
        """The whole point: total bytes written across a large run — the sum
        of every flushed snapshot's size — must be far smaller than a fixed
        flush_every would have produced for the same data, not just fewer
        flush *calls* that each happen to write more each time."""
        n = 4000
        flush_every = 50
        entry_bytes = len(json.dumps(OK_BODY, ensure_ascii=False))

        def run_with_threshold(threshold_fn) -> int:
            with tempfile.TemporaryDirectory() as tmp:
                c = _client(
                    tmp, flush_every=flush_every,
                    responses=[_FakeResponse(200, OK_BODY) for _ in range(n)],
                )
                c._next_flush_threshold = threshold_fn.__get__(c)
                total = 0
                original_flush = c.flush

                def counting_flush():
                    nonlocal total
                    total += len(json.dumps(c.cache, ensure_ascii=False))
                    original_flush()

                c.flush = counting_flush
                for i in range(n):
                    c.get(URL, {}, f"k{i}")
                counting_flush()  # final explicit flush, as every phase does
                return total

        actual_bytes = run_with_threshold(RateLimitedClient._next_flush_threshold)
        fixed_cadence_bytes = run_with_threshold(lambda self: flush_every)

        # Same data, same entry count, same flush_every floor — only the
        # growth behavior differs. Growing the threshold should cut total
        # bytes written by a large factor at this scale.
        assert actual_bytes < fixed_cadence_bytes / 3
        # Sanity: the fixed-cadence baseline really is roughly quadratic-ish
        # for this run — otherwise the comparison above is meaningless.
        assert fixed_cadence_bytes > n * entry_bytes * (n / flush_every) / 4
