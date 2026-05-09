"""Phase 5 — Apple Music availability check via iTunes Search API.

For each track, queries https://itunes.apple.com/search?term=<artist track>&entity=song&country=us
and matches the response back to the track. Sets:
  - apple_music_available: bool   (probable, NOT confirmed — iTunes Search has known false positives)
  - apple_music_id: str | None    (Apple's trackId — distinct from iTunes Persistent ID)
  - apple_music_checked_at: str   (ISO date)

Caching: ``.cache/itunes_search.json``, keyed by ``artist_norm|track_norm``.
Re-checks only when ``apple_music_checked_at`` is older than
``APPLE_MUSIC_CACHE_DAYS`` (90 by default).

Usage:
    python -m pipeline.check_apple_music
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline._http import RateLimitedClient
from pipeline.config import (
    APPLE_MUSIC_CACHE,
    APPLE_MUSIC_CACHE_DAYS,
    ITUNES_RATE_LIMIT,
    ITUNES_SEARCH_API,
    REPO_ROOT,
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_WITH_METADATA_PATH,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)

DEFAULT_INPUT = TRACKS_WITH_METADATA_PATH


def _best_match(response: Any, artist_norm: str, track_norm: str) -> dict[str, Any] | None:
    """Pick the iTunes Search result whose normalized artist+track matches best.

    Strategy: exact normalized match on both artist and track wins. If no
    exact match, return None (we'd rather report "not available" than guess).
    """
    if not isinstance(response, dict):
        return None
    if response.get("_error"):
        return None
    results = response.get("results") or []
    for r in results:
        if not isinstance(r, dict):
            continue
        r_artist = r.get("artistName") or ""
        r_track = r.get("trackName") or ""
        if (normalize_artist(r_artist) == artist_norm
                and normalize_track(r_track) == track_norm):
            return r
    return None


def _is_stale(checked_at: str | None) -> bool:
    """True if `checked_at` is missing or older than APPLE_MUSIC_CACHE_DAYS."""
    if not checked_at:
        return True
    try:
        dt = datetime.strptime(checked_at, "%Y-%m-%d")
    except ValueError:
        return True
    age = datetime.now(timezone.utc).replace(tzinfo=None) - dt
    return age > timedelta(days=APPLE_MUSIC_CACHE_DAYS)


def check(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_AVAILABILITY_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Probe iTunes Search for each track. Returns stats dict."""
    configure_logging(run_log_path)
    log.info("=== Phase 5: Apple Music availability ===")

    if input_path is None:
        input_path = DEFAULT_INPUT if DEFAULT_INPUT.exists() else (
            REPO_ROOT / "tracks_with_apple.jsonl"
        )
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)
    log.info("Cache : %s", APPLE_MUSIC_CACHE)

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
    log.info("Tracks to check: %d", len(tracks))

    client = RateLimitedClient(
        APPLE_MUSIC_CACHE,
        rate_per_second=ITUNES_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0 (q9nf44tycd@privaterelay.appleid.com)",
        flush_every=50,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {"total": len(tracks), "available": 0, "unavailable": 0, "skipped_fresh": 0, "errors": 0}
    t0 = time.monotonic()
    enriched: list[dict] = []

    for i, track in enumerate(tracks, start=1):
        # Skip if checked recently
        if not _is_stale(track.get("apple_music_checked_at")):
            stats["skipped_fresh"] += 1
            enriched.append(track)
            continue

        cache_key = f"{track['artist_normalized']}|{track['track_normalized']}"
        # Build search term — limit to 200 chars per iTunes API guidance
        term = f"{track['artist']} {track['track']}"[:200]
        params = {"term": term, "entity": "song", "country": "us", "limit": 25}
        response = client.get(ITUNES_SEARCH_API, params, cache_key)

        if isinstance(response, dict) and response.get("_error"):
            stats["errors"] += 1
            track.update({
                "apple_music_available": False,
                "apple_music_id": None,
                "apple_music_checked_at": today,
            })
            enriched.append(track)
            continue

        match = _best_match(response, track["artist_normalized"], track["track_normalized"])
        if match:
            stats["available"] += 1
            track.update({
                "apple_music_available": True,
                "apple_music_id": str(match.get("trackId") or "") or None,
                "apple_music_checked_at": today,
            })
        else:
            stats["unavailable"] += 1
            track.update({
                "apple_music_available": False,
                "apple_music_id": None,
                "apple_music_checked_at": today,
            })
        enriched.append(track)

        if i % 250 == 0 or i == len(tracks):
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta_min = (len(tracks) - i) / rate / 60 if rate > 0 else 0
            log.info(
                "Progress: %d/%d (%.1f%%) — %.2f tracks/sec — ETA %.1f min — avail=%d unavail=%d errors=%d",
                i, len(tracks), 100 * i / len(tracks), rate, eta_min,
                stats["available"], stats["unavailable"], stats["errors"],
            )

    client.flush()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in enriched:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "Phase 5 done: avail=%d  unavail=%d  errors=%d  skipped_fresh=%d  /  %d total",
        stats["available"], stats["unavailable"], stats["errors"], stats["skipped_fresh"],
        stats["total"],
    )
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    stats = check()
    sys.exit(0)
