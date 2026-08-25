"""Phase 8 — final merge into canonical tracks.jsonl.

Reads the deepest available intermediate and merges it into ``tracks.jsonl``.
On re-runs: human-edited fields and higher-confidence mood data are PRESERVED,
everything else is UPDATED. ``enrichment_sources`` is recomputed every run;
``enriched_at`` is bumped only for rows that actually changed, so a no-op regen
doesn't churn every line.

Validates the schema before writing — aborts on invalid rows.

Usage:
    python -m pipeline.update_tracks
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import (
    REPO_ROOT,
    TRACKS_PATH,
    TRACKS_SKELETON_PATH,
    TRACKS_WITH_AUDIO_PATH,
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_WITH_METADATA_PATH,
    TRACKS_WITH_MOODS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.apply_taste_profile import OUTPUT_PATH as TRACKS_WITH_TASTE_PATH
from pipeline.enrich_apple_library import TRACKS_WITH_APPLE_PATH
from pipeline.schema import (
    HUMAN_EDITED_FIELDS,
    compute_canonical_track_id,
    fill_defaults,
    read_jsonl,
    validate_dataset,
    write_jsonl,
)

log = get_logger(__name__)

# Deepest in the chain first — the first path that exists wins, so skipping an
# optional phase doesn't drop the fields an earlier one added.
_INPUT_PRIORITY: list[Path] = [
    TRACKS_WITH_TASTE_PATH,
    TRACKS_WITH_MOODS_PATH,
    REPO_ROOT / "tracks_with_features.jsonl",
    REPO_ROOT / "tracks_with_isrcs.jsonl",
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_WITH_METADATA_PATH,
    TRACKS_WITH_AUDIO_PATH,
    TRACKS_WITH_APPLE_PATH,
    TRACKS_SKELETON_PATH,
]

# Field present ⇒ these sources contributed.
_SOURCE_TRIGGERS: dict[str, list[str]] = {
    "lastfm_tags": ["lastfm_tags"],
    "musicbrainz_id": ["musicbrainz"],
    "discogs_styles": ["discogs"],     # populated in Phase 4b
    "itunes_persistent_id": ["itunes_xml"],
    "apple_music_checked_at": ["itunes_search"],
    "mood_source": ["mood_classifier"],
}

# Phase 4d's genre_backfill.source → its enrichment_sources marker. A row whose
# source is None was examined and yielded nothing, so it earns no marker.
_GENRE_BACKFILL_SOURCES: dict[str, str] = {
    "lastfm_artist": "lastfm_artist_tags",
    "musicbrainz_artist": "musicbrainz_artist",
}


def _pick_input(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    for p in _INPUT_PRIORITY:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"No intermediate JSONL found. Expected one of: "
        f"{', '.join(p.name for p in _INPUT_PRIORITY)}"
    )


def _load_jsonl(path: Path) -> list[dict]:
    return read_jsonl(path)


def _candidate_keys(row: dict, context: str) -> list[tuple[str, str]]:
    """Identity candidates for the merge, strongest first.

    Neither key alone is sufficient — both move for reasons unrelated to the
    track changing:

    - The **name pair** moves whenever normalization improves. #27's feat-credit
      work rewrote it on 183 rows; on names alone the merge saw 183 brand-new
      tracks and dropped human-edited fields that exist only in tracks.jsonl.
    - The **canonical id** moves as the identity chain promotes a row: 5a
      resolving an ISRC re-keys it ``norm:…`` → ``isrc:…``.

    Measured on this library, each alone matched fewer rows than the two
    together. An existing row is claimed at most once, so a promoted id cannot
    pull two source rows onto the same track.
    """
    keys: list[tuple[str, str]] = []

    canonical = row.get("canonical_track_id") or compute_canonical_track_id(row)
    if canonical:
        keys.append(("id", canonical))

    artist = row.get("artist_normalized")
    track = row.get("track_normalized")
    if not artist or not track:
        raise ValueError(
            f"{context} missing artist_normalized or track_normalized"
        )
    keys.append(("name", f"{artist}|{track}"))
    return keys


def _track_key(row: dict, context: str) -> str:
    """The row's own primary key — used to detect duplicates within one file."""
    return _candidate_keys(row, context)[-1][1]


