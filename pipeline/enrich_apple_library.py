"""iTunes/Apple Music XML enrichment.

Parses the iTunes library XML export and merges per-track metadata into
tracks_skeleton.jsonl, producing tracks_with_apple.jsonl. Supplies these
useful fields that aren't in the Last.fm scrobble export:

- duration_ms        ← Total Time (ms)
- release_year       ← Year (or Release Date if Year missing)
- explicit           ← Explicit (boolean, present when true)
- itunes_genre       ← Genre (kept separate from Last.fm/MusicBrainz genres)
- itunes_play_count  ← Play Count (analytics; Last.fm `play_count` is canonical)
- itunes_skip_count  ← Skip Count (analytics)
- itunes_date_added  ← Date Added (ISO 8601)
- itunes_last_played ← Play Date UTC (ISO 8601)
- itunes_persistent_id ← Persistent ID (local library UUID, NOT Apple Music streaming ID)
- itunes_kind        ← Kind ("Apple Music AAC audio file", "Purchased AAC audio file", etc.)

Match strategy: normalize (artist, track) on both sides and look up.
Multiple iTunes entries with the same join key are merged: take max(play_count),
max(skip_count), most recent date_added/last_played.

Usage:
    python -m pipeline.enrich_apple_library
"""

from __future__ import annotations

import json
import plistlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.config import (
    INPUT_APPLE_MUSIC_LIBRARY,
    TRACKS_SKELETON_PATH,
    REPO_ROOT,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)

# Output: a new intermediate between skeleton and audio-features
TRACKS_WITH_APPLE_PATH = REPO_ROOT / "tracks_with_apple.jsonl"

# Audio file kinds we accept (skip videos, podcasts, etc.)
_AUDIO_KIND_SUBSTRINGS = ("audio file", "music")


def _is_audio_track(record: dict) -> bool:
    """True if the iTunes record looks like an audio track (not video/podcast)."""
    kind = (record.get("Kind") or "").lower()
    if not kind:
        # Fall back: must have artist + name
        return bool(record.get("Artist") and record.get("Name"))
    if "video" in kind or "movie" in kind or "podcast" in kind:
        return False
    return any(s in kind for s in _AUDIO_KIND_SUBSTRINGS)


