"""Phase 4d — genre backfill from artist-level sources.

Phase 4c works from a track's *own* iTunes genre, Discogs styles, and Last.fm
*track* tags. What it leaves at ``genres: []`` is mostly not genre-less — the
artist-level sources were simply never asked.

Backfills ONLY the gap rows, cheapest source first, neither needing new auth:

  1. Last.fm ``artist.getTopTags`` — phase 4's API key, cached per artist so a
     heavily-scrobbled artist costs one call for all its tracks.
  2. MusicBrainz ``artist/{mbid}?inc=genres+tags`` — only when Last.fm returned
     nothing and an ``artist_mbid`` exists.

Both are artist-level, so an untagged deep cut inherits its artist's genre.
Mapped through 4c's GENRE_TAG_MAP so the taxonomy never diverges. Rows that
already have genres pass through untouched — API cost scales with the gap, not
the library.

Also writes raw ``lastfm_artist_tags`` / ``musicbrainz_genres`` for transparency.

Usage:
    python -m pipeline.enrich_genre_backfill
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipeline._http import FORCE_OFF, RateLimitedClient
from pipeline.config import (
    LASTFM_API_ROOT,
    LASTFM_CACHE,
    LASTFM_RATE_LIMIT,
    MUSICBRAINZ_API_ROOT,
    MUSICBRAINZ_CACHE,
    MUSICBRAINZ_RATE_LIMIT,
    REPO_ROOT,
    TRACKS_WITH_GENRE_BACKFILL_PATH,
    TRACKS_WITH_GENRES_PATH,
    configure_logging,
    get_logger,
)
from pipeline.derive_genres import _genres_from_tags
from pipeline.name_variations import first_artist
from pipeline.normalize import normalize_artist

log = get_logger(__name__)

DEFAULT_INPUT = TRACKS_WITH_GENRES_PATH

# Cap raw tags we keep per track so a noisy artist can't bloat the record.
MAX_RAW_TAGS: int = 12


def _names_from_lastfm_toptags(response: Any) -> list[str]:
    """Extract tag names from an artist.getTopTags response."""
    if not isinstance(response, dict) or response.get("_error"):
        return []
    toptags = (response.get("toptags") or {}).get("tag") or []
    if isinstance(toptags, dict):  # single-tag responses can arrive as a dict
        toptags = [toptags]
    names = [t.get("name") for t in toptags if isinstance(t, dict) and t.get("name")]
    return names[:MAX_RAW_TAGS]


def _names_from_musicbrainz_artist(response: Any) -> list[str]:
    """Extract genre + tag names from an artist?inc=genres+tags response.

    MusicBrainz exposes curated ``genres`` (voted) and folksonomy ``tags``;
    both carry a ``name``. Genres first (higher quality), then tags.
    """
    if not isinstance(response, dict) or response.get("_error"):
        return []
    names: list[str] = []
    for key in ("genres", "tags"):
        for item in response.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
    return names[:MAX_RAW_TAGS]


def _fetch_lastfm_artist_tags(client: RateLimitedClient, api_key: str, artist: str,
                              artist_norm: str) -> list[str]:
    params = {
        "method": "artist.getTopTags",
        "api_key": api_key,
        "artist": artist,
        "format": "json",
        "autocorrect": "1",
    }
    resp = client.get(LASTFM_API_ROOT, params, f"artisttags|{artist_norm}")
    return _names_from_lastfm_toptags(resp)


def _fetch_musicbrainz_artist_genres(client: RateLimitedClient, artist_mbid: str) -> list[str]:
    url = MUSICBRAINZ_API_ROOT.rstrip("/") + f"/artist/{artist_mbid}"
    params = {"inc": "genres+tags", "fmt": "json"}
    resp = client.get(url, params, f"mbartist|{artist_mbid}")
    return _names_from_musicbrainz_artist(resp)


def _propagate_within_artist(tracks: list[dict], today: str) -> int:
    """Fill a still-empty ``genres`` from the richest sibling row by the same artist.

    Whatever emptied the row — a run interrupted before it, a transient error
    cached as a negative, an autocorrect miss on a collab credit — the answer is
    often already in this file, on another track by the same artist. Fetching
    per track meant no row ever consulted what a sibling had resolved.

    No network. Fills blanks only, so it can never overwrite a fetched value.
    Returns the number of rows filled.
    """
    best_by_artist: dict[str, list[str]] = {}
    for track in tracks:
        genres = track.get("genres")
        if not genres:
            continue
        artist = track.get("artist_normalized") or ""
        # Richest row wins; play_count then title break ties so a re-run over the
        # same library propagates the same answer.
        current = best_by_artist.get(artist)
        if current is None or len(genres) > len(current):
            best_by_artist[artist] = list(genres)

    filled = 0
    for track in tracks:
        if track.get("genres"):
            continue
        inherited = best_by_artist.get(track.get("artist_normalized") or "")
        if not inherited:
            continue
        track["genres"] = list(inherited)
        track["genre_backfill"] = {
            "source": "artist_propagation",
            "retrieved_at": today,
            "pipeline_phase": "4d",
            # Another of the artist's tracks, not this one — weaker than a
            # direct artist-level answer, and never a track-level one.
            "confidence": "low",
        }
        filled += 1
    return filled


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_GENRE_BACKFILL_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
    force: str = FORCE_OFF,
) -> dict[str, int]:
    """Backfill genres for tracks left empty by phase 4c, from artist sources.

    ``force`` (see ``pipeline._http.FORCE_MODES``) applies to both the Last.fm
    and MusicBrainz caches. It does not widen the candidate set — this phase
    only ever touches tracks that still have no genre.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 4d: genre backfill (artist-level sources) ===")

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("LASTFM_API_KEY")
    if not api_key:
        log.error("LASTFM_API_KEY not set in .env")
        raise RuntimeError("LASTFM_API_KEY missing")
    mb_user_agent = os.getenv("MUSICBRAINZ_USER_AGENT") or "MusicEnrichment/1.0"

    if input_path is None:
        input_path = DEFAULT_INPUT
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tracks: list[dict] = []
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))
    if limit is not None:
        tracks = tracks[:limit]

    gap = [t for t in tracks if not t.get("genres")]
    log.info("Tracks: %d total, %d with no genre (backfill candidates)",
             len(tracks), len(gap))

    lastfm = RateLimitedClient(
        LASTFM_CACHE, rate_per_second=LASTFM_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0", flush_every=100, force=force,
    )
    musicbrainz = RateLimitedClient(
        MUSICBRAINZ_CACHE, rate_per_second=MUSICBRAINZ_RATE_LIMIT,
        user_agent=mb_user_agent, flush_every=25, force=force,
    )
    lastfm.warn_if_forced(len(gap))
    musicbrainz.warn_if_forced(len(gap))

    stats = {
        "total": len(tracks),
        "gap": len(gap),
        "recovered_lastfm_artist": 0,
        "recovered_via_first_artist": 0,
        "recovered_musicbrainz": 0,
        "still_empty": 0,
    }
    t0 = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # finally, not trailing calls: an interrupt mid-loop must still persist
    # every response already paid for — MusicBrainz is capped at 1 req/s.
    try:
        for i, track in enumerate(gap, start=1):
            # 1 — Last.fm artist tags. Try the full credit first; if a collab string
            # (e.g. "A$AP NAST & D33J") yields nothing, retry on the primary artist,
            # which Last.fm indexes the genre under.
            candidates = [(track["artist"], track["artist_normalized"])]
            primary = first_artist(track["artist"])
            if primary != track["artist"]:
                candidates.append((primary, normalize_artist(primary)))

            la_tags: list[str] = []
            genres: list[str] = []
            via_first_artist = False
            for idx, (cand_artist, cand_norm) in enumerate(candidates):
                tags = _fetch_lastfm_artist_tags(lastfm, api_key, cand_artist, cand_norm)
                if idx == 0:
                    la_tags = tags  # keep the full-credit tags for transparency
                mapped = _genres_from_tags(tags)
                if mapped:
                    genres = mapped
                    la_tags = tags
                    via_first_artist = idx > 0
                    break
            track["lastfm_artist_tags"] = la_tags

            backfill_source: str | None = None
            confidence: str | None = None
            if genres:
                stats["recovered_lastfm_artist"] += 1
                backfill_source = "lastfm_artist"
                # The primary-artist retry drops the rest of a collab credit, so
                # the genre is the lead artist's, not necessarily this track's.
                confidence = "low" if via_first_artist else "medium"
                if via_first_artist:
                    stats["recovered_via_first_artist"] += 1
            elif track.get("artist_mbid"):
                # 2 — MusicBrainz artist genres (slow, only when Last.fm missed)
                mb_names = _fetch_musicbrainz_artist_genres(musicbrainz, track["artist_mbid"])
                track["musicbrainz_genres"] = mb_names
                genres = _genres_from_tags(mb_names)
                if genres:
                    stats["recovered_musicbrainz"] += 1
                    backfill_source = "musicbrainz_artist"
                    confidence = "medium"

            # Written for every gap row, recovered or not: a null source means
            # 4d looked and found nothing, which an absent block cannot express.
            track["genre_backfill"] = {
                "source": backfill_source,
                "retrieved_at": today,
                "pipeline_phase": "4d",
                "confidence": confidence,
            }

            track["genres"] = genres
            if not genres:
                stats["still_empty"] += 1

            if i % 100 == 0 or i == len(gap):
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                log.info(
                    "Progress: %d/%d gap tracks — %.1f/sec — lastfm=%d mb=%d empty=%d",
                    i, len(gap), rate, stats["recovered_lastfm_artist"],
                    stats["recovered_musicbrainz"], stats["still_empty"],
                )
    finally:
        lastfm.flush()
        musicbrainz.flush()

    stats["recovered_artist_propagation"] = _propagate_within_artist(tracks, today)
    stats["still_empty"] -= stats["recovered_artist_propagation"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in tracks:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    recovered = (stats["recovered_lastfm_artist"] + stats["recovered_musicbrainz"]
                 + stats["recovered_artist_propagation"])
    pct = recovered / stats["gap"] * 100 if stats["gap"] else 0
    log.info(
        "Phase 4d done: recovered %d/%d gap tracks (%.1f%%) — "
        "lastfm_artist=%d (of which %d via primary-artist) musicbrainz=%d "
        "artist_propagation=%d still_empty=%d",
        recovered, stats["gap"], pct,
        stats["recovered_lastfm_artist"], stats["recovered_via_first_artist"],
        stats["recovered_musicbrainz"], stats["recovered_artist_propagation"],
        stats["still_empty"],
    )
    log.info("  %s", lastfm.cache_summary())
    log.info("  %s", musicbrainz.cache_summary())
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    result = enrich()
    sys.exit(0 if (result["recovered_lastfm_artist"] + result["recovered_musicbrainz"]) > 0 else 1)
