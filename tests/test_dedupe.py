"""Tests for pipeline.dedupe."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.dedupe import _most_common_value, _peak_year, dedupe
from pipeline.normalize import normalize_artist, normalize_track


# ── helpers ──────────────────────────────────────────────────────────────


def _make_scrobble(
    artist: str = "Portishead",
    track: str = "Roads",
    album: str = "Dummy",
    year: int = 2024,
    scrobbled_at: str = "2024-11-03T03:54:00Z",
) -> dict:
    return {
        "artist": artist,
        "track": track,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(track),
        "album": album,
        "scrobbled_at": scrobbled_at,
        "year": year,
        "month": 11,
        "day_of_week": 6,
        "hour": 3,
        "season": "fall",
    }


def _write_scrobbles(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_skeleton(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── unit tests ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_most_common_value_basic(self) -> None:
        assert _most_common_value(["a", "b", "a", "c", "a"]) == "a"

    def test_most_common_value_empty_list(self) -> None:
        assert _most_common_value([]) == ""

    def test_most_common_value_all_empty(self) -> None:
        assert _most_common_value(["", "", ""]) == ""

    def test_most_common_value_ignores_empty(self) -> None:
        # "" appears 3 times, "x" once — but non-empty wins
        assert _most_common_value(["", "", "", "x"]) == "x"

    def test_peak_year_single(self) -> None:
        assert _peak_year([2022]) == 2022

    def test_peak_year_majority(self) -> None:
        assert _peak_year([2022, 2023, 2023, 2023, 2022]) == 2023


# ── integration tests ─────────────────────────────────────────────────────


class TestDedupe:
    def test_single_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [_make_scrobble()])
            n = dedupe(inp, out, log_p)
            assert n == 1
            rows = _read_skeleton(out)
            assert rows[0]["artist"] == "Portishead"
            assert rows[0]["play_count"] == 1
            assert rows[0]["first_scrobbled"] == "2024-11-03"
            assert rows[0]["last_scrobbled"] == "2024-11-03"

    def test_deduplication_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [
                _make_scrobble(year=2022, scrobbled_at="2022-01-01T00:00:00Z"),
                _make_scrobble(year=2023, scrobbled_at="2023-06-15T12:00:00Z"),
                _make_scrobble(year=2023, scrobbled_at="2023-11-03T03:54:00Z"),
            ])
            n = dedupe(inp, out, log_p)
            assert n == 1
            row = _read_skeleton(out)[0]
            assert row["play_count"] == 3
            assert row["first_scrobbled"] == "2022-01-01"
            assert row["last_scrobbled"] == "2023-11-03"
            assert row["peak_year"] == 2023

    def test_no_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Glory Box"),
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Portishead", "Roads"),   # duplicate
            ])
            n = dedupe(inp, out, log_p)
            assert n == 3
            rows = _read_skeleton(out)
            keys = [(r["artist_normalized"], r["track_normalized"]) for r in rows]
            assert len(keys) == len(set(keys)), "Duplicate join keys in output"

    def test_output_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Arcade Fire", "Rebellion"),
            ])
            dedupe(inp, out, log_p)
            rows = _read_skeleton(out)
            artists = [r["artist_normalized"] for r in rows]
            assert artists == sorted(artists)

    def test_most_common_display_name(self) -> None:
        """Capitalisation drift across scrobbles → most common form wins."""
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [
                _make_scrobble("portishead", "roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
            ])
            dedupe(inp, out, log_p)
            row = _read_skeleton(out)[0]
            assert row["artist"] == "Portishead"
            assert row["track"] == "Roads"

    def test_multiple_artists_correct_play_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            log_p = Path(tmp) / "run.log"
            _write_scrobbles(inp, [
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Radiohead", "Karma Police"),
            ])
            dedupe(inp, out, log_p)
            rows = {r["artist_normalized"]: r for r in _read_skeleton(out)}
            assert rows["portishead"]["play_count"] == 3
            assert rows["radiohead"]["play_count"] == 2
