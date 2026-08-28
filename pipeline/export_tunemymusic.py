"""Phase 3a — Export TuneMyMusic-compatible CSV.

Reads tracks_skeleton.jsonl (artist/track/album is all this needs) and writes a
CSV TuneMyMusic can import. The playlist it creates is a *means*: the owner runs
Exportify against it to obtain audio features, which land via Phase 3b/3c. No
playlist is produced as a deliverable. Issue #37 plans to remove this detour.

Usage:
    python -m pipeline.export_tunemymusic
Output: inputs/tunemymusic_upload.csv
"""

from __future__ import annotations

import csv
from pathlib import Path

from pipeline.config import INPUTS_DIR, TRACKS_PATH, TRACKS_SKELETON_PATH, configure_logging, get_logger
from pipeline.schema import atomic_open, read_jsonl

OUTPUT_PATH = INPUTS_DIR / "tunemymusic_upload.csv"
PENDING_OUTPUT_PATH = INPUTS_DIR / "pending_exportify.csv"

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

    tracks = read_jsonl(skeleton_path)

    log.info("Read %d unique tracks", len(tracks))

    with atomic_open(output_path, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Artist", "Track", "Album"])
        for t in tracks:
            writer.writerow([t["artist"], t["track"], t.get("album", "")])

    log.info("Wrote %d rows → %s", len(tracks), output_path)
    return len(tracks)


def export_pending(
    tracks_path: Path = TRACKS_PATH,
    output_path: Path = PENDING_OUTPUT_PATH,
) -> int:
    """Write a CSV of tracks that still need Exportify audio features.

    Reads tracks.jsonl (the final pipeline output), filters to tracks where
    audio_features is null/absent, and writes Artist,Track,Album CSV.
    The user can take this file, run it through Exportify, and drop the
    result into inputs/exportify.csv for the next pipeline run.

    Returns the number of pending tracks written (0 if all tracks have features).
    """
    if not tracks_path.exists():
        log.warning("export_pending: tracks file not found: %s", tracks_path)
        return 0

    pending = [t for t in read_jsonl(tracks_path) if not t.get("audio_features")]

    with atomic_open(output_path, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Artist", "Track", "Album"])
        for t in pending:
            writer.writerow([t["artist"], t["track"], t.get("album", "")])

    log.info("export_pending: %d tracks missing audio features → %s", len(pending), output_path)
    return len(pending)


if __name__ == "__main__":
    import sys
    n = export()
    print(f"Phase 3a done: {n} tracks exported to inputs/tunemymusic_upload.csv")
    sys.exit(0 if n > 0 else 1)
