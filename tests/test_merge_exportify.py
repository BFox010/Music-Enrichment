"""Tests for pipeline.merge_exportify."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import pytest

from pipeline.merge_exportify import (
    _b,
    _check_energy_bug,
    _f,
    _i,
    _spotify_id_from_uri,
    _year_from_date,
    merge,
    parse_exportify_row,
)


class TestParseHelpers:
    def test_float_basic(self) -> None:
        assert _f("0.531") == 0.531
        assert _f("0") == 0.0
        assert _f("") is None
        assert _f(None) is None
        assert _f("garbage") is None

    def test_int_basic(self) -> None:
        assert _i("42") == 42
        assert _i("42.7") == 42  # truncates float-strings
        assert _i("") is None
        assert _i(None) is None

    def test_bool(self) -> None:
        assert _b("true") is True
        assert _b("True") is True
        assert _b("1") is True
        assert _b("false") is False
        assert _b("0") is False
        assert _b("") is None
        assert _b(None) is None
        assert _b("maybe") is None

    def test_year_from_date(self) -> None:
        assert _year_from_date("2015") == 2015
        assert _year_from_date("2015-07") == 2015
        assert _year_from_date("2015-07-17") == 2015
        assert _year_from_date("") is None
        assert _year_from_date(None) is None

    def test_spotify_id_from_uri(self) -> None:
        assert _spotify_id_from_uri("spotify:track:abc123") == "abc123"
        assert _spotify_id_from_uri("") is None
        assert _spotify_id_from_uri("not a uri") is None
        # Bare 22-char alphanumeric ID is also accepted
        assert _spotify_id_from_uri("3n3Ppam7vgaVa1iaRUc9Lp") == "3n3Ppam7vgaVa1iaRUc9Lp"


class TestParseExportifyRow:
    def test_basic_row(self) -> None:
        row = {
            "Track URI": "spotify:track:abc123",
            "Track Name": "Roads",
            "Artist Name(s)": "Portishead",
            "Album Name": "Dummy",
            "Album Release Date": "1994-08-22",
            "Track Duration (ms)": "305000",
            "Explicit": "false",
            "ISRC": "GBABC9412345",
            "Danceability": "0.421",
            "Energy": "0.448",
            "Valence": "0.189",
            "Tempo": "98.4",
            "Loudness": "-11.2",
            "Speechiness": "0.031",
            "Acousticness": "0.612",
            "Instrumentalness": "0.002",
            "Liveness": "0.089",
            "Key": "4",
            "Mode": "1",
            "Time Signature": "4",
        }
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "portishead"
        assert block["track_normalized"] == "roads"
        assert block["spotify_id"] == "abc123"
        assert block["duration_ms"] == 305000
        assert block["explicit"] is False
        assert block["isrc"] == "GBABC9412345"
        assert block["release_year"] == 1994

        af = block["audio_features"]
        assert af["danceability"] == 0.421
        assert af["energy"] == 0.448
        assert af["valence"] == 0.189
        assert af["tempo"] == 98.4
        assert af["key"] == 4
        assert af["mode"] == 1
        assert af["time_signature"] == 4
        assert af["source"] == "exportify"

    def test_first_artist_used_for_match(self) -> None:
        row = {
            "Track URI": "spotify:track:x",
            "Track Name": "Jumpman",
            "Artist Name(s)": "Drake, Future",
            "Energy": "0.5",
        }
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "drake"

    def test_semicolon_artist_separator_exportify_convention(self) -> None:
        # Real Exportify CSVs use ';' between artists per v1 spec
        row = {
            "Track Name": "Jumpman",
            "Artist Name(s)": "Drake;Future",
            "Energy": "0.5",
        }
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "drake"

    def test_semicolon_with_spaces(self) -> None:
        row = {
            "Track Name": "Jumpman",
            "Artist Name(s)": "Drake; Future; Migos",
            "Energy": "0.5",
        }
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "drake"

    def test_missing_track_returns_none(self) -> None:
        assert parse_exportify_row({"Artist Name(s)": "x"}) is None

    def test_missing_artist_returns_none(self) -> None:
        assert parse_exportify_row({"Track Name": "x"}) is None

    def test_alternate_column_names(self) -> None:
        row = {"Name": "Roads", "Artist": "Portishead", "Energy": "0.5"}
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "portishead"

    def test_no_audio_features_present_is_none(self) -> None:
        row = {
            "Track Name": "x", "Artist Name(s)": "y",
            "Energy": "", "Danceability": "", "Tempo": "",
        }
        block = parse_exportify_row(row)
        assert block is not None
        # No features present at all → audio_features is None (don't pollute schema)
        assert block["audio_features"] is None

    def test_partial_audio_features_keeps_block(self) -> None:
        row = {"Track Name": "x", "Artist Name(s)": "y", "Energy": "0.5"}
        block = parse_exportify_row(row)
        assert block is not None
        assert block["audio_features"] is not None
        assert block["audio_features"]["energy"] == 0.5
        assert block["audio_features"]["danceability"] is None
        assert block["audio_features"]["source"] == "exportify"

    def test_tunemymusic_column_names(self) -> None:
        # Spotify Library export from TuneMyMusic uses "Track name" / "Artist name" / "Spotify - id"
        row = {
            "Track name": "Roads",
            "Artist name": "Portishead",
            "Album": "Dummy",
            "Playlist name": "Scrobbles 2026",
            "Type": "Playlist",
            "ISRC": "GBABC9412345",
            "Spotify - id": "5i5fCpsnqDJ9AfeObgd0gW",
        }
        block = parse_exportify_row(row)
        assert block is not None
        assert block["artist_normalized"] == "portishead"
        assert block["track_normalized"] == "roads"
        assert block["spotify_id"] == "5i5fCpsnqDJ9AfeObgd0gW"
        assert block["isrc"] == "GBABC9412345"
        assert block["audio_features"] is None  # no features in this format


class TestEnergyBug:
    def test_normal_energy_passes(self) -> None:
        rows = [{"audio_features": {"energy": e}} for e in [0.4, 0.5, 0.6, 0.45, 0.55]]
        median = _check_energy_bug(rows)
        assert median is not None
        assert 0.4 < median < 0.6

    def test_off_by_10_bug_aborts(self) -> None:
        rows = [{"audio_features": {"energy": e}} for e in [0.04, 0.05, 0.06, 0.045, 0.055]]
        with pytest.raises(ValueError, match="off-by-10"):
            _check_energy_bug(rows)

    def test_no_energy_returns_none(self) -> None:
        assert _check_energy_bug([]) is None
        assert _check_energy_bug([{"audio_features": {"energy": None}}]) is None


class TestMerge:
    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _load_jsonl(self, path: Path) -> list[dict]:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_merge_audio_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            csv_path = Path(tmp) / "exportify.csv"
            out = Path(tmp) / "with_audio.jsonl"

            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads"},
            ])
            self._write_csv(csv_path, [{
                "Track URI": "spotify:track:abc123",
                "Track Name": "Roads",
                "Artist Name(s)": "Portishead",
                "Track Duration (ms)": "305000",
                "Energy": "0.448",
                "Danceability": "0.421",
                "Tempo": "98.4",
            }])

            stats = merge(csv_path=csv_path, input_path=inp, output_path=out)
            assert stats["matched"] == 1
            row = self._load_jsonl(out)[0]
            assert row["spotify_id"] == "abc123"
            assert row["audio_features"]["energy"] == 0.448
            assert row["duration_ms"] == 305000

    def test_unmatched_logged_not_aborted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            csv_path = Path(tmp) / "exportify.csv"
            out = Path(tmp) / "with_audio.jsonl"

            self._write_jsonl(inp, [
                {"artist": "A", "track": "1", "artist_normalized": "a", "track_normalized": "1"},
                {"artist": "B", "track": "2", "artist_normalized": "b", "track_normalized": "2"},
                {"artist": "C", "track": "3", "artist_normalized": "c", "track_normalized": "3"},
            ])
            self._write_csv(csv_path, [{
                "Track URI": "spotify:track:x", "Track Name": "1", "Artist Name(s)": "A",
                "Energy": "0.5",
            }])

            stats = merge(csv_path=csv_path, input_path=inp, output_path=out)
            assert stats["total"] == 3
            assert stats["matched"] == 1
            assert stats["match_rate"] == pytest.approx(33.33, rel=1e-2)
