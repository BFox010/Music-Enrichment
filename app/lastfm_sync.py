"""Last.fm scrobble sync — fetch recent plays and append to scrobbles.jsonl."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from pipeline.config import LASTFM_API_ROOT, LASTFM_RATE_LIMIT, SCROBBLES_PATH
from pipeline.ingest_scrobbles import ingest_from_records

_PAGE_SIZE = 200


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


async def fetch_recent_scrobbles(
    username: str,
    api_key: str,
    since_ts: int = 0,
) -> list[dict]:
    """Fetch all pages of user.getRecentTracks since since_ts.

    Paginates automatically (_PAGE_SIZE records / page).
    Skips now-playing stubs (no date field).
    Returns raw Last.fm API records (not yet parsed by parse_raw_scrobble).
    """
    all_records: list[dict] = []
    page = 1
    total_pages = 1
    interval = 1.0 / LASTFM_RATE_LIMIT

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

            resp = await client.get(LASTFM_API_ROOT, params=params)
            resp.raise_for_status()
            body = resp.json()

            if "error" in body:
                raise RuntimeError(
                    f"Last.fm API error {body['error']}: {body.get('message', '')}"
                )

            rt = body.get("recenttracks", {})
            attr = rt.get("@attr", {})
            total_pages = int(attr.get("totalPages", 1))
            tracks = rt.get("track", [])

            # Last.fm can return a single dict instead of a list when there's one result
            if isinstance(tracks, dict):
                tracks = [tracks]

            # Skip now-playing stubs (they have @attr.nowplaying and no date block)
            all_records.extend(t for t in tracks if t.get("date"))

            page += 1
            if page <= total_pages:
                await asyncio.sleep(interval)

    return all_records


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

    records = await fetch_recent_scrobbles(username, api_key, since_ts)
    pages_fetched = max(1, (len(records) + _PAGE_SIZE - 1) // _PAGE_SIZE) if records else 0

    total = ingest_from_records(records, output_path=scrobbles_path, mode="append")

    return {
        "new": total - prev_count,
        "fetched": len(records),
        "total": total,
        "pages_fetched": pages_fetched,
    }
