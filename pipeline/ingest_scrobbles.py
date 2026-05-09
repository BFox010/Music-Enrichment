"""Phase 1 — Scrobble ingest.

Reads the Last.fm JSON export (owner-provided, placed in inputs/) and writes
scrobbles.jsonl — one row per play event in the canonical schema.

Usage:
    python -m pipeline.ingest_scrobbles
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import (
    INPUT_LASTFM_EXPORT,
    SCROBBLES_PATH,
    SEASON_BY_MONTH,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)


def parse_raw_scrobble(record: dict) -> dict | None:
    """Convert one raw Last.fm API record into a scrobbles.jsonl row.

    Returns None for:
    - "now playing" stubs that lack a real timestamp
    - Records missing artist or track name
    - Records with an unparseable timestamp

    Last.fm export format uses nested ``{"#text": ..., "mbid": ...}`` blocks
    for artist and album fields, and a ``date.uts`` Unix timestamp.
    """
    date_block = record.get("date")
    if not date_block or not date_block.get("uts"):
        return None

    artist: str = ((record.get("artist") or {}).get("#text") or "").strip()
    track: str = (record.get("name") or "").strip()
    album: str = ((record.get("album") or {}).get("#text") or "").strip()

    if not artist or not track:
        log.debug("Skipping record with missing artist/track: %r", record)
        return None

    try:
        uts = int(date_block["uts"])
    except (ValueError, TypeError):
        log.debug("Skipping record with invalid uts: %r", date_block)
        return None

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
        "day_of_week": dt.weekday(),   # 0=Monday … 6=Sunday
        "hour": dt.hour,
        "season": SEASON_BY_MONTH[dt.month],
    }


def ingest(
    export_path: Path = INPUT_LASTFM_EXPORT,
    output_path: Path = SCROBBLES_PATH,
    run_log_path: Path | None = None,
) -> int:
    """Read Last.fm JSON export → write scrobbles.jsonl.

    The export is a list-of-lists (one inner list per API page).
    Returns count of rows written.
    """
    run_log_path = configure_logging(run_log_path)
    log.info("=== Phase 1: scrobble ingest ===")
    log.info("Input : %s", export_path)
    log.info("Output: %s", output_path)

    if not export_path.exists():
        log.error("Export file not found: %s", export_path)
        raise FileNotFoundError(export_path)

    with open(export_path, "r", encoding="utf-8") as fh:
        raw_pages: list[list[dict]] = json.load(fh)

    # Flatten paginated export (list of pages → flat list)
    raw_records = [rec for page in raw_pages for rec in page]
    log.info("Raw records across %d pages: %d", len(raw_pages), len(raw_records))

    parsed: list[dict] = []
    skipped = 0
    for record in raw_records:
        row = parse_raw_scrobble(record)
        if row is None:
            skipped += 1
        else:
            parsed.append(row)

    log.info("Parsed: %d  |  Skipped (nowplaying/malformed): %d", len(parsed), skipped)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in parsed:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("Wrote %d rows → %s", len(parsed), output_path)
    log.info("Run log: %s", run_log_path)
    return len(parsed)


if __name__ == "__main__":
    import sys

    n = ingest()
    print(f"Phase 1 done: {n} scrobbles written.")
    sys.exit(0 if n > 0 else 1)
