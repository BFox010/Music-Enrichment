"""Shared HTTP utilities: rate-limited client with retry and disk-backed cache.

Each client instance owns its own rate limit and cache file.

Cache: a flat JSON dict keyed by ``cache_key``. Successes stored raw as the API
returned them; failures as ``{"_error": <reason>, "_cached_at": <epoch>}``, whose
timestamp is what lets the entry expire. An entry without ``_cached_at`` reads
as expired. Only failures expire, so a search API's empty result needs
``classify_empty`` to store the miss as ``not_found``; a plain success is cached
permanently until ``--force`` (#109).
"""

from __future__ import annotations

import json
import random
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from pipeline.config import (
    HTTP_AUTH_BREAKER_THRESHOLD,
    HTTP_BACKOFF_BASE,
    HTTP_BACKOFF_MAX,
    HTTP_MAX_RETRIES,
    HTTP_NEGATIVE_TTL_SECONDS,
    HTTP_TRANSIENT_TTL_SECONDS,
    get_logger,
)

log = get_logger(__name__)

# Force modes. "off" honours the cache; "errors" re-fetches cached failures only
# (the cheap way to clear poisoned entries); "all" bypasses the cache entirely.
FORCE_OFF = "off"
FORCE_ERRORS = "errors"
FORCE_ALL = "all"
FORCE_MODES = (FORCE_OFF, FORCE_ERRORS, FORCE_ALL)

# Error-kind labels used to pick a TTL. ``classify`` may also return
# ``KIND_AUTH``, which is transient and counts toward the circuit breaker.
KIND_NOT_FOUND = "not_found"
KIND_TRANSIENT = "transient"
KIND_AUTH = "auth_error"

# ``_error`` values as stable as a 404, sharing its long TTL. Don't add
# ``auth_error``: it is never retried within a run (see ``get``), but a 30-day
# entry would keep serving the refusal long after the operator fixes the
# credential. The 6 h transient TTL lets the fix land on the next run.
_STABLE_ERRORS = frozenset({"not_found", "bad_request"})

# Statuses worth retrying. Every other 4xx is deterministic — retrying it spends
# the full ~8-11s backoff ladder per cache key to reach the same refusal.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


def _error_kind(entry: Any) -> str | None:
    """``None`` for a success, else the error kind. ``not_found`` and
    ``bad_request`` are stable negatives; everything else (``auth_error``,
    ``max_retries``, ``invalid_json``) is transient.
    """
    if not isinstance(entry, dict):
        return None
    error = entry.get("_error")
    if not error:
        return None
    return KIND_NOT_FOUND if error in _STABLE_ERRORS else KIND_TRANSIENT


def _is_expired(
    entry: Any,
    now: float,
    negative_ttl: float,
    transient_ttl: float,
) -> bool:
    """True if ``entry`` is a failure whose TTL has elapsed.

    Successes never expire here; an empty search result expires only if its
    caller's ``classify`` made it a ``not_found`` (#109). A missing ``_cached_at``
    or one in the future counts as expired, so a skewed clock can't pin an entry.
    """
    kind = _error_kind(entry)
    if kind is None:
        return False
    cached_at = entry.get("_cached_at")
    if not isinstance(cached_at, (int, float)):
        return True
    if cached_at > now:
        return True
    ttl = negative_ttl if kind == KIND_NOT_FOUND else transient_ttl
    return (now - cached_at) > ttl


# Last.fm signals errors in the body ({"error": 6} no such track, 29 rate limit,
# 4/10/26 bad key). Codes describing the *request* (2, 3, 5, 6, 7) are stable
# no-matches on the 30 d TTL; everything else (key, session, service, rate
# limit) is transient, so a fixed key or recovered service shows on the next run.
LASTFM_STABLE_ERROR_CODES = frozenset({2, 3, 5, 6, 7})
# Bad, failed or suspended API key: transient like any auth_error, and counted
# by the circuit breaker so a dead key stops the run's requests.
LASTFM_AUTH_ERROR_CODES = frozenset({4, 10, 26})


def classify_lastfm(body: Any) -> str | None:
    """``classify`` hook for the Last.fm API. See ``RateLimitedClient.get``."""
    if not isinstance(body, dict):
        return None
    code = body.get("error")
    if code is None:
        return None
    if code in LASTFM_STABLE_ERROR_CODES:
        return KIND_NOT_FOUND
    return KIND_AUTH if code in LASTFM_AUTH_ERROR_CODES else KIND_TRANSIENT


def classify_empty(*path: str) -> Callable[[Any], str | None]:
    """``classify`` hook for a search API that answers a miss with a 200 and an
    empty list at ``path`` (``"results"``; ``"tracks", "items"``).

    The miss is stored as ``not_found`` on the 30 d TTL rather than as a success
    that never expires (#109). Callers must treat that ``not_found`` exactly as
    they treated the empty body. Any other shape passes through as a success.
    """
    def classify(body: Any) -> str | None:
        node = body
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return KIND_NOT_FOUND if isinstance(node, list) and not node else None
    return classify


def _retry_after_seconds(response: Any) -> float | None:
    """Seconds from a ``Retry-After`` header (delta or HTTP-date), else None."""
    headers = getattr(response, "headers", None) or {}
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


