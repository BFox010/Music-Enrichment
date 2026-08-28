"""Phase 1 — Scrobble ingest.

Reads the Last.fm JSON export (owner-provided, placed in inputs/) and writes
scrobbles.jsonl — one row per play event in the canonical schema.

**Ingest is additive.** The owner's export is a partial pull ("today back to the
last pull"), so the default merges into scrobbles.jsonl, dedupes on
``(scrobbled_at, artist_normalized, track_normalized)`` and re-sorts. ``--replace``
overwrites. Either way, a write leaving fewer rows than the file on disk raises
``ScrobbleShrinkError`` unless ``--allow-shrink`` is also passed: this file is the
base record for every play-weighted number, and losing rows is silent.

Usage:
    python -m pipeline.ingest_scrobbles
    python -m pipeline.ingest_scrobbles --replace
    python -m pipeline.ingest_scrobbles --replace --allow-shrink
"""

from __future__ import annotations

import argparse
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
from pipeline.schema import atomic_open

log = get_logger(__name__)


class ScrobbleShrinkError(RuntimeError):
    """Raised when a write would leave fewer scrobbles than already on disk.

    Deliberately a ``RuntimeError`` and not a ``FileNotFoundError``:
    ``run_full_pipeline._phase()`` maps the latter to the benign SKIPPED status,
    and losing play history must fail the run loudly.
    """


def _count_existing_rows(path: Path) -> int:
    """Non-blank line count of ``path``, or 0 if it does not exist.

    Counts *lines*, not parsed rows, on purpose: the append path below drops
    unparseable lines silently, and the shrink guard should notice that too.
    """
    if not path.exists():
        return 0
    n = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


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

    # MBIDs ride along free in the export and are the strongest identity evidence
    # in the pipeline — Phase 4e folds credit variants on them.
    return {
        "artist": artist,
        "track": track,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(track),
        "album": album,
        "musicbrainz_id": (record.get("mbid") or "").strip() or None,
        "artist_mbid": ((record.get("artist") or {}).get("mbid") or "").strip() or None,
        "scrobbled_at": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": dt.year,
        "month": dt.month,
        "day_of_week": dt.weekday(),   # 0=Monday … 6=Sunday
        "hour": dt.hour,
        "season": SEASON_BY_MONTH[dt.month],
    }


