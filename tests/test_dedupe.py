"""Tests for pipeline.dedupe."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.dedupe import (
    _merge_by_export_mbid,
    _merge_skeleton_group,
    _most_common_value,
    _peak_year,
    dedupe,
)
from pipeline.normalize import normalize_artist, normalize_track


# ── helpers ──


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


# ── unit tests ──


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

    def test_most_common_value_tie_breaks_deterministically(self) -> None:
        """A tie must resolve the same way regardless of arrival order —
        insertion-order tie-breaking (Counter.most_common's default) let the
        same input set pick a different winner across runs, which could flip
        canonical_track_id for the musicbrainz_id field."""
        assert _most_common_value(["bbb", "aaa"]) == "aaa"
        assert _most_common_value(["aaa", "bbb"]) == "aaa"

    def test_peak_year_tie_breaks_deterministically(self) -> None:
        assert _peak_year([2023, 2022]) == 2022
        assert _peak_year([2022, 2023]) == 2022


def _skel(artist: str, track: str, *, play_count: int = 1,
          musicbrainz_id: str | None = None, artist_mbid: str | None = None,
          first_scrobbled: str = "2024-01-01", last_scrobbled: str = "2024-01-01") -> dict:
    return {
        "artist": artist,
        "track": track,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(track),
        "album": "",
        "musicbrainz_id": musicbrainz_id,
        "artist_mbid": artist_mbid,
        "play_count": play_count,
        "first_scrobbled": first_scrobbled,
        "last_scrobbled": last_scrobbled,
        "peak_year": 2024,
    }


class TestMergeByExportMbid:
    """Phase 2 merge on an export-supplied MusicBrainz ID (#43)."""

    def test_rows_without_a_shared_mbid_are_untouched(self) -> None:
        rows = [_skel("Clipse", "So Far Ahead"), _skel("Unrelated", "Other Song")]
        assert _merge_by_export_mbid(rows) == rows

    def test_rows_sharing_an_mbid_merge(self) -> None:
        rows = [
            _skel("Clipse", "So Far Ahead", play_count=2, musicbrainz_id="mbid-1"),
            _skel("Clipse, Pharrell Williams, Pusha T & Malice", "So Far Ahead",
                  play_count=22, musicbrainz_id="mbid-1"),
        ]
        merged = _merge_by_export_mbid(rows)
        assert len(merged) == 1
        assert merged[0]["play_count"] == 24

    def test_only_matching_mbids_merge_others_stay_separate(self) -> None:
        rows = [
            _skel("A", "X", musicbrainz_id="mbid-1"),
            _skel("A Featuring B", "X", musicbrainz_id="mbid-1"),
            _skel("C", "Y", musicbrainz_id="mbid-2"),
        ]
        merged = _merge_by_export_mbid(rows)
        assert len(merged) == 2

    def test_rows_without_an_mbid_never_merge_with_each_other(self) -> None:
        """A null musicbrainz_id must never act as a shared bucket key."""
        rows = [_skel("A", "X"), _skel("B", "Y")]
        assert len(_merge_by_export_mbid(rows)) == 2

    def test_merged_row_records_both_original_keys_as_aliases(self) -> None:
        """scrobbles.jsonl is never rewritten — without this, a play logged
        under the merged-away key would have nothing left to match in the
        app's scrobble→track index (app/metrics.py)."""
        rows = [
            _skel("Clipse", "So Far Ahead", play_count=2, musicbrainz_id="mbid-1"),
            _skel("Clipse Pharrell", "So Far Ahead", play_count=22, musicbrainz_id="mbid-1"),
        ]
        merged = _merge_by_export_mbid(rows)[0]
        assert [normalize_artist("Clipse"), normalize_track("So Far Ahead")] \
            in merged["identity_aliases"]
        assert [normalize_artist("Clipse Pharrell"), normalize_track("So Far Ahead")] \
            in merged["identity_aliases"]

    def test_scrobble_span_and_artist_mbid_are_combined(self) -> None:
        rows = [
            _skel("A", "X", play_count=2, musicbrainz_id="m", artist_mbid="am-1",
                  first_scrobbled="2021-01-01", last_scrobbled="2021-06-01"),
            _skel("A B", "X", play_count=5, musicbrainz_id="m", artist_mbid="am-1",
                  first_scrobbled="2020-01-01", last_scrobbled="2023-01-01"),
        ]
        merged = _merge_by_export_mbid(rows)[0]
        assert merged["first_scrobbled"] == "2020-01-01"
        assert merged["last_scrobbled"] == "2023-01-01"
        assert merged["artist_mbid"] == "am-1"


# ── integration tests ──


class TestDedupe:
    def test_single_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            _write_scrobbles(inp, [_make_scrobble()])
            n = dedupe(inp, out)
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
            _write_scrobbles(inp, [
                _make_scrobble(year=2022, scrobbled_at="2022-01-01T00:00:00Z"),
                _make_scrobble(year=2023, scrobbled_at="2023-06-15T12:00:00Z"),
                _make_scrobble(year=2023, scrobbled_at="2023-11-03T03:54:00Z"),
            ])
            n = dedupe(inp, out)
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
            _write_scrobbles(inp, [
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Glory Box"),
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Portishead", "Roads"),
            ])
            n = dedupe(inp, out)
            assert n == 3
            rows = _read_skeleton(out)
            keys = [(r["artist_normalized"], r["track_normalized"]) for r in rows]
            assert len(keys) == len(set(keys)), "Duplicate join keys in output"

    def test_output_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            _write_scrobbles(inp, [
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Arcade Fire", "Rebellion"),
            ])
            dedupe(inp, out)
            rows = _read_skeleton(out)
            artists = [r["artist_normalized"] for r in rows]
            assert artists == sorted(artists)

    def test_most_common_display_name(self) -> None:
        """Capitalisation drift across scrobbles → most common form wins."""
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            _write_scrobbles(inp, [
                _make_scrobble("portishead", "roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
            ])
            dedupe(inp, out)
            row = _read_skeleton(out)[0]
            assert row["artist"] == "Portishead"
            assert row["track"] == "Roads"

    def test_multiple_artists_correct_play_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            _write_scrobbles(inp, [
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Portishead", "Roads"),
                _make_scrobble("Radiohead", "Karma Police"),
                _make_scrobble("Radiohead", "Karma Police"),
            ])
            dedupe(inp, out)
            rows = {r["artist_normalized"]: r for r in _read_skeleton(out)}
            assert rows["portishead"]["play_count"] == 3
            assert rows["radiohead"]["play_count"] == 2

    def test_output_is_invariant_to_scrobble_order(self) -> None:
        """A tied musicbrainz_id vote must not depend on which scrobble
        happened to arrive first — that would flip canonical_track_id
        between runs and undermine every downstream identity join."""
        import random

        scrobbles = [
            _make_scrobble("Portishead", "Roads", scrobbled_at="2024-01-01T00:00:00Z"),
            _make_scrobble("Portishead", "Roads", scrobbled_at="2024-01-02T00:00:00Z"),
        ]
        scrobbles[0]["musicbrainz_id"] = "bbb-mbid"
        scrobbles[1]["musicbrainz_id"] = "aaa-mbid"

        outputs = []
        for seed in range(5):
            shuffled = list(scrobbles)
            random.Random(seed).shuffle(shuffled)
            with tempfile.TemporaryDirectory() as tmp:
                inp = Path(tmp) / "scrobbles.jsonl"
                out = Path(tmp) / "skeleton.jsonl"
                _write_scrobbles(inp, shuffled)
                dedupe(inp, out)
                outputs.append(out.read_text(encoding="utf-8"))

        assert len(set(outputs)) == 1, "Shuffled scrobble order changed the output"

    def test_scrobbles_sharing_an_export_mbid_merge_at_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scrobbles.jsonl"
            out = Path(tmp) / "skeleton.jsonl"
            clipse = _make_scrobble("Clipse", "So Far Ahead")
            clipse["musicbrainz_id"] = "mbid-1"
            clipse_full = _make_scrobble(
                "Clipse, Pharrell Williams, Pusha T & Malice", "So Far Ahead"
            )
            clipse_full["musicbrainz_id"] = "mbid-1"
            _write_scrobbles(inp, [clipse, clipse_full])
            n = dedupe(inp, out)
            assert n == 1
            row = _read_skeleton(out)[0]
            assert row["play_count"] == 2
            assert [normalize_artist("Clipse"), normalize_track("So Far Ahead")] \
                in row["identity_aliases"]
