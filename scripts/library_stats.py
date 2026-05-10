"""Quick analytics summary of the current tracks.jsonl + scrobbles.jsonl.

Prints library-level stats: top artists, top tags, year distribution, mood
distribution, iTunes coverage, time-of-day listening patterns. Useful as a
"how's the data looking" pulse-check between pipeline runs.

Usage:
    python scripts/library_stats.py
    python scripts/library_stats.py --top 30
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import SCROBBLES_PATH, TRACKS_PATH  # noqa: E402


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _bar(n: int, max_n: int, width: int = 40) -> str:
    if max_n == 0:
        return ""
    return "#" * int(n / max_n * width)


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f" {title}")
    print("=" * 70)


def main(top: int = 20) -> None:
    tracks = _load_jsonl(TRACKS_PATH)
    scrobbles = _load_jsonl(SCROBBLES_PATH)

    if not tracks:
        print(f"No tracks found at {TRACKS_PATH}")
        return

    _print_header(f"LIBRARY: {len(tracks)} unique tracks  /  {len(scrobbles)} scrobbles")
    if scrobbles:
        years = Counter(s["year"] for s in scrobbles)
        first = min(years.keys())
        last = max(years.keys())
        print(f"  Scrobble range: {first}-{last}")

    # ── Coverage ─────────────────────────────────────────────────────────
    _print_header("Coverage")
    fields = [
        ("Last.fm tags", "lastfm_tags"),
        ("MusicBrainz ID", "musicbrainz_id"),
        ("Spotify ID", "spotify_id"),
        ("Audio features", "audio_features"),
        ("Apple Music checked", "apple_music_checked_at"),
        ("Apple Music available", "apple_music_available"),
        ("iTunes match", "itunes_persistent_id"),
        ("Mood tags", "mood_tags"),
        ("Saturation tier", "saturation_tier"),
    ]
    for label, field in fields:
        n = sum(1 for t in tracks if t.get(field))
        pct = n / len(tracks) * 100
        print(f"  {label:25s} {n:>6}  ({pct:>4.1f}%)  {_bar(n, len(tracks), 30)}")

    # ── Top artists by play count ────────────────────────────────────────
    _print_header(f"Top {top} artists by total plays")
    artist_plays: Counter[str] = Counter()
    for t in tracks:
        artist_plays[t["artist"]] += int(t.get("play_count") or 0)
    max_plays = max(artist_plays.values()) if artist_plays else 0
    for artist, plays in artist_plays.most_common(top):
        print(f"  {plays:>6}  {_bar(plays, max_plays, 20)}  {artist}")

    # ── Top tracks by play count ─────────────────────────────────────────
    _print_header(f"Top {top} tracks by play count")
    by_plays = sorted(tracks, key=lambda t: -int(t.get("play_count") or 0))[:top]
    max_plays = int(by_plays[0].get("play_count") or 0) if by_plays else 0
    for t in by_plays:
        plays = int(t.get("play_count") or 0)
        print(f"  {plays:>4}  {_bar(plays, max_plays, 20)}  "
              f"{t['artist'][:30]:30s}  {t['track'][:40]}")

    # ── Top Last.fm tags ─────────────────────────────────────────────────
    _print_header(f"Top {top} Last.fm tags")
    tag_counts: Counter[str] = Counter()
    for t in tracks:
        for tag in t.get("lastfm_tags") or []:
            tag_counts[tag] += 1
    max_n = max(tag_counts.values()) if tag_counts else 0
    for tag, n in tag_counts.most_common(top):
        print(f"  {n:>5}  {_bar(n, max_n, 20)}  {tag}")

    # ── iTunes genres ────────────────────────────────────────────────────
    _print_header("iTunes genres (122 matched tracks)")
    itunes_genres: Counter[str] = Counter(
        t["itunes_genre"] for t in tracks if t.get("itunes_genre")
    )
    if itunes_genres:
        max_n = max(itunes_genres.values())
        for genre, n in itunes_genres.most_common(top):
            print(f"  {n:>4}  {_bar(n, max_n, 20)}  {genre}")
    else:
        print("  (no iTunes-genre tags yet)")

    # ── Year distribution (release year) ─────────────────────────────────
    _print_header("Release year distribution")
    decades: Counter[str] = Counter()
    for t in tracks:
        y = t.get("release_year")
        if y:
            decade = f"{(y // 10) * 10}s"
            decades[decade] += 1
    if decades:
        max_n = max(decades.values())
        for decade in sorted(decades.keys()):
            n = decades[decade]
            print(f"  {decade}  {n:>4}  {_bar(n, max_n, 30)}")
    else:
        print("  (no release_year data yet — comes from iTunes / Exportify)")

    # ── Listening-time patterns from scrobbles ───────────────────────────
    if scrobbles:
        _print_header("Listening pattern by hour of day (UTC)")
        by_hour: Counter[int] = Counter(s["hour"] for s in scrobbles)
        max_n = max(by_hour.values())
        for h in range(24):
            n = by_hour.get(h, 0)
            print(f"  {h:>2}h  {n:>5}  {_bar(n, max_n, 40)}")

        _print_header("Listening pattern by day of week")
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        by_dow: Counter[int] = Counter(s["day_of_week"] for s in scrobbles)
        max_n = max(by_dow.values())
        for i, day in enumerate(days):
            n = by_dow.get(i, 0)
            print(f"  {day}  {n:>5}  {_bar(n, max_n, 40)}")

        _print_header("Listening pattern by season")
        by_season: Counter[str] = Counter(s["season"] for s in scrobbles)
        max_n = max(by_season.values())
        for season in ("winter", "spring", "summer", "fall"):
            n = by_season.get(season, 0)
            print(f"  {season:8s} {n:>5}  {_bar(n, max_n, 40)}")

    # ── Mood distribution (when available) ───────────────────────────────
    mood_counts: Counter[str] = Counter()
    for t in tracks:
        for m in t.get("mood_tags") or []:
            mood_counts[m] += 1
    if mood_counts:
        _print_header("Mood distribution")
        max_n = max(mood_counts.values())
        for mood, n in mood_counts.most_common():
            print(f"  {n:>5}  {_bar(n, max_n, 25)}  {mood}")

    # ── iTunes vs Last.fm play counts (top 10) ───────────────────────────
    itunes_overlap = [t for t in tracks
                      if t.get("itunes_play_count") and t.get("play_count")]
    if itunes_overlap:
        _print_header("iTunes vs Last.fm play count delta (top 10 differences)")
        for t in sorted(itunes_overlap,
                        key=lambda t: -abs(t["itunes_play_count"] - t["play_count"]))[:10]:
            delta = t["itunes_play_count"] - t["play_count"]
            print(f"  iTunes:{t['itunes_play_count']:>3}  Last.fm:{t['play_count']:>3}  "
                  f"Δ{delta:+}  {t['artist'][:25]:25s} - {t['track'][:35]}")

    print()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Library analytics summary.")
    p.add_argument("--top", type=int, default=20)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    main(top=args.top)
