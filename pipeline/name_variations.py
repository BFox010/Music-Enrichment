"""Artist/track name variations for Last.fm lookup retries.

Last.fm indexes literal artist/title strings, so long ``(feat. …)`` credits and
``A & B`` multi-artist strings miss even when the track is well tagged under a
simpler name. scripts/test_match_variations.py measured which normalisations
actually recover an MBID or tags across the unmatched set:

    strip_feat 45   first_artist 17   strip_parens 11   (clean_artist $→S: 0)

Hence the cascade, ordered by first-hit yield:

    original → strip_feat → strip_parens → first_artist → first_artist+strip_feat

``clean_artist`` ($→S) is deliberately omitted — ``autocorrect=1`` already
resolves it, and it recovered nothing.

Shared by the pipeline and the diagnostic so the rules can't drift apart.
"""

from __future__ import annotations

import re


def strip_feat(track: str) -> str:
    """'1 Train (feat. Kendrick Lamar, ...)' → '1 Train'.

    Strips a ``(feat. …)`` / ``[feat. …]`` annotation and everything after it.
    """
    return re.sub(r"\s*[\(\[]feat\.?.*", "", track, flags=re.IGNORECASE).strip()


def strip_parens(track: str) -> str:
    """Remove ALL parenthetical/bracketed annotations from a track title.

    'Guilty Conscience (Tame Impala Remix)' → 'Guilty Conscience'.
    """
    t = re.sub(r"\s*\([^)]*\)", "", track)
    t = re.sub(r"\s*\[[^\]]*\]", "", t)
    return t.strip()


def first_artist(artist: str) -> str:
    """Return the primary artist from a multi-artist credit.

    '070 Shake & Tame Impala' → '070 Shake'
    'JAY-Z & Kanye West'      → 'JAY-Z'
    'A$AP Rocky, Tyler, ...'   → 'A$AP Rocky'

    Splits on ' & ', ' x '/' X ' (before a capitalised name or $), or ',';
    Last.fm indexes collaborations under the primary artist only.
    """
    parts = re.split(r"\s+&\s+|\s+[Xx]\s+(?=[A-Z$])|,\s*", artist)
    return parts[0].strip() if parts else artist


def lookup_variations(artist: str, track: str) -> list[tuple[str, str, str]]:
    """Return ``(label, artist, track)`` query variations to try, in order.

    The first entry is always ``original``. Subsequent entries apply the
    recovery rules measured to actually yield usable data, most productive
    first. Variations that collapse to an earlier (artist, track) pair are
    dropped, so a track with no ``feat.``/parens/multi-artist credit yields
    just ``original`` — no wasted API calls.
    """
    clean_track_feat = strip_feat(track)
    clean_track_parens = strip_parens(track)
    first_art = first_artist(artist)

    candidates = [
        ("original", artist, track),
        ("strip_feat", artist, clean_track_feat),
        ("strip_parens", artist, clean_track_parens),
        ("first_artist", first_art, track),
        ("first_artist+strip_feat", first_art, clean_track_feat),
    ]

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for label, a, t in candidates:
        key = (a.casefold(), t.casefold())
        if key not in seen:
            seen.add(key)
            unique.append((label, a, t))
    return unique
