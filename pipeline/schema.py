"""Canonical schema for tracks.jsonl.

Defines:
- The full set of expected fields and their default values
- A simple validator that flags rows missing required identity fields or
  with type mismatches on critical fields

Add fields here when extending the schema, then bump SCHEMA_VERSION in config.
"""

from __future__ import annotations

from typing import Any

# Required identity fields — every track row must have these
REQUIRED_FIELDS: tuple[str, ...] = (
    "artist",
    "track",
    "artist_normalized",
    "track_normalized",
)

# Full field set with default values used by Phase 8 to fill missing fields.
# Order is preserved when writing JSONL for stable git diffs.
FIELD_DEFAULTS: dict[str, Any] = {
    # Identity
    "artist": "",
    "track": "",
    "artist_normalized": "",
    "track_normalized": "",
    "album": "",
    # Release / shape
    "release_year": None,
    "duration_ms": None,
    "explicit": None,
    # External IDs
    "spotify_id": None,
    "musicbrainz_id": None,
    "artist_mbid": None,
    "apple_music_available": None,
    "apple_music_id": None,
    "apple_music_checked_at": None,
    # Audio features (Phase 3c)
    "audio_features": None,  # full block when present, else None
    # Genres / tags
    "genres": [],
    "lastfm_tags": [],
    "discogs_styles": [],
    "itunes_genre": None,
    # Mood (Phase 6)
    "mood_tags": None,
    "mood_source": None,
    "mood_confidence": None,
    # Listening / counts
    "play_count": 0,
    "first_scrobbled": None,
    "last_scrobbled": None,
    "peak_year": None,
    # iTunes XML extras (analytics + cross-reference)
    "itunes_play_count": 0,
    "itunes_skip_count": 0,
    "itunes_date_added": None,
    "itunes_last_played": None,
    "itunes_persistent_id": None,
    "itunes_kind": None,
    # Curation (Phase 7 — derived from taste_profile.md)
    "saturation_tier": None,
    "blacklisted": False,
    "playlists": [],
    "curation_state": None,
    "rejected_reason": None,
    # Provenance
    "enriched_at": None,
    "enrichment_sources": [],
}

# Fields that are human-edited and MUST be preserved across re-runs of Phase 8
HUMAN_EDITED_FIELDS: tuple[str, ...] = (
    "curation_state",
    "rejected_reason",
)

# Fields whose existing values should win over a fresh enrichment pass —
# typically because Phase 6 Claude review marks `mood_source: claude_batch`
# and that should not be overwritten by a centroid pass.
PROTECTED_WHEN_HIGHER_QUALITY: dict[str, dict[str, str]] = {
    # field name → {"source_field": ..., "high_quality_values": [...]}
    "mood_tags": {
        "source_field": "mood_source",
        "high_quality_values": "claude_batch,manual",
    },
}


def fill_defaults(row: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with all schema fields populated; existing values win."""
    out: dict[str, Any] = {}
    for key, default in FIELD_DEFAULTS.items():
        if key in row and row[key] is not None:
            out[key] = row[key]
        elif key in row:  # explicit None present
            out[key] = row[key] if default is not False else default
            # the above keeps None; override only if default is exactly False
            if default is False and row[key] is None:
                out[key] = False
        else:
            # copy mutable defaults to avoid shared lists
            if isinstance(default, list):
                out[key] = []
            elif isinstance(default, dict):
                out[key] = {}
            else:
                out[key] = default

    # Preserve any extra fields that aren't in the canonical schema (forward-compat)
    for key, value in row.items():
        if key not in FIELD_DEFAULTS:
            out[key] = value
    return out


def validate_row(row: dict[str, Any]) -> list[str]:
    """Return a list of error strings for ``row``. Empty list = valid."""
    errors: list[str] = []
    for f in REQUIRED_FIELDS:
        if not row.get(f):
            errors.append(f"missing required field {f!r}")

    if not isinstance(row.get("genres", []), list):
        errors.append("genres must be a list")
    if not isinstance(row.get("lastfm_tags", []), list):
        errors.append("lastfm_tags must be a list")
    if not isinstance(row.get("playlists", []), list):
        errors.append("playlists must be a list")
    if not isinstance(row.get("blacklisted", False), bool):
        errors.append("blacklisted must be a bool")

    pc = row.get("play_count")
    if pc is not None and (not isinstance(pc, int) or pc < 0):
        errors.append("play_count must be a non-negative int")

    return errors


def validate_dataset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a full dataset. Returns {valid_count, invalid_count, errors_by_row}."""
    invalid: dict[int, list[str]] = {}
    for i, row in enumerate(rows):
        errs = validate_row(row)
        if errs:
            invalid[i] = errs
    return {
        "valid_count": len(rows) - len(invalid),
        "invalid_count": len(invalid),
        "errors_by_row": invalid,
    }
