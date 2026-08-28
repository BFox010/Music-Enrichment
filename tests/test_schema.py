"""Tests for pipeline.schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.schema import (
    FIELD_DEFAULTS,
    atomic_open,
    fill_defaults,
    read_jsonl,
    validate_dataset,
    validate_row,
    write_jsonl,
)


class TestFillDefaults:
    def test_all_defaults_when_empty(self) -> None:
        out = fill_defaults({})
        # Every canonical field must be present
        for key in FIELD_DEFAULTS:
            assert key in out

    def test_existing_values_win(self) -> None:
        out = fill_defaults({"artist": "Portishead", "play_count": 47})
        assert out["artist"] == "Portishead"
        assert out["play_count"] == 47

    def test_lists_are_independent(self) -> None:
        a = fill_defaults({})
        b = fill_defaults({})
        a["genres"].append("rock")
        assert b["genres"] == []  # not shared!

    def test_extra_fields_preserved(self) -> None:
        out = fill_defaults({"artist": "x", "track": "y", "custom_field": "kept"})
        assert out["custom_field"] == "kept"


class TestValidateRow:
    def _good_row(self) -> dict:
        return {
            "artist": "Portishead",
            "track": "Roads",
            "artist_normalized": "portishead",
            "track_normalized": "roads",
            "play_count": 47,
            "genres": [],
            "lastfm_tags": [],
            "playlists": [],
        }

    def test_valid_row(self) -> None:
        assert validate_row(self._good_row()) == []

    def test_missing_required(self) -> None:
        row = self._good_row()
        del row["artist"]
        errs = validate_row(row)
        assert any("artist" in e for e in errs)

    def test_genres_must_be_list(self) -> None:
        row = self._good_row()
        row["genres"] = "rock"
        errs = validate_row(row)
        assert any("genres" in e for e in errs)

    def test_negative_play_count(self) -> None:
        row = self._good_row()
        row["play_count"] = -1
        errs = validate_row(row)
        assert any("play_count" in e for e in errs)


class TestValidateDataset:
    def test_all_valid(self) -> None:
        rows = [
            {"artist": "x", "track": "y",
             "artist_normalized": "x", "track_normalized": "y",
             "play_count": 1,
             "genres": [], "lastfm_tags": [], "playlists": []}
        ]
        result = validate_dataset(rows)
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 0

    def test_some_invalid(self) -> None:
        rows = [
            {"artist": "x", "track": "y",
             "artist_normalized": "x", "track_normalized": "y",
             "genres": [], "lastfm_tags": [], "playlists": []},
            {"artist": "", "track": "y",
             "artist_normalized": "", "track_normalized": "y",
             "genres": [], "lastfm_tags": [], "playlists": []},
        ]
        result = validate_dataset(rows)
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 1
        assert 1 in result["errors_by_row"]


class TestAtomicOpen:
    """F-04: canonical/intermediate JSONL writes must never leave a truncated or
    partially-written file behind — a crash, disk-full, or exception mid-write
    must leave the previous good file exactly as it was."""

    def test_successful_write_replaces_destination(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        with atomic_open(path) as fh:
            fh.write("hello\n")
        assert path.read_text(encoding="utf-8") == "hello\n"

    def test_no_temp_file_left_behind_on_success(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        with atomic_open(path) as fh:
            fh.write("hello\n")
        leftovers = list(tmp_path.iterdir())
        assert leftovers == [path]

    def test_exception_mid_write_leaves_existing_destination_untouched(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text("original content\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            with atomic_open(path) as fh:
                fh.write("partial write that never completes\n")
                raise RuntimeError("simulated crash mid-write")

        assert path.read_text(encoding="utf-8") == "original content\n"

    def test_exception_mid_write_leaves_no_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text("original\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            with atomic_open(path) as fh:
                fh.write("junk\n")
                raise RuntimeError("boom")

        assert list(tmp_path.iterdir()) == [path]

    def test_exception_before_any_destination_exists_leaves_nothing(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        with pytest.raises(RuntimeError):
            with atomic_open(path) as fh:
                fh.write("junk\n")
                raise RuntimeError("boom")
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "out.jsonl"
        with atomic_open(path) as fh:
            fh.write("hello\n")
        assert path.read_text(encoding="utf-8") == "hello\n"

    def test_concurrent_temp_names_do_not_collide(self, tmp_path: Path) -> None:
        """Two writers targeting the same destination must not share a fixed
        temp filename — each gets its own unique temp file."""
        path = tmp_path / "out.jsonl"
        with atomic_open(path) as fh1:
            fh1.write("first\n")
            # A second writer opens its own temp file mid-write of the first.
            with atomic_open(path) as fh2:
                fh2.write("second\n")
        # The second writer's replace happened first (inner context exits
        # first), then the first writer's replace overwrote it last.
        assert path.read_text(encoding="utf-8") == "first\n"


class TestWriteJsonlAtomic:
    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "tracks.jsonl"
        rows = [
            {"artist": "Portishead", "track": "Roads"},
            {"artist": "Massive Attack", "track": "Teardrop"},
        ]
        n = write_jsonl(rows, path)
        assert n == 2
        read_back = read_jsonl(path)
        assert [r["artist"] for r in read_back] == ["Portishead", "Massive Attack"]

    def test_failed_write_preserves_existing_canonical_file(self, tmp_path: Path) -> None:
        """A generator that raises partway through must not corrupt the
        previously-written tracks.jsonl — this is the Phase 8 failure mode
        F-04 exists to close off."""
        path = tmp_path / "tracks.jsonl"
        write_jsonl([{"artist": "Existing", "track": "Track"}], path)
        original = path.read_bytes()

        def _rows_that_blow_up():
            yield {"artist": "New", "track": "One"}
            raise RuntimeError("simulated phase failure")

        with pytest.raises(RuntimeError):
            write_jsonl(_rows_that_blow_up(), path)

        assert path.read_bytes() == original
