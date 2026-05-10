"""Tests for pipeline.schema."""

from __future__ import annotations

from pipeline.schema import FIELD_DEFAULTS, fill_defaults, validate_dataset, validate_row


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

    def test_blacklisted_default_is_false(self) -> None:
        out = fill_defaults({})
        assert out["blacklisted"] is False


class TestValidateRow:
    def _good_row(self) -> dict:
        return {
            "artist": "Portishead",
            "track": "Roads",
            "artist_normalized": "portishead",
            "track_normalized": "roads",
            "play_count": 47,
            "blacklisted": False,
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

    def test_blacklisted_must_be_bool(self) -> None:
        row = self._good_row()
        row["blacklisted"] = "no"
        errs = validate_row(row)
        assert any("blacklisted" in e for e in errs)

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
             "play_count": 1, "blacklisted": False,
             "genres": [], "lastfm_tags": [], "playlists": []}
        ]
        result = validate_dataset(rows)
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 0

    def test_some_invalid(self) -> None:
        rows = [
            {"artist": "x", "track": "y",
             "artist_normalized": "x", "track_normalized": "y",
             "blacklisted": False, "genres": [], "lastfm_tags": [], "playlists": []},
            {"artist": "", "track": "y",
             "artist_normalized": "", "track_normalized": "y",
             "blacklisted": False, "genres": [], "lastfm_tags": [], "playlists": []},
        ]
        result = validate_dataset(rows)
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 1
        assert 1 in result["errors_by_row"]
