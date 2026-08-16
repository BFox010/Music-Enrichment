"""Phase 8 — final merge into canonical tracks.jsonl.

Reads the latest available intermediate (preferring the deepest in the
enrichment chain) and writes/updates ``tracks.jsonl``. On re-runs:
  - Human-edited fields (curation_state, rejected_reason) are PRESERVED
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
    fill_defaults,
    read_jsonl,
    validate_dataset,
    write_jsonl,
)

log = get_logger(__name__)

# Preferred input order — deepest in the chain first.
# update_tracks picks the first one that exists.
_INPUT_PRIORITY: list[Path] = [
    TRACKS_WITH_TASTE_PATH,
    TRACKS_WITH_MOODS_PATH,
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
    "audio_features": ["exportify"],   # populated in Phase 3c
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


def _track_key(row: dict, context: str) -> str:
    artist = row.get("artist_normalized")
    track = row.get("track_normalized")
    if not artist or not track:
        raise ValueError(
            f"{context} missing artist_normalized or track_normalized"
        )
    return f"{artist}|{track}"


def _index_by_key(rows: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for i, row in enumerate(rows, start=1):
        key = _track_key(row, f"existing row {i}")
        if key in index:
            raise ValueError(f"duplicate existing track key {key!r}")
        index[key] = row
    return index


def _enrichment_sources(row: dict) -> list[str]:
    """Determine which enrichment sources contributed to this row."""
    sources: list[str] = []
    for trigger, src_list in _SOURCE_TRIGGERS.items():
        value = row.get(trigger)
        if value:  # non-empty/non-None
            for src in src_list:
                if src not in sources:
                    sources.append(src)
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

    # Start with existing as base, layer new on top — but only fill from new
    # when new actually has a non-empty value.
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

    # Preserve human-edited fields (override anything new has)
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

    # Playlist semantics: playlists are derived from taste_profile.md (Phase 7),
    # not human-edited directly. Always trust the latest Phase 7 output — otherwise
    # tracks get stuck in playlist sections that no longer exist in the markdown.
    # Only curation_state is in HUMAN_EDITED_FIELDS; preserving playlists here
    # was double-counting that preservation. See δ-1 in TODO.
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

    existing_index: dict[str, dict] = {}
    if output_path.exists():
        existing_rows = _load_jsonl(output_path)
        existing_index = _index_by_key(existing_rows)
        log.info("Existing tracks.jsonl has %d rows — merging", len(existing_rows))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    merged_rows: list[dict] = []
    new_count = 0
    updated_count = 0

    seen_new: set[str] = set()
    for i, row in enumerate(new_rows, start=1):
        key = _track_key(row, f"source row {i}")
        if key in seen_new:
            raise ValueError(f"duplicate source track key {key!r}")
        seen_new.add(key)
        existing = existing_index.get(key)
        merged = _merge_with_existing(row, existing)
        merged = fill_defaults(merged)
        merged["enrichment_sources"] = _enrichment_sources(merged)
        # Refresh enriched_at only when the row actually changed. A no-op regen,
        # or one that only touched other rows, keeps each row's existing stamp —
        # so tracks.jsonl diffs show real changes instead of churning all 2,730
        # lines whenever a rerun crosses midnight UTC.
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

    # Sort for stable output
    merged_rows.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    # Validate
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
