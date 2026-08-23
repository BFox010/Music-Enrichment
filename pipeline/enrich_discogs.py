"""Phase 4b — Discogs styles enrichment.

For each track, queries the Discogs ``database/search`` endpoint and pulls the
``style`` list off the best-matching release. Discogs is release/album-oriented,
so we search by ``artist`` + ``release_title`` (the track's album) — which our
data has for ~99% of tracks — and fall back to an ``artist`` + ``track`` search
for the handful with no album. Sets:
  - discogs_styles: list[str]   (e.g. ["Conscious", "Boom Bap"]; [] if no match)

Caching: ``.cache/discogs.json``. Because many tracks share an album, the cache
is keyed by (artist, album) — so a 20-track album costs ONE API call, not 20.
Negative results are cached too (via RateLimitedClient), so re-runs are cheap.
Authenticated Discogs allows 60 req/min (1 req/s); see DISCOGS_RATE_LIMIT.

Requires ``DISCOGS_TOKEN`` in ``.env``.

Usage:
    python -m pipeline.enrich_discogs
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipeline._http import FORCE_OFF, RateLimitedClient
from pipeline.config import (
    DISCOGS_API_ROOT,
    DISCOGS_CACHE,
    DISCOGS_RATE_LIMIT,
    REPO_ROOT,
    TRACKS_WITH_DISCOGS_PATH,
    TRACKS_WITH_METADATA_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)

# Discogs search endpoint
DISCOGS_SEARCH_URL: str = DISCOGS_API_ROOT.rstrip("/") + "/database/search"

# Input preference: with_metadata (Phase 4 output) → with_audio → skeleton
DEFAULT_INPUT = TRACKS_WITH_METADATA_PATH

# Cap styles per track so a noisy compilation can't bloat the field.
MAX_STYLES: int = 6


def _extract_discogs_styles(response: Any, *, max_styles: int = MAX_STYLES) -> list[str]:
    """Pull the ``style`` list off the best-matching Discogs search result.

    Returns the first result that carries a non-empty ``style`` list (Discogs
    ranks results by relevance, so the top hit for an artist+title search is
    the right release). De-duplicated, order-preserving, capped at
    ``max_styles``. Returns ``[]`` on error responses or no match.
    """
    if not isinstance(response, dict) or response.get("_error"):
        return []
    results = response.get("results") or []
    if not isinstance(results, list):
        return []
    for r in results:
        if not isinstance(r, dict):
            continue
        styles = [s for s in (r.get("style") or []) if isinstance(s, str) and s.strip()]
        if styles:
            # de-dupe while preserving order
            seen: set[str] = set()
            unique = [s for s in styles if not (s in seen or seen.add(s))]
            return unique[:max_styles]
    return []


def _query_for_track(track: dict, token: str) -> tuple[dict[str, Any], str]:
    """Build the Discogs search params + cache key for a track.

    Prefer an album (release_title) search — Discogs is release-oriented and
    that matches far better than a track-title search. Cache key is keyed by
    (artist, album) so tracks sharing an album collapse to one API call.
    """
    album = (track.get("album") or "").strip()
    base = {"artist": track["artist"], "type": "release", "token": token, "per_page": 5}
    if album:
        params = {**base, "release_title": album}
        cache_key = f"{track['artist_normalized']}|album:{album.lower()}"
    else:
        params = {**base, "track": track["track"]}
        cache_key = f"{track['artist_normalized']}|track:{track['track_normalized']}"
    return params, cache_key


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_DISCOGS_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
    force: str = FORCE_OFF,
) -> dict[str, int]:
    """Enrich tracks with Discogs styles.

    Parameters
    ----------
    input_path: Path | None
        Defaults to ``tracks_with_metadata.jsonl`` if it exists, else falls
        back to the skeleton.
    limit: int | None
        Process only the first N tracks (debug/dry-run). None = all.
    force: str
        Cache force mode — see ``pipeline._http.FORCE_MODES``.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 4b: Discogs styles ===")

    load_dotenv(REPO_ROOT / ".env")
    token = os.getenv("DISCOGS_TOKEN")
    if not token:
        log.error("DISCOGS_TOKEN not set in .env")
        raise RuntimeError("DISCOGS_TOKEN missing")
    # Discogs requires a descriptive User-Agent; reuse the MusicBrainz one if set.
    user_agent = os.getenv("MUSICBRAINZ_USER_AGENT") or "MusicEnrichment/1.0"

    if input_path is None:
        input_path = DEFAULT_INPUT if DEFAULT_INPUT.exists() else (
            REPO_ROOT / "tracks_with_audio.jsonl"
        )
        if not input_path.exists():
            input_path = REPO_ROOT / "tracks_skeleton.jsonl"
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)
    log.info("Cache : %s", DISCOGS_CACHE)

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
        DISCOGS_CACHE,
        rate_per_second=DISCOGS_RATE_LIMIT,
        user_agent=user_agent,
        flush_every=25,
        force=force,
    )
    client.warn_if_forced(len(tracks))

    stats = {
        "total": len(tracks),
        "with_styles": 0,
        "no_styles": 0,
        "errors": 0,
        "from_cache": len(client.cache),
    }
    t0 = time.monotonic()
    enriched: list[dict] = []

    # finally, not a trailing call: an interrupt mid-loop must still persist
    # every response already paid for at the Discogs rate limit.
    try:
        for i, track in enumerate(tracks, start=1):
            params, cache_key = _query_for_track(track, token)
            response = client.get(DISCOGS_SEARCH_URL, params, cache_key)

            if isinstance(response, dict) and response.get("_error"):
                stats["errors"] += 1
                track["discogs_styles"] = []
            else:
                styles = _extract_discogs_styles(response)
                track["discogs_styles"] = styles
                if styles:
                    stats["with_styles"] += 1
                else:
                    stats["no_styles"] += 1
            # Keep downstream genre field present without overwriting it
            track.setdefault("genres", [])
            enriched.append(track)

            if i % 250 == 0 or i == len(tracks):
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                log.info(
                    "Progress: %d/%d (%.1f%%) — %.2f tracks/sec — with_styles=%d no_styles=%d errors=%d",
                    i, len(tracks), 100 * i / len(tracks),
                    rate, stats["with_styles"], stats["no_styles"], stats["errors"],
                )
    finally:
        client.flush()
    stats.update(client.stats)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in enriched:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "Phase 4b done: %d/%d with styles (%.1f%%) — %d no-style, %d errors",
        stats["with_styles"], stats["total"],
        100 * stats["with_styles"] / stats["total"] if stats["total"] else 0,
        stats["no_styles"], stats["errors"],
    )
    log.info("  %s", client.cache_summary())
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    stats = enrich()
    sys.exit(0 if stats["with_styles"] > 0 else 1)
