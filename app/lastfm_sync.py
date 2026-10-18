"""Last.fm scrobble sync — fetch recent plays and append to scrobbles.jsonl."""

from __future__ import annotations

import asyncio
import json
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
    LAST_SYNC_PATH,
    LASTFM_API_ROOT,
    LASTFM_RATE_LIMIT,
    SCROBBLES_PATH,
)
from pipeline.ingest_scrobbles import ingest_from_records
from pipeline.schema import atomic_open

log = logging.getLogger(__name__)

_PAGE_SIZE = 200
# Safety cap so a since_ts=0 full-history fetch (or a misbehaving API reporting a
# huge totalPages) can't loop unbounded. 250 pages × 200 = 50k scrobbles.
_MAX_PAGES = 250
# Re-fetch this far behind the newest stored play: offline scrobblers submit
# late plays with their original timestamps, which a fetch strictly after the
# newest play misses (#105). Last.fm refuses scrobbles over 14 days old, so
# nothing arrives later; ingest dedupes the overlap.
_OVERLAP_SECONDS = 14 * 86400


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
    """One page, retrying 429, 5xx and dropped connections with backoff.

    Any other 4xx (bad key, malformed request) is deterministic, so it fails
    at once. Every failure leaves as ``RuntimeError``, which the routes turn
    into a 400; don't let ``httpx.HTTPError`` or ``ValueError`` escape, or it
    surfaces as an unhandled 500.
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


def _latest_ts_on_disk(path: Path) -> int:
    """Unix timestamp of the newest scrobble in ``path``, or 0.

    Read from the file, not the server's snapshot: ``python -m app.refresh``
    never loads one, so it would fetch the entire history every run (#105),
    and a snapshot gone stale behind a CLI ingest would understate it too.
    """
    if not path.exists():
        return 0
    latest = ""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                stamp = json.loads(line).get("scrobbled_at") or ""
            except (json.JSONDecodeError, AttributeError):
                continue
            if stamp > latest:
                latest = stamp
    return get_last_scrobble_ts([{"scrobbled_at": latest}]) if latest else 0


async def fetch_recent_scrobbles(
    username: str,
    api_key: str,
    since_ts: int = 0,
) -> tuple[list[dict], int, bool]:
    """Fetch all pages of user.getRecentTracks since since_ts.

    Returns ``(raw Last.fm records, pages fetched, complete)``, skipping
    now-playing stubs. Past ``_MAX_PAGES`` only the *oldest* pages are fetched
    and ``complete`` is False. Don't keep the newest pages instead (Last.fm
    pages newest-first): the next sync resumes after them and strands the
    older remainder for good (#105).
    """
    all_records: list[dict] = []
    page = 1
    total_pages = 1
    pages_fetched = 0
    complete = True
    pages: list[int] | None = None
    interval = 1.0 / LASTFM_RATE_LIMIT
    # Freeze the upper bound before page 1. Last.fm pages newest-first, so a
    # scrobble landing mid-fetch shifts every subsequent page by one and can
    # push a record across a page boundary unseen. Pinning `to` makes the
    # paginated result set immutable for the duration of the walk.
    to_ts = int(time.time())

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params: dict = {
                "method": "user.getRecentTracks",
                "user": username,
                "api_key": api_key,
                "format": "json",
                "limit": _PAGE_SIZE,
                "page": page,
            }
            if since_ts > 0:
                params["from"] = max(since_ts - _OVERLAP_SECONDS, 0)
            params["to"] = to_ts

            body = await _get_page(client, params)
            pages_fetched += 1

            if "error" in body:
                raise RuntimeError(
                    f"Last.fm API error {body['error']}: {body.get('message', '')}"
                )

            rt = body.get("recenttracks", {})
            if pages is None:
                attr = rt.get("@attr", {})
                total_pages = int(attr.get("totalPages", 1))
                if total_pages > _MAX_PAGES:
                    log.warning(
                        "Last.fm reported %d pages; fetching the oldest %d (%d "
                        "scrobbles). The history is INCOMPLETE until another sync "
                        "fetches the rest.",
                        total_pages, _MAX_PAGES, _MAX_PAGES * _PAGE_SIZE,
                    )
                    complete = False
                    # Oldest first. Page 1's newest plays are dropped here and
                    # picked up by the sync that continues from these.
                    pages = list(range(total_pages, total_pages - _MAX_PAGES, -1))
                    page = pages.pop(0)
                    await asyncio.sleep(interval)
                    continue
                pages = list(range(2, total_pages + 1))

            tracks = rt.get("track", [])

            # Last.fm returns a bare dict, not a list, when there's one result
            if isinstance(tracks, dict):
                tracks = [tracks]

            # Skip now-playing stubs (they have @attr.nowplaying and no date block)
            all_records.extend(t for t in tracks if t.get("date"))

            if not pages:
                break
            page = pages.pop(0)
            await asyncio.sleep(interval)

    return all_records, pages_fetched, complete


async def sync(scrobbles_path: Path = SCROBBLES_PATH) -> dict:
    """Full incremental sync: fetch new scrobbles → append → return stats."""
    username = os.getenv("LASTFM_USERNAME")
    api_key = os.getenv("LASTFM_API_KEY")
    if not username or not api_key:
        raise RuntimeError(
            "LASTFM_USERNAME and LASTFM_API_KEY must be set in .env"
        )

    from app.data import get_scrobbles
    prev_count = len(get_scrobbles())
    since_ts = await asyncio.to_thread(_latest_ts_on_disk, scrobbles_path)
    if since_ts == 0 and scrobbles_path.exists():
        log.warning(
            "No usable latest-scrobble timestamp in %s — fetching full history "
            "(duplicates will be de-duped on append).",
            scrobbles_path,
        )

    records, pages_fetched, complete = await fetch_recent_scrobbles(
        username, api_key, since_ts
    )

    # ingest_from_records rewrites the whole scrobbles file; inline, it would
    # block the event loop of the coroutine routes awaiting sync().
    # Imported here: app.refresh imports this module at load time.
    from app.refresh import run_to_completion
    on_disk_before = _count_rows(scrobbles_path)
    total = await run_to_completion(
        ingest_from_records, records, output_path=scrobbles_path, mode="append"
    )

    stats = {
        # Against the file, not the in-memory snapshot: a snapshot already
        # stale before the sync would count rows this sync didn't add as new.
        "new": total - on_disk_before,
        "fetched": len(records),
        "total": total,
        "pages_fetched": pages_fetched,
        "in_memory_before": prev_count,
        # False when the fetch hit _MAX_PAGES: run another sync for the rest.
        "complete": complete,
    }
    # Don't let a failed stamp fail the sync: the scrobbles are already on
    # disk, and an error here would report a sync that happened as failed.
    try:
        stamp = await asyncio.to_thread(record_sync, stats["new"])
        stats["synced_at"] = stamp["synced_at"]
    except OSError as exc:
        log.warning("Sync succeeded but its timestamp was not saved: %s", exc)
    return stats


def record_sync(new: int, path: Path | None = None) -> dict:
    """Stamp ``LAST_SYNC_PATH`` with the time and yield of a successful sync.

    Call it only after a sync has succeeded: a failed sync must leave the
    previous stamp in place.
    """
    stamp = {"synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "new": int(new)}
    with atomic_open(path or LAST_SYNC_PATH) as fh:
        json.dump(stamp, fh)
        fh.write("\n")
    return stamp


def read_last_sync(path: Path | None = None) -> dict | None:
    """The last successful sync as ``{"synced_at", "new"}``, or None.

    None covers never-synced and an unreadable or malformed file alike. Don't
    let it raise: /api/lastfm/status calls it on every request.
    """
    path = path or LAST_SYNC_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        synced_at, new = raw["synced_at"], raw["new"]
        datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        log.warning("Ignoring unreadable %s: %s", path, exc)
        return None
    if isinstance(new, bool) or not isinstance(new, int):
        log.warning("Ignoring %s: 'new' is not an integer", path)
        return None
    return {"synced_at": synced_at, "new": new}


def _count_rows(path: Path) -> int:
    """Non-blank lines in a JSONL file, 0 if absent."""
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())
