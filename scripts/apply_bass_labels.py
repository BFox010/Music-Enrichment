"""Apply owner-reviewed Heavy Bass labels to tracks.jsonl (durable overlay).

Heavy Bass is *suppressed* on the centroid path (the 9 audio features carry no
bass descriptor — see docs/mood_centroid_decisions.md), so the automated pipeline
can never assign it. This script layers the owner's hand-reviewed Heavy Bass
verdicts back on top, recovering recall the suppression necessarily gave up.

The verdicts live in a version-controlled CSV (artist, track, decision) produced
from an Excel review sheet. Only ``decision == "keep"`` rows are applied. For each
kept track this script:

  * adds "Heavy Bass" to the existing mood_tags (co-moods are PRESERVED — nothing
    is pruned), and
  * marks the row owner-owned: mood_source="manual", mood_confidence="high",

so the label is protected from the centroid cleanup/suppression and survives a
future Phase 6 re-run. Run it as the LAST mood step (after Phase 8 and
cleanup_centroid_moods) so the human bass overlay sits on top of fresh pipeline
output. It is idempotent — re-running changes nothing.

    python scripts/apply_bass_labels.py            # dry-run diff
    python scripts/apply_bass_labels.py --apply    # write tracks.jsonl
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import TRACKS_PATH, get_logger  # noqa: E402
from pipeline.schema import (  # noqa: E402
    fill_defaults,
    read_jsonl,
    validate_dataset,
    write_jsonl,
)

log = get_logger(__name__)

BASS_MOOD = "Heavy Bass"
DEFAULT_DECISIONS = REPO_ROOT / "docs" / "bass_review_2026-06-05.csv"


def load_keeps(path: Path) -> set[tuple[str, str]]:
    """Return the set of (artist, track) marked ``keep`` in the decisions CSV."""
    keeps: set[tuple[str, str]] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("decision") or "").strip().lower() == "keep":
                keeps.add((row["artist"], row["track"]))
    return keeps


def apply_bass_keeps(
    tracks: list[dict], keeps: set[tuple[str, str]]
) -> tuple[list[dict], dict]:
    """Layer Heavy Bass onto kept rows in place. Returns (rows, stats).

    Co-moods are preserved. A kept row is promoted to mood_source="manual" so the
    overlay is durable. Already-Heavy-Bass rows are left untouched (idempotent).
    """
    stats = {
        "keeps_total": len(keeps),
        "matched": 0,
        "added": 0,
        "already_had": 0,
        "promoted": 0,
    }
    index = {(t.get("artist"), t.get("track")): t for t in tracks}
    for key in keeps:
        row = index.get(key)
        if row is None:
            continue
        stats["matched"] += 1
        tags = list(row.get("mood_tags") or [])
        if BASS_MOOD in tags:
            stats["already_had"] += 1
        else:
            tags.append(BASS_MOOD)
            row["mood_tags"] = tags
            stats["added"] += 1
        if row.get("mood_source") != "manual":
            row["mood_source"] = "manual"
            stats["promoted"] += 1
        row["mood_confidence"] = "high"
    return tracks, stats


def _bass_census(tracks: list[dict]) -> int:
    return sum(1 for t in tracks if BASS_MOOD in (t.get("mood_tags") or []))


def _source_census(tracks: list[dict]) -> Counter:
    return Counter(t.get("mood_source") or "(none)" for t in tracks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    ap.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    ap.add_argument("--apply", action="store_true",
                    help="write changes to tracks.jsonl (default: dry-run)")
    args = ap.parse_args(argv)

    rows = read_jsonl(args.tracks)
    if not rows:
        print(f"No tracks found at {args.tracks}", file=sys.stderr)
        return 1
    if not args.decisions.exists():
        print(f"Decisions CSV not found: {args.decisions}", file=sys.stderr)
        return 1

    keeps = load_keeps(args.decisions)
    before_bass = _bass_census(rows)
    before_src = _source_census(rows)

    cleaned, stats = apply_bass_keeps(rows, keeps)
    cleaned = [fill_defaults(r) for r in cleaned]
    cleaned.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    validation = validate_dataset(cleaned)
    if validation["invalid_count"] > 0:
        log.error("Validation failed: %d invalid rows — refusing to write",
                  validation["invalid_count"])
        for idx, errs in list(validation["errors_by_row"].items())[:5]:
            log.error("  row %d: %s", idx, "; ".join(errs))
        return 1

    after_bass = _bass_census(cleaned)
    after_src = _source_census(cleaned)

    print("=" * 60)
    print(" APPLY HEAVY BASS LABELS")
    print("=" * 60)
    print(f"  keep decisions        : {stats['keeps_total']}")
    print(f"  matched in tracks      : {stats['matched']}")
    print(f"  Heavy Bass added       : {stats['added']}")
    print(f"  already had Heavy Bass : {stats['already_had']}")
    print(f"  rows promoted to manual: {stats['promoted']}")
    print()
    print(f"  Heavy Bass total : {before_bass} -> {after_bass}")
    print("  mood_source census (before -> after):")
    for src in sorted(set(before_src) | set(after_src),
                      key=lambda s: -before_src.get(s, 0)):
        print(f"    {src:12s} {before_src.get(src, 0):>5d} -> {after_src.get(src, 0):>5d}")
    print()

    if stats["matched"] != stats["keeps_total"]:
        log.warning("%d keep decisions did not match a track",
                    stats["keeps_total"] - stats["matched"])

    if args.apply:
        n = write_jsonl(cleaned, args.tracks)
        print(f"  WROTE {n} rows -> {args.tracks}")
    else:
        print("  DRY RUN — pass --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