def ingest_from_records(
    raw_records: list[dict],
    output_path: Path = SCROBBLES_PATH,
    mode: str = "append",
    *,
    allow_shrink: bool = False,
) -> int:
    """Parse raw Last.fm API records and write to output_path.

    mode="append"   — (default) merge with existing rows, deduplicate by
                      (scrobbled_at, artist_normalized, track_normalized),
                      then sort chronologically and rewrite.
    mode="replace"  — overwrite output_path entirely.

    Whatever the mode, a write that would leave fewer rows than are already in
    ``output_path`` raises ``ScrobbleShrinkError`` unless ``allow_shrink`` is
    True. The guard lives here rather than in ``ingest()`` so every caller —
    Phase 1, ``app.lastfm_sync.sync()``, anything future — is covered.

    Returns total rows written (not just new rows in append mode).
    """
    parsed: list[dict] = []
    skipped = 0
    for record in raw_records:
        row = parse_raw_scrobble(record)
        if row is None:
            skipped += 1
        else:
            parsed.append(row)

    log.info("Parsed: %d  |  Skipped (nowplaying/malformed): %d", len(parsed), skipped)

    if mode == "append" and output_path.exists():
        existing: list[dict] = []
        with open(output_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        existing.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        # Re-derive the normalized fields on disk rows before keying on them.
        # They are *derived*, so any change to normalize_artist/normalize_track
        # silently redefines the key: every stored scrobble stops matching itself
        # and the export re-delivers it as a duplicate. (`&` → "and" in #27 did
        # exactly this — 1102 plays re-added, 28 recordings split in two.)
        # Recomputing both sides is immune, and rewriting the stored values lets
        # the file heal itself.
        renormalized = 0
        for row in existing:
            artist_norm = normalize_artist(row.get("artist", ""))
            track_norm = normalize_track(row.get("track", ""))
            if (row.get("artist_normalized"), row.get("track_normalized")) != (
                artist_norm, track_norm
            ):
                renormalized += 1
            row["artist_normalized"] = artist_norm
            row["track_normalized"] = track_norm
        if renormalized:
            log.info(
                "Re-normalized %d existing rows whose stored keys were stale",
                renormalized,
            )

        seen = {
            (r["scrobbled_at"], r["artist_normalized"], r["track_normalized"])
            for r in existing
        }
        new_only = []
        for row in parsed:
            key = (row["scrobbled_at"], row["artist_normalized"], row["track_normalized"])
            if key in seen:
                continue
            seen.add(key)   # the export itself can repeat a play
            new_only.append(row)
        log.info(
            "Existing: %d  |  New: %d  |  Duplicates dropped: %d",
            len(existing), len(new_only), len(parsed) - len(new_only),
        )
        parsed = sorted(existing + new_only, key=lambda r: r["scrobbled_at"])

    existing_rows = _count_existing_rows(output_path)
    if len(parsed) < existing_rows and not allow_shrink:
        raise ScrobbleShrinkError(
            f"Refusing to write {len(parsed)} scrobbles over the "
            f"{existing_rows} already in {output_path} — that would drop "
            f"{existing_rows - len(parsed)} rows (mode={mode!r}). A partial "
            f"Last.fm export is additive by default; if you really mean to "
            f"shrink the history, pass allow_shrink=True (CLI: "
            f"--replace --allow-shrink)."
        )

    # Atomic: a crash mid-write would half-truncate the file — the same history
    # loss the shrink guard above exists to prevent.
    with atomic_open(output_path) as fh:
        for row in parsed:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("Wrote %d rows (was %d) → %s", len(parsed), existing_rows, output_path)
    return len(parsed)


def ingest(
    export_path: Path = INPUT_LASTFM_EXPORT,
    output_path: Path = SCROBBLES_PATH,
    run_log_path: Path | None = None,
    *,
    mode: str = "append",
    allow_shrink: bool = False,
) -> int:
    """Read Last.fm JSON export → write scrobbles.jsonl.

    The export is a list-of-lists (one inner list per API page).

    Defaults to append semantics: the owner's documented workflow is a partial
    export covering "today back to the last pull", and replacing on that would
    truncate the history to just that window. Returns count of rows written.
    """
    run_log_path = configure_logging(run_log_path)
    log.info("=== Phase 1: scrobble ingest ===")
    log.info("Input : %s", export_path)
    log.info("Output: %s", output_path)
    log.info("Mode  : %s%s", mode, " (shrink allowed)" if allow_shrink else "")

    if not export_path.exists():
        log.error("Export file not found: %s", export_path)
        raise FileNotFoundError(export_path)

    with open(export_path, "r", encoding="utf-8") as fh:
        raw_pages: list[list[dict]] = json.load(fh)

    raw_records = [rec for page in raw_pages for rec in page]
    log.info("Raw records across %d pages: %d", len(raw_pages), len(raw_records))

    n = ingest_from_records(
        raw_records, output_path=output_path, mode=mode, allow_shrink=allow_shrink,
    )
    log.info("Run log: %s", run_log_path)
    return n


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest a Last.fm export into scrobbles.jsonl.")
    p.add_argument("--export", type=Path, default=INPUT_LASTFM_EXPORT,
                   help=f"Last.fm JSON export to read. Default: {INPUT_LASTFM_EXPORT}")
    p.add_argument("--output", type=Path, default=SCROBBLES_PATH,
                   help=f"Scrobble log to write. Default: {SCROBBLES_PATH}")
    p.add_argument("--replace", action="store_true",
                   help="Overwrite the scrobble log instead of merging into it. "
                        "Still refused if it would drop rows — add --allow-shrink "
                        "for that.")
    p.add_argument("--allow-shrink", action="store_true",
                   help="Permit a write that leaves fewer scrobbles than are "
                        "already on disk. Destroys play history; use only when "
                        "the reduction is intended.")
    return p.parse_args(argv)


if __name__ == "__main__":
    import sys

    args = _parse_args(sys.argv[1:])
    try:
        n = ingest(
            export_path=args.export,
            output_path=args.output,
            mode="replace" if args.replace else "append",
            allow_shrink=args.allow_shrink,
        )
    except ScrobbleShrinkError as e:
        log.error("%s", e)
        sys.exit(2)
    print(f"Phase 1 done: {n} scrobbles written.")
    sys.exit(0 if n > 0 else 1)
