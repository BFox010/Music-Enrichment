"""Canonical schema + registry for tracks.jsonl.

Defines:
- Versioned dataclass (TrackV5) describing the canonical Track shape
- A schema registry: version → dataclass
- FIELD_DEFAULTS with stable emit order (_schema_version FIRST, then
  canonical_track_id, then identity, then enrichment groups)
- compute_canonical_track_id() — MBID → ISRC → normalized artist+track → hash
- write_jsonl / read_jsonl — enforce field order on write; ignore (but
  preserve) unknown fields on read
- validators (validate_row, validate_dataset)
- fill_defaults — also populates _schema_version and canonical_track_id

Schema policy (mirrored from README):
- _schema_version is the FIRST field on every JSONL record
- Writers emit fields in stable, documented order
- Readers silently ignore unknown fields (here: preserve, lossless)
- Minor additive fields do NOT bump SCHEMA_VERSION
- Breaking renames/removals DO bump SCHEMA_VERSION + require migration tests
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import SCHEMA_VERSION

# Required identity fields — every track row must have these
REQUIRED_FIELDS: tuple[str, ...] = (
    "artist",
    "track",
    "artist_normalized",
    "track_normalized",
)

# Full field set with default values used by Phase 8 to fill missing fields.
# Order is preserved when writing JSONL for stable git diffs.
# _schema_version MUST stay first; canonical_track_id second.
FIELD_DEFAULTS: dict[str, Any] = {
    # Schema
    "_schema_version": SCHEMA_VERSION,
    # Canonical identity
    "canonical_track_id": "",   # computed by compute_canonical_track_id()
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
    "isrc": None,
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
    "mood_tags": {
        "source_field": "mood_source",
        "high_quality_values": "claude_batch,manual",
    },
}


# ── Versioned dataclasses ────────────────────────────────────────────────


@dataclass
class TrackV5:
    """Canonical Track schema, version 5.

    Field order mirrors FIELD_DEFAULTS. Use to_dict() to emit a JSONL-ready
    dict with stable key order; use from_dict() to parse one (extra fields
    are preserved on the dataclass as `_extras`).
    """

    _schema_version: int = SCHEMA_VERSION
    canonical_track_id: str = ""
    artist: str = ""
    track: str = ""
    artist_normalized: str = ""
    track_normalized: str = ""
    album: str = ""
    release_year: int | None = None
    duration_ms: int | None = None
    explicit: bool | None = None
    spotify_id: str | None = None
    musicbrainz_id: str | None = None
    artist_mbid: str | None = None
    isrc: str | None = None
    apple_music_available: bool | None = None
    apple_music_id: str | None = None
    apple_music_checked_at: str | None = None
    audio_features: dict[str, Any] | None = None
    genres: list[str] = field(default_factory=list)
    lastfm_tags: list[str] = field(default_factory=list)
    discogs_styles: list[str] = field(default_factory=list)
    itunes_genre: str | None = None
    mood_tags: list[str] | None = None
    mood_source: str | None = None
    mood_confidence: str | None = None
    play_count: int = 0
    first_scrobbled: str | None = None
    last_scrobbled: str | None = None
    peak_year: int | None = None
    itunes_play_count: int = 0
    itunes_skip_count: int = 0
    itunes_date_added: str | None = None
    itunes_last_played: str | None = None
    itunes_persistent_id: str | None = None
    itunes_kind: str | None = None
    saturation_tier: str | None = None
    blacklisted: bool = False
    playlists: list[str] = field(default_factory=list)
    curation_state: str | None = None
    rejected_reason: str | None = None
    enriched_at: str | None = None
    enrichment_sources: list[str] = field(default_factory=list)
    # Unknown-but-preserved fields (forward compat). Not emitted as a single
    # blob — to_dict() spreads them at the end of the record.
    _extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "TrackV5":
        known = {f.name for f in fields(cls) if f.name != "_extras"}
        kwargs = {k: v for k, v in row.items() if k in known}
        extras = {k: v for k, v in row.items() if k not in known}
        return cls(**kwargs, _extras=extras)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "_extras":
                continue
            out[f.name] = getattr(self, f.name)
        # Spread unknown-but-preserved fields at the end
        for k, v in self._extras.items():
            if k not in out:
                out[k] = v
        return out


# Schema registry — version → dataclass. Extend additively when bumping.
SCHEMA_REGISTRY: dict[int, type] = {
    5: TrackV5,
}


def get_schema(version: int = SCHEMA_VERSION) -> type:
    """Return the dataclass for `version`. Raises KeyError if unknown."""
    return SCHEMA_REGISTRY[version]


# ── Canonical track identity ─────────────────────────────────────────────


def compute_canonical_track_id(row: dict[str, Any]) -> str:
    """Stable per-track ID using the documented priority chain.

    1. MusicBrainz recording MBID  → "mbid:<id>"
    2. ISRC                         → "isrc:<code>"
    3. Normalized artist + track    → "norm:<artist>|<track>"
    4. Fallback hash                → "hash:<sha1[:16]>"

    Returns "" only if the row has no usable identity fields at all.
    """
    mbid = row.get("musicbrainz_id")
    if mbid:
        return f"mbid:{mbid}"

    isrc = row.get("isrc")
    if isrc:
        return f"isrc:{isrc}"

    artist_n = row.get("artist_normalized") or ""
    track_n = row.get("track_normalized") or ""
    if artist_n and track_n:
        return f"norm:{artist_n}|{track_n}"

    # Fallback: hash whatever identity-shaped fields exist
    raw = "|".join(
        str(row.get(k) or "")
        for k in ("artist", "track", "album", "spotify_id", "apple_music_id")
    )
    if raw.strip("|"):
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"hash:{digest}"
    return ""


# ── Defaults + validation ────────────────────────────────────────────────


def fill_defaults(row: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with all schema fields populated.

    Existing non-None values win. Mutable defaults (lists, dicts) are
    independent per call. Extra (unknown) fields are preserved at the end
    of the output for forward compatibility.

    Also:
    - _schema_version defaults to current SCHEMA_VERSION
    - canonical_track_id is computed if missing or empty
    """
    out: dict[str, Any] = {}
    for key, default in FIELD_DEFAULTS.items():
        if key in row and row[key] is not None:
            out[key] = row[key]
        elif key in row:
            out[key] = row[key] if default is not False else default
            if default is False and row[key] is None:
                out[key] = False
        else:
            if isinstance(default, list):
                out[key] = []
            elif isinstance(default, dict):
                out[key] = {}
            else:
                out[key] = default

    # Compute canonical_track_id if missing/empty
    if not out.get("canonical_track_id"):
        out["canonical_track_id"] = compute_canonical_track_id(out)

    # Preserve any extra fields that aren't in the canonical schema
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


# ── JSONL IO with version + order discipline ─────────────────────────────


def write_jsonl(rows: Iterable[dict[str, Any]], path: Path) -> int:
    """Write rows to JSONL with stable field order.

    Every emitted record has _schema_version as its FIRST key. Other known
    fields follow in FIELD_DEFAULTS order. Unknown fields are appended at
    the end (preserving forward-compat data).

    Returns row count written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            ordered = _order_for_emit(row)
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL. Unknown fields preserved; records without _schema_version load fine."""
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _order_for_emit(row: dict[str, Any]) -> dict[str, Any]:
    """Reorder a row so _schema_version is first, known fields follow defaults order, extras last."""
    out: dict[str, Any] = {}
    # _schema_version stamped first; default to current if absent
    out["_schema_version"] = row.get("_schema_version", SCHEMA_VERSION)
    # Other known fields in FIELD_DEFAULTS order
    for key in FIELD_DEFAULTS:
        if key == "_schema_version":
            continue
        if key in row:
            out[key] = row[key]
    # Extras at the end (forward-compat)
    for key, value in row.items():
        if key not in out:
            out[key] = value
    return out
