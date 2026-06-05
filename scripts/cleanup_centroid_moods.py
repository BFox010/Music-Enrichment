"""One-time migration: scrub stale centroid mood tags from tracks.jsonl.

Adding a mood to CENTROID_SUPPRESSED_MOODS (or a gate to CENTROID_MOOD_GATES)
only affects FUTURE Phase 6 runs — centroid tags already written to tracks.jsonl
persist until the phase is re-run. Re-running Phase 6 needs the owner's audit +
Exportify CSVs, which aren't present in every environment. This script applies
the CURRENT suppression + gate policy to already-emitted centroid tags directly,
in place, using only the audio_features already baked into tracks.jsonl.

It reuses ``apply_centroid_policy`` from pipeline.classify_moods (the exact same
filter the live classifier uses), so a later authoritative Phase 6 re-run on the
owner's machine reproduces this result and this script becomes a no-op.

Only rows with ``mood_source == "centroid"`` are touched; audit / claude_batch /
manual rows are never modified. Default is a DRY RUN — pass --apply to write.

    python scripts/cleanup_centroid_moods.py            # dry-run diff
    python scripts/cleanup_centroid_moods.py --apply    # write tracks.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.classify_moods import (  # noqa: E402
    CENTROID_MOOD_GATES,
    CENTROID_SUPPRESSED_MOODS,
    apply_centroid_policy,
)
from pipeline.config import MOOD_CATEGORIES, TRACKS_PATH, get_logger  # noqa: E402
from pipeline.schema import (  # noqa: E402
    fill_defaults,
    read_jsonl,
    validate_dataset,
    write_jsonl,
)

log = get_logger(__name__)


def clean_centroid_moods(
    tracks: list[dict],
    *,
    suppressed_moods=CENTROID_SUPPRESSED_MOODS,
    gates=CENTROID_MOOD_GATES,
) -> tuple[list[dict], dict]:
    """Apply suppression + gates to centroid rows in place. Returns (rows, stats).

    A row whose centroid tags are entirely removed has its mood triple
    (mood_tags / mood_source / mood_confidence) cleared to None — matching how
    classify() writes a no-match row, so the result stays schema-consistent and
    a future Phase 6 pass can re-fill it cleanly.
    """
    stats = {
        "rows_total": len(tracks),
        "centroid_rows": 0,
        "rows_changed": 0,
        "rows_cleared": 0,
        "tags_removed": 0,
        "removed_by_mood": Counter(),
    }
    for row in tracks:
        if row.get("mood_source") != "centroid":
            continue
        stats["centroid_rows"] += 1
        old = list(row.get("mood_tags") or [])
        features = row.get("audio_features") or {}
        new = apply_centroid_policy(
            old, features, suppressed_moods=suppressed_moods, gates=gates
        )
        if new == old:
            continue
        stats["rows_changed"] += 1
        for m in old:
            if m not in new:
                stats["removed_by_mood"][m] += 1
                stats["tags_removed"] += 1
        if new:
            row["mood_tags"] = new  # source/confidence stay centroid/medium
        else:
            row["mood_tags"] = None
            row["mood_source"] = None
            row["mood_confidence"] = None
            stats["rows_cleared"] += 1
    return tracks, stats


def _mood_census(tracks: list[dict]) -> Counter:
    c: Counter = Counter()
    for t in tracks:
        for m in (t.get("mood_tags") or []):
            c[m] += 1
    return c


def _source_census(tracks: list[dict]) -> Counter:
    return Counter(t.get("mood_source") or "(none)" for t in tracks)


def _print_diff(before_mood: Counter, after_mood: Counter,
                before_src: Counter, after_src: Counter, stats: dict) -> None:
    print("=" * 66)
    print(" CENTROID MOOD CLEANUP")
    print("=" * 66)
    print(f"  rows total            : {stats['rows_total']}")
    print(f"  centroid rows          : {stats['centroid_rows']}")
    print(f"  rows changed           : {stats['rows_changed']}")
    print(f"  rows fully cleared     : {stats['rows_cleared']}  (→ mood_source None)")
    print(f"  centroid tags removed  : {stats['tags_removed']}")
    print()
    print("  removed centroid tags by mood:")
    for mood, n in stats["removed_by_mood"].most_common():
        print(f"    {mood:12s}  -{n}")
    print()
    print("  mood_source census  (before → after):")
    for src in sorted(set(before_src) | set(after_src), key=lambda s: -before_src.get(s, 0)):
        print(f"    {src:12s}  {before_src.get(src, 0):>5d} → {after_src.get(src, 0):>5d}")
    print()
    print("  per-mood tag totals  (before → after):")
    for mood in MOOD_CATEGORIES:
        b, a = before_mood.get(mood, 0), after_mood.get(mood, 0)
        flag = "  *" if a != b else ""
        print(f"    {mood:12s}  {b:>5d} → {a:>5d}{flag}")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    ap.add_argument("--apply", action="store_true",
                    help="write changes to tracks.jsonl (default: dry-run)")
    args = ap.parse_args(argv)

    rows = read_jsonl(args.tracks)
    if not rows:
        print(f"No tracks found at {args.tracks}", file=sys.stderr)
        return 1

    before_mood = _mood_census(rows)
    before_src = _source_census(rows)

    cleaned, stats = clean_centroid_moods(rows)
    cleaned = [fill_defaults(r) for r in cleaned]
    cleaned.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    validation = validate_dataset(cleaned)
    if validation["invalid_count"] > 0:
        log.error("Validation failed: %d invalid rows — refusing to write",
                  validation["invalid_count"])
        for idx, errs in list(validation["errors_by_row"].items())[:5]:
            log.error("  row %d: %s", idx, "; ".join(errs))
        return 1

    after_mood = _mood_census(cleaned)
    after_src = _source_census(cleaned)
    _print_diff(before_mood, after_mood, before_src, after_src, stats)

    if args.apply:
        n = write_jsonl(cleaned, args.tracks)
        print(f"  WROTE {n} rows → {args.tracks}")
    else:
        print("  DRY RUN — pass --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
