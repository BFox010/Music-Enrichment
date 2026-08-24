"""Phase 5a — resolve ISRCs via MusicBrainz (ID join), then Deezer (name search).

Part of the Spotify-free audio-feature chain (#37). ``audio_features`` and
``spotify_id`` correlated perfectly on this library because Exportify was the
only source of both — closing that gap first needs an ISRC, since that is the
identifier Phase 5b's ReccoBeats lookup and every named fallback (AcousticBrainz,
frozen Kaggle corpora) key on. ``spotify_id`` is not resolved here at all.

Resolver order, cheapest and most durable first (see issue #37's second
comment for the measurement this follows):

1. **MusicBrainz** — a track that already carries ``musicbrainz_id`` gets its
   ISRC via ``recording/<mbid>?inc=isrcs``. An exact ID join, no auth, and
   Phase 4 already talks to MusicBrainz through the same rate-limited client.
2. **Deezer** — for everything MusicBrainz doesn't cover: search by
   artist/track (retried through the shared ``name_variations`` cascade),
   then read the ISRC off the matched track. No auth, no key.

A track that already holds an ``isrc`` is skipped outright — it came from
Exportify (or Phase B) by a different route and is not this phase's to
second-guess; ``pipeline.update_tracks._FILL_ONLY_FIELDS`` enforces the same
rule again at the final merge.

Both API shapes below are unverified against a live response — the environment
this was built in has no outbound access to musicbrainz.org or
api.deezer.com — so each is isolated in one small parsing function
(``_isrc_from_musicbrainz_response`` / ``_best_deezer_match`` /
``_isrc_from_deezer_track``) for a one-line fix if the real response disagrees.
Confirm on the first real run, the same posture PR #24 (Phase B) took for
Spotify's ``external_ids.isrc``.

Does **not** touch ``canonical_track_id``. That is computed once, in Phase 4e,
before this phase ever runs — this phase must not recompute it, or a track
currently keyed ``norm:...`` would re-key to ``isrc:...`` the moment its ISRC
resolves, reshuffling which rows Phase 4e's identity clustering considers the
same recording on the next run.

Usage:
    python -m pipeline.resolve_isrcs
    python -m pipeline.resolve_isrcs --limit 100
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline._http import FORCE_OFF, RateLimitedClient
from pipeline.config import (
    DEEZER_API_ROOT,
    DEEZER_CACHE,
    DEEZER_RATE_LIMIT,
    MUSICBRAINZ_API_ROOT,
    MUSICBRAINZ_CACHE,
    MUSICBRAINZ_RATE_LIMIT,
    REPO_ROOT,
    TRACKS_WITH_AVAILABILITY_PATH,
    configure_logging,
    get_logger,
)
from pipeline.name_variations import lookup_variations
from pipeline.normalize import normalize_artist, normalize_track
from pipeline.schema import read_jsonl, write_jsonl

log = get_logger(__name__)

TRACKS_WITH_ISRCS_PATH: Path = REPO_ROOT / "tracks_with_isrcs.jsonl"

# Input preference — deepest in the chain first. Phase 5 (iTunes availability)
# is the immediate predecessor.
_INPUT_PRIORITY = [
    TRACKS_WITH_AVAILABILITY_PATH,
    REPO_ROOT / "tracks_resolved.jsonl",
]
DEFAULT_INPUT = TRACKS_WITH_AVAILABILITY_PATH


# ── MusicBrainz: recording ID → ISRC ──────────────────────────────────────


def _isrc_from_musicbrainz_response(response: Any) -> str | None:
    """Pull the first ISRC off a ``recording?inc=isrcs`` response.

    A recording can carry several ISRCs (regional re-releases); the first is
    good enough — ReccoBeats only needs one that resolves.
    """
    if not isinstance(response, dict) or response.get("_error"):
        return None
    isrcs = response.get("isrcs")
    if isinstance(isrcs, list):
        for candidate in isrcs:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().upper()
    return None


def _resolve_musicbrainz(client: RateLimitedClient, mbid: str) -> str | None:
    url = f"{MUSICBRAINZ_API_ROOT}recording/{mbid}"
    params = {"inc": "isrcs", "fmt": "json"}
    # Distinct prefix from Phase 4's cache keys on the same shared cache file —
    # Phase 4 never requests ``inc=isrcs``, so the two can't collide, but the
    # prefix keeps that true even if Phase 4's key shape changes later.
    cache_key = f"recording-isrcs:{mbid}"
    response = client.get(url, params, cache_key)
    return _isrc_from_musicbrainz_response(response)


# ── Deezer: name search → track → ISRC ────────────────────────────────────


def _best_deezer_match(
    response: Any, artist_norm: str, track_norm: str
) -> dict[str, Any] | None:
    """Pick the search result whose normalized artist+title matches exactly."""
    if not isinstance(response, dict) or response.get("_error"):
        return None
    data = response.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        artist = (item.get("artist") or {}).get("name") or ""
        if (normalize_track(title) == track_norm
                and normalize_artist(artist) == artist_norm):
            return item
    return None


def _isrc_from_deezer_track(response: Any) -> str | None:
    """Pull ``isrc`` off a Deezer ``/track/<id>`` response."""
    if not isinstance(response, dict) or response.get("_error"):
        return None
    isrc = response.get("isrc")
    return isrc.strip().upper() if isinstance(isrc, str) and isrc.strip() else None


def _resolve_deezer(client: RateLimitedClient, artist: str, track: str) -> str | None:
    search_url = f"{DEEZER_API_ROOT}search"
    for label, var_artist, var_track in lookup_variations(artist, track):
        artist_norm = normalize_artist(var_artist)
        track_norm = normalize_track(var_track)
        cache_key = f"search:{artist_norm}|{track_norm}"
        if label != "original":
            cache_key += f"#{label}"
        query = f'artist:"{var_artist}" track:"{var_track}"'
        response = client.get(search_url, {"q": query}, cache_key)
        match = _best_deezer_match(response, artist_norm, track_norm)
        if not match or not match.get("id"):
            continue
        track_response = client.get(
            f"{DEEZER_API_ROOT}track/{match['id']}", {}, f"track:{match['id']}"
        )
        isrc = _isrc_from_deezer_track(track_response)
        if isrc:
            return isrc
    return None


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_ISRCS_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
    force: str = FORCE_OFF,
) -> dict[str, int]:
    """Resolve ISRCs for tracks that lack one. Returns a stats dict."""
    configure_logging(run_log_path)
    log.info("=== Phase 5a: ISRC resolution (MusicBrainz -> Deezer) ===")

    if input_path is None:
        input_path = next((p for p in _INPUT_PRIORITY if p.exists()), DEFAULT_INPUT)
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tracks = read_jsonl(input_path)
    if limit is not None:
        tracks = tracks[:limit]
    log.info("Tracks loaded: %d", len(tracks))

    mb_client = RateLimitedClient(
        MUSICBRAINZ_CACHE,
        rate_per_second=MUSICBRAINZ_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0 (q9nf44tycd@privaterelay.appleid.com)",
        flush_every=50,
        force=force,
    )
    dz_client = RateLimitedClient(
        DEEZER_CACHE,
        rate_per_second=DEEZER_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0",
        flush_every=50,
        force=force,
    )
    mb_client.warn_if_forced(sum(1 for t in tracks if not t.get("isrc") and t.get("musicbrainz_id")))
    dz_client.warn_if_forced(sum(1 for t in tracks if not t.get("isrc")))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {
        "total": len(tracks), "already_had": 0,
        "resolved_musicbrainz": 0, "resolved_deezer": 0, "unresolved": 0,
    }
    t0 = time.monotonic()
    to_resolve = sum(1 for t in tracks if not t.get("isrc"))
    done = 0

    try:
        for track in tracks:
            if track.get("isrc"):
                stats["already_had"] += 1
                continue

            isrc: str | None = None
            source: str | None = None
            mbid = track.get("musicbrainz_id")
            if mbid:
                isrc = _resolve_musicbrainz(mb_client, mbid)
                if isrc:
                    source = "musicbrainz"
            if not isrc:
                isrc = _resolve_deezer(
                    dz_client, track.get("artist", ""), track.get("track", "")
                )
                if isrc:
                    source = "deezer"

            if isrc:
                track["isrc"] = isrc
                track["isrc_source"] = source
                track["isrc_retrieved_at"] = today
                sources = track.setdefault("enrichment_sources", [])
                if source not in sources:
                    sources.append(source)
                stats[f"resolved_{source}"] += 1
            else:
                stats["unresolved"] += 1

            done += 1
            if done % 250 == 0 or done == to_resolve:
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta_min = (to_resolve - done) / rate / 60 if rate > 0 else 0
                log.info(
                    "Progress: %d/%d to-resolve (%.2f/s, ETA %.1f min) — "
                    "mb=%d deezer=%d unresolved=%d",
                    done, to_resolve, rate, eta_min,
                    stats["resolved_musicbrainz"], stats["resolved_deezer"],
                    stats["unresolved"],
                )
    finally:
        mb_client.flush()
        dz_client.flush()

    write_jsonl(tracks, output_path)
    resolved = stats["resolved_musicbrainz"] + stats["resolved_deezer"]
    log.info(
        "Phase 5a done: resolved=%d (musicbrainz=%d deezer=%d)  unresolved=%d  "
        "already_had=%d  /  %d total",
        resolved, stats["resolved_musicbrainz"], stats["resolved_deezer"],
        stats["unresolved"], stats["already_had"], stats["total"],
    )
    log.info("  %s", mb_client.cache_summary())
    log.info("  %s", dz_client.cache_summary())
    log.info("Wrote -> %s", output_path)
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resolve ISRCs via MusicBrainz then Deezer.")
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--output", type=Path, default=TRACKS_WITH_ISRCS_PATH)
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    enrich(input_path=args.input, output_path=args.output, limit=args.limit)
    sys.exit(0)
