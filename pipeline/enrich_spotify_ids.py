"""Phase B — resolve ISRCs and Spotify track IDs via the Spotify Search API.

For each track without a ``spotify_id``, queries
``https://api.spotify.com/v1/search?type=track&q=<artist track>`` and matches
the response back to the track using the same exact-normalized strategy as
Phase 5 (``check_apple_music._best_match``): an exact normalized artist+track
match wins, otherwise we move on rather than guess. On a miss we retry with the
shared ``name_variations`` recovery rules (strip_feat / strip_parens /
first_artist), exactly like the Last.fm enrichment phase.

**The ISRC is the point of this phase; ``spotify_id`` is incidental.** A matched
result carries ``external_ids.isrc`` — an open standard ReccoBeats, MusicBrainz
and AcousticBrainz all accept, which keeps the feature source swappable.
``spotify_id`` is re-resolvable only through Spotify (whose eligibility rules
keep tightening) and is stored only because ReccoBeats also takes it.

Spotify closed ``audio-features`` to new apps (2024-11-27); **Search is unaffected**.

This is the **last-resort resolver**: Phases 5a/5b now cover the same ground
without auth, so B only runs for what they leave unresolved.

Auth: Client-Credentials (no user login) via ``SPOTIFY_CLIENT_ID`` /
``SPOTIFY_CLIENT_SECRET`` or ``inputs/spotify_credentials.json``. Neither present
⇒ raises ``FileNotFoundError`` and the orchestrator SKIPs the phase.

Cache ``.cache/spotify_search.json``, keyed ``artist_norm|track_norm`` (+variation).

No ISRC → ``spotify_id`` lookup: a track that has an ISRC needs nothing further
from Spotify, so it would only spend rate limit on a redundant key.

Usage:
    python -m pipeline.enrich_spotify_ids
    python -m pipeline.enrich_spotify_ids --limit 100
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from pipeline._http import RateLimitedClient
from pipeline.config import (
    INPUT_SPOTIFY_CREDENTIALS,
    SPOTIFY_API_ROOT,
    SPOTIFY_CACHE,
    SPOTIFY_RATE_LIMIT,
    SPOTIFY_TOKEN_URL,
    TRACKS_SKELETON_PATH,
    TRACKS_WITH_SPOTIFY_PATH,
    configure_logging,
    get_logger,
)
from pipeline.enrich_apple_library import TRACKS_WITH_APPLE_PATH
from pipeline.name_variations import lookup_variations
from pipeline.normalize import normalize_artist, normalize_track
from pipeline.schema import read_jsonl, write_jsonl

log = get_logger(__name__)

# Input preference — deepest in the chain first. Phase A (iTunes XML) is the
# immediate predecessor; fall back to the dedupe skeleton if A was skipped.
_INPUT_PRIORITY = [
    TRACKS_WITH_APPLE_PATH,
    TRACKS_SKELETON_PATH,
]
DEFAULT_INPUT = TRACKS_WITH_APPLE_PATH


class SpotifyAuth:
    """Client-Credentials token provider with lazy refresh.

    Tokens last ~3600s; ``token()`` refreshes a minute before expiry so a long
    run never hands a stale token to the search client (which would otherwise
    cache a 401 as a permanent negative).
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        if self._token is None or time.monotonic() >= self._expires_at - 60:
            self._refresh()
        assert self._token is not None
        return self._token

    def _refresh(self) -> None:
        resp = requests.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        log.info("Obtained Spotify access token (expires in %ss)", payload.get("expires_in"))


