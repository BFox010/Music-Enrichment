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
    "curation_state", "playlists",
    "first_scrobbled", "last_scrobbled",
]

# The only raw-track fields the client reads (dashboard.jsx::normalizeTrack plus
# the chart/explorer components). /tracks.min.jsonl projects to this set to shrink
# the first-paint payload; tracks.jsonl and /api/* still carry every field.
MIN_TRACK_FIELDS: tuple[str, ...] = (
    "artist", "track", "album", "release_year",
    "genres", "lastfm_tags", "discogs_styles",
    "mood_tags", "mood_source", "mood_confidence",
    "play_count", "peak_year",
    "first_scrobbled", "last_scrobbled",
    "apple_music_available", "enrichment_sources", "saturation_tier", "playlists",
    # join keys for the client-side scrobble cross-join — identity_aliases lets
    # the browser join a scrobble logged under a historical artist credit the
    # same way app.metrics._track_index() does server-side (F-03).
    "artist_normalized", "track_normalized", "identity_aliases",
)

# Charted client-side. The rest (tempo, loudness, …) stay server-side in
# /api/audio-features.
_MIN_AUDIO_FEATURES: tuple[str, ...] = (
    "energy", "valence", "danceability", "acousticness",
)


def project_min_track(row: dict) -> dict:
    """Project a full track row down to the fields the browser renders.

    ``musicbrainz_id``/``spotify_id`` collapse to booleans because the UI only
    checks presence (``normalizeTrack`` does ``!!(raw.musicbrainz_id …)``); this
    drops the long id strings from the payload.
    """
    out: dict[str, Any] = {k: row[k] for k in MIN_TRACK_FIELDS if k in row}
    af = row.get("audio_features")
    if isinstance(af, dict):
        out["audio_features"] = {
            k: af[k] for k in _MIN_AUDIO_FEATURES if k in af
        }
    out["musicbrainz_id"] = bool(row.get("musicbrainz_id"))
    out["spotify_id"] = bool(row.get("spotify_id"))
    return out


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
