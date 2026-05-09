"""Phase 3a — Export TuneMyMusic-compatible CSV.

Reads tracks_skeleton.jsonl and writes a CSV that TuneMyMusic can import
to create a Spotify playlist. Owner then runs Exportify on that playlist
to get audio features (Phase 3b/3c).

Usage:
    python -m pipeline.export_tunemymusic
Output: inputs/tunemymusic_upload.csv
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pipeline.config import INPUTS_DIR, TRACKS_SKELETON_PATH, configure_logging, get_logger

OUTPUT_PATH = INPUTS_DIR / "tunemymusic_upload.csv"

log = get_logger(__name__)


def export(
    skeleton_path: Path = TRACKS_SKELETON_PATH,
    output_path: Path = OUTPUT_PATH,
    run_log_path: Path | None = None,
) -> int:
    """Write TuneMyMusic-compatible CSV from tracks_skeleton.jsonl.

    Returns number of rows written.
    """
    configure_logging(run_log_path)
    log.info("=== Phase 3a: TuneMyMusic export ===")
    log.info("Input : %s", skeleton_path)
    log.info("Output: %s", output_path)

    if not skeleton_path.exists():
        raise FileNotFoundError(f"tracks_skeleton.jsonl not found: {skeleton_path}")

    tracks: list[dict] = []
    with open(skeleton_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))

    log.info("Read %d unique tracks", len(tracks))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Artist", "Track", "Album"])
        for t in tracks:
            writer.writerow([t["artist"], t["track"], t.get("album", "")])

    log.info("Wrote %d rows → %s", len(tracks), output_path)
    return len(tracks)


if __name__ == "__main__":
    import sys
    n = export()
    print(f"Phase 3a done: {n} tracks exported to inputs/tunemymusic_upload.csv")
    sys.exit(0 if n > 0 else 1)
