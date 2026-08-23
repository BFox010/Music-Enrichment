"""Shared HTTP utilities: rate-limited client with retry and disk-backed cache.

Used by enrich_metadata (Last.fm), enrich_discogs, enrich_genre_backfill
(Last.fm + MusicBrainz) and check_apple_music (iTunes Search). Each client
instance owns its rate limit + cache file path.

Cache format
------------
A flat JSON dict keyed by ``cache_key``. Successful responses are stored raw,
exactly as the API returned them. Failures are stored as
``{"_error": "<reason>", "_cached_at": <epoch seconds>}`` — the timestamp is what
lets a negative entry expire. Entries written before ``_cached_at`` existed load
fine and are treated as expired, so upgrading gives one free retry pass over
previously-poisoned keys.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.config import (
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

# Error-kind labels used to pick a TTL.
KIND_NOT_FOUND = "not_found"
KIND_TRANSIENT = "transient"


def _error_kind(entry: Any) -> str | None:
    """Classify a cache entry: ``None`` for a success, else its error kind.

    ``not_found`` is a stable negative (the API says this track does not exist).
    Everything else — ``max_retries``, ``invalid_json: …`` — is transient and
    worth retrying sooner.
    """
    if not isinstance(entry, dict):
        return None
    error = entry.get("_error")
    if not error:
        return None
    return KIND_NOT_FOUND if error == "not_found" else KIND_TRANSIENT


def _is_expired(
    entry: Any,
    now: float,
    negative_ttl: float,
    transient_ttl: float,
) -> bool:
    """True if ``entry`` is a failure whose TTL has elapsed.

    Success entries never expire here — phases that need freshness enforce it
    themselves (e.g. ``check_apple_music`` via ``apple_music_checked_at``).
    A missing ``_cached_at`` (pre-TTL cache file) counts as expired, and so does
    a timestamp in the future, so a skewed clock cannot pin an entry forever.
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


class RateLimitedClient:
    """A small HTTP client with rate limiting, exponential backoff, and JSON cache.

    Parameters
    ----------
    cache_path: Path
        File path to load/save the cache JSON.
    rate_per_second: float
        Maximum sustained request rate (requests/second).
    user_agent: str
        Sent in the User-Agent header. MusicBrainz requires this.
    flush_every: int
        Flush cache to disk every N new entries.
    force: str
        One of ``FORCE_MODES``. See the module docstring.
    negative_ttl / transient_ttl: float
        Seconds a cached ``not_found`` / transient failure stays valid.
        Injectable so tests need not sleep.
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

        Never raises: this is called from ``finally`` blocks in every phase, and
        a write failure there must not mask the exception that got us there.
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

    # ── Observability ────────────────────────────────────────────────────

    def error_counts(self) -> dict[str, int]:
        """How many cached entries are failures, split by kind.

        This is what makes "how much of the miss rate is a genuine no-match
        versus a poisoned cache entry" answerable.
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
        return (
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

    # ── Fetch ────────────────────────────────────────────────────────────

    def _should_refetch(self, cache_key: str) -> bool:
        """Decide whether ``cache_key`` must go to the network. Updates stats."""
        if cache_key not in self.cache:
            self.stats["misses"] += 1
            return True
        # Already re-fetched this run — serve what we just stored.
        if cache_key in self._refetched:
            self.stats["hits"] += 1
            return False
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
    ) -> Any:
        """GET ``url`` with ``params``, caching the JSON response under ``cache_key``.

        On 404 or max-retries-exceeded, caches and returns
        ``{"_error": "<reason>", "_cached_at": <epoch>}`` so callers can
        short-circuit and so the entry expires on a later run.
        """
        if not self._should_refetch(cache_key):
            return self.cache[cache_key]

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
                        result = r.json()
                    except ValueError:
                        result = {"_error": f"invalid_json: {r.text[:200]}"}
                    break
                if r.status_code == 404:
                    result = {"_error": "not_found"}
                    break
                if r.status_code == 429:
                    log.warning("429 rate-limited; backing off")
                # 5xx, 429, etc. → backoff and retry
                if attempt == HTTP_MAX_RETRIES - 1:
                    continue
                wait = min(HTTP_BACKOFF_BASE * (2 ** attempt) + random.random(),
                           HTTP_BACKOFF_MAX)
                log.debug("HTTP %s on attempt %d/%d for %s — waiting %.1fs",
                          r.status_code, attempt + 1, HTTP_MAX_RETRIES, cache_key, wait)
                time.sleep(wait)
            except requests.RequestException as e:
                self._last_request = time.monotonic()
                if attempt == HTTP_MAX_RETRIES - 1:
                    continue
                wait = min(HTTP_BACKOFF_BASE * (2 ** attempt), HTTP_BACKOFF_MAX)
                log.debug("Network error %s on attempt %d/%d — waiting %.1fs",
                          e, attempt + 1, HTTP_MAX_RETRIES, wait)
                time.sleep(wait)
        else:
            result = {"_error": "max_retries"}

        # Stamp failures so they expire. A success overwrites the old error
        # entry outright, so entries heal rather than accumulate.
        if _error_kind(result) is not None:
            result["_cached_at"] = time.time()

        self.cache[cache_key] = result
        self._refetched.add(cache_key)
        self._dirty_count += 1
        if self._dirty_count >= self.flush_every:
            self.flush()
        return result
