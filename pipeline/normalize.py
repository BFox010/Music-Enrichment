"""Artist/track normalization for join-key matching across data sources.

Normalization is intentionally lossy and one-way. The same input always
maps to the same key. Both sides of any join MUST be normalized with these
functions — never compare a raw string to a normalized one.

Search-cleaning functions (clean_track_for_search, clean_artist_for_search,
search_join_key) are a separate layer used for TuneMyMusic CSV export and as a
fallback join in Phase 3c. They strip feat. credits and metadata noise while
preserving original casing — they do NOT replace the identity join keys.
"""

from __future__ import annotations

import re
import unicodedata

# Curly + straight apostrophes get DELETED (don’t → dont, not "don t")
# U+0027 ASCII · U+2018 left quote · U+2019 right quote · U+02BC modifier
_APOSTROPHE_RE = re.compile("['‘’ʼ]")
# Anything not word-char or whitespace → space
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
# feat / ft / featuring / with → "feat"
_FEAT_RE = re.compile(
    r"\b(?:feat\.?|ft\.?|featuring)\b",
    flags=re.IGNORECASE,
)
# Leading "the " on artist names only
_LEADING_THE_RE = re.compile(r"^the\s+", flags=re.IGNORECASE)

# ── Search-cleaning patterns ──────────────────────────────────────────────────
# Full parenthetical feat. block: (feat. X, Y) or [ft. X]
_FEAT_BLOCK_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+[^\)\]]+[\)\]]",
    flags=re.IGNORECASE,
)
# Inline feat. trailing the title: "Song feat. X, Y"
_FEAT_INLINE_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$",
    flags=re.IGNORECASE,
)
# Pure metadata qualifiers — things that identify a release format, not the
# song itself. Remixes are intentionally excluded (they ARE a distinct version).
_NOISE_QUALIFIER_RE = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"\d{4}\s+remaster(?:ed)?"
    r"|remaster(?:ed)?(?:\s+\d{4})?"
    r"|radio\s+edit"
    r"|album\s+version"
    r"|single\s+version"
    r"|original\s+mix"
    r"|clean(?:\s+version)?"
    r"|explicit(?:\s+version)?"
    r"|acoustic(?:\s+version)?"
    r"|instrumental(?:\s+version)?"
    r"|bonus\s+track"
    r"|interlude"
    r"|live[^\)\]]*"
    r"|extended(?:\s+(?:version|mix|edit))?"
    r")\s*[\)\]]",
    flags=re.IGNORECASE,
)
# " - Live" / " - Live at X" dash-style suffix
_LIVE_DASH_RE = re.compile(r"\s+-\s+live\b.*$", flags=re.IGNORECASE)
# Split artist string at first collaborative separator to get primary artist
_ARTIST_COLLAB_RE = re.compile(
    r"\s*(?:&\s+|\s+feat\.?\s+|\s+ft\.?\s+|\s+featuring\s+)",
    flags=re.IGNORECASE,
)


def _fold(text: str) -> str:
    """Lowercase + NFKD-decompose + strip combining marks (diacritics)."""
    decomposed = unicodedata.normalize("NFKD", text)
    no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return no_marks.lower()


def _strip_punct(text: str) -> str:
    """Drop apostrophes, replace remaining punctuation with space, collapse whitespace."""
    text = _APOSTROPHE_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for use as a join key.

    Steps: fold → collapse leading/inner whitespace → collapse "feat" variants
    → drop leading "the " → strip punct.
    """
    if not artist:
        return ""
    text = _fold(artist)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _FEAT_RE.sub("feat", text)
    text = _LEADING_THE_RE.sub("", text)
    return _strip_punct(text)


def normalize_track(track: str) -> str:
    """Normalize a track title for use as a join key.

    Like `normalize_artist` but does NOT strip a leading "the " (track titles
    legitimately start with "The …").
    """
    if not track:
        return ""
    text = _fold(track)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _FEAT_RE.sub("feat", text)
    return _strip_punct(text)


def join_key(artist: str, track: str) -> str:
    """Composite key used to dedupe scrobbles into unique tracks."""
    return f"{normalize_artist(artist)}|{normalize_track(track)}"


# ── Search-cleaning public API ────────────────────────────────────────────────

def clean_track_for_search(track: str) -> str:
    """Strip feat. credits and metadata noise from a track title, preserving case.

    Keeps remix/version identifiers that distinguish tracks. Strips:
    ``(feat. X)``, inline ``ft. X``, ``(Remastered)``, ``(Radio Edit)``,
    ``(Live …)``, ``(Extended)``.  Remixes are intentionally kept.
    """
    text = _FEAT_BLOCK_RE.sub("", track)
    text = _FEAT_INLINE_RE.sub("", text)
    text = _NOISE_QUALIFIER_RE.sub("", text)
    text = _LIVE_DASH_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_artist_for_search(artist: str) -> str:
    """Return the primary artist name only, dropping collaborative credits.

    ``"Drake & 21 Savage"`` → ``"Drake"``
    ``"A$AP Mob feat. A$AP Rocky"`` → ``"A$AP Mob"``
    """
    return _ARTIST_COLLAB_RE.split(artist, maxsplit=1)[0].strip()


def search_join_key(artist: str, track: str) -> str:
    """Fallback composite join key with feat./noise stripped.

    Used as a secondary lookup in Phase 3c when the primary (full) join key
    misses due to feat. credit or metadata formatting differences between
    Last.fm scrobble names and Spotify canonical names.
    """
    return (
        f"{normalize_artist(clean_artist_for_search(artist))}"
        f"|{normalize_track(clean_track_for_search(track))}"
    )
