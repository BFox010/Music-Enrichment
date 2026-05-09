"""Tests for pipeline.enrich_apple_library."""

from __future__ import annotations

from datetime import datetime, timezone

from pipeline.enrich_apple_library import (
    _is_audio_track,
    _merge_apple_blocks,
    _record_to_apple_block,
    _release_year,
    _to_iso,
)


class TestIsAudioTrack:
    def test_purchased_aac(self) -> None:
        assert _is_audio_track({"Kind": "Purchased AAC audio file"}) is True

    def test_apple_music(self) -> None:
        assert _is_audio_track({"Kind": "Apple Music AAC audio file"}) is True

    def test_mpeg(self) -> None:
        assert _is_audio_track({"Kind": "MPEG audio file"}) is True

    def test_quicktime_movie(self) -> None:
        assert _is_audio_track({"Kind": "QuickTime movie file"}) is False

    def test_no_kind_with_artist(self) -> None:
        assert _is_audio_track({"Artist": "x", "Name": "y"}) is True

    def test_no_kind_no_artist(self) -> None:
        assert _is_audio_track({}) is False


class TestReleaseYear:
    def test_year_field(self) -> None:
        assert _release_year({"Year": 2015}) == 2015

    def test_release_date_fallback(self) -> None:
        assert _release_year({"Release Date": datetime(2015, 7, 17)}) == 2015

    def test_year_takes_precedence(self) -> None:
        assert _release_year({"Year": 2015, "Release Date": datetime(2010, 1, 1)}) == 2015

    def test_implausible_year_falls_back(self) -> None:
        assert _release_year({"Year": 0, "Release Date": datetime(2020, 1, 1)}) == 2020

    def test_neither_present(self) -> None:
        assert _release_year({}) is None


class TestToIso:
    def test_naive_datetime_assumed_utc(self) -> None:
        assert _to_iso(datetime(2020, 9, 14, 8, 44, 9)) == "2020-09-14T08:44:09Z"

    def test_aware_datetime_converted(self) -> None:
        dt = datetime(2020, 9, 14, 8, 44, 9, tzinfo=timezone.utc)
        assert _to_iso(dt) == "2020-09-14T08:44:09Z"

    def test_none(self) -> None:
        assert _to_iso(None) is None


class TestRecordToAppleBlock:
    def test_basic(self) -> None:
        record = {
            "Artist": "Tame Impala",
            "Name": "Let It Happen",
            "Album": "Currents",
            "Genre": "Alternative",
            "Year": 2015,
            "Total Time": 466885,
            "Explicit": True,
            "Play Count": 8,
            "Skip Count": 2,
            "Date Added": datetime(2015, 7, 16, 18, 0, 51),
            "Play Date UTC": datetime(2023, 6, 21, 23, 2, 0),
            "Persistent ID": "7453EB2348D90176",
            "Kind": "Purchased AAC audio file",
        }
        block = _record_to_apple_block(record)
        assert block is not None
        assert block["artist"] == "Tame Impala"
        assert block["track"] == "Let It Happen"
        assert block["artist_normalized"] == "tame impala"
        assert block["track_normalized"] == "let it happen"
        assert block["duration_ms"] == 466885
        assert block["release_year"] == 2015
        assert block["explicit"] is True
        assert block["itunes_genre"] == "Alternative"
        assert block["itunes_play_count"] == 8
        assert block["itunes_skip_count"] == 2
        assert block["itunes_persistent_id"] == "7453EB2348D90176"
        assert block["itunes_kind"] == "Purchased AAC audio file"

    def test_missing_artist(self) -> None:
        assert _record_to_apple_block({"Name": "x"}) is None

    def test_missing_name(self) -> None:
        assert _record_to_apple_block({"Artist": "x"}) is None

    def test_album_artist_fallback(self) -> None:
        block = _record_to_apple_block({"Album Artist": "x", "Name": "y"})
        assert block is not None
        assert block["artist"] == "x"

    def test_play_skip_count_default_zero(self) -> None:
        block = _record_to_apple_block({"Artist": "x", "Name": "y"})
        assert block is not None
        assert block["itunes_play_count"] == 0
        assert block["itunes_skip_count"] == 0
        assert block["explicit"] is False


class TestMergeAppleBlocks:
    def test_single_block(self) -> None:
        block = {"itunes_play_count": 5}
        assert _merge_apple_blocks([block]) is block

    def test_max_play_count(self) -> None:
        merged = _merge_apple_blocks([
            {"itunes_play_count": 3, "itunes_skip_count": 1},
            {"itunes_play_count": 7, "itunes_skip_count": 0},
            {"itunes_play_count": 5, "itunes_skip_count": 4},
        ])
        assert merged["itunes_play_count"] == 7
        assert merged["itunes_skip_count"] == 4

    def test_earliest_date_added(self) -> None:
        merged = _merge_apple_blocks([
            {"itunes_date_added": "2020-01-01T00:00:00Z", "itunes_play_count": 0, "itunes_skip_count": 0},
            {"itunes_date_added": "2018-05-15T00:00:00Z", "itunes_play_count": 0, "itunes_skip_count": 0},
        ])
        assert merged["itunes_date_added"] == "2018-05-15T00:00:00Z"

    def test_latest_last_played(self) -> None:
        merged = _merge_apple_blocks([
            {"itunes_last_played": "2020-01-01T00:00:00Z", "itunes_play_count": 0, "itunes_skip_count": 0},
            {"itunes_last_played": "2023-05-15T00:00:00Z", "itunes_play_count": 0, "itunes_skip_count": 0},
        ])
        assert merged["itunes_last_played"] == "2023-05-15T00:00:00Z"

    def test_explicit_or(self) -> None:
        merged = _merge_apple_blocks([
            {"explicit": False, "itunes_play_count": 0, "itunes_skip_count": 0},
            {"explicit": True, "itunes_play_count": 0, "itunes_skip_count": 0},
        ])
        assert merged["explicit"] is True
