"""Diagnostic: test artist/track name variations against Last.fm for the 308 tracks
the pipeline couldn't match at all (no MusicBrainz ID, no tags).

For each track, generates up to 5 name variations and queries Last.fm track.getInfo.
Records which variation (if any) gets a hit, so we know what normalisation rule
to add permanently to the pipeline.

Results are written to inputs/match_variation_results.csv (gitignored).

Usage:
    python scripts/test_match_variations.py [--limit N]

Requires LASTFM_API_KEY in .env. Uses a separate cache
(.cache/match_variations.json) so it never pollutes the main lastfm cache.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import os

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline._http import RateLimitedClient
from pipeline.config import LASTFM_API_ROOT, INPUTS_DIR, CACHE_DIR

VARIATION_CACHE  = CACHE_DIR / "match_variations.json"
RESULTS_CSV      = INPUTS_DIR / "match_variation_results.csv"
TRACKS_JSONL     = REPO_ROOT / "tracks.jsonl"
RATE_LIMIT       = 4.0   # req/sec — stay under Last.fm's 5/sec hard limit


# ── Name variation generators ─────────────────────────────────────────────────

def _strip_feat_from_track(track: str) -> str:
    """'1 Train (feat. Kendrick Lamar, ...)' → '1 Train'"""
    return re.sub(r"\s*[\(\[]feat\.?.*", "", track, flags=re.IGNORECASE).strip()

def _strip_all_parens(track: str) -> str:
    """Remove ALL parenthetical/bracketed annotations from track title."""
    t = re.sub(r"\s*\([^)]*\)", "", track)
    t = re.sub(r"\s*\[[^\]]*\]", "", t)
    return t.strip()

def _first_artist(artist: str) -> str:
    """'070 Shake & Tame Impala' → '070 Shake'
       'JAY-Z & Kanye West'      → 'JAY-Z'
       'A$AP Rocky, Tyler, ...'  → 'A$AP Rocky'
    """
    # Split on ' & ', ' x ' (word boundary), ' and ', or ','
    parts = re.split(r"\s+&\s+|\s+[Xx]\s+(?=[A-Z$])|,\s*", artist)
    return parts[0].strip() if parts else artist

def _remove_special_chars(s: str) -> str:
    """'A$AP Rocky' → 'ASAP Rocky', '$uicideboy$' → 'Suicideboys'"""
    # $ is commonly used as S in hip-hop names (A$AP, $uicideboy$)
    s = s.replace("$", "S")
    return re.sub(r"[@!#%^*]", "", s).strip()

def _normalize_artist(artist: str) -> str:
    """Lowercase + remove special chars — matches Last.fm's internal normalisation."""
    return _remove_special_chars(artist).lower()


