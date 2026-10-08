"""Artist/track normalization for join-key matching across data sources.

Normalization is intentionally lossy and one-way. The same input always
maps to the same key. Both sides of any join MUST be normalized with these
functions — never compare a raw string to a normalized one.
"""

from __future__ import annotations

import re
import unicodedata

# Curly + straight apostrophes get DELETED (don't → dont, not "don t")
_APOSTROPHE_RE = re.compile(r"[\'‘’ʼ]")
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
# "&" → " and " on artist names only, so "X & Y" and "X and Y" join.
_AMPERSAND_RE = re.compile(r"\s*&\s*")


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


def _symbol_only_fallback(raw: str) -> str:
    """Key for a name that is nothing but punctuation or symbols ("!!!", "?").

    Stripping punctuation empties it, and Phase 8 refuses a row with an empty
    normalized name — so one such scrobble, which ingest keeps forever, failed
    every later run at Phase 8 (#106). Keep the folded symbols instead: the key
    stays stable and distinct, and names with any word character never get here,
    so no existing key moves.
    """
    return _WHITESPACE_RE.sub(" ", _fold(raw)).strip()


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for use as a join key.

    Steps: fold → collapse leading/inner whitespace → collapse "&" into "and"
    → collapse "feat" variants → drop leading "the " → strip punct.
    """
    if not artist:
        return ""
    text = _fold(artist)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _AMPERSAND_RE.sub(" and ", text)
    text = _FEAT_RE.sub("feat", text)
    text = _LEADING_THE_RE.sub("", text)
    return _strip_punct(text) or _symbol_only_fallback(artist)


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
    return _strip_punct(text) or _symbol_only_fallback(track)


def join_key(artist: str, track: str) -> str:
    """Composite key used to dedupe scrobbles into unique tracks."""
    return f"{normalize_artist(artist)}|{normalize_track(track)}"