def _index_by_key(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Index existing rows under every candidate key they answer to."""
    index: dict[tuple[str, str], dict] = {}
    seen_primary: set[str] = set()
    for i, row in enumerate(rows, start=1):
        candidates = _candidate_keys(row, f"existing row {i}")
        primary = candidates[-1][1]
        if primary in seen_primary:
            raise ValueError(f"duplicate existing track key {primary!r}")
        seen_primary.add(primary)
        for candidate in candidates:
            # First row wins an ambiguous key; the loser is still reachable by
            # its other candidate.
            index.setdefault(candidate, row)
    return index


def _find_existing(
    row: dict,
    index: dict[tuple[str, str], dict],
    claimed: set[int],
    context: str,
) -> dict | None:
    """Strongest unclaimed match for a source row, or None if it is new."""
    for candidate in _candidate_keys(row, context):
        existing = index.get(candidate)
        if existing is not None and id(existing) not in claimed:
            claimed.add(id(existing))
            return existing
    return None


def _enrichment_sources(row: dict) -> list[str]:
    """Determine which enrichment sources contributed to this row."""
    sources: list[str] = []
    for trigger, src_list in _SOURCE_TRIGGERS.items():
        value = row.get(trigger)
        if value:
            for src in src_list:
                if src not in sources:
                    sources.append(src)

    # audio_features and isrc carry their own provenance, so read the actual
    # source rather than mapping from a fixed trigger.
    af = row.get("audio_features")
    if isinstance(af, dict) and af.get("source") and af["source"] not in sources:
        sources.append(af["source"])
    isrc_source = row.get("isrc_source")
    if isrc_source and isrc_source not in sources:
        sources.append(isrc_source)

    # Phase 4d's two routes are distinct provenance: an artist-level genre from
    # Last.fm's folksonomy is weaker evidence than one from MusicBrainz. Read the
    # recorded source rather than inferring from the raw-tag fields, which are
    # written even when the row recovered nothing.
    backfill = row.get("genre_backfill")
    if isinstance(backfill, dict):
        marker = _GENRE_BACKFILL_SOURCES.get(backfill.get("source"))
        if marker and marker not in sources:
            sources.append(marker)

    return sources


# Fill a gap, never overwrite. These feed compute_canonical_track_id() and 4e's
# clustering, so churn reshuffles which rows merge between runs. A scrobble-derived
# MBID may fill a blank but must not displace a dedicated lookup's answer.
_FILL_ONLY_FIELDS: frozenset[str] = frozenset({"musicbrainz_id", "artist_mbid", "isrc"})

# An explicit null here means "no value", not "this file lacked the field" —
# Phase 6 sets all of them on every row it touches, so null is a verdict.
_AUTHORITATIVE_NULL_FIELDS: frozenset[str] = frozenset(
    {"mood_tags", "mood_source", "mood_confidence", "mood_distance"}
)


def _merge_with_existing(new: dict, existing: dict | None) -> dict:
    """Merge a freshly enriched row into the existing tracks.jsonl row.

    Priority: (1) human-edited fields, (2) higher-confidence mood
    (audit/claude_batch/manual), then (3) new wins unless it is None/empty — which
    keeps enrichment a later phase simply didn't carry forward.
    """
    if existing is None:
        return new

    # Layer new over existing, but only where new carries a non-empty value.
    merged: dict = dict(existing)
    for key, new_value in new.items():
        if new_value is None:
            # A null mood from Phase 6 is a verdict, not a gap — the classifier
            # declined to guess, and that blank must survive. Rule 3 is for fields
            # an intermediate didn't carry, which arrive as an *absent* key.
            if key in _AUTHORITATIVE_NULL_FIELDS:
                merged[key] = None
            continue
        if isinstance(new_value, (list, dict)) and len(new_value) == 0:
            # Empty container from new never clears a populated existing one.
            if merged.get(key):
                continue
        if key in _FILL_ONLY_FIELDS and merged.get(key):
            continue
        merged[key] = new_value

    # Human-edited fields always win over anything regenerated.
    for field in HUMAN_EDITED_FIELDS:
        if existing.get(field) is not None:
            merged[field] = existing[field]

    # "audit" counts as high-quality: it is the owner's own labelling and the
    # classifier's training signal. A fresher centroid pass must never overwrite it.
    existing_source = existing.get("mood_source")
    new_source = new.get("mood_source")
    if existing_source in ("audit", "claude_batch", "manual") and new_source != existing_source:
        merged["mood_tags"] = existing.get("mood_tags") or merged.get("mood_tags")
        merged["mood_source"] = existing_source
        merged["mood_confidence"] = existing.get("mood_confidence")

    # playlists is derived from taste_profile.md by Phase 7, not hand-edited, so the
    # latest output always wins — preserving it strands tracks in sections the
    # markdown no longer has. curation_state is the hand-edited half (HUMAN_EDITED_FIELDS).
    merged["playlists"] = list(new.get("playlists") or [])

    return merged


def update(
    input_path: Path | None = None,
    output_path: Path = TRACKS_PATH,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Merge intermediate → tracks.jsonl. Returns stats dict."""
    configure_logging(run_log_path)
    log.info("=== Phase 8: final merge → tracks.jsonl ===")

    chosen = _pick_input(input_path)
    log.info("Source: %s", chosen)
    log.info("Output: %s", output_path)

    new_rows = _load_jsonl(chosen)
    log.info("Loaded %d source rows", len(new_rows))

    existing_index: dict[tuple[str, str], dict] = {}
    if output_path.exists():
        existing_rows = _load_jsonl(output_path)
        existing_index = _index_by_key(existing_rows)
        log.info("Existing tracks.jsonl has %d rows — merging", len(existing_rows))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    merged_rows: list[dict] = []
    new_count = 0
    updated_count = 0

    seen_new: set[str] = set()
    claimed: set[int] = set()
    for i, row in enumerate(new_rows, start=1):
        context = f"source row {i}"
        key = _track_key(row, context)
        if key in seen_new:
            raise ValueError(f"duplicate source track key {key!r}")
        seen_new.add(key)
        existing = _find_existing(row, existing_index, claimed, context)
        merged = _merge_with_existing(row, existing)
        merged = fill_defaults(merged)
        merged["enrichment_sources"] = _enrichment_sources(merged)
        # Stamp enriched_at only when the row actually changed, so a no-op regen
        # crossing midnight UTC doesn't churn every line of tracks.jsonl.
        prev_stamp = existing.get("enriched_at") if existing else None
        if existing is not None and {**merged, "enriched_at": prev_stamp} == existing:
            merged["enriched_at"] = prev_stamp
        else:
            merged["enriched_at"] = today
        merged_rows.append(merged)
        if existing is None:
            new_count += 1
        else:
            updated_count += 1

    merged_rows.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    validation = validate_dataset(merged_rows)
    if validation["invalid_count"] > 0:
        log.error("Validation failed: %d invalid rows", validation["invalid_count"])
        for idx, errs in list(validation["errors_by_row"].items())[:5]:
            log.error("  row %d: %s", idx, "; ".join(errs))
        raise ValueError(
            f"{validation['invalid_count']} invalid rows — refusing to write tracks.jsonl"
        )

    write_jsonl(merged_rows, output_path)

    log.info(
        "Phase 8 done: %d total (%d new, %d updated) → %s",
        len(merged_rows), new_count, updated_count, output_path,
    )
    return {
        "total": len(merged_rows),
        "new": new_count,
        "updated": updated_count,
    }


if __name__ == "__main__":
    update()
    sys.exit(0)
