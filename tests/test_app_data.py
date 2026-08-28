"""Tests for app.data's Snapshot publication (F-05).

The risk this guards against: app.data used to hold tracks and scrobbles as
two separate module globals, reassigned one after the other in load(). A
concurrent reader (FastAPI runs sync route handlers in a threadpool) could
observe a new track set paired with a still-old scrobble set, or the reverse,
if a reload() landed between the two reassignments. Snapshot makes the pair
one immutable object published with a single reassignment, and get_snapshot()
lets a request capture one generation and use it for a whole computation
instead of calling get_tracks()/get_scrobbles() separately.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import app.data as data


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class TestSnapshotPublication:
    def test_get_snapshot_bundles_tracks_and_scrobbles_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write(tp, [{"artist": "A", "track": "song"}])
            _write(sp, [{"artist": "A", "track": "song", "year": 2025}])
            with data.use_paths(tp, sp):
                snap = data.get_snapshot()
                assert len(snap.tracks) == 1
                assert len(snap.scrobbles) == 1
                assert snap.tracks is data.get_tracks()
                assert snap.scrobbles is data.get_scrobbles()

    def test_load_advances_the_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write(tp, [{"artist": "A", "track": "song"}])
            _write(sp, [])
            with data.use_paths(tp, sp):
                gen1 = data.get_snapshot().generation
                data.load()
                gen2 = data.get_snapshot().generation
                assert gen2 > gen1

    def test_a_captured_snapshot_is_unaffected_by_a_later_load(self):
        """The whole point of Snapshot: a reference captured before a reload
        keeps reading the generation it was captured from, never a mix of it
        and whatever load() publishes next."""
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write(tp, [{"artist": "Old", "track": "song"}])
            _write(sp, [{"artist": "Old", "track": "song", "year": 2020}])
            with data.use_paths(tp, sp):
                captured = data.get_snapshot()

                _write(tp, [{"artist": "New", "track": "song"}])
                _write(sp, [{"artist": "New", "track": "song", "year": 2026}])
                data.load()

                # The reference captured before load() still sees the old,
                # internally-consistent pair — not New tracks with Old
                # scrobbles or any other torn mix.
                assert captured.tracks[0]["artist"] == "Old"
                assert captured.scrobbles[0]["artist"] == "Old"
                assert data.get_snapshot().tracks[0]["artist"] == "New"
                assert data.get_snapshot().scrobbles[0]["artist"] == "New"
                assert data.get_snapshot().generation > captured.generation

    def test_use_paths_restores_the_previous_snapshot_on_exit(self):
        outer_before = data.get_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write(tp, [{"artist": "Fixture", "track": "song"}])
            _write(sp, [])
            with data.use_paths(tp, sp):
                assert data.get_snapshot() is not outer_before
        assert data.get_snapshot() is outer_before

    def test_generation_numbers_never_repeat_across_separate_use_paths_fixtures(self):
        """Regression: generation used to be derived from the *previous*
        Snapshot's own stored value. use_paths() restores an older Snapshot
        (with its original, lower generation) on exit, so the next load()
        recomputed from that restored baseline and could hand out a
        generation number a cache elsewhere had already seen — for
        completely different data. The counter must be independent of any
        one Snapshot's stored generation so numbers are never reused.
        """
        seen: set[int] = set()
        for i in range(3):
            with tempfile.TemporaryDirectory() as tmp:
                tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
                _write(tp, [{"artist": f"Artist{i}", "track": "song"}])
                _write(sp, [])
                with data.use_paths(tp, sp):
                    gen = data.get_snapshot().generation
                    assert gen not in seen, "generation number reused across fixtures"
                    seen.add(gen)
