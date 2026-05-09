"""Phase 1: Read Last.fm scrobble JSON export → write scrobbles.jsonl.

Input  : `inputs/lastfm_export.json` from lastfmstats.com — a list of pages
         (each page a list of scrobble dicts in Last.fm `recenttracks` shape).
Output : `scrobbles.jsonl` — one row per play, schema per CLAUDE.md.

Currently-playing scrobbles (no `date.uts`) are dropped with a WARN log,
since they have no canonical timestamp to anchor analytics against.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import config
from pipeline.normalize import normalize_artist, normalize_track

logger = config.get_logger(__name__)


def _iter_scrobbles(raw: list[Any]) -> Iterator[dict[str, Any]]:
    """Flatten a paginated lastfmstats.com export into individual scrobbles."""
    for page in raw:
        if not isinstance(page, list):
            logger.warning("non-list page encountered, skipping: %s", type(page).__name__)
            continue
        yield from page


def _transform_scrobble(s: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Last.fm scrobble record → scrobbles.jsonl row (or None if unusable)."""
    track = (s.get("name") or "").strip()
    if not track:
        return None

    date_obj = s.get("date") or {}
    uts_raw = date_obj.get("uts") if isinstance(date_obj, dict) else None
    if not uts_raw:
        return None  # currently-playing — no timestamp

    try:
        uts = int(uts_raw)
    except (TypeError, ValueError):
        return None

    artist_obj = s.get("artist") or {}
    artist = (artist_obj.get("#text") or "").strip() if isinstance(artist_obj, dict) else ""
    album_obj = s.get("album") or {}
    album = (album_obj.get("#text") or "").strip() if isinstance(album_obj, dict) else ""

    dt = datetime.fromtimestamp(uts, tz=timezone.utc)
    return {
        "artist": artist,
        "track": track,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(track),
        "album": album,
        "scrobbled_at": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": dt.year,
        "month": dt.month,
        "day_of_week": dt.weekday(),  # Mon=0 … Sun=6 per spec
        "hour": dt.hour,
        "season": config.SEASON_BY_MONTH[dt.month],
    }


def transform(raw: list[Any]) -> list[dict[str, Any]]:
    """Transform raw export → list of normalized scrobble rows.

    Drops malformed/incomplete records, logging counts.
    """
    out: list[dict[str, Any]] = []
    skipped = 0
    for s in _iter_scrobbles(raw):
        row = _transform_scrobble(s)
        if row is None:
            skipped += 1
        else:
            out.append(row)
    if skipped:
        logger.warning("dropped %d unusable scrobbles (no timestamp / no track)", skipped)
    return out


def write_jsonl(rows: Iterable[dict[str, Any]], path: Path) -> int:
    """Write rows as JSONL. Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def run(
    input_path: Path = config.INPUT_LASTFM_EXPORT,
    output_path: Path = config.SCROBBLES_PATH,
) -> int:
    """Phase 1 entrypoint. Returns number of scrobbles written."""
    logger.info("Phase 1: ingest scrobbles from %s", input_path)
    start = time.time()

    if not input_path.exists():
        raise FileNotFoundError(f"Last.fm export not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"expected top-level list, got {type(raw).__name__}")

    rows = transform(raw)
    n = write_jsonl(rows, output_path)

    logger.info(
        "Phase 1 done: %d scrobbles -> %s (%.2fs)",
        n, output_path, time.time() - start,
    )
    return n


if __name__ == "__main__":
    config.configure_logging()
    run()