def _to_iso(dt: Any) -> str | None:
    """Convert a plistlib datetime to ISO 8601 UTC string, or None."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _release_year(record: dict) -> int | None:
    """Prefer ``Year``; fall back to first 4 digits of ``Release Date``."""
    year = record.get("Year")
    if isinstance(year, int) and 1900 < year < 2100:
        return year
    rd = record.get("Release Date")
    if isinstance(rd, datetime):
        return rd.year
    return None


def _record_to_apple_block(record: dict) -> dict | None:
    """Convert one iTunes Tracks-dict entry to our flat enrichment block."""
    artist = (record.get("Artist") or record.get("Album Artist") or "").strip()
    name = (record.get("Name") or "").strip()
    if not artist or not name:
        return None

    return {
        "artist": artist,
        "track": name,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(name),
        "duration_ms": record.get("Total Time"),
        "release_year": _release_year(record),
        "explicit": bool(record.get("Explicit", False)),
        "itunes_genre": record.get("Genre") or None,
        "itunes_play_count": int(record.get("Play Count") or 0),
        "itunes_skip_count": int(record.get("Skip Count") or 0),
        "itunes_date_added": _to_iso(record.get("Date Added")),
        "itunes_last_played": _to_iso(record.get("Play Date UTC")),
        "itunes_persistent_id": record.get("Persistent ID"),
        "itunes_kind": record.get("Kind"),
    }


def _merge_apple_blocks(blocks: list[dict]) -> dict:
    """Merge multiple iTunes entries that share the same join key.

    - Counters (play_count, skip_count): take MAX
    - Dates (date_added): take EARLIEST (when track was first added)
    - Dates (last_played): take LATEST
    - Other scalar fields: prefer non-null, prefer most recent record otherwise
    """
    if len(blocks) == 1:
        return blocks[0]

    out: dict = {**blocks[0]}
    out["itunes_play_count"] = max((b.get("itunes_play_count") or 0) for b in blocks)
    out["itunes_skip_count"] = max((b.get("itunes_skip_count") or 0) for b in blocks)

    added = [b.get("itunes_date_added") for b in blocks if b.get("itunes_date_added")]
    if added:
        out["itunes_date_added"] = min(added)

    last = [b.get("itunes_last_played") for b in blocks if b.get("itunes_last_played")]
    if last:
        out["itunes_last_played"] = max(last)

    # For scalar fields: keep first non-null
    for key in ("duration_ms", "release_year", "itunes_genre",
                "itunes_persistent_id", "itunes_kind"):
        for b in blocks:
            if b.get(key) is not None:
                out[key] = b[key]
                break

    # explicit: True if any record says explicit
    out["explicit"] = any(b.get("explicit") for b in blocks)
    return out


def parse_library(xml_path: Path) -> dict[tuple[str, str], dict]:
    """Parse iTunes XML, return {(artist_norm, track_norm): apple_block} map."""
    log.info("Parsing iTunes XML: %s", xml_path)
    with open(xml_path, "rb") as fh:
        plist = plistlib.load(fh)

    tracks_dict: dict = plist.get("Tracks", {})
    log.info("Total iTunes track entries: %d", len(tracks_dict))

    # Group by join key (multiple entries can share the same artist+track)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    audio_count = 0
    for _, record in tracks_dict.items():
        if not _is_audio_track(record):
            continue
        block = _record_to_apple_block(record)
        if block is None:
            continue
        audio_count += 1
        key = (block["artist_normalized"], block["track_normalized"])
        groups[key].append(block)

    log.info("Audio tracks: %d  |  Unique join keys: %d", audio_count, len(groups))

    # Merge groups
    merged: dict[tuple[str, str], dict] = {}
    for key, blocks in groups.items():
        merged[key] = _merge_apple_blocks(blocks)
    return merged


def enrich(
    skeleton_path: Path = TRACKS_SKELETON_PATH,
    xml_path: Path = INPUT_APPLE_MUSIC_LIBRARY,
    output_path: Path = TRACKS_WITH_APPLE_PATH,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Merge iTunes XML metadata into tracks_skeleton.jsonl.

    Returns: dict with keys total, matched, unmatched.
    """
    configure_logging(run_log_path)
    log.info("=== iTunes/Apple library enrichment ===")
    log.info("Skeleton: %s", skeleton_path)
    log.info("XML     : %s", xml_path)
    log.info("Output  : %s", output_path)

    if not skeleton_path.exists():
        raise FileNotFoundError(skeleton_path)
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    apple_index = parse_library(xml_path)

    enriched: list[dict] = []
    matched = 0
    with open(skeleton_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            track = json.loads(line)
            key = (track["artist_normalized"], track["track_normalized"])
            apple = apple_index.get(key)
            if apple:
                matched += 1
                track.update({
                    "duration_ms": apple.get("duration_ms"),
                    "release_year": apple.get("release_year"),
                    "explicit": apple.get("explicit"),
                    "itunes_genre": apple.get("itunes_genre"),
                    "itunes_play_count": apple.get("itunes_play_count"),
                    "itunes_skip_count": apple.get("itunes_skip_count"),
                    "itunes_date_added": apple.get("itunes_date_added"),
                    "itunes_last_played": apple.get("itunes_last_played"),
                    "itunes_persistent_id": apple.get("itunes_persistent_id"),
                    "itunes_kind": apple.get("itunes_kind"),
                })
            else:
                # Set fields to None for unmatched tracks — keeps schema uniform
                track.update({
                    "duration_ms": None,
                    "release_year": None,
                    "explicit": None,
                    "itunes_genre": None,
                    "itunes_play_count": 0,
                    "itunes_skip_count": 0,
                    "itunes_date_added": None,
                    "itunes_last_played": None,
                    "itunes_persistent_id": None,
                    "itunes_kind": None,
                })
            enriched.append(track)

    total = len(enriched)
    unmatched = total - matched
    pct = (matched / total * 100) if total else 0.0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in enriched:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("Matched: %d / %d (%.1f%%)  |  Unmatched: %d", matched, total, pct, unmatched)
    log.info("Wrote → %s", output_path)
    return {"total": total, "matched": matched, "unmatched": unmatched}


if __name__ == "__main__":
    import sys
    stats = enrich()
    print(f"Apple enrichment done: {stats['matched']}/{stats['total']} matched "
          f"({stats['matched']/stats['total']*100:.1f}%)")
    sys.exit(0)
