"""Canonical schema + registry for tracks.jsonl.

- ``TrackV5`` (legacy) / ``TrackV6`` (current) dataclasses, indexed by SCHEMA_REGISTRY
- ``FIELD_DEFAULTS`` — the full field set, in stable emit order
- ``compute_canonical_track_id`` — MBID → ISRC → normalized artist+track → hash
- ``read_jsonl`` / ``write_jsonl`` — unknown fields preserved losslessly on read,
  field order enforced on write
- ``fill_defaults``, ``validate_row``, ``validate_dataset``

Policy: ``_schema_version`` is first on every record; readers ignore unknown
fields; additive fields do NOT bump SCHEMA_VERSION, breaking renames/removals do
and require migration tests.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

from pipeline.config import SCHEMA_VERSION

# Every track row must have these.
REQUIRED_FIELDS: tuple[str, ...] = (
    "artist",
    "track",
    "artist_normalized",
    "track_normalized",
)

# Defaults Phase 8 fills gaps from. Write order is this order, for stable git
# diffs — _schema_version MUST stay first, canonical_track_id second.
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
    # Which resolver found the isrc (Phase 5a). None for an Exportify-sourced
    # ISRC, which predates the field.
    "isrc_source": None,
    "isrc_retrieved_at": None,
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
    # Phase 4d artist-level backfill: the raw evidence, then its provenance.
    # None (absent source) on a row 4d examined and found nothing for; the whole
    # block absent on a row 4d never saw, since it only visits genre gaps.
    "lastfm_artist_tags": [],
    "musicbrainz_genres": [],
    "genre_backfill": None,
    # Global popularity, free-riding on the track.getInfo call made for tags/MBIDs.
    "lastfm_listeners": None,
    "lastfm_playcount": None,
    # Mood (Phase 6)
    "mood_tags": None,
    "mood_source": None,
    "mood_confidence": None,
    # Nearest-centroid distance, so the UI can show fit. Centroid rows only.
    "mood_distance": None,
    # Every (artist_normalized, track_normalized) Phase 4e folded into this row.
    # scrobbles.jsonl is never rewritten; aggregation resolves old credits here.
    "identity_aliases": [],
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
    "playlists": [],
    "curation_state": None,
    # Provenance
    "enriched_at": None,
    "enrichment_sources": [],
}

# Human-edited: MUST survive every Phase 8 re-run.
HUMAN_EDITED_FIELDS: tuple[str, ...] = (
    "curation_state",
)


# ── Versioned dataclasses ──


@dataclass
class TrackV5:
    """SUPERSEDED — parses pre-migration v5 rows only. New code uses TrackV6.

    ``_schema_version`` is hardcoded to 5, not SCHEMA_VERSION, which now means 6.
    """

    _schema_version: int = 5
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
    lastfm_listeners: int | None = None
    lastfm_playcount: int | None = None
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
    # Forward-compat. to_dict() spreads these at the end, not as a nested blob.
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
        for k, v in self._extras.items():
            if k not in out:
                out[k] = v
        return out


@dataclass
class TrackV6:
    """Current canonical Track schema. Field order mirrors FIELD_DEFAULTS.

    v5 minus ``blacklisted``/``rejected_reason`` (#63), plus
    ``isrc_source``/``isrc_retrieved_at`` (#37).
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
    isrc_source: str | None = None
    isrc_retrieved_at: str | None = None
    apple_music_available: bool | None = None
    apple_music_id: str | None = None
    apple_music_checked_at: str | None = None
    audio_features: dict[str, Any] | None = None
    genres: list[str] = field(default_factory=list)
    lastfm_tags: list[str] = field(default_factory=list)
    discogs_styles: list[str] = field(default_factory=list)
    itunes_genre: str | None = None
    lastfm_listeners: int | None = None
    lastfm_playcount: int | None = None
    mood_tags: list[str] | None = None
    mood_source: str | None = None
    mood_confidence: str | None = None
    mood_distance: float | None = None
    identity_aliases: list[list[str]] = field(default_factory=list)
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
    playlists: list[str] = field(default_factory=list)
    curation_state: str | None = None
    enriched_at: str | None = None
    enrichment_sources: list[str] = field(default_factory=list)
    # Forward-compat. to_dict() spreads these at the end, not as a nested blob.
    _extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "TrackV6":
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
        for k, v in self._extras.items():
            if k not in out:
                out[k] = v
        return out


# Schema registry — version → dataclass. Extend additively when bumping.
SCHEMA_REGISTRY: dict[int, type] = {
    5: TrackV5,
    6: TrackV6,
}


def get_schema(version: int = SCHEMA_VERSION) -> type:
    """Return the dataclass for `version`. Raises KeyError if unknown."""
    return SCHEMA_REGISTRY[version]


# ── Canonical track identity ──


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

    raw = "|".join(
        str(row.get(k) or "")
        for k in ("artist", "track", "album", "spotify_id", "apple_music_id")
    )
    if raw.strip("|"):
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"hash:{digest}"
    return ""


# ── Defaults + validation ──


def fill_defaults(row: dict[str, Any]) -> dict[str, Any]:
    """New dict with every schema field populated. Existing non-None values win;
    mutable defaults are fresh per call; unknown fields survive at the end.

    Also stamps _schema_version and computes canonical_track_id when empty.
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

    if not out.get("canonical_track_id"):
        out["canonical_track_id"] = compute_canonical_track_id(out)

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


# ── JSONL IO with version + order discipline ──


# Attempts to find an unused temp-file name before giving up. A collision needs
# two writers to draw the same 12 hex digits in the same directory.
_TMP_NAME_ATTEMPTS = 32


def _open_new_temp(directory: Path, prefix: str) -> tuple[int, Path]:
    """Create and open a uniquely named temp file in ``directory``.

    ``tempfile.mkstemp`` would be the obvious call, but it hardcodes mode 0600
    and ``os.replace`` carries the temp file's mode onto the destination — so
    every pipeline output would silently drop from 0644 to owner-only, breaking
    any setup where the pipeline and the dashboard run as different users.
    Opening with 0o666 instead lets the kernel apply the process umask, which is
    exactly what a plain ``open(path, "w")`` does for a file that doesn't exist.
    """
    for _ in range(_TMP_NAME_ATTEMPTS):
        candidate = directory / f"{prefix}{os.urandom(6).hex()}.tmp"
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        return fd, candidate
    raise FileExistsError(f"could not create a temp file in {directory}")


@contextlib.contextmanager
def atomic_open(path: Path, encoding: str = "utf-8", newline: str = "\n") -> Iterator[TextIO]:
    """Open a unique temp file in ``path``'s directory for writing; on clean exit,
    fsync it and ``os.replace()`` it onto ``path`` so a crash, disk-full condition,
    or exception mid-write can never leave ``path`` truncated or partially written.
    The temp file is removed if the block raises before the replace.

    Permissions match what rewriting ``path`` in place would have produced: an
    existing file keeps its own mode, a new one gets the umask default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = _open_new_temp(path.parent, f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as fh:
            try:
                existing_mode = stat.S_IMODE(os.stat(path).st_mode)
            except OSError:
                pass  # Nothing to inherit — the umask default above stands.
            else:
                os.fchmod(fh.fileno(), existing_mode)
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_jsonl(rows: Iterable[dict[str, Any]], path: Path) -> int:
    """Write JSONL in stable field order: _schema_version first, then FIELD_DEFAULTS
    order, then unknown fields. Returns rows written. Writes atomically — see
    ``atomic_open`` — so an interrupted or failed write never corrupts ``path``.
    """
    n = 0
    with atomic_open(path) as fh:
        for row in rows:
            ordered = _order_for_emit(row)
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL. Unknown fields preserved; records without _schema_version load fine."""
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSONL row: {e}") from e
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object row")
            rows.append(row)
    return rows


def _order_for_emit(row: dict[str, Any]) -> dict[str, Any]:
    """Reorder a row so _schema_version is first, known fields follow defaults order, extras last."""
    out: dict[str, Any] = {}
    out["_schema_version"] = row.get("_schema_version", SCHEMA_VERSION)
    for key in FIELD_DEFAULTS:
        if key == "_schema_version":
            continue
        if key in row:
            out[key] = row[key]
    for key, value in row.items():
        if key not in out:
            out[key] = value
    return out
