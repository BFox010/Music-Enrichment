"""Last.fm scrobble sync — fetch recent plays and append to scrobbles.jsonl."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from pipeline.config import (
    HTTP_BACKOFF_BASE,
    HTTP_BACKOFF_MAX,
    HTTP_MAX_RETRIES,
    LASTFM_API_ROOT,
    LASTFM_RATE_LIMIT,
    SCROBBLES_PATH,
)
from pipeline.ingest_scrobbles import ingest_from_records

log = logging.getLogger(__name__)

_PAGE_SIZE = 200
# Safety cap so a since_ts=0 full-history fetch (or a misbehaving API reporting a
# huge totalPages) can't loop unbounded. 250 pages × 200 = 50k scrobbles.
_MAX_PAGES = 250


def get_last_scrobble_ts(scrobbles: list[dict]) -> int:
    """Return Unix timestamp of the most recent scrobble, or 0 if empty."""
    if not scrobbles:
        return 0
    latest = max((s.get("scrobbled_at") or "" for s in scrobbles), default="")
    if not latest:
        return 0
    try:
        dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0


async def _get_page(client: httpx.AsyncClient, params: dict) -> dict:
    """One page, with the same retry ladder the pipeline's RateLimitedClient uses.

    Without this a single 502/503/429 or a dropped connection from
    ws.audioscrobbler.com aborted the whole sync. Only 429 and 5xx are retried:
    a 4xx (bad key, malformed request) is deterministic, so retrying it just
    burns ~10s to reach the same answer.

    Every failure leaves as ``RuntimeError`` — the routes catch that and return
    a 400/502, where an escaping ``httpx.HTTPError`` or ``ValueError`` used to
    surface as an unhandled 500 with a traceback.
    """
    last_error = "unknown error"
    for attempt in range(HTTP_MAX_RETRIES):
        try:
            resp = await client.get(LASTFM_API_ROOT, params=params)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise RuntimeError(f"Last.fm returned a non-JSON body: {exc}") from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code} from Last.fm"
            else:
                raise RuntimeError(
                    f"HTTP {resp.status_code} from Last.fm: {resp.text[:200]}"
                )
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt == HTTP_MAX_RETRIES - 1:
            break
        wait = min(HTTP_BACKOFF_BASE * (2 ** attempt) + random.random(), HTTP_BACKOFF_MAX)
        log.warning("Last.fm sync: %s — retry %d/%d in %.1fs",
                    last_error, attempt + 1, HTTP_MAX_RETRIES, wait)
        await asyncio.sleep(wait)

    raise RuntimeError(f"Last.fm sync failed after {HTTP_MAX_RETRIES} attempts: {last_error}")


async def fetch_recent_scrobbles(
    username: str,
    api_key: str,
    since_ts: int = 0,
) -> tuple[list[dict], int]:
    """Fetch all pages of user.getRecentTracks since since_ts.

    Paginates automatically (_PAGE_SIZE records / page).
    Skips now-playing stubs (no date field).
    Returns ``(raw Last.fm records, pages actually fetched)`` — not yet parsed
    by parse_raw_scrobble.
    """
    all_records: list[dict] = []
    page = 1
    total_pages = 1
    pages_fetched = 0
    interval = 1.0 / LASTFM_RATE_LIMIT
    # Freeze the upper bound before page 1. Last.fm pages newest-first, so a
    # scrobble landing mid-fetch shifts every subsequent page by one and can
    # push a record across a page boundary unseen. Pinning `to` makes the
    # paginated result set immutable for the duration of the walk.
    to_ts = int(time.time())

    async with httpx.AsyncClient(timeout=30) as client:
        while page <= total_pages:
            params: dict = {
                "method": "user.getRecentTracks",
                "user": username,
                "api_key": api_key,
                "format": "json",
                "limit": _PAGE_SIZE,
                "page": page,
            }
            if since_ts > 0:
                params["from"] = since_ts + 1  # exclude the already-stored timestamp
            params["to"] = to_ts

            body = await _get_page(client, params)
            pages_fetched += 1

            if "error" in body:
                raise RuntimeError(
                    f"Last.fm API error {body['error']}: {body.get('message', '')}"
                )

            rt = body.get("recenttracks", {})
            attr = rt.get("@attr", {})
            total_pages = int(attr.get("totalPages", 1))
            if total_pages > _MAX_PAGES:
                log.warning(
                    "Last.fm reported %d pages; capping at %d (%d scrobbles)",
                    total_pages, _MAX_PAGES, _MAX_PAGES * _PAGE_SIZE,
                )
                total_pages = _MAX_PAGES
            tracks = rt.get("track", [])

            # Last.fm can return a single dict instead of a list when there's one result
            if isinstance(tracks, dict):
                tracks = [tracks]

            # Skip now-playing stubs (they have @attr.nowplaying and no date block)
            all_records.extend(t for t in tracks if t.get("date"))

            page += 1
            if page <= total_pages:
                await asyncio.sleep(interval)

    return all_records, pages_fetched


async def sync(scrobbles_path: Path = SCROBBLES_PATH) -> dict:
    """Full incremental sync: fetch new scrobbles → append → return stats."""
    username = os.getenv("LASTFM_USERNAME")
    api_key = os.getenv("LASTFM_API_KEY")
    if not username or not api_key:
        raise RuntimeError(
            "LASTFM_USERNAME and LASTFM_API_KEY must be set in .env"
        )

    from app.data import get_scrobbles
    existing = get_scrobbles()
    since_ts = get_last_scrobble_ts(existing)
    prev_count = len(existing)
    if since_ts == 0 and prev_count > 0:
        log.warning(
            "No usable latest-scrobble timestamp from %d existing rows — "
            "fetching full history (duplicates will be de-duped on append).",
            prev_count,
        )

    records, pages_fetched = await fetch_recent_scrobbles(username, api_key, since_ts)

    # ingest_from_records re-reads, re-normalizes and rewrites the whole
    # scrobbles file (~540 ms on the committed history). sync() is awaited from
    # coroutine routes, so running it inline blocked the event loop for that
    # long on every sync and refresh.
    on_disk_before = _count_rows(scrobbles_path)
    total = await asyncio.to_thread(
        ingest_from_records, records, output_path=scrobbles_path, mode="append"
    )

    return {
        # Against the file, not the in-memory snapshot: a snapshot that was
        # already stale before the sync made this over-report the difference as
        # "new" rows this sync had nothing to do with.
        "new": total - on_disk_before,
        "fetched": len(records),
        "total": total,
        "pages_fetched": pages_fetched,
        "in_memory_before": prev_count,
    }


def _count_rows(path: Path) -> int:
    """Non-blank lines in a JSONL file, 0 if absent."""
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())
