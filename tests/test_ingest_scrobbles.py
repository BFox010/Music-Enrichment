"""Tests for pipeline.ingest_scrobbles.parse_raw_scrobble."""

from __future__ import annotations

from pipeline.ingest_scrobbles import parse_raw_scrobble

# Verified timestamps (UTC):
# 1730606040 = 2024-11-03T03:54:00Z  (November → fall, Sunday)
# 1705320000 = 2024-01-15T12:00:00Z  (January  → winter)
# 1713139200 = 2024-04-15T00:00:00Z  (April    → spring)
# 1721001600 = 2024-07-15T00:00:00Z  (July     → summer)


def _make_record(
    artist: str = "Portishead",
    track: str = "Roads",
    album: str = "Dummy",
    uts: str = "1730606040",
) -> dict:
    return {
        "artist": {"mbid": "", "#text": artist},
        "name": track,
        "album": {"mbid": "", "#text": album},
        "date": {"uts": uts, "#text": "03 Nov 2024, 03:54"},
        "streamable": "0",
        "image": [],
        "mbid": "",
        "url": "",
    }


class TestParseRawScrobble:
    def test_basic_parse(self) -> None:
        row = parse_raw_scrobble(_make_record())
        assert row is not None
        assert row["artist"] == "Portishead"
        assert row["track"] == "Roads"
        assert row["album"] == "Dummy"
        assert row["artist_normalized"] == "portishead"
        assert row["track_normalized"] == "roads"

    def test_scrobbled_at_utc_format(self) -> None:
        row = parse_raw_scrobble(_make_record(uts="1730606040"))
        assert row is not None
        assert row["scrobbled_at"] == "2024-11-03T03:54:00Z"
        assert row["year"] == 2024
        assert row["month"] == 11
        assert row["hour"] == 3

    def test_day_of_week_sunday(self) -> None:
        # 2024-11-03 is a Sunday → weekday() == 6
        row = parse_raw_scrobble(_make_record(uts="1730606040"))
        assert row is not None
        assert row["day_of_week"] == 6

    def test_season_fall(self) -> None:
        row = parse_raw_scrobble(_make_record(uts="1730606040"))  # November
        assert row is not None
        assert row["season"] == "fall"

    def test_season_winter(self) -> None:
        row = parse_raw_scrobble(_make_record(uts="1705320000"))  # January
        assert row is not None
        assert row["season"] == "winter"

    def test_season_spring(self) -> None:
        row = parse_raw_scrobble(_make_record(uts="1713139200"))  # April
        assert row is not None
        assert row["season"] == "spring"

    def test_season_summer(self) -> None:
        row = parse_raw_scrobble(_make_record(uts="1721001600"))  # July
        assert row is not None
        assert row["season"] == "summer"

    def test_nowplaying_no_date_returns_none(self) -> None:
        record = _make_record()
        del record["date"]
        assert parse_raw_scrobble(record) is None

    def test_empty_uts_returns_none(self) -> None:
        record = _make_record()
        record["date"] = {"uts": "", "#text": ""}
        assert parse_raw_scrobble(record) is None

    def test_missing_artist_returns_none(self) -> None:
        record = _make_record()
        record["artist"] = {"#text": "", "mbid": ""}
        assert parse_raw_scrobble(record) is None

    def test_missing_track_returns_none(self) -> None:
        record = _make_record()
        record["name"] = ""
        assert parse_raw_scrobble(record) is None

    def test_empty_album_is_ok(self) -> None:
        row = parse_raw_scrobble(_make_record(album=""))
        assert row is not None
        assert row["album"] == ""

    def test_normalization_applied(self) -> None:
        row = parse_raw_scrobble(_make_record(artist="The Beatles", track="Don't Stop"))
        assert row is not None
        assert row["artist_normalized"] == "beatles"
        assert row["track_normalized"] == "dont stop"

    def test_diacritics_normalized(self) -> None:
        row = parse_raw_scrobble(_make_record(artist="Sigur Rós", track="Hoppípolla"))
        assert row is not None
        assert row["artist_normalized"] == "sigur ros"
        assert row["track_normalized"] == "hoppipolla"
