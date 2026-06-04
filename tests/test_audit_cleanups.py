"""Regression tests for the P2/P3 audit-cleanup batch.

Covers:
  #5 — forgotten_favorites must not emit blank artist/track rows
  #6 — the JSONL loader / reload() must report skipped (corrupt) row counts
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import app.data as data
import app.metrics as metrics


def _write_jsonl(path: Path, rows) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


# ── #5 — Forgotten Favorites label fallback ──────────────────────────────────

class TestForgottenFavoritesLabels:
    def _data(self):
        tracks = [
            {"artist": "Portishead", "track": "Roads",
             "release_year": 1994, "genres": ["trip-hop"], "mood_tags": ["Moody"]},
        ]
        scrobbles = (
            # matched track, peaked 2018 then faded → forgotten
            [{"artist": "Portishead", "track": "Roads", "year": 2018}] * 6
            # NOT in tracks.jsonl — must fall back to the scrobble's own labels
            + [{"artist": "Faded Artist", "track": "Old Song", "year": 2018}] * 5
            # unlabelable (no artist/track) — must be skipped, not blank
            + [{"artist": "", "track": "", "year": 2017}] * 5
            # recent anchor so max_year=2024, recent window = 2023-2024
            + [{"artist": "Recent Artist", "track": "New Song", "year": 2024}]
        )
        return tracks, scrobbles

    def _run(self):
        tracks, scrobbles = self._data()
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write_jsonl(tp, tracks)
            _write_jsonl(sp, scrobbles)
            with data.use_paths(tp, sp):
                return metrics.forgotten_favorites(top=30, min_peak=5, recent_years=2)

    def test_no_blank_rows(self):
        rows = self._run()
        assert rows, "expected at least one forgotten favorite"
        for r in rows:
            assert r["artist"].strip() or r["track"].strip(), f"blank row: {r}"

    def test_unmatched_key_uses_scrobble_label(self):
        rows = self._run()
        labels = {(r["artist"], r["track"]) for r in rows}
        # falls back to the scrobble's artist/track instead of empty strings
        assert ("Faded Artist", "Old Song") in labels

    def test_matched_track_keeps_canonical_metadata(self):
        rows = self._run()
        roads = next(r for r in rows if r["track"] == "Roads")
        assert roads["artist"] == "Portishead"
        assert roads["release_year"] == 1994

    def test_unlabelable_key_is_skipped(self):
        rows = self._run()
        assert all(r["artist"] or r["track"] for r in rows)


# ── #6 — Loader surfaces skipped-row counts ──────────────────────────────────

class TestLoaderSkippedCounts:
    def test_reload_reports_skipped_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write_jsonl(tp, [
                {"artist": "A", "track": "T"},
                "this is not json",          # malformed → skipped
                "[1, 2, 3]",                 # valid JSON but not an object → skipped
                {"artist": "B", "track": "U"},
            ])
            _write_jsonl(sp, [{"artist": "A", "track": "T", "year": 2020}])
            with data.use_paths(tp, sp):
                stats = data.reload()
        assert stats["tracks"] == 2
        assert stats["scrobbles"] == 1
        assert stats["skipped"] == {"tracks": 2, "scrobbles": 0}

    def test_clean_data_reports_zero_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
            _write_jsonl(tp, [{"artist": "A", "track": "T"}])
            _write_jsonl(sp, [{"artist": "A", "track": "T", "year": 2020}])
            with data.use_paths(tp, sp):
                stats = data.reload()
        assert stats["skipped"] == {"tracks": 0, "scrobbles": 0}
