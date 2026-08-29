"""In-memory cache for tracks.jsonl and scrobbles.jsonl.

The JSONL files are the git-tracked source of truth. This module loads them
once into module globals and exposes read-only accessors. ``use_paths()``
lets tests inject temporary fixture files without touching the real data.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from pipeline.config import SCROBBLES_PATH, TRACKS_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """One consistent generation of the in-memory library.

    ``tracks`` and ``scrobbles`` are always published together as a single
    unit: ``load()`` builds both lists in local variables first and publishes
    them with one reassignment of the module-level reference below, which is
    the one operation here that's atomic under the GIL. FastAPI runs sync
    route handlers in a threadpool, so without this a reader could otherwise
    see a new track set paired with a still-old scrobble set (or vice versa)
    if a ``reload()`` landed between two separate global reads.

    A computation that needs both collections should call ``get_snapshot()``
    once and read both off the result, rather than calling ``get_tracks()``
    and ``get_scrobbles()`` separately — those aren't guaranteed to land on
    the same generation either, for the same reason.
    """

    tracks: list[dict]
    scrobbles: list[dict]
    tracks_skipped: int
    scrobbles_skipped: int
    generation: int


_tracks_path: Path = TRACKS_PATH
_scrobbles_path: Path = SCROBBLES_PATH
_snapshot: Snapshot = Snapshot(tracks=[], scrobbles=[], tracks_skipped=0, scrobbles_skipped=0, generation=0)

# Monotonic and independent of any Snapshot's own stored value, so a generation
# number is never reused — use_paths() restoring an older Snapshot on exit (its
# generation intact) must not make the next load() hand out a number a cache
# elsewhere (app.metrics._track_index) has already seen for different data.
_generation_counter = 0


def _load_jsonl(path: Path) -> tuple[list[dict], int]:
    """Load a JSONL file, skipping malformed lines instead of crashing.

    This module loads at import time, so a single bad line must not take down
    the whole API. Unparseable rows are logged and skipped. Returns the parsed
    rows plus the number of rows skipped, so callers can surface corruption.
    """
    if not path.exists():
        return [], 0
    rows: list[dict] = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping malformed JSONL row %s:%d", path, line_no)
                skipped += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                logger.warning("skipping non-object JSONL row %s:%d", path, line_no)
                skipped += 1
    return rows, skipped


def load() -> None:
    """Load both files and publish them as one new Snapshot generation.

    See ``Snapshot`` for why both lists are built before the module-level
    reference is touched, and published with a single reassignment.
    """
    global _snapshot, _generation_counter
    tracks, tracks_skipped = _load_jsonl(_tracks_path)
    scrobbles, scrobbles_skipped = _load_jsonl(_scrobbles_path)
    _generation_counter += 1
    _snapshot = Snapshot(
        tracks=tracks,
        scrobbles=scrobbles,
        tracks_skipped=tracks_skipped,
        scrobbles_skipped=scrobbles_skipped,
        generation=_generation_counter,
    )


def reload() -> dict:
    load()
    snap = _snapshot
    return {
        "tracks": len(snap.tracks),
        "scrobbles": len(snap.scrobbles),
        "skipped": {"tracks": snap.tracks_skipped, "scrobbles": snap.scrobbles_skipped},
    }


def get_snapshot() -> Snapshot:
    """The current (tracks, scrobbles) pair as one consistent generation.

    Prefer this over calling ``get_tracks()``/``get_scrobbles()`` separately
    when a computation needs both — see ``Snapshot``'s docstring.
    """
    return _snapshot


def get_tracks() -> list[dict]:
    return _snapshot.tracks


def get_scrobbles() -> list[dict]:
    return _snapshot.scrobbles


@contextmanager
def use_paths(
    tracks_path: Path, scrobbles_path: Path
) -> Generator[None, None, None]:
    """Temporarily redirect data loading to the given paths (for tests)."""
    global _tracks_path, _scrobbles_path, _snapshot
    saved_paths = (_tracks_path, _scrobbles_path)
    saved_snapshot = _snapshot
    _tracks_path = tracks_path
    _scrobbles_path = scrobbles_path
    load()
    try:
        yield
    finally:
        _tracks_path, _scrobbles_path = saved_paths
        _snapshot = saved_snapshot
