"""Phase 4b — Discogs styles enrichment.

For each track, searches the Discogs database (by artist + track title) and
extracts release-level style tags such as "Boom Bap", "Shoegaze", or "Crunk".
Only applies styles when there is a clear, unambiguous match (artist
similarity >= MATCH_THRESHOLD). Ambiguous or missing results leave
``discogs_styles`` unchanged (empty list from Phase 4).

Auth: reads ``DISCOGS_TOKEN`` from ``.env``.  Token is sent as an
``Authorization: Discogs token=<value>`` header, not in the URL.

Resumable: every response (including negatives) is cached to
``.cache/discogs.json``.  Re-running is free for already-seen tracks.

Usage:
    python -m pipeline.enrich_discogs
"""

from __future__ import annotations

import json
import os
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipeline._http import RateLimitedClient
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
from pipeline.normalize import normalize_artist

log = get_logger(__name__)

DEFAULT_INPUT = TRACKS_WITH_METADATA_PATH
_SEARCH_URL = DISCOGS_API_ROOT + "database/search"
_USER_AGENT = "MusicEnrichment/1.0 (https://github.com/bfox010/music-enrichment)"

# Artist-name similarity required for a result to be accepted.
MATCH_THRESHOLD = 0.85


def _parse_artist_from_title(title: str) -> str:
    """Extract the artist portion from a Discogs title ('Artist – Release').

    Discogs uses an en-dash or plain hyphen as separator depending on the
    pressing.  We split on the first occurrence of either.
    """
    for sep in (" – ", " — ", " - "):
        if sep in title:
            return title.split(sep, 1)[0].strip()
    return title.strip()


def _artist_similarity(result: dict[str, Any], artist_normalized: str) -> float:
    """Return SequenceMatcher ratio between result artist and our artist."""
    title = result.get("title", "")
    artist_part = _parse_artist_from_title(title)
    disc_norm = normalize_artist(artist_part)
    return SequenceMatcher(None, disc_norm, artist_normalized).ratio()


def _extract_discogs_fields(
    response: Any,
    artist_normalized: str,
) -> dict[str, list[str]]:
    """Return ``{"discogs_styles": [...]}`` from a Discogs search response.

    Returns an empty list if the response is an error, has no results, or the
    top result's artist does not meet MATCH_THRESHOLD.
    """
    if not isinstance(response, dict) or response.get("_error"):
        return {"discogs_styles": []}

    results = response.get("results") or []
    if not results:
        return {"discogs_styles": []}

    top = results[0]
    if _artist_similarity(top, artist_normalized) < MATCH_THRESHOLD:
        return {"discogs_styles": []}

    styles = top.get("style") or []
    if not isinstance(styles, list):
        styles = [str(styles)] if styles else []
    return {"discogs_styles": list(styles)}


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_DISCOGS_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Enrich tracks with Discogs style tags.

    Parameters
    ----------
    input_path:
        Defaults to ``tracks_with_metadata.jsonl``.
    limit:
        Process only the first N tracks (debug/dry-run). None = all.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 4b: Discogs styles enrichment ===")

    load_dotenv(REPO_ROOT / ".env")
    token = os.getenv("DISCOGS_TOKEN")
    if not token:
        log.error("DISCOGS_TOKEN not set in .env — skipping Discogs enrichment")
        raise RuntimeError("DISCOGS_TOKEN missing")

    if input_path is None:
        input_path = DEFAULT_INPUT
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
        user_agent=_USER_AGENT,
        flush_every=100,
    )
    client.session.headers["Authorization"] = f"Discogs token={token}"

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
        artist_norm = track.get("artist_normalized", "")
        track_norm = track.get("track_normalized", "")
        cache_key = f"discogs|{artist_norm}|{track_norm}"

        params = {
            "type": "release",
            "artist": track.get("artist", ""),
            "track": track.get("track", ""),
            "per_page": 5,
        }
        response = client.get(_SEARCH_URL, params, cache_key)
        fields = _extract_discogs_fields(response, artist_norm)

        if isinstance(response, dict) and response.get("_error"):
            stats["errors"] += 1
        elif fields["discogs_styles"]:
            stats["matched"] += 1
        else:
            stats["no_match"] += 1

        track.update(fields)
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

    match_pct = 100 * stats["matched"] / stats["total"] if stats["total"] else 0
    log.info(
        "Phase 4b done: %d/%d with styles (%.1f%%) — %d no-match, %d errors",
        stats["matched"], stats["total"], match_pct,
        stats["no_match"], stats["errors"],
    )
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    result = enrich()
    sys.exit(0 if result["matched"] > 0 else 1)
