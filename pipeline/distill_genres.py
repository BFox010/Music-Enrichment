"""Phase 4c — canonical genres field distillation.

Merges three raw genre signals into a single deduplicated ``genres`` list per
track, in source-priority order:

    1. discogs_styles  — most curated (sub-genre granularity)
    2. lastfm_tags     — folksonomy; obvious non-genre junk removed
    3. itunes_genre    — single broad Apple genre label (coarsest)

Duplicates are detected case-insensitively; the first (highest-priority)
spelling wins.  User chose "keep mood-ish tags" so the filter is minimal:
only strips interaction meta-tags, decade strings, and vague quality words.

This is a pure in-process transform — no API calls, no cache.

Input:  tracks_with_discogs.jsonl  (falls back to tracks_with_metadata.jsonl)
Output: tracks_with_genres.jsonl

Usage:
    python -m pipeline.distill_genres
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from pipeline.config import (
    REPO_ROOT,
    TRACKS_WITH_DISCOGS_PATH,
    TRACKS_WITH_GENRES_PATH,
    TRACKS_WITH_METADATA_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)

DEFAULT_INPUT = (
    TRACKS_WITH_DISCOGS_PATH if TRACKS_WITH_DISCOGS_PATH.exists()
    else TRACKS_WITH_METADATA_PATH
)

# Minimal blocklist — interaction/meta tags and decade strings only.
# Mood-adjacent tags (melancholic, dark, sad, upbeat, etc.) are intentionally
# kept per user preference.
JUNK_TAGS: frozenset[str] = frozenset({
    # Interaction / ownership meta-tags
    "seen live",
    "favorites", "favourite", "my favorites", "my favourites",
    "favorite songs", "favourite songs",
    "loved tracks", "love tracks",
    "best of", "top tracks", "top songs",
    "owned", "downloaded", "wishlist", "library",
    "to listen", "to buy",
    # Decade strings — year references, not genres
    "60s", "70s", "80s", "90s", "00s",
    "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s",
    # Generic quality words too vague to be genre-useful
    "good", "great", "amazing", "beautiful", "brilliant",
    # Language-only tags
    "english",
    # Last.fm housekeeping
    "all", "under 2000 listeners",
})


def _filter_lastfm_tags(tags: list[str]) -> list[str]:
    """Drop obvious non-genre tags from a Last.fm tag list."""
    return [t for t in tags if t.lower().strip() not in JUNK_TAGS]


def _merge_genres(
    discogs_styles: list[str],
    lastfm_tags: list[str],
    itunes_genre: str | None,
) -> list[str]:
    """Union of genre signals in priority order, deduplicated case-insensitively."""
    seen: set[str] = set()
    genres: list[str] = []

    for tag in discogs_styles:
        norm = tag.strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            genres.append(norm)

    for tag in _filter_lastfm_tags(lastfm_tags):
        norm = tag.strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            genres.append(norm)

    if itunes_genre:
        norm = itunes_genre.strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            genres.append(norm)

    return genres


def distill(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_GENRES_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Distill canonical genres from raw genre signals.

    Parameters
    ----------
    input_path:
        Defaults to ``tracks_with_discogs.jsonl`` if present, else
        ``tracks_with_metadata.jsonl``.
    limit:
        Process only the first N tracks (debug/dry-run). None = all.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 4c: genres distillation ===")

    if input_path is None:
        input_path = (
            TRACKS_WITH_DISCOGS_PATH if TRACKS_WITH_DISCOGS_PATH.exists()
            else TRACKS_WITH_METADATA_PATH
        )
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tracks: list[dict[str, Any]] = []
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))
    if limit is not None:
        tracks = tracks[:limit]
    log.info("Tracks to distill: %d", len(tracks))

    stats = {
        "total": len(tracks),
        "with_genres": 0,
        "from_discogs": 0,
        "from_lastfm": 0,
        "from_itunes": 0,
        "empty": 0,
    }
    t0 = time.monotonic()

    enriched: list[dict[str, Any]] = []
    for track in tracks:
        discogs = track.get("discogs_styles") or []
        lastfm = track.get("lastfm_tags") or []
        itunes = track.get("itunes_genre")

        genres = _merge_genres(discogs, lastfm, itunes)
        track["genres"] = genres

        if genres:
            stats["with_genres"] += 1
            if discogs:
                stats["from_discogs"] += 1
            elif lastfm:
                stats["from_lastfm"] += 1
            elif itunes:
                stats["from_itunes"] += 1
        else:
            stats["empty"] += 1

        enriched.append(track)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in enriched:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    elapsed = time.monotonic() - t0
    pct = 100 * stats["with_genres"] / stats["total"] if stats["total"] else 0
    log.info(
        "Phase 4c done: %d/%d with genres (%.1f%%) in %.1fs — "
        "discogs_only=%d lastfm_only=%d itunes_only=%d empty=%d",
        stats["with_genres"], stats["total"], pct, elapsed,
        stats["from_discogs"], stats["from_lastfm"],
        stats["from_itunes"], stats["empty"],
    )
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    result = distill()
    sys.exit(0 if result["with_genres"] > 0 else 1)
