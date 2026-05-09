"""Phase 2 — Dedupe scrobbles into unique track skeleton.

Groups scrobbles.jsonl by (artist_normalized, track_normalized) and emits
tracks_skeleton.jsonl — one row per unique track with play statistics.

Usage:
    python -m pipeline.dedupe
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.config import (
    SCROBBLES_PATH,
    TRACKS_SKELETON_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)


def _most_common_value(values: list[str]) -> str:
    """Most frequently occurring non-empty string, or '' if all empty."""
    non_empty = [v for v in values if v]
    if not non_empty:
        return ""
    return Counter(non_empty).most_common(1)[0][0]


def _peak_year(years: list[int]) -> int:
    """Year with the most plays."""
    return Counter(years).most_common(1)[0][0]


def dedupe(
    scrobbles_path: Path = SCROBBLES_PATH,
    output_path: Path = TRACKS_SKELETON_PATH,
    run_log_path: Path | None = None,
) -> int:
    """Group scrobbles by join key, aggregate stats, write tracks_skeleton.jsonl.

    Returns count of unique tracks written.
    """
    run_log_path = configure_logging(run_log_path)
    log.info("=== Phase 2: dedupe ===")
    log.info("Input : %s", scrobbles_path)
    log.info("Output: %s", output_path)

    if not scrobbles_path.exists():
        log.error("scrobbles.jsonl not found: %s", scrobbles_path)
        raise FileNotFoundError(scrobbles_path)

    rows: list[dict] = []
    with open(scrobbles_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    log.info("Read %d scrobble rows", len(rows))

    # Group by composite join key
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = f"{row['artist_normalized']}|{row['track_normalized']}"
        groups[key].append(row)

    log.info("Unique (artist, track) pairs: %d", len(groups))

    skeletons: list[dict] = []
    for scrobbles in groups.values():
        # Use most-common display names to handle capitalisation drift
        artist = _most_common_value([s["artist"] for s in scrobbles])
        track = _most_common_value([s["track"] for s in scrobbles])
        album = _most_common_value([s["album"] for s in scrobbles])

        dates = sorted(s["scrobbled_at"] for s in scrobbles)
        years = [s["year"] for s in scrobbles]

        skeletons.append(
            {
                "artist": artist,
                "track": track,
                "artist_normalized": scrobbles[0]["artist_normalized"],
                "track_normalized": scrobbles[0]["track_normalized"],
                "album": album,
                "play_count": len(scrobbles),
                "first_scrobbled": dates[0][:10],   # YYYY-MM-DD
                "last_scrobbled": dates[-1][:10],
                "peak_year": _peak_year(years),
            }
        )

    # Stable sort → deterministic output and readable git diffs
    skeletons.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in skeletons:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("Wrote %d unique tracks → %s", len(skeletons), output_path)
    log.info("Run log: %s", run_log_path)
    return len(skeletons)


if __name__ == "__main__":
    import sys

    n = dedupe()
    print(f"Phase 2 done: {n} unique tracks.")
    sys.exit(0 if n > 0 else 1)
