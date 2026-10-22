"""Build web/prototypes/data.json — the compact dataset the design prototypes read.

The prototypes are throwaway explorations of a visual direction (see
web/DESIGN.md), so they get a small pre-aggregated file rather than the full
library the dashboard loads. Artwork comes straight out of the API caches: the
iTunes and Deezer responses already carry cover and artist-image URLs that no
pipeline phase extracts yet.

    python scripts/build_prototype_data.py
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "prototypes" / "data.json"
TOP_N = 12


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_cache(name: str) -> dict:
    path = ROOT / ".cache" / f"{name}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _artwork_indexes():
    """(track key → cover url, loose artist name → artist image url)."""
    covers: dict[tuple[str, str], str] = {}
    artist_images: dict[str, str] = {}

    for resp in _load_cache("deezer").values():
        if not isinstance(resp, dict):
            continue
        for hit in resp.get("data") or []:
            if not isinstance(hit, dict):
                continue
            artist = hit.get("artist") or {}
            album = hit.get("album") or {}
            a_key = _loose(artist.get("name", ""))
            if a_key and artist.get("picture_xl") and "/artist//" not in artist["picture_xl"]:
                artist_images.setdefault(a_key, artist["picture_xl"])
            if album.get("cover_xl"):
                covers.setdefault((a_key, _loose(hit.get("title_short") or hit.get("title", ""))), album["cover_xl"])

    for resp in _load_cache("apple_music").values():
        if not isinstance(resp, dict):
            continue
        for hit in resp.get("results") or []:
            url = hit.get("artworkUrl100") if isinstance(hit, dict) else None
            if not url:
                continue
            # mzstatic serves any square size from the same path; 100px is too
            # small to sit behind a hero.
            url = url.replace("/100x100bb.", "/600x600bb.")
            for artist_part in re.split(r"\s*(?:&|,|feat\.?|with)\s*", hit.get("artistName", "")):
                covers.setdefault((_loose(artist_part), _loose(hit.get("trackName", ""))), url)

    return covers, artist_images


def _cover_for(covers, artist: str, track: str) -> str | None:
    a = _loose(artist)
    t = _loose(re.sub(r"\s*[\(\[].*?[\)\]]", "", track))
    return covers.get((a, _loose(track))) or covers.get((a, t))


def main() -> None:
    tracks = _read_jsonl(ROOT / "tracks.jsonl")
    scrobbles = _read_jsonl(ROOT / "scrobbles.jsonl")
    covers, artist_images = _artwork_indexes()

    by_hour = [0] * 24
    by_dow = [0] * 7  # Monday first, matching scrobbles.jsonl's day_of_week
    by_season = Counter()
    by_year = Counter()
    hour_by_year: dict[int, list[int]] = defaultdict(lambda: [0] * 24)
    artist_plays = Counter()
    track_plays = Counter()
    for s in scrobbles:
        by_hour[s["hour"]] += 1
        by_dow[s["day_of_week"]] += 1
        by_season[s["season"]] += 1
        by_year[s["year"]] += 1
        hour_by_year[s["year"]][s["hour"]] += 1
        artist_plays[s["artist"]] += 1
        track_plays[(s["artist"], s["track"])] += 1

    tracks_per_artist = Counter(t["artist"] for t in tracks)
    top_track_of = {}
    for (artist, track), n in track_plays.most_common():
        top_track_of.setdefault(artist, track)

    top_artists = []
    for artist, plays in artist_plays.most_common(TOP_N):
        top_artists.append({
            "name": artist,
            "plays": plays,
            "tracks": tracks_per_artist.get(artist, 0),
            "image": artist_images.get(_loose(artist)),
            "cover": _cover_for(covers, artist, top_track_of.get(artist, "")),
        })

    top_tracks = []
    for (artist, track), plays in track_plays.most_common(TOP_N):
        top_tracks.append({"track": track, "artist": artist, "plays": plays,
                           "cover": _cover_for(covers, artist, track)})

    genres = Counter()
    moods = Counter()
    for t in tracks:
        weight = t.get("play_count") or 1
        for g in (t.get("genres") or [])[:1]:
            genres[g] += weight
        for m in t.get("mood_tags") or []:
            moods[m] += weight

    stamps = sorted(s["scrobbled_at"] for s in scrobbles)
    data = {
        "meta": {
            "tracks": len(tracks),
            "scrobbles": len(scrobbles),
            "artists": len({t["artist"] for t in tracks}),
            "first": stamps[0] if stamps else None,
            "last": stamps[-1] if stamps else None,
        },
        "byHour": by_hour,
        "byDow": by_dow,
        "bySeason": {k: by_season.get(k, 0) for k in ("winter", "spring", "summer", "fall")},
        "byYear": dict(sorted(by_year.items())),
        "hourByYear": {y: hour_by_year[y] for y in sorted(hour_by_year)},
        "topArtists": top_artists,
        "topTracks": top_tracks,
        "topGenres": genres.most_common(10),
        "topMoods": moods.most_common(8),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with_art = sum(1 for t in top_tracks if t["cover"]) + sum(1 for a in top_artists if a["image"])
    print(f"wrote {OUT.relative_to(ROOT)}: {len(scrobbles)} scrobbles, "
          f"artwork on {with_art}/{len(top_tracks) + len(top_artists)} top items")


if __name__ == "__main__":
    main()
