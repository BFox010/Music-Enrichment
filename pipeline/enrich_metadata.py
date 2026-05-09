"""Phase 4 — metadata enrichment via Last.fm + MusicBrainz IDs.

For each track, calls Last.fm ``track.getInfo`` which returns BOTH:
  - top tags (folksonomy)
  - track MusicBrainz ID
  - artist MusicBrainz ID

That single call covers what the spec lists as Last.fm + MusicBrainz lookups.
Discogs is left as TODO — the spec says only-if-clear-match anyway.

Resumable: every response (including negatives) is cached to
``.cache/lastfm_track_info.json``. Re-running picks up where it left off.

Usage:
    python -m pipeline.enrich_metadata
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipeline._http import RateLimitedClient
from pipeline.config import (
    LASTFM_API_ROOT,
    LASTFM_CACHE,
    LASTFM_RATE_LIMIT,
    REPO_ROOT,
    TRACKS_WITH_METADATA_PATH,
    configure_logging,
    get_logger,
)
from pipeline.enrich_apple_library import TRACKS_WITH_APPLE_PATH

log = get_logger(__name__)

# Input preference order: with_apple → skeleton (fallback)
DEFAULT_INPUT = TRACKS_WITH_APPLE_PATH


def _extract_lastfm_fields(response: Any) -> dict[str, Any]:
    """Pull the fields we care about out of a track.getInfo response."""
    if not isinstance(response, dict):
        return {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}
    if response.get("_error"):
        return {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}
    track = response.get("track") or {}
    if not isinstance(track, dict):
        return {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}

    # Tags
    toptags = (track.get("toptags") or {}).get("tag") or []
    if isinstance(toptags, dict):  # single-tag responses can come as dict
        toptags = [toptags]
    tags = [t.get("name") for t in toptags if isinstance(t, dict) and t.get("name")]

    # MBIDs
    track_mbid = track.get("mbid") or None
    artist = track.get("artist") or {}
    artist_mbid = artist.get("mbid") if isinstance(artist, dict) else None

    return {
        "lastfm_tags": tags,
        "musicbrainz_id": track_mbid or None,
        "artist_mbid": artist_mbid or None,
    }


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_METADATA_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Enrich tracks with Last.fm tags and MusicBrainz IDs.

    Parameters
    ----------
    input_path: Path | None
        Defaults to ``tracks_with_apple.jsonl`` if it exists, otherwise skeleton.
    limit: int | None
        Process only the first N tracks (debug/dry-run). None = all.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 4: Last.fm + MusicBrainz enrichment ===")

    # Load .env from repo root explicitly (CWD may differ)
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("LASTFM_API_KEY")
    if not api_key:
        log.error("LASTFM_API_KEY not set in .env")
        raise RuntimeError("LASTFM_API_KEY missing")

    # Pick input
    if input_path is None:
        input_path = DEFAULT_INPUT if DEFAULT_INPUT.exists() else (
            REPO_ROOT / "tracks_skeleton.jsonl"
        )
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)
    log.info("Cache : %s", LASTFM_CACHE)

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
    log.info("Tracks to enrich: %d", len(tracks))

    client = RateLimitedClient(
        LASTFM_CACHE,
        rate_per_second=LASTFM_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0",
        flush_every=100,
    )

    stats = {
        "total": len(tracks),
        "matched": 0,
        "no_match": 0,
        "errors": 0,
        "from_cache": len(client.cache),
    }
    t0 = time.monotonic()

    enriched: list[dict] = []
    for i, track in enumerate(tracks, start=1):
        cache_key = f"{track['artist_normalized']}|{track['track_normalized']}"
        params = {
            "method": "track.getInfo",
            "api_key": api_key,
            "artist": track["artist"],
            "track": track["track"],
            "format": "json",
            "autocorrect": "1",
        }
        response = client.get(LASTFM_API_ROOT, params, cache_key)
        fields = _extract_lastfm_fields(response)

        if isinstance(response, dict) and response.get("_error"):
            stats["errors" if response["_error"] != "not_found" else "no_match"] += 1
        elif fields["musicbrainz_id"] or fields["lastfm_tags"]:
            stats["matched"] += 1
        else:
            stats["no_match"] += 1

        track.update(fields)
        # Initialise downstream fields if absent
        track.setdefault("genres", [])
        track.setdefault("discogs_styles", [])
        enriched.append(track)

        if i % 250 == 0 or i == len(tracks):
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed > 0 else 0
            log.info(
                "Progress: %d/%d (%.1f%%) — %.1f tracks/sec — matched=%d no_match=%d errors=%d",
                i, len(tracks), 100 * i / len(tracks),
                rate, stats["matched"], stats["no_match"], stats["errors"],
            )

    client.flush()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in enriched:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "Phase 4 done: %d/%d matched (%.1f%%) — %d no-match, %d errors",
        stats["matched"], stats["total"],
        100 * stats["matched"] / stats["total"] if stats["total"] else 0,
        stats["no_match"], stats["errors"],
    )
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    stats = enrich()
    sys.exit(0 if stats["matched"] > 0 else 1)