def generate_variations(artist: str, track: str) -> list[dict]:
    """Return a list of dicts with keys: variation_name, artist, track."""
    clean_track_feat   = _strip_feat_from_track(track)
    clean_track_parens = _strip_all_parens(track)
    first_art          = _first_artist(artist)
    clean_artist       = _remove_special_chars(artist)

    variations = [
        # 1 — original (baseline, already failed — included for completeness)
        {"variation": "original",
         "artist": artist, "track": track},

        # 2 — strip feat. and everything after from track title
        {"variation": "strip_feat",
         "artist": artist, "track": clean_track_feat},

        # 3 — strip ALL parentheticals from track title
        {"variation": "strip_parens",
         "artist": artist, "track": clean_track_parens},

        # 4 — first artist only (split on & / x / ,)
        {"variation": "first_artist",
         "artist": first_art, "track": track},

        # 5 — first artist + strip feat. (most aggressive clean, most likely to hit)
        {"variation": "first_artist+strip_feat",
         "artist": first_art, "track": clean_track_feat},

        # 6 — remove $ and other special chars from artist name
        {"variation": "clean_artist",
         "artist": clean_artist, "track": track},

        # 7 — clean artist + strip feat. (for A$AP tracks with long feat. strings)
        {"variation": "clean_artist+strip_feat",
         "artist": clean_artist, "track": clean_track_feat},
    ]

    # De-duplicate: skip variations identical to a prior one
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for v in variations:
        key = (v["artist"].lower(), v["track"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique


# ── Last.fm query ─────────────────────────────────────────────────────────────

def _query_lastfm(client: RateLimitedClient, api_key: str, artist: str, track: str) -> dict:
    cache_key = f"var|{artist.lower()}|{track.lower()}"
    params = {
        "method": "track.getInfo",
        "api_key": api_key,
        "artist": artist,
        "track": track,
        "format": "json",
        "autocorrect": "1",
    }
    return client.get(LASTFM_API_ROOT, params, cache_key)


def _is_hit(response: dict) -> bool:
    """An *actionable* hit yields something the pipeline can actually store:
    a track-level MBID or at least one top tag. A bare ``listeners`` count
    means Last.fm has a thin page for the name but nothing usable — that is
    exactly the state these tracks are already in, so it does not count.
    This mirrors enrich_metadata's own keep-condition (musicbrainz_id or tags)."""
    if not isinstance(response, dict) or response.get("_error"):
        return False
    t = response.get("track") or {}
    if t.get("mbid"):
        return True
    toptags = (t.get("toptags") or {}).get("tag") or []
    if isinstance(toptags, dict):  # single-tag responses can come as dict
        toptags = [toptags]
    return any(isinstance(tag, dict) and tag.get("name") for tag in toptags)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(limit: int | None = None) -> None:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("LASTFM_API_KEY")
    if not api_key:
        print("ERROR: LASTFM_API_KEY not set in .env")
        sys.exit(1)

    # Load the 308 genuine misses
    no_source_no_mbid: list[dict] = []
    with open(TRACKS_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if (not t.get("lastfm_tags") and not t.get("discogs_styles")
                    and not t.get("itunes_genre") and not t.get("musicbrainz_id")):
                no_source_no_mbid.append(t)

    if limit:
        no_source_no_mbid = no_source_no_mbid[:limit]

    print(f"Testing {len(no_source_no_mbid)} tracks × up to 7 variations each")
    print(f"Cache: {VARIATION_CACHE}")
    print(f"Results: {RESULTS_CSV}")
    print()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)

    client = RateLimitedClient(
        VARIATION_CACHE,
        rate_per_second=RATE_LIMIT,
        flush_every=50,
    )

    rows: list[dict] = []
    hits_by_variation: dict[str, int] = {}
    total_tracks_fixed = 0

    for i, track in enumerate(no_source_no_mbid, 1):
        orig_artist = track["artist"]
        orig_track  = track["track"]
        variations  = generate_variations(orig_artist, orig_track)

        track_hit_variation = None
        for v in variations:
            resp = _query_lastfm(client, api_key, v["artist"], v["track"])
            hit  = _is_hit(resp)
            rows.append({
                "original_artist":  orig_artist,
                "original_track":   orig_track,
                "variation":        v["variation"],
                "query_artist":     v["artist"],
                "query_track":      v["track"],
                "hit":              "Y" if hit else "N",
            })
            if hit and track_hit_variation is None:
                track_hit_variation = v["variation"]
                hits_by_variation[v["variation"]] = hits_by_variation.get(v["variation"], 0) + 1

        if track_hit_variation:
            total_tracks_fixed += 1

        if i % 25 == 0 or i == len(no_source_no_mbid):
            print(f"  {i}/{len(no_source_no_mbid)} tracks tested — "
                  f"{total_tracks_fixed} fixable so far")

    client.flush()

    # Write CSV
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["original_artist", "original_track",
                                                  "variation", "query_artist",
                                                  "query_track", "hit"])
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"=== Results: {total_tracks_fixed}/{len(no_source_no_mbid)} tracks fixable by some variation ===")
    print()
    print("First-hit variation breakdown (which rule fixed it):")
    for var, count in sorted(hits_by_variation.items(), key=lambda x: -x[1]):
        print(f"  {count:3d}  {var}")
    print()
    print(f"Full results written to: {RESULTS_CSV}")
    unfixable = len(no_source_no_mbid) - total_tracks_fixed
    print(f"Still unfixable: {unfixable} tracks "
          f"(genuinely not in Last.fm, or need Discogs-side fix)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Test only the first N tracks (for a quick dry run)")
    args = parser.parse_args()
    main(limit=args.limit)
