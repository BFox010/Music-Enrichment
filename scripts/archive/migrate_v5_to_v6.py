"""One-shot migration: drop `blacklisted`/`rejected_reason` from tracks.jsonl (#63).

`fill_defaults()` deliberately preserves unknown fields for forward
compatibility, so simply removing the two fields from `FIELD_DEFAULTS` does
NOT remove them from `tracks.jsonl` — every existing row would keep carrying
them, just relocated to the end of the record as unrecognized extras. This
script is the actual removal: it pops both keys off every row and stamps
`_schema_version: 6`.

Run once, from the repo root:
    python -m scripts.archive.migrate_v5_to_v6

Verifies afterward that both fields are gone as *keys*, not merely defaulted,
and that the row count and every `canonical_track_id` are unchanged.
"""

from __future__ import annotations

import sys

from pipeline.config import TRACKS_PATH, SCHEMA_VERSION
from pipeline.schema import read_jsonl, write_jsonl

_REMOVED_FIELDS = ("blacklisted", "rejected_reason")


def migrate(path=TRACKS_PATH) -> dict[str, int]:
    rows = read_jsonl(path)
    before_ids = [r.get("canonical_track_id") for r in rows]
    before_count = len(rows)

    dropped = 0
    for row in rows:
        for field in _REMOVED_FIELDS:
            if field in row:
                del row[field]
                dropped += 1
        row["_schema_version"] = SCHEMA_VERSION

    write_jsonl(rows, path)

    # Re-read to verify the write actually dropped the fields and preserved
    # identity — canonical_track_id churn here would violate CLAUDE.md's
    # "preserved by every phase" invariant.
    after = read_jsonl(path)
    assert len(after) == before_count, (
        f"row count changed: {before_count} -> {len(after)}"
    )
    after_ids = [r.get("canonical_track_id") for r in after]
    assert after_ids == before_ids, "canonical_track_id changed for at least one row"
    for row in after:
        for field in _REMOVED_FIELDS:
            assert field not in row, f"{field!r} still present after migration"

    return {"rows": before_count, "fields_dropped": dropped}


if __name__ == "__main__":
    stats = migrate()
    print(
        f"Migrated {stats['rows']} rows: dropped {stats['fields_dropped']} "
        f"blacklisted/rejected_reason occurrences. _schema_version -> {SCHEMA_VERSION}."
    )
    sys.exit(0)
