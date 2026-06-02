"""Last.fm folksonomy tag noise filtering.

Last.fm community tags carry a lot of noise that pollutes genre/mood signal.
This module drops four specific noise classes the owner flagged, while
preserving genres, decades, and genuine descriptive/reaction tags:

  1. Radio-station tags     — e.g. "wsum 91.7 fm madison", "88.5 fm".
  2. Artist-name-as-tag      — e.g. "kanye west", "drake" (matched against the
                               library's own artist set so it adapts per run).
  3. Personal-collection tags— e.g. "my top songs", "my favorites".
  4. Specific-year tags      — e.g. "2016", "2022". Decades ("90s", "2010s",
                               "00s") are intentionally KEPT.

Design bias is toward *under*-blocking: a small protected allowlist of
genre/mood/reaction words guarantees real descriptors are never dropped even
if they happen to coincide with an artist name.

Used by Phase 4 (``enrich_metadata``) to filter before writing, and by the
one-off cleanup that scrubs already-written JSONL files.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pipeline.normalize import normalize_artist

# Bare 4-digit year, 1900–2099 → block. Decade forms ("90s", "2010s", "00s")
# do not match this anchored pattern and therefore survive.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# A radio frequency embedded anywhere in the tag, e.g. "wsum 91.7 fm madison",
# "88.5 fm", "1010 am". The digits-immediately-before-fm/am combo is the
# station signature; requiring ≥2 leading digits keeps song-ish tags like
# "3am" from matching.
_FREQ_RE = re.compile(r"\b\d{2,4}(?:\.\d+)?\s*(?:fm|am)\b", re.IGNORECASE)

# Personal-collection tags: "my top songs", "my favorites", "my favourites".
_MY_RE = re.compile(r"^my\b", re.IGNORECASE)

# Genre / mood / reaction words that must NEVER be dropped, even if they
# coincide with an artist name in the library (e.g. the band "Muse", the
# rapper "Future"). This only guards the artist-name rule — the year/radio/
# "my" rules never hit these words anyway.
_PROTECTED_RAW = {
    # 14 mood categories
    "fast", "moody", "slow", "heavy bass", "dance", "sad", "groove",
    "heartbreak", "dark", "love", "hype", "uplifting", "happy", "sunny",
    # core genres + common descriptors
    "rap", "rock", "pop", "hip hop", "hip-hop", "alternative", "indie",
    "rnb", "r&b", "trap", "electronic", "soul", "funk", "house", "disco",
    "jazz", "metal", "folk", "punk", "emo", "blues", "country", "reggae",
    "techno", "ambient", "soundtrack", "chill", "mellow", "smooth",
    "downtempo", "edm", "acoustic", "instrumental", "experimental",
    "shoegaze", "grunge", "future",
    # reaction tags the owner wants to keep
    "beautiful", "amazing", "party", "upbeat", "energetic", "dreamy",
    "summer", "sexy", "cool", "fire",
}
_PROTECTED = frozenset(normalize_artist(t) for t in _PROTECTED_RAW)


def is_noise_tag(tag: object, artist_block: frozenset[str] = frozenset()) -> bool:
    """Return True if ``tag`` is a noise tag that should be dropped.

    ``artist_block`` is a set of *normalized* artist names (see
    :func:`build_artist_block`). Pass an empty set to skip the artist-name rule.
    """
    if not isinstance(tag, str):
        return True
    raw = tag.strip()
    if not raw:
        return True
    low = raw.lower()
    norm = normalize_artist(raw)

    # Protected genre/mood/reaction words are always kept.
    if norm in _PROTECTED:
        return False

    # 1. Specific year (decades survive — they don't match _YEAR_RE).
    if _YEAR_RE.match(low):
        return True
    # 2. Radio-station frequency.
    if _FREQ_RE.search(low):
        return True
    # 3. Personal-collection ("my …") tags.
    if _MY_RE.match(raw):
        return True
    # 4. Artist-name-as-tag.
    if norm and norm in artist_block:
        return True
    return False


def filter_tags(
    tags: Iterable[object] | None,
    artist_block: frozenset[str] = frozenset(),
) -> list[str]:
    """Return ``tags`` with noise tags removed, preserving order and originals."""
    if not tags:
        return []
    return [t for t in tags if not is_noise_tag(t, artist_block)]


def build_artist_block(
    tracks: Iterable[dict],
    *,
    min_len: int = 2,
) -> frozenset[str]:
    """Build the normalized artist-name set used to detect artist-as-tag.

    Reads each track's ``artist_normalized`` (falling back to normalizing
    ``artist``). Drops protected words and names shorter than ``min_len`` so a
    one-character or genre-like artist name can't trigger over-blocking.
    """
    block: set[str] = set()
    for t in tracks:
        norm = t.get("artist_normalized") or normalize_artist(t.get("artist", "") or "")
        if norm and len(norm) >= min_len and norm not in _PROTECTED:
            block.add(norm)
    return frozenset(block)
