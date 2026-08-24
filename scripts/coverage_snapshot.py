"""Field-coverage snapshot of tracks.jsonl, and before/after diffs of two runs.

Coverage numbers drift the moment new scrobbles land, so they belong in a tool
rather than in prose. Take a snapshot before a pipeline run, another after, and
diff them to see what the run actually moved:

    python scripts/coverage_snapshot.py --out before.json
    python -m pipeline.run_full_pipeline
    python scripts/coverage_snapshot.py --out after.json --compare before.json

A field counts as populated when it is not None/""/[]/{}. audio_features is
additionally broken down by its `source`, which is what separates a gain made
by the 5a/5b chain from one made by the legacy Exportify route.

Snapshots are plain JSON and safe to keep around; the diff only needs the
baseline file, not the tracks.jsonl it came from.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import TRACKS_PATH  # noqa: E402

# Called out first in the report; everything else follows alphabetically.
HEADLINE = [
    "isrc",
    "audio_features",
    "spotify_id",
    "musicbrainz_id",
    "artist_mbid",
    "genres",
    "discogs_styles",
    "lastfm_tags",
    "mood_tags",
    "apple_music_id",
    "apple_music_available",
    "release_year",
    "duration_ms",
    "saturation_tier",
    "curation_state",
]

_EMPTY = (None, "", [], {})


def snapshot(path: Path) -> dict:
    counts: Counter = Counter()
    sources: Counter = Counter()
    total = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            row = json.loads(line)
            for key, value in row.items():
                if value not in _EMPTY:
                    counts[key] += 1
            features = row.get("audio_features")
            if isinstance(features, dict) and features.get("source"):
                sources[features["source"]] += 1
    return {
        "path": str(path),
        "records": total,
        "counts": dict(counts),
        "audio_feature_sources": dict(sources),
    }


def _pct(count: int, total: int) -> float:
    return (count / total * 100) if total else 0.0


def _print_plain(snap: dict) -> None:
    total = snap["records"]
    print(f"records: {total}")
    for field in HEADLINE:
        count = snap["counts"].get(field, 0)
        print(f"  {_pct(count, total):>7.2f}%  {count:>6}  {field}")
    print("\naudio_features by source:")
    for source, count in sorted(snap["audio_feature_sources"].items()):
        print(f"  {source:<20} {count:>6}")


def _print_diff(snap: dict, base: dict) -> None:
    total, base_total = snap["records"], base["records"]
    print(f"records: {base_total} -> {total}  ({total - base_total:+d})")
    print()
    print(f"{'field':<24} {'before':>16} {'after':>16} {'delta':>9}")
    print("-" * 68)

    rest = sorted((set(snap["counts"]) | set(base["counts"])) - set(HEADLINE))
    for field in HEADLINE + rest:
        before = base["counts"].get(field, 0)
        after = snap["counts"].get(field, 0)
        before_pct, after_pct = _pct(before, base_total), _pct(after, total)
        moved = "  <--" if abs(after_pct - before_pct) >= 0.5 else ""
        print(
            f"{field:<24} {before:>6} {before_pct:>7.2f}% "
            f"{after:>6} {after_pct:>7.2f}% {after_pct - before_pct:>+8.2f}{moved}"
        )

    print("\naudio_features by source:")
    for source in sorted(
        set(snap["audio_feature_sources"]) | set(base["audio_feature_sources"])
    ):
        before = base["audio_feature_sources"].get(source, 0)
        after = snap["audio_feature_sources"].get(source, 0)
        print(f"  {source:<20} {before:>6} -> {after:>6}  ({after - before:+d})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path, default=TRACKS_PATH,
                        help="tracks JSONL to measure. Default: tracks.jsonl")
    parser.add_argument("--out", type=Path, help="write the snapshot as JSON")
    parser.add_argument("--compare", type=Path, help="baseline snapshot to diff against")
    args = parser.parse_args()

    if not args.path.exists():
        parser.error(f"no such file: {args.path}")

    snap = snapshot(args.path)

    if args.out:
        args.out.write_text(
            json.dumps(snap, indent=2, sort_keys=True), encoding="utf-8"
        )

    if args.compare:
        base = json.loads(args.compare.read_text(encoding="utf-8"))
        _print_diff(snap, base)
    else:
        _print_plain(snap)


if __name__ == "__main__":
    main()
