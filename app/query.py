"""Filterable, paginated track table for the dashboard.

Column selection and dot-notation flattening adapted from
``scripts/make_view.py`` (``COLUMNS`` / ``_flatten``), but returns structured
dicts (not CSV strings) for the JSON API.
"""

from __future__ import annotations

from typing import Any

from app.data import get_tracks

DISPLAY_COLUMNS: list[str] = [
    "artist", "track", "album", "play_count", "release_year",
    "genres", "mood_tags", "mood_confidence", "saturation_tier",
    "audio_features.energy", "audio_features.valence",
    "audio_features.danceability", "audio_features.tempo",
    "curation_state", "playlists", "blacklisted",
    "first_scrobbled", "last_scrobbled",
]


def _get_nested(row: dict, key: str) -> Any:
    """Retrieve a possibly-nested value using dot notation."""
    if "." not in key:
        return row.get(key)
    head, _, tail = key.partition(".")
    sub = row.get(head)
    if isinstance(sub, dict):
        return sub.get(tail)
    return None


def _flatten_row(row: dict) -> dict:
    return {col: _get_nested(row, col) for col in DISPLAY_COLUMNS}


def query_tracks(
    *,
    genre: str | None = None,
    mood: str | None = None,
    year: int | None = None,
    artist: str | None = None,
    min_energy: float | None = None,
    max_energy: float | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    rows = list(get_tracks())

    if genre:
        needle = genre.lower()
        rows = [r for r in rows if any(needle in g.lower() for g in (r.get("genres") or []))]

    if mood:
        needle = mood.lower()
        rows = [r for r in rows if any(needle in m.lower() for m in (r.get("mood_tags") or []))]

    if year is not None:
        rows = [r for r in rows if r.get("release_year") == year]

    if artist:
        needle = artist.lower()
        rows = [r for r in rows if needle in (r.get("artist") or "").lower()]

    if min_energy is not None or max_energy is not None:
        def _energy(r: dict) -> float | None:
            af = r.get("audio_features")
            if isinstance(af, dict):
                v = af.get("energy")
                return float(v) if v is not None else None
            return None

        if min_energy is not None:
            rows = [r for r in rows if (_energy(r) or -1.0) >= min_energy]
        if max_energy is not None:
            rows = [r for r in rows if (_energy(r) or 2.0) <= max_energy]

    total = len(rows)
    start = (page - 1) * per_page
    page_rows = rows[start : start + per_page]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "tracks": [_flatten_row(r) for r in page_rows],
    }
