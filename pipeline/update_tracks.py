"""Phase 8 — final merge into canonical tracks.jsonl.

Reads the latest available intermediate (preferring the deepest in the
enrichment chain) and writes/updates ``tracks.jsonl``. On re-runs:
  - Human-edited fields (curation_state) are PRESERVED
  - Higher-confidence mood data is PRESERVED over fresher centroid passes
  - All other enrichment fields are UPDATED from the new pass
  - enrichment_sources is recomputed each run; enriched_at is bumped only for
    rows whose data actually changed (so reruns don't churn every line)

Schema is validated before write — aborts if invalid rows are present.

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

# Map presence of fields → which sources contributed
_SOURCE_TRIGGERS: dict[str, list[str]] = {
    "lastfm_tags": ["lastfm_tags"],
    "musicbrainz_id": ["musicbrainz"],
    "discogs_styles": ["discogs"],     # populated in Phase 4b
    "itunes_persistent_id": ["itunes_xml"],
    "apple_music_checked_at": ["itunes_search"],
    "mood_source": ["mood_classifier"],
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

    Neither key alone is sufficient, because both move for reasons that have
    nothing to do with the track changing:

    - The **normalized name pair** moves whenever normalization improves. #27's
      feat-credit work rewrote it on 183 rows; keyed on names alone the merge
      saw 183 brand-new tracks and dropped the human-edited fields that live
      only in tracks.jsonl — 54 rows lost curation_state, 49 lost mood labels.
    - The **canonical id** moves as the identity chain promotes a row. It is not
      carried through the intermediates (Phase 4e stamps it on the few rows it
      clusters; fill_defaults computes the rest at write time), so it is derived
      fresh each run from whatever identity fields exist *then*. Phase 5a
      resolving an ISRC re-keys a row from ``norm:…`` to ``isrc:…`` — measured
      against this library, keying on it alone matched 2608 rows where the name
      pair matched 3105.

    Trying both matched 3172 — more than either alone. An existing row is
    claimed at most once, so a promoted id can't pull two source rows onto the
    same track.
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
            # First row wins an ambiguous key rather than silently replacing the
            # earlier one; the loser is still reachable by its other candidate.
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
        if value:  # non-empty/non-None
            for src in src_list:
                if src not in sources:
                    sources.append(src)

    # audio_features and isrc each carry their own provenance (Exportify vs.
    # ReccoBeats; MusicBrainz vs. Deezer vs. Spotify Search) rather than a
    # fixed trigger — read the actual source instead of assuming one.
    af = row.get("audio_features")
    if isinstance(af, dict) and af.get("source") and af["source"] not in sources:
        sources.append(af["source"])
    isrc_source = row.get("isrc_source")
    if isrc_source and isrc_source not in sources:
        sources.append(isrc_source)

    return sources


# Identity fields: fill a gap, never overwrite. These feed
# compute_canonical_track_id() and Phase 4e's clustering, so churn here would
# reshuffle which rows merge from one run to the next. Scrobble-derived MBIDs
# are welcome where a track has none and must not displace one that a
# dedicated enrichment lookup already established.
_FILL_ONLY_FIELDS: frozenset[str] = frozenset({"musicbrainz_id", "artist_mbid", "isrc"})

# Fields where an explicit null from the incoming row means "there is no value"
# rather than "this file didn't carry the field". Phase 6 sets all of these on
# every row it processes, so a null here is a deliberate verdict.
_AUTHORITATIVE_NULL_FIELDS: frozenset[str] = frozenset(
    {"mood_tags", "mood_source", "mood_confidence", "mood_distance"}
)


def _merge_with_existing(new: dict, existing: dict | None) -> dict:
    """Merge a freshly enriched row with the existing tracks.jsonl row.

    Rules in priority order:
    1. Human-edited fields: existing always wins if set
    2. Higher-confidence mood data: existing wins (claude_batch/manual)
    3. Locked/approved playlist memberships: existing wins
    4. Any other field: NEW value wins UNLESS new is None/empty,
       in which case existing wins. This preserves enrichment from
       earlier intermediate files when a later phase didn't carry
       those fields forward (e.g. mood phase reading from a file
       that lacked Apple Music availability).
    """
    if existing is None:
        return new

    # Layer new over existing, but only where new carries a non-empty value.
    merged: dict = dict(existing)
    for key, new_value in new.items():
        if new_value is None:
            # A null mood from Phase 6 is a verdict, not a gap: the classifier
            # declines to guess moods the audio features cannot predict, and
            # that blank has to survive the merge. Rule 4 exists for fields a
            # later intermediate simply didn't carry, which shows up as the key
            # being absent rather than explicitly null.
            if key in _AUTHORITATIVE_NULL_FIELDS:
                merged[key] = None
            continue
        if isinstance(new_value, (list, dict)) and len(new_value) == 0:
            # Empty list/dict from new → keep existing if existing has content
            if merged.get(key):
                continue
        if key in _FILL_ONLY_FIELDS and merged.get(key):
            continue
        merged[key] = new_value

    # Human-edited fields always win over anything regenerated.
    for field in HUMAN_EDITED_FIELDS:
        if existing.get(field) is not None:
            merged[field] = existing[field]

    # Preserve high-quality mood data. "audit" is included because those are
    # the owner's own judgements — the training signal the whole classifier is
    # built on. A fresher centroid pass must never overwrite one.
    existing_source = existing.get("mood_source")
    new_source = new.get("mood_source")
    if existing_source in ("audit", "claude_batch", "manual") and new_source != existing_source:
        merged["mood_tags"] = existing.get("mood_tags") or merged.get("mood_tags")
        merged["mood_source"] = existing_source
        merged["mood_confidence"] = existing.get("mood_confidence")

    # playlists comes from taste_profile.md via Phase 7, not from hand-editing, so
    # always take the latest Phase 7 output — preserving it here would strand tracks
    # in sections the markdown no longer has. curation_state is the human-edited half
    # and is covered by HUMAN_EDITED_FIELDS.
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
