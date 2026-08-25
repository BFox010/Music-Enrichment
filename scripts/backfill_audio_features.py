"""Run Phase 5a/5b directly against tracks.jsonl (issue #37).

The full pipeline can't run in a fresh checkout (inputs/ is gitignored), but the
audio_features gap it closes lives in the committed tracks.jsonl. Runs 5a then 5b
straight back into tracks.jsonl instead of threading the intermediate chain.

Usage:
    python scripts/backfill_audio_features.py             # resolve + fetch, write in place
    python scripts/backfill_audio_features.py --dry-run    # report coverage before/after only
    python scripts/backfill_audio_features.py --limit 100  # smoke-test on a subset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline._http import FORCE_OFF  # noqa: E402
from pipeline.config import TRACKS_PATH  # noqa: E402
from pipeline.enrich_audio_features import enrich as fetch_features  # noqa: E402
from pipeline.resolve_isrcs import enrich as resolve_isrcs  # noqa: E402
from pipeline.schema import read_jsonl  # noqa: E402


def _coverage(path: Path) -> dict[str, int]:
    rows = read_jsonl(path)
    return {
        "total": len(rows),
        "isrc": sum(1 for r in rows if r.get("isrc")),
        "audio_features": sum(1 for r in rows if r.get("audio_features")),
    }


def _print_coverage(label: str, cov: dict[str, int]) -> None:
    n = cov["total"] or 1
    print(f"{label}: {cov['total']} tracks")
    print(f"  isrc            {cov['isrc']:5d} ({100 * cov['isrc'] / n:.1f}%)")
    print(f"  audio_features  {cov['audio_features']:5d} ({100 * cov['audio_features'] / n:.1f}%)")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                    help="Only report current coverage; make no API calls or writes.")
    p.add_argument("--limit", type=int, default=None,
                    help="Cap the number of tracks processed (smoke-testing).")
    p.add_argument("--force", action="store_true",
                    help="Bypass the HTTP cache and re-fetch everything.")
    args = p.parse_args(argv)

    before = _coverage(TRACKS_PATH)
    _print_coverage("Before", before)

    if args.dry_run:
        gap = before["total"] - before["audio_features"]
        print(f"\n--dry-run: no changes made. {gap} tracks lack audio_features.")
        return 0

    force = "all" if args.force else FORCE_OFF

    print("\n=== Phase 5a: ISRC resolution ===")
    isrc_stats = resolve_isrcs(
        input_path=TRACKS_PATH, output_path=TRACKS_PATH, limit=args.limit, force=force,
    )
    print(isrc_stats)

    print("\n=== Phase 5b: ReccoBeats audio features ===")
    feature_stats = fetch_features(
        input_path=TRACKS_PATH, output_path=TRACKS_PATH, limit=args.limit, force=force,
    )
    print(feature_stats)

    after = _coverage(TRACKS_PATH)
    print()
    _print_coverage("After", after)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
