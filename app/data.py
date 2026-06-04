"""In-memory cache for tracks.jsonl and scrobbles.jsonl.

The JSONL files are the git-tracked source of truth. This module loads them
once into module globals and exposes read-only accessors. ``use_paths()``
lets tests inject temporary fixture files without touching the real data.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from pipeline.config import SCROBBLES_PATH, TRACKS_PATH

logger = logging.getLogger(__name__)

_tracks: list[dict] = []
_scrobbles: list[dict] = []
_tracks_path: Path = TRACKS_PATH
_scrobbles_path: Path = SCROBBLES_PATH


def _load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file, skipping malformed lines instead of crashing.

    This module loads at import time, so a single bad line must not take down
    the whole API. Unparseable rows are logged and skipped.
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping malformed JSONL row %s:%d", path, line_no)
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                logger.warning("skipping non-object JSONL row %s:%d", path, line_no)
    return rows


def load() -> None:
    global _tracks, _scrobbles
    _tracks = _load_jsonl(_tracks_path)
    _scrobbles = _load_jsonl(_scrobbles_path)


def reload() -> dict:
    load()
    return {"tracks": len(_tracks), "scrobbles": len(_scrobbles)}


def get_tracks() -> list[dict]:
    return _tracks


def get_scrobbles() -> list[dict]:
    return _scrobbles


@contextmanager
def use_paths(
    tracks_path: Path, scrobbles_path: Path
) -> Generator[None, None, None]:
    """Temporarily redirect data loading to the given paths (for tests)."""
    global _tracks_path, _scrobbles_path, _tracks, _scrobbles
    saved = (_tracks_path, _scrobbles_path, _tracks, _scrobbles)
    _tracks_path = tracks_path
    _scrobbles_path = scrobbles_path
    load()
    try:
        yield
    finally:
        _tracks_path, _scrobbles_path, _tracks, _scrobbles = saved