def load_credentials() -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from env or the credentials JSON.

    Raises FileNotFoundError if neither source is available — the orchestrator
    treats that as a benign SKIP for this optional phase.
    """
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if cid and secret:
        return cid, secret

    if INPUT_SPOTIFY_CREDENTIALS.exists():
        with open(INPUT_SPOTIFY_CREDENTIALS, encoding="utf-8") as fh:
            data = json.load(fh)
        cid = data.get("client_id")
        secret = data.get("client_secret")
        if cid and secret:
            return cid, secret
        raise ValueError(
            f"{INPUT_SPOTIFY_CREDENTIALS} must contain 'client_id' and 'client_secret'"
        )

    raise FileNotFoundError(
        "Spotify credentials not found. Set SPOTIFY_CLIENT_ID / "
        f"SPOTIFY_CLIENT_SECRET, or create {INPUT_SPOTIFY_CREDENTIALS} with "
        '{"client_id": "...", "client_secret": "..."}'
    )


def _best_match(response: Any, artist_norm: str, track_norm: str) -> dict[str, Any] | None:
    """Pick the search result whose normalized artist+track matches exactly.

    Strategy mirrors ``check_apple_music._best_match``: the track title must
    match exactly (normalized) and at least one credited artist must match
    exactly (normalized). No exact match → None (rather report nothing than
    attach the wrong ID).
    """
    if not isinstance(response, dict) or response.get("_error"):
        return None
    items = ((response.get("tracks") or {}).get("items")) or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if normalize_track(item.get("name") or "") != track_norm:
            continue
        for artist in item.get("artists") or []:
            if isinstance(artist, dict) and normalize_artist(artist.get("name") or "") == artist_norm:
                return item
    return None


def _extract_isrc(item: dict[str, Any]) -> str | None:
    """Pull ``external_ids.isrc`` off a matched Spotify track object.

    Tolerant by design: the field is absent on some catalogue entries, and a
    missing ISRC must degrade to "we only learned the Spotify ID", never fail
    the track.
    """
    external = item.get("external_ids")
    if not isinstance(external, dict):
        return None
    isrc = external.get("isrc")
    return isrc.strip().upper() if isinstance(isrc, str) and isrc.strip() else None


def _resolve_one(
    client: RateLimitedClient,
    auth: SpotifyAuth,
    artist: str,
    track: str,
) -> tuple[str | None, str | None]:
    """Return ``(spotify_id, isrc)`` for ``artist``/``track``; either may be None.

    Walks the measured name-variation cascade and returns both identifiers off
    the first exact match. The ISRC is the more valuable of the two: it is an
    open standard that ReccoBeats, AcousticBrainz and MusicBrainz all accept,
    so it keeps the audio-feature source swappable, while ``spotify_id`` is
    re-resolvable only through Spotify (see issue #37).

    The Authorization header is refreshed immediately before each network call
    so a long run can't go stale.
    """
    search_url = SPOTIFY_API_ROOT + "search"
    base_key = f"{normalize_artist(artist)}|{normalize_track(track)}"

    for label, var_artist, var_track in lookup_variations(artist, track):
        cache_key = base_key if label == "original" else f"{base_key}#{label}"
        client.session.headers["Authorization"] = f"Bearer {auth.token()}"
        term = f"{var_artist} {var_track}".strip()[:250]
        params = {"q": term, "type": "track", "limit": 10}
        response = client.get(search_url, params, cache_key)
        match = _best_match(
            response, normalize_artist(var_artist), normalize_track(var_track)
        )
        if match and match.get("id"):
            return match["id"], _extract_isrc(match)
    return None, None


def enrich(
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_SPOTIFY_PATH,
    run_log_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Resolve Spotify IDs for tracks that lack one. Returns a stats dict."""
    configure_logging(run_log_path)
    log.info("=== Phase B: Spotify ID resolution ===")

    # Fail fast (→ orchestrator SKIP) before reading anything if no creds.
    client_id, client_secret = load_credentials()

    if input_path is None:
        input_path = next((p for p in _INPUT_PRIORITY if p.exists()), DEFAULT_INPUT)
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)
    log.info("Cache : %s", SPOTIFY_CACHE)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tracks = read_jsonl(input_path)
    if limit is not None:
        tracks = tracks[:limit]
    log.info("Tracks loaded: %d", len(tracks))

    auth = SpotifyAuth(client_id, client_secret)
    client = RateLimitedClient(
        SPOTIFY_CACHE,
        rate_per_second=SPOTIFY_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0",
        flush_every=50,
    )

    stats = {
        "total": len(tracks),
        "already_had": 0,
        "resolved": 0,
        "unmatched": 0,
        "isrc_captured": 0,
    }
    t0 = time.monotonic()
    to_resolve = sum(1 for t in tracks if not t.get("spotify_id"))

    for i, track in enumerate(tracks, start=1):
        if track.get("spotify_id"):
            stats["already_had"] += 1
            continue

        spotify_id, isrc = _resolve_one(
            client,
            auth,
            track.get("artist", ""),
            track.get("track", ""),
        )
        if spotify_id:
            track["spotify_id"] = spotify_id
            # The ISRC rides along free on the same matched object. Never
            # overwrite one we already hold — an Exportify-sourced ISRC was
            # matched by a different route and is not ours to second-guess.
            if isrc and not track.get("isrc"):
                track["isrc"] = isrc
                stats["isrc_captured"] += 1
            sources = track.setdefault("enrichment_sources", [])
            if "spotify_search" not in sources:
                sources.append("spotify_search")
            stats["resolved"] += 1
        else:
            stats["unmatched"] += 1

        done = stats["resolved"] + stats["unmatched"]
        if done % 250 == 0 or done == to_resolve:
            elapsed = time.monotonic() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta_min = (to_resolve - done) / rate / 60 if rate > 0 else 0
            log.info(
                "Progress: %d/%d to-resolve (%.2f/s, ETA %.1f min) — resolved=%d unmatched=%d",
                done, to_resolve, rate, eta_min, stats["resolved"], stats["unmatched"],
            )

    client.flush()

    write_jsonl(tracks, output_path)
    log.info(
        "Phase B done: resolved=%d (isrc captured on %d)  unmatched=%d  "
        "already_had=%d  /  %d total",
        stats["resolved"], stats["isrc_captured"], stats["unmatched"],
        stats["already_had"], stats["total"],
    )
    log.info("Wrote → %s", output_path)
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resolve Spotify track IDs via Search API.")
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--output", type=Path, default=TRACKS_WITH_SPOTIFY_PATH)
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    enrich(input_path=args.input, output_path=args.output, limit=args.limit)
    sys.exit(0)
