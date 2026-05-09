"""Shared HTTP utilities: rate-limited client with retry and disk-backed cache.

Used by enrich_metadata (Last.fm) and check_apple_music (iTunes Search).
Each client instance owns its rate limit + cache file path.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

import requests

from pipeline.config import HTTP_BACKOFF_BASE, HTTP_BACKOFF_MAX, HTTP_MAX_RETRIES, get_logger

log = get_logger(__name__)


class RateLimitedClient:
    """A small HTTP client with rate limiting, exponential backoff, and JSON cache.

    The cache is a flat dict keyed by ``cache_key`` (caller-supplied string).
    Negative results (404, max-retries) are cached too, so a re-run does not
    re-hit endpoints that already failed.

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
    """

    def __init__(
        self,
        cache_path: Path,
        *,
        rate_per_second: float,
        user_agent: str = "MusicEnrichment/1.0",
        flush_every: int = 50,
    ) -> None:
        self.cache_path = cache_path
        self.min_interval = 1.0 / rate_per_second
        self.flush_every = flush_every
        self._last_request = 0.0
        self._dirty_count = 0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.cache: dict[str, Any] = self._load_cache()

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
        """Write the cache to disk."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, ensure_ascii=False)
        tmp.replace(self.cache_path)
        self._dirty_count = 0

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
        ``{"_error": "<reason>"}`` so callers can short-circuit.
        """
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Rate limit
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

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
                wait = min(HTTP_BACKOFF_BASE * (2 ** attempt) + random.random(),
                           HTTP_BACKOFF_MAX)
                log.debug("HTTP %s on attempt %d/%d for %s — waiting %.1fs",
                          r.status_code, attempt + 1, HTTP_MAX_RETRIES, cache_key, wait)
                time.sleep(wait)
            except requests.RequestException as e:
                self._last_request = time.monotonic()
                wait = min(HTTP_BACKOFF_BASE * (2 ** attempt), HTTP_BACKOFF_MAX)
                log.debug("Network error %s on attempt %d/%d — waiting %.1fs",
                          e, attempt + 1, HTTP_MAX_RETRIES, wait)
                time.sleep(wait)
        else:
            result = {"_error": "max_retries"}

        self.cache[cache_key] = result
        self._dirty_count += 1
        if self._dirty_count >= self.flush_every:
            self.flush()
        return result
