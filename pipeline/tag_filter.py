"""Last.fm folksonomy tag noise filtering.

Drops four noise classes, keeping genres, decades, and real descriptors:

  1. Radio stations   — "wsum 91.7 fm madison", "88.5 fm"
  2. Artist-as-tag    — "kanye west" (matched against the library's own artist
                        set, so it adapts per run)
  3. Personal lists   — "my top songs", "my favorites"
  4. Specific years   — "2016". Decades ("90s", "2010s") are KEPT.

Biased toward *under*-blocking: a protected allowlist of genre/mood/reaction
words guarantees real descriptors survive even when they collide with an
artist name.

Used by Phase 4 before writing, and by the cleanup that scrubs written JSONL.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pipeline.normalize import normalize_artist

# Bare 4-digit year. Anchored, so decade forms ("90s", "2010s") survive.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# Station signature: digits immediately before fm/am ("wsum 91.7 fm madison").
# The ≥2-digit requirement keeps song-ish tags like "3am" from matching.
_FREQ_RE = re.compile(r"\b\d{2,4}(?:\.\d+)?\s*(?:fm|am)\b", re.IGNORECASE)

# "my top songs", "my favorites", ...
_MY_RE = re.compile(r"^my\b", re.IGNORECASE)

# NEVER dropped, even when they collide with an artist in the library (the band
# "Muse", the rapper "Future"). Guards the artist-name rule only — the other
# three never hit these words.
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

    if norm in _PROTECTED:
        return False

    if _YEAR_RE.match(low):
        return True
    if _FREQ_RE.search(low):
        return True
    if _MY_RE.match(raw):
        return True
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
