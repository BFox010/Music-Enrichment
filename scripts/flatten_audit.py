"""Flatten a TuneMyMusic playlist export into existing_audit.csv.

Input: a TuneMyMusic CSV where each row is one (track, playlist) pair —
the user's v1 mood-categorized playlists exported as a single CSV.

Output: inputs/existing_audit.csv with one row per unique (artist, track)
and a comma-separated mood_tags column. Playlist names get mapped to the
14 canonical mood categories.

Usage:
    python scripts/flatten_audit.py --input "C:/Users/Branden/Downloads/My Spotify Library(1).csv"
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import INPUT_EXISTING_AUDIT, INPUTS_DIR  # noqa: E402

# Map TuneMyMusic playlist names → spec's 14 canonical moods.
# Anything not in this map is logged and skipped.
PLAYLIST_TO_MOOD: dict[str, str] = {
    "Fastlist":       "Fast",
    "Moodylist":      "Moody",
    "Slowlist":       "Slow",
    "Basslist":       "Heavy Bass",
    "Dancelist":      "Dance",
    "Sadlist":        "Sad",
    "Groovelist":     "Groove",
    "Heartbreaklist": "Heartbreak",
    "Darklist":       "Dark",
    "Lovelist":       "Love",
    "Hypelist":       "Hype",
    "Uplist":         "Uplifting",
    "Upliftlist":     "Uplifting",
    "Upliftinglist":  "Uplifting",
    "Happylist":      "Happy",
    "Sunlist":        "Sunny",
    "Sunnylist":      "Sunny",
    # v1 had a "Weird" category; the spec doesn't, so we drop it
    "Weirdlist":      None,
}


def flatten(
    input_csv: Path,
    output_csv: Path = INPUT_EXISTING_AUDIT,
) -> dict[str, int]:
    """Group rows by (artist, track), collect mapped moods, write CSV."""
    if not input_csv.exists():
        raise FileNotFoundError(input_csv)

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    seen_pairs: set[tuple[str, str, str]] = set()
    unknown_playlists: dict[str, int] = {}

    with open(input_csv, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            artist = (row.get("Artist name") or "").strip()
            track = (row.get("Track name") or "").strip()
            playlist = (row.get("Playlist name") or "").strip()
            if not artist or not track or not playlist:
                continue

            mood = PLAYLIST_TO_MOOD.get(playlist)
            if mood is None and playlist not in PLAYLIST_TO_MOOD:
                unknown_playlists[playlist] = unknown_playlists.get(playlist, 0) + 1
                continue
            if mood is None:
                continue  # explicitly skipped (e.g. Weirdlist)

            key = (artist, track)
            seen_key = (artist, track, mood)
            if seen_key in seen_pairs:
                continue
            seen_pairs.add(seen_key)
            grouped[key].append(mood)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["artist", "track", "mood_tags"])
        for (artist, track), moods in sorted(grouped.items()):
            writer.writerow([artist, track, ", ".join(sorted(moods))])

    print(f"Wrote {len(grouped)} unique tracks -> {output_csv}")

    # Distribution by mood
    mood_counts: dict[str, int] = {}
    for moods in grouped.values():
        for m in moods:
            mood_counts[m] = mood_counts.get(m, 0) + 1
    print()
    print("Mood distribution:")
    for mood, n in sorted(mood_counts.items(), key=lambda x: -x[1]):
        print(f"  {n:>5}  {mood}")

    if unknown_playlists:
        print()
        print("Unknown playlist names (skipped):")
        for name, n in unknown_playlists.items():
            print(f"  {n:>5}  {name}")

    return {
        "tracks": len(grouped),
        "moods": len(mood_counts),
        "skipped_unknown": sum(unknown_playlists.values()),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Flatten TuneMyMusic playlists CSV → existing_audit.csv")
    p.add_argument("--input", type=Path, required=True, help="Path to the TuneMyMusic export CSV")
    p.add_argument("--output", type=Path, default=INPUT_EXISTING_AUDIT,
                   help=f"Output path (default: {INPUT_EXISTING_AUDIT})")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    flatten(args.input, args.output)
