"""Phase 5b — batch audio-feature lookup via ReccoBeats, keyed by ISRC.

Second half of the Spotify-free chain (#37): 5a resolves an ISRC, this phase
spends it. ReccoBeats mirrors the pre-deprecation Spotify audio-features corpus
(bit-identical on 11 shared fields across 33 Exportify rows) and takes ISRCs
directly — no Spotify account anywhere in this path.

Two-step lookup:
1. ``_resolve_track_ids`` — ``GET track?ids=<isrc,...>``, a page at a time.
2. ``_fetch_audio_features`` — ``GET track/<id>/audio-features`` per resolved ID.

Emits the same block shape Exportify does (``{"source", "danceability", ...}``),
so every reader is unchanged; only ``source`` differs.

``time_signature`` is the one Exportify field ReccoBeats lacks. Left unset rather
than guessed — nothing downstream reads it.

Never overwrites an existing ``audio_features`` block without ``--force``: that
block is Exportify data acquired at real cost.

Usage:
    python -m pipeline.enrich_audio_features
    python -m pipeline.enrich_audio_features --limit 100
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
    RECCOBEATS_API_ROOT,
    RECCOBEATS_CACHE,
    RECCOBEATS_RATE_LIMIT,
    REPO_ROOT,
    TRACKS_RESOLVED_PATH,
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_WITH_FEATURES_PATH,
    TRACKS_WITH_ISRCS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.schema import read_jsonl, write_jsonl

log = get_logger(__name__)

# Phase 5 (iTunes availability) is the immediate predecessor — 5a resolved the
# ISRCs further upstream, ahead of 4e, so its output is no longer adjacent.
_INPUT_PRIORITY = [
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_RESOLVED_PATH,
    TRACKS_WITH_ISRCS_PATH,
]
DEFAULT_INPUT = TRACKS_WITH_AVAILABILITY_PATH

# How many ISRCs go in one resolve request. Conservative — no published cap.
_BATCH_SIZE = 40

# ReccoBeats field names, identical to the Spotify/Exportify vocabulary.
# time_signature is absent because ReccoBeats doesn't carry it.
_FEATURE_KEYS: tuple[str, ...] = (
    "danceability", "energy", "valence", "tempo", "loudness",
    "speechiness", "acousticness", "instrumentalness", "liveness",
    "key", "mode",
)


def _resolve_track_ids(client: RateLimitedClient, isrcs: list[str]) -> dict[str, str]:
    """Batch-resolve ISRCs to ReccoBeats track IDs. Returns ``{isrc: track_id}``.

    Only ISRCs the response actually matched are present in the result — a
    partial batch match is expected and not an error.
    """
    out: dict[str, str] = {}
    for i in range(0, len(isrcs), _BATCH_SIZE):
        batch = isrcs[i:i + _BATCH_SIZE]
        cache_key = "resolve:" + ",".join(sorted(batch))
        response = client.get(
            f"{RECCOBEATS_API_ROOT}track", {"ids": ",".join(batch)}, cache_key
        )
        if not isinstance(response, dict) or response.get("_error"):
            continue
        content = response.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            isrc = item.get("isrc")
            track_id = item.get("id")
            if isinstance(isrc, str) and isinstance(track_id, str) and isrc and track_id:
                out[isrc.strip().upper()] = track_id
    return out


def _parse_features(response: Any) -> dict[str, Any] | None:
    """Extract the feature dict from a ``track/<id>/audio-features`` response."""
    if not isinstance(response, dict) or response.get("_error"):
        return None
    features = {k: response[k] for k in _FEATURE_KEYS if k in response}
    return features if features else None


def _fetch_audio_features(client: RateLimitedClient, track_id: str) -> dict[str, Any] | None:
    url = f"{RECCOBEATS_API_ROOT}track/{track_id}/audio-features"
    response = client.get(url, {}, f"features:{track_id}")
    return _parse_features(response)


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_FEATURES_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
    force: str = FORCE_OFF,
) -> dict[str, int]:
    """Fetch ReccoBeats audio features for tracks with an ISRC but no features."""
    configure_logging(run_log_path)
    log.info("=== Phase 5b: ReccoBeats audio features ===")

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

    client = RateLimitedClient(
        RECCOBEATS_CACHE,
        rate_per_second=RECCOBEATS_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0",
        flush_every=50,
        force=force,
    )

    candidates = [
        t for t in tracks
        if t.get("isrc") and (force != FORCE_OFF or not t.get("audio_features"))
    ]
    client.warn_if_forced(len(candidates))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {"total": len(tracks), "already_had": len(tracks) - len(candidates),
              "resolved": 0, "unresolved": 0}

    try:
        isrcs = [t["isrc"] for t in candidates]
        track_ids = _resolve_track_ids(client, isrcs)
        log.info("Resolved %d/%d ISRCs to ReccoBeats track IDs", len(track_ids), len(isrcs))

        t0 = time.monotonic()
        for i, track in enumerate(candidates, start=1):
            track_id = track_ids.get(track["isrc"])
            features = _fetch_audio_features(client, track_id) if track_id else None
            if features:
                features["source"] = "reccobeats"
                features["retrieved_at"] = today
                track["audio_features"] = features
                sources = track.setdefault("enrichment_sources", [])
                if "reccobeats" not in sources:
                    sources.append("reccobeats")
                stats["resolved"] += 1
            else:
                stats["unresolved"] += 1

            if i % 250 == 0 or i == len(candidates):
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta_min = (len(candidates) - i) / rate / 60 if rate > 0 else 0
                log.info(
                    "Progress: %d/%d (%.2f/s, ETA %.1f min) — resolved=%d unresolved=%d",
                    i, len(candidates), rate, eta_min,
                    stats["resolved"], stats["unresolved"],
                )
    finally:
        client.flush()

    write_jsonl(tracks, output_path)
    log.info(
        "Phase 5b done: resolved=%d  unresolved=%d  already_had=%d  /  %d total",
        stats["resolved"], stats["unresolved"], stats["already_had"], stats["total"],
    )
    log.info("  %s", client.cache_summary())
    log.info("Wrote -> %s", output_path)
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch ReccoBeats audio features by ISRC.")
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--output", type=Path, default=TRACKS_WITH_FEATURES_PATH)
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    enrich(input_path=args.input, output_path=args.output, limit=args.limit)
    sys.exit(0)
