"""Phase 4c — Genre derivation from existing tag data.

Derives canonical genre labels for every track by mapping three already-present
sources (in priority order):

  1. ``itunes_genre``   — exact but rare; authoritative wherever it exists
  2. ``discogs_styles`` — release-level styles, very genre-specific
  3. ``lastfm_tags``    — community tags, broadest coverage, loosest

Maps each source against GENRE_TAG_MAP to produce a de-duplicated list of
canonical genres (e.g. ``["Hip-Hop / Rap", "Electronic"]``).  Tracks with none
of the three sources get ``genres: []`` — a subsequent Claude batch can fill
those if desired.

No API calls.  Runs in seconds.

Usage:
    python -m pipeline.derive_genres
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pipeline.config import (
    REPO_ROOT,
    TRACKS_WITH_DISCOGS_PATH,
    TRACKS_WITH_GENRES_PATH,
    configure_logging,
    get_logger,
)
from pipeline.schema import atomic_open

log = get_logger(__name__)

# ── Canonical genre labels ──
HIP_HOP      = "Hip-Hop / Rap"
RNB_SOUL     = "R&B / Soul"
ELECTRONIC   = "Electronic"
INDIE_ALT    = "Indie / Alternative"
ROCK         = "Rock"
POP          = "Pop"
JAZZ         = "Jazz"
COUNTRY_FOLK = "Country / Folk"
METAL        = "Metal"
EXPERIMENTAL = "Experimental"

GENRE_CATEGORIES: tuple[str, ...] = (
    HIP_HOP, RNB_SOUL, ELECTRONIC, INDIE_ALT, ROCK,
    POP, JAZZ, COUNTRY_FOLK, METAL, EXPERIMENTAL,
)

# ── Tag → genre mapping ──
# Keys are lowercase. A tag can map to multiple genres (e.g. "jazz rap" → both).
# More specific entries take precedence when ordering matters, but since we
# collect all matches the only risk is over-tagging — kept conservative.
GENRE_TAG_MAP: dict[str, list[str]] = {
    # Hip-Hop / Rap
    "rap":                  [HIP_HOP],
    "hip-hop":              [HIP_HOP],
    "hip hop":              [HIP_HOP],
    "trap":                 [HIP_HOP],
    "pop rap":              [HIP_HOP, POP],
    "cloud rap":            [HIP_HOP],
    "jazz rap":             [HIP_HOP, JAZZ],
    "jazzy hip-hop":        [HIP_HOP, JAZZ],
    "boom bap":             [HIP_HOP],
    "conscious hip hop":    [HIP_HOP],
    "conscious":            [HIP_HOP],
    "west coast hip hop":   [HIP_HOP],
    "gangsta rap":          [HIP_HOP],
    "gangsta":              [HIP_HOP],
    "emo rap":              [HIP_HOP],
    "hardcore hip hop":     [HIP_HOP],
    "hardcore hip-hop":     [HIP_HOP],
    "southern hip hop":     [HIP_HOP],
    "crunk":                [HIP_HOP],
    "drill":                [HIP_HOP],
    "grime":                [HIP_HOP],
    "abstract":             [HIP_HOP],
    # Regional and underground hip-hop, the densest family in this library and
    # absent from the map entirely. Every key below was taken from a census of
    # unmapped tag occurrences on the committed data, not from imagination —
    # trailing counts are that census (#71).
    "underground hip hop":  [HIP_HOP],                  # 170
    "east coast hip hop":   [HIP_HOP],                  # 58
    "east coast rap":       [HIP_HOP],                  # 46
    "alternative hip hop":  [HIP_HOP],                  # 44
    "dirty south":          [HIP_HOP],                  # 53
    "southern rap":         [HIP_HOP],                  # 38
    "experimental hip hop": [HIP_HOP, EXPERIMENTAL],    # 56
    "memphis rap":          [HIP_HOP],                  # 32
    "underground rap":      [HIP_HOP],                  # 27
    "trap rap":             [HIP_HOP],                  # 23
    "alternative rap":      [HIP_HOP],                  # 17
    "west coast rap":       [HIP_HOP],                  # 11
    "thug rap":             [HIP_HOP],                  # 11
    "horrorcore":           [HIP_HOP],                  # 41
    "phonk":                [HIP_HOP, ELECTRONIC],      # 34
    "trillwave":            [HIP_HOP],                  # 14
    "hiphop":               [HIP_HOP],                  # 11 — no separator to fold

    # R&B / Soul
    "rnb":                  [RNB_SOUL],
    "r&b":                  [RNB_SOUL],
    "rnb/swing":            [RNB_SOUL],
    "soul":                 [RNB_SOUL],
    "neo-soul":             [RNB_SOUL],
    "neo soul":             [RNB_SOUL],
    "neosoul":              [RNB_SOUL],
    "krnb":                 [RNB_SOUL],
    "k-r&b":                [RNB_SOUL],
    "alternative rnb":      [RNB_SOUL],
    "alternative r&b":      [RNB_SOUL],
    "contemporary r&b":     [RNB_SOUL],
    "psychedelic soul":     [RNB_SOUL],
    "funk":                 [RNB_SOUL],
    "gospel":               [RNB_SOUL],
    "motown":               [RNB_SOUL],
    "rhythm & blues":       [RNB_SOUL],                 # 12

    # Electronic
    "electronic":           [ELECTRONIC],
    "synthpop":             [ELECTRONIC],
    "synth-pop":            [ELECTRONIC],
    "electropop":           [ELECTRONIC],
    "electro":              [ELECTRONIC],
    "dance":                [ELECTRONIC],
    "house":                [ELECTRONIC],
    "techno":               [ELECTRONIC],
    "tech house":           [ELECTRONIC],
    "indietronica":         [ELECTRONIC, INDIE_ALT],
    "electronica":          [ELECTRONIC],
    "trip hop":             [ELECTRONIC],
    "downtempo":            [ELECTRONIC],
    "leftfield":            [ELECTRONIC],
    "ambient":              [ELECTRONIC],
    "idm":                  [ELECTRONIC],
    "drum and bass":        [ELECTRONIC],
    "dubstep":              [ELECTRONIC],
    "trance":               [ELECTRONIC],
    "hard trance":          [ELECTRONIC],
    "tech-trance":          [ELECTRONIC],
    "tech trance":          [ELECTRONIC],
    "psytrance":            [ELECTRONIC],
    "progressive trance":   [ELECTRONIC],
    "edm":                  [ELECTRONIC],
    "dance-pop":            [ELECTRONIC, POP],
    "disco":                [ELECTRONIC, RNB_SOUL],
    "deep house":           [ELECTRONIC],               # 29
    "uk garage":            [ELECTRONIC],               # 23
    "industrial":           [ELECTRONIC, EXPERIMENTAL], # 22
    "electro house":        [ELECTRONIC],               # 20
    "chillwave":            [ELECTRONIC],               # 19
    "progressive house":    [ELECTRONIC],               # 16
    "drum n bass":          [ELECTRONIC],               # 15
    "breakbeat":            [ELECTRONIC],               # 12
    "alternative dance":    [ELECTRONIC],               # 11

    # Indie / Alternative
    "indie":                [INDIE_ALT],
    "indie rock":           [INDIE_ALT, ROCK],
    "indie pop":            [INDIE_ALT, POP],
    "alternative":          [INDIE_ALT],
    "alternative rock":     [INDIE_ALT, ROCK],
    "art rock":             [INDIE_ALT, ROCK],
    "bedroom pop":          [INDIE_ALT, POP],
    "dream pop":            [INDIE_ALT, POP],
    "lo-fi":                [INDIE_ALT],
    "shoegaze":             [INDIE_ALT],
    "post-rock":            [INDIE_ALT, ROCK],
    "alt-pop":              [INDIE_ALT, POP],
    "neo-psychedelia":      [INDIE_ALT, ROCK],          # 53
    "indie folk":           [INDIE_ALT, COUNTRY_FOLK],  # 16

    # Rock
    "rock":                 [ROCK],
    "psychedelic rock":     [ROCK],
    "psychedelic":          [ROCK],
    "classic rock":         [ROCK],
    "hard rock":            [ROCK],
    "post-hardcore":        [ROCK],
    "hardcore":             [ROCK],
    "punk":                 [ROCK],
    "pop punk":             [ROCK, POP],
    "folk rock":            [ROCK, COUNTRY_FOLK],
    "prog rock":            [ROCK],
    "progressive rock":     [ROCK],
    "space rock":           [ROCK],
    "garage rock":          [ROCK],
    "grunge":               [ROCK],
    "new wave":             [ROCK, ELECTRONIC],
    "post-punk":            [ROCK],
    "emo":                  [ROCK],
    "southern rock":        [ROCK],
    "country rock":         [ROCK, COUNTRY_FOLK],
    "art pop":              [POP, INDIE_ALT],
    "psychedelic pop":      [POP, ROCK],
    "soft rock":            [ROCK],                     # 50
    "math rock":            [ROCK, INDIE_ALT],          # 48
    "punk rock":            [ROCK],                     # 22
    "emocore":              [ROCK],                     # 17
    "blues rock":           [ROCK],                     # 13

    # Pop
    "pop":                  [POP],
    "pop rock":             [POP, ROCK],
    "k-pop":                [POP],
    "kpop":                 [POP],
    "j-pop":                [POP],
    "jpop":                 [POP],
    "city pop":             [POP],
    "sophisti-pop":         [POP],                      # 12
    "alternative pop":      [POP, INDIE_ALT],           # 11
    "power pop":            [POP, ROCK],                # 10

    # Jazz
    "jazz":                 [JAZZ],
    "soul-jazz":            [JAZZ, RNB_SOUL],
    "bebop":                [JAZZ],
    "smooth jazz":          [JAZZ],

    # Country / Folk
    "country":              [COUNTRY_FOLK],
    "folk":                 [COUNTRY_FOLK],
    "bluegrass":            [COUNTRY_FOLK],
    "americana":            [COUNTRY_FOLK],
    "singer-songwriter":    [COUNTRY_FOLK],
    "acoustic":             [COUNTRY_FOLK],

    # Metal
    "metal":                [METAL],
    "heavy metal":          [METAL],
    "metalcore":            [METAL],
    "death metal":          [METAL],
    "black metal":          [METAL],
    "nu metal":             [METAL],
    "thrash metal":         [METAL],
    "screamo":              [METAL],
    "deathcore":            [METAL],                    # 29
    "alternative metal":    [METAL],                    # 14
    "progressive metal":    [METAL],                    # 12

    # Experimental
    "experimental":         [EXPERIMENTAL],
    "avantgarde":           [EXPERIMENTAL],
    "avant-garde":          [EXPERIMENTAL],
    "noise":                [EXPERIMENTAL],
    "abstract hip-hop":     [HIP_HOP, EXPERIMENTAL],
}

# iTunes genre string → canonical genres (iTunes uses title-case broad labels)
ITUNES_GENRE_MAP: dict[str, list[str]] = {
    "hip-hop":          [HIP_HOP],
    "hip-hop/rap":      [HIP_HOP],
    "r&b/soul":         [RNB_SOUL],
    "electronic":       [ELECTRONIC],
    "dance":            [ELECTRONIC],
    "alternative":      [INDIE_ALT],
    "rock":             [ROCK],
    "pop":              [POP],
    "jazz":             [JAZZ],
    "country":          [COUNTRY_FOLK],
    "folk":             [COUNTRY_FOLK],
    "bluegrass":        [COUNTRY_FOLK],
    "metal":            [METAL],
    "classical":        [],   # not in our taxonomy
    "world":            [],
    "reggae":           [],
    "latin":            [],
}


def normalize_tag_key(tag: str) -> str:
    """Fold a tag to its separator-insensitive lookup key.

    Last.fm and Discogs disagree about punctuation for the same genre —
    "trip-hop" vs "trip hop", "southern hip-hop" vs "southern hip hop" — so an
    exact dict lookup missed whichever spelling the map didn't happen to list.
    """
    return re.sub(r"[^a-z0-9]+", " ", tag.lower()).strip()


# Built once at import. Every colliding pair in GENRE_TAG_MAP carries identical
# values today, and test_no_normalized_key_collisions_lose_a_mapping keeps it
# that way — otherwise folding would silently drop one side.
_NORMALIZED_GENRE_TAG_MAP: dict[str, list[str]] = {}
for _key, _genres in GENRE_TAG_MAP.items():
    _NORMALIZED_GENRE_TAG_MAP.setdefault(normalize_tag_key(_key), _genres)


def _genres_from_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        for genre in _NORMALIZED_GENRE_TAG_MAP.get(normalize_tag_key(tag), []):
            if genre not in seen:
                seen.add(genre)
                result.append(genre)
    return result


def _genres_from_itunes(itunes_genre: str | None) -> list[str]:
    if not itunes_genre:
        return []
    return ITUNES_GENRE_MAP.get(itunes_genre.lower(), [])


def derive_genres_for_track(track: dict) -> list[str]:
    """Return a de-duplicated list of canonical genres for a single track."""
    seen: set[str] = set()
    result: list[str] = []

    def _add(genres: list[str]) -> None:
        for g in genres:
            if g not in seen:
                seen.add(g)
                result.append(g)

    # Priority 1: iTunes genre (most authoritative when present)
    _add(_genres_from_itunes(track.get("itunes_genre")))

    # Priority 2: Discogs styles (release-level, very genre-specific)
    _add(_genres_from_tags(track.get("discogs_styles") or []))

    # Priority 3: Last.fm tags (community, broader coverage)
    _add(_genres_from_tags(track.get("lastfm_tags") or []))

    return result


def derive(
    input_path: Path = TRACKS_WITH_DISCOGS_PATH,
    output_path: Path = TRACKS_WITH_GENRES_PATH,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    configure_logging(run_log_path)
    log.info("=== Phase 4c: Genre derivation ===")
    log.info("Input : %s", input_path)
    log.info("Output: %s", output_path)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tracks: list[dict] = []
    with open(input_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))

    stats = {"total": len(tracks), "with_genres": 0, "no_sources": 0}

    for track in tracks:
        genres = derive_genres_for_track(track)
        track["genres"] = genres
        if genres:
            stats["with_genres"] += 1
        else:
            stats["no_sources"] += 1

    with atomic_open(output_path) as fh:
        for row in tracks:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    pct = stats["with_genres"] / stats["total"] * 100 if stats["total"] else 0
    log.info(
        "Phase 4c done: %d/%d with genres (%.1f%%) — %d no source data",
        stats["with_genres"], stats["total"], pct, stats["no_sources"],
    )
    log.info("Wrote → %s", output_path)
    return stats


if __name__ == "__main__":
    stats = derive()
    sys.exit(0 if stats["with_genres"] > 0 else 1)
