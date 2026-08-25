"""Diagnostic: measure Phase B (Spotify ID resolution) against ground truth.

Treats the Exportify-sourced spotify_ids in tracks.jsonl as ground truth:
re-resolves each from scratch through Phase B's own matcher and reports recall
(found an ID) and precision (found the right one). Validates match quality on
real data before trusting the resolver on rows that have no ID.

Ignores any stored ISRC by default, so it measures the artist+track path a fresh
run would rely on. --use-isrc also exercises the ISRC-exact lookup.

Uses a SEPARATE cache (.cache/spotify_eval.json) — never pollutes Phase B's.

Usage:
    python scripts/eval_spotify_resolution.py [--sample N] [--use-isrc]

Requires Spotify credentials (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET env
vars, or inputs/spotify_credentials.json).
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline._http import RateLimitedClient  # noqa: E402
from pipeline.config import (  # noqa: E402
    CACHE_DIR,
    SPOTIFY_RATE_LIMIT,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.enrich_spotify_ids import SpotifyAuth, _resolve_one, load_credentials  # noqa: E402
from pipeline.schema import read_jsonl  # noqa: E402

log = get_logger(__name__)
EVAL_CACHE = CACHE_DIR / "spotify_eval.json"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=None,
                        help="Randomly sample N ground-truth tracks (default: all).")
    parser.add_argument("--use-isrc", action="store_true",
                        help="Also try the ISRC-exact lookup (default: artist+track only).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    configure_logging()
    client_id, client_secret = load_credentials()

    tracks = read_jsonl(TRACKS_PATH)
    truth = [t for t in tracks if t.get("spotify_id")]
    log.info("Ground-truth tracks (have a known spotify_id): %d", len(truth))

    if args.sample is not None and args.sample < len(truth):
        random.seed(args.seed)
        truth = random.sample(truth, args.sample)
        log.info("Sampled down to %d", len(truth))

    auth = SpotifyAuth(client_id, client_secret)
    client = RateLimitedClient(
        EVAL_CACHE, rate_per_second=SPOTIFY_RATE_LIMIT,
        user_agent="MusicEnrichment/1.0", flush_every=50,
    )

    correct = wrong = unresolved = 0
    mismatches: list[tuple[str, str, str, str]] = []
    t0 = time.monotonic()

    for i, track in enumerate(truth, start=1):
        known = track["spotify_id"]
        isrc = track.get("isrc") if args.use_isrc else None
        got = _resolve_one(client, auth, track.get("artist", ""), track.get("track", ""), isrc)
        if got is None:
            unresolved += 1
        elif got == known:
            correct += 1
        else:
            wrong += 1
            if len(mismatches) < 25:
                mismatches.append((track.get("artist", ""), track.get("track", ""), known, got))

        if i % 100 == 0 or i == len(truth):
            rate = i / (time.monotonic() - t0)
            log.info("  %d/%d  (%.1f/s)  correct=%d wrong=%d unresolved=%d",
                     i, len(truth), rate, correct, wrong, unresolved)

    client.flush()

    n = len(truth)
    resolved = correct + wrong
    print("\n=== Spotify resolution vs. ground truth ===")
    print(f"  tracks evaluated : {n}")
    print(f"  resolved (recall): {resolved}/{n}  = {resolved / n:.1%}" if n else "  no tracks")
    if resolved:
        print(f"  correct (precision of resolved): {correct}/{resolved} = {correct / resolved:.1%}")
    print(f"  end-to-end accuracy (correct/total): {correct}/{n} = {correct / n:.1%}" if n else "")
    print(f"  unresolved: {unresolved}   wrong-id: {wrong}")
    if mismatches:
        print("\n  sample wrong-id matches (artist — track | known -> got):")
        for a, t, known, got in mismatches:
            print(f"    {a} — {t}  |  {known} -> {got}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
