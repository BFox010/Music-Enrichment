"""Phase 8 — final merge into canonical tracks.jsonl.

Reads the latest available intermediate (preferring the deepest in the
enrichment chain) and writes/updates ``tracks.jsonl``. On re-runs:
  - Human-edited fields (curation_state, rejected_reason) are PRESERVED
  - Higher-confidence mood data is PRESERVED over fresher centroid passes
  - All other enrichment fields are UPDATED from the new pass
  - enriched_at + enrichment_sources are refreshed each run

Schema is validated before write — aborts if invalid rows are present.

Usage:
    python -m pipeline.update_tracks
"""

from __future__ import annotations

import json
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
    validate_dataset,
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
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _index_by_key(rows: list[dict]) -> dict[str, dict]:
    return {f"{r['artist_normalized']}|{r['track_normalized']}": r for r in rows}


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
            continue
        if isinstance(new_value, (list, dict)) and len(new_value) == 0:
            # Empty list/dict from new → keep existing if existing has content
            if merged.get(key):
                continue
        merged[key] = new_value

    # Preserve human-edited fields (override anything new has)
    for field in HUMAN_EDITED_FIELDS:
        if existing.get(field) is not None:
            merged[field] = existing[field]

    # Preserve high-quality mood data
    existing_source = existing.get("mood_source")
    new_source = new.get("mood_source")
    if existing_source in ("claude_batch", "manual") and new_source != existing_source:
        merged["mood_tags"] = existing.get("mood_tags") or merged.get("mood_tags")
        merged["mood_source"] = existing_source
        merged["mood_confidence"] = existing.get("mood_confidence")

    # Playlist semantics: preserve when user has locked/approved them,
    # but explicitly clear when curation_state is None (don't keep stale memberships).
    if existing.get("curation_state") in ("locked", "approved") and existing.get("playlists"):
        merged["playlists"] = existing["playlists"]
    elif existing.get("curation_state") is None:
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

    for row in new_rows:
        key = f"{row['artist_normalized']}|{row['track_normalized']}"
        existing = existing_index.get(key)
        merged = _merge_with_existing(row, existing)
        merged = fill_defaults(merged)
        merged["enriched_at"] = today
        merged["enrichment_sources"] = _enrichment_sources(merged)
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in merged_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

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