class RateLimitedClient:
    """Rate-limited HTTP client with exponential backoff and a JSON disk cache.

    ``user_agent`` is required by MusicBrainz. ``flush_every`` is the minimum
    new entries between disk writes (see ``_next_flush_threshold``). ``force``
    is one of ``FORCE_MODES``. The TTLs are injectable so tests need not sleep.
    After ``auth_breaker_threshold`` consecutive auth errors the circuit opens:
    no further requests this run, and uncached keys get an uncached auth_error.
    """

    def __init__(
        self,
        cache_path: Path,
        *,
        rate_per_second: float,
        user_agent: str = "MusicEnrichment/1.0",
        flush_every: int = 50,
        force: str = FORCE_OFF,
        negative_ttl: float = HTTP_NEGATIVE_TTL_SECONDS,
        transient_ttl: float = HTTP_TRANSIENT_TTL_SECONDS,
        auth_breaker_threshold: int = HTTP_AUTH_BREAKER_THRESHOLD,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be greater than 0")
        if flush_every <= 0:
            raise ValueError("flush_every must be greater than 0")
        if force not in FORCE_MODES:
            raise ValueError(f"force must be one of {FORCE_MODES}, got {force!r}")
        self.cache_path = cache_path
        self.min_interval = 1.0 / rate_per_second
        self.flush_every = flush_every
        self.force = force
        self.negative_ttl = negative_ttl
        self.transient_ttl = transient_ttl
        self.auth_breaker_threshold = auth_breaker_threshold
        self._consecutive_auth_errors = 0
        self.circuit_open = False
        self._last_request = 0.0
        self._dirty_count = 0
        # Keys already re-fetched during this run. Under --force a repeated key
        # must not cost a second request.
        self._refetched: set[str] = set()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.cache: dict[str, Any] = self._load_cache()
        self.stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "expired_retries": 0,
            "forced": 0,
            "fetches": 0,
            "circuit_skipped": 0,
        }

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Cache %s unreadable (%s) — starting fresh", self.cache_path, e)
            return {}

    def flush(self) -> None:
        """Write the cache to disk.

        Never raises: the API phases call this from ``finally`` blocks, and a
        write failure there must not mask the exception that got us there.
        """
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.cache, fh, ensure_ascii=False)
            tmp.replace(self.cache_path)
            self._dirty_count = 0
        except OSError as e:
            log.error("Failed to write cache %s: %s", self.cache_path, e)

    # ── Observability ──

    def error_counts(self) -> dict[str, int]:
        """Cached failures by kind — separates a genuine no-match rate from a
        poisoned cache.
        """
        counts = {KIND_NOT_FOUND: 0, KIND_TRANSIENT: 0}
        for entry in self.cache.values():
            kind = _error_kind(entry)
            if kind is not None:
                counts[kind] += 1
        return counts

    def cache_summary(self) -> str:
        """One-line cache report for phase logs."""
        errors = self.error_counts()
        summary = (
            "cache %s: %d entries (%d not_found, %d transient) — "
            "%d hits, %d misses, %d expired-retries, %d forced, %d fetched"
            % (
                self.cache_path.name,
                len(self.cache),
                errors[KIND_NOT_FOUND],
                errors[KIND_TRANSIENT],
                self.stats["hits"],
                self.stats["misses"],
                self.stats["expired_retries"],
                self.stats["forced"],
                self.stats["fetches"],
            )
        )
        if self.circuit_open:
            summary += ", CIRCUIT OPEN (%d skipped)" % self.stats["circuit_skipped"]
        return summary

    def warn_if_forced(self, n_requests: int) -> None:
        """Log the estimated wall time when a forced re-fetch is about to run."""
        if self.force == FORCE_OFF:
            return
        minutes = n_requests * self.min_interval / 60
        log.warning(
            "force=%s on %s — up to %d requests at %.2f req/s (~%.0f min)",
            self.force, self.cache_path.name, n_requests,
            1.0 / self.min_interval, minutes,
        )

    # ── Fetch ──

    def _should_refetch(self, cache_key: str, refresh: bool = False) -> bool:
        """Decide whether ``cache_key`` must go to the network. Updates stats."""
        if cache_key not in self.cache:
            self.stats["misses"] += 1
            return True
        # Already re-fetched this run — serve what we just stored.
        if cache_key in self._refetched:
            self.stats["hits"] += 1
            return False
        if refresh:
            self.stats["expired_retries"] += 1
            return True
        entry = self.cache[cache_key]
        if self.force == FORCE_ALL or (
            self.force == FORCE_ERRORS and _error_kind(entry) is not None
        ):
            self.stats["forced"] += 1
            return True
        if _is_expired(entry, time.time(), self.negative_ttl, self.transient_ttl):
            self.stats["expired_retries"] += 1
            return True
        self.stats["hits"] += 1
        return False

    def get(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str,
        *,
        timeout: float = 15.0,
        classify: Callable[[Any], str | None] | None = None,
        refresh: bool = False,
    ) -> Any:
        """GET ``url``, caching the JSON response under ``cache_key``. A failure
        is cached and returned as ``{"_error": "<reason>", "_cached_at": <epoch>}``.

        ``classify`` maps a 200 body to ``"transient"``/``"auth_error"`` (6 h),
        ``"not_found"`` (30 d) or ``None`` (success); without it an in-band
        failure is cached as a success and never expires. ``refresh`` skips a
        cached entry for this call, for a caller whose own freshness gate expired.
        """
        if not self._should_refetch(cache_key, refresh):
            return self.cache[cache_key]
        if self.circuit_open:
            # Don't cache this: it would overwrite whatever the key held.
            self.stats["circuit_skipped"] += 1
            return {"_error": KIND_AUTH, "_circuit_open": True}

        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        self.stats["fetches"] += 1
        result: Any = None
        for attempt in range(HTTP_MAX_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=timeout)
                self._last_request = time.monotonic()
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError:
                        result = {"_error": f"invalid_json: {r.text[:200]}"}
                        break
                    kind = classify(body) if classify is not None else None
                    if kind is None:
                        result = body
                    else:
                        # Stored as a failure, not a success, so that it
                        # expires -- an in-band error cached as a success is
                        # permanent.
                        result = {"_error": kind, "_detail": repr(body)[:200]}
                        if kind == KIND_AUTH:
                            log.error("in-band auth error for %s — credential "
                                      "rejected; not retrying", cache_key)
                        else:
                            log.debug("in-band %s for %s", kind, cache_key)
                    break
                if r.status_code == 404:
                    result = {"_error": "not_found"}
                    break
                if r.status_code in (401, 403):
                    # Deterministic and operator-actionable, so ERROR, not
                    # DEBUG. Once per key until the circuit breaker opens.
                    log.error("HTTP %s for %s — credential rejected; not retrying",
                              r.status_code, cache_key)
                    result = {"_error": KIND_AUTH, "_status": r.status_code}
                    break
                if 400 <= r.status_code < 500 and r.status_code not in _RETRYABLE_STATUSES:
                    log.debug("HTTP %s for %s — deterministic; not retrying",
                              r.status_code, cache_key)
                    result = {"_error": "bad_request", "_status": r.status_code}
                    break
                if r.status_code == 429:
                    log.warning("429 rate-limited; backing off")
                # 5xx and the retryable 4xx → backoff and retry
                if attempt == HTTP_MAX_RETRIES - 1:
                    continue
                wait = HTTP_BACKOFF_BASE * (2 ** attempt) + random.random()
                if r.status_code in (429, 503):
                    retry_after = _retry_after_seconds(r)
                    if retry_after is not None:
                        wait = retry_after
                wait = min(wait, HTTP_BACKOFF_MAX)
                log.debug("HTTP %s on attempt %d/%d for %s — waiting %.1fs",
                          r.status_code, attempt + 1, HTTP_MAX_RETRIES, cache_key, wait)
                time.sleep(wait)
            except requests.RequestException as e:
                self._last_request = time.monotonic()
                if attempt == HTTP_MAX_RETRIES - 1:
                    continue
                wait = min(HTTP_BACKOFF_BASE * (2 ** attempt), HTTP_BACKOFF_MAX)
                # Never `e` itself: requests puts the full URL in the message,
                # query string included, and Last.fm's api_key and Discogs'
                # token travel there — straight into runs/*.log (#104).
                log.debug("Network error %s from %s on attempt %d/%d — waiting %.1fs",
                          type(e).__name__, urlsplit(url).hostname,
                          attempt + 1, HTTP_MAX_RETRIES, wait)
                time.sleep(wait)
        else:
            result = {"_error": "max_retries"}

        self._note_auth_outcome(result, cache_key)

        # Stamp failures so they expire. A success overwrites the old error
        # entry outright, so entries heal rather than accumulate.
        if _error_kind(result) is not None:
            result["_cached_at"] = time.time()

        self.cache[cache_key] = result
        self._refetched.add(cache_key)
        self._dirty_count += 1
        if self._dirty_count >= self._next_flush_threshold():
            self.flush()
        return result

    def _note_auth_outcome(self, result: Any, cache_key: str) -> None:
        """Count consecutive auth errors; open the circuit at the threshold."""
        if not (isinstance(result, dict) and result.get("_error") == KIND_AUTH):
            self._consecutive_auth_errors = 0
            return
        self._consecutive_auth_errors += 1
        if self._consecutive_auth_errors >= self.auth_breaker_threshold:
            self.circuit_open = True
            log.error(
                "%s: %d consecutive auth errors (last %s) — circuit open; no "
                "further requests this run. Fix the credential and re-run.",
                self.cache_path.name, self._consecutive_auth_errors, cache_key,
            )

    def _next_flush_threshold(self) -> int:
        """Dirty-entry count needed to trigger the next flush.

        Every flush rewrites the whole cache, so a fixed ``flush_every`` makes a
        large first run's total bytes written quadratic. Flushing after ~10%
        growth (floored at ``flush_every``) makes it ``O(size * log(size))``
        while keeping the single plain JSON file format.
        """
        return max(self.flush_every, int(len(self.cache) * 0.1))
