"""Tests for pipeline.ingest_scrobbles."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from pipeline.ingest_scrobbles import (
    ScrobbleShrinkError,
    _parse_args,
    ingest,
    ingest_from_records,
    parse_raw_scrobble,
)

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


class TestIngestFromRecords:
    def _rec(self, uts="1730606040", artist="Portishead", track="Roads"):
        return {
            "artist": {"#text": artist, "mbid": ""},
            "name": track,
            "album": {"#text": "Dummy", "mbid": ""},
            "date": {"uts": uts, "#text": ""},
        }

    def test_replace_writes_all(self) -> None:
        records = [self._rec("1730606040"), self._rec("1705320000")]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            n = ingest_from_records(records, output_path=out, mode="replace")
            assert n == 2
            lines = [json.loads(l) for l in out.read_text().splitlines() if l]
            assert len(lines) == 2

    def test_append_adds_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            # First write
            ingest_from_records([self._rec("1730606040")], output_path=out, mode="replace")
            # Append a new scrobble
            n = ingest_from_records([self._rec("1705320000")], output_path=out, mode="append")
            assert n == 2

    def test_append_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec("1730606040")], output_path=out, mode="replace")
            # Append the same record again
            n = ingest_from_records([self._rec("1730606040")], output_path=out, mode="append")
            assert n == 1  # still only one row

    def test_append_sorts_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec("1730606040")], output_path=out, mode="replace")
            ingest_from_records([self._rec("1705320000")], output_path=out, mode="append")
            lines = [json.loads(l) for l in out.read_text().splitlines() if l]
            timestamps = [l["scrobbled_at"] for l in lines]
            assert timestamps == sorted(timestamps)

    def test_skips_malformed_records(self) -> None:
        records = [self._rec(), {"name": "", "artist": {"#text": ""}}]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            n = ingest_from_records(records, output_path=out, mode="replace")
            assert n == 1


# ── Shrink guard: scrobbles.jsonl is the base record; never lose rows ──


@contextmanager
def _run_log():
    """Yield a run-log path outside the fixture directory, then close it.

    ``configure_logging`` leaves a FileHandler open on the path it is given, and
    on Windows that open handle blocks TemporaryDirectory cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        yield Path(path)
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                root.removeHandler(handler)
        os.unlink(path)


def _export_file(path: Path, records: list[dict]) -> Path:
    """Write records as a Last.fm export (list-of-pages) at `path`."""
    path.write_text(json.dumps([records]), encoding="utf-8")
    return path


class TestShrinkGuard:
    def _rec(self, uts, artist="Portishead", track="Roads"):
        return {
            "artist": {"#text": artist, "mbid": ""},
            "name": track,
            "album": {"#text": "Dummy", "mbid": ""},
            "date": {"uts": str(uts), "#text": ""},
        }

    def _seed(self, out: Path, n: int) -> None:
        ingest_from_records(
            [self._rec(1700000000 + i) for i in range(n)],
            output_path=out, mode="replace",
        )

    def test_shrinking_replace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 5)
            with pytest.raises(ScrobbleShrinkError) as exc:
                ingest_from_records([self._rec(1800000000)], output_path=out,
                                    mode="replace")
            msg = str(exc.value)
            assert "5" in msg and "1" in msg and "--allow-shrink" in msg

    def test_shrinking_replace_leaves_the_file_untouched(self) -> None:
        # The guard must abort *before* the write, not half-way through it.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 5)
            before = out.read_text(encoding="utf-8")
            with pytest.raises(ScrobbleShrinkError):
                ingest_from_records([self._rec(1800000000)], output_path=out,
                                    mode="replace")
            assert out.read_text(encoding="utf-8") == before
            assert not (Path(tmp) / "s.jsonl.tmp").exists()

    def test_allow_shrink_permits_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 5)
            n = ingest_from_records([self._rec(1800000000)], output_path=out,
                                    mode="replace", allow_shrink=True)
            assert n == 1

    def test_equal_count_is_not_a_shrink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 3)
            n = ingest_from_records([self._rec(1900000000 + i) for i in range(3)],
                                    output_path=out, mode="replace")
            assert n == 3

    def test_growing_write_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 2)
            n = ingest_from_records([self._rec(1900000000 + i) for i in range(4)],
                                    output_path=out, mode="replace")
            assert n == 4

    def test_first_write_to_a_missing_file_is_never_a_shrink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            assert ingest_from_records([self._rec(1700000000)], output_path=out) == 1

    def test_corrupt_existing_line_trips_the_guard(self) -> None:
        # append silently drops unparseable rows; that is data loss too.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            self._seed(out, 3)
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("{corrupt\n")
            with pytest.raises(ScrobbleShrinkError):
                ingest_from_records([self._rec(1700000000)], output_path=out,
                                    mode="append")


class TestIngestDefaultsToAppend:
    """The bug: ingest() hard-coded mode="replace", so a partial export —
    the documented owner workflow — silently truncated the whole history."""

    def _rec(self, uts):
        return {
            "artist": {"#text": "Portishead", "mbid": ""},
            "name": "Roads",
            "album": {"#text": "Dummy", "mbid": ""},
            "date": {"uts": str(uts), "#text": ""},
        }

    def test_ingest_from_records_defaults_to_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec(1700000000)], output_path=out, mode="replace")
            assert ingest_from_records([self._rec(1800000000)], output_path=out) == 2

    def test_partial_export_preserves_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            out = tmp_p / "s.jsonl"
            # An established history …
            ingest_from_records([self._rec(1700000000 + i * 3600) for i in range(10)],
                                output_path=out, mode="replace")
            before = {json.loads(l)["scrobbled_at"]
                      for l in out.read_text(encoding="utf-8").splitlines() if l}

            # … then a partial export covering only the newest window.
            export = _export_file(tmp_p / "export.json",
                                  [self._rec(1800000000), self._rec(1800003600)])
            with _run_log() as logp:
                n = ingest(export_path=export, output_path=out, run_log_path=logp)

            after = {json.loads(l)["scrobbled_at"]
                     for l in out.read_text(encoding="utf-8").splitlines() if l}
            assert n == 12
            assert before <= after, "pre-existing scrobbles were lost"

    def test_ingest_replace_mode_is_still_shrink_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            out = tmp_p / "s.jsonl"
            ingest_from_records([self._rec(1700000000 + i) for i in range(10)],
                                output_path=out, mode="replace")
            export = _export_file(tmp_p / "export.json", [self._rec(1800000000)])
            with _run_log() as logp:
                with pytest.raises(ScrobbleShrinkError):
                    ingest(export_path=export, output_path=out,
                           run_log_path=logp, mode="replace")


class TestDedupeSurvivesNormalizationChange:
    """The dedupe key is derived, so our own code can redefine it.

    ``normalize_artist`` gaining `&` → "and" (#27) meant every scrobble already
    on disk stopped matching its own re-export: 1102 historical plays across 104
    artists were appended a second time, inflating play_count and splitting 28
    recordings into two rows. Recomputing the key on both sides is what makes a
    normalization change safe.
    """

    def _rec(self, uts: int, artist: str = "Of Mice & Men"):
        return {
            "artist": {"#text": artist, "mbid": ""},
            "name": "O.G. Loko",
            "album": {"#text": "The Flood", "mbid": ""},
            "date": {"uts": str(uts), "#text": ""},
        }

    def _write(self, path: Path, rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_stale_normalization_does_not_re_add_the_play(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec(1700000000)], output_path=out, mode="replace")

            # Simulate history written before normalize_artist changed: the raw
            # fields are untouched, only the derived key is from the old rules.
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
            assert rows[0]["artist_normalized"] == "of mice and men"
            rows[0]["artist_normalized"] = "of mice men"
            self._write(out, rows)

            # Re-delivering the same play must not append a duplicate.
            total = ingest_from_records([self._rec(1700000000)], output_path=out)
            assert total == 1, "the same play was ingested twice"

    def test_stale_rows_are_healed_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec(1700000000)], output_path=out, mode="replace")
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
            rows[0]["artist_normalized"] = "of mice men"
            self._write(out, rows)

            ingest_from_records([self._rec(1700003600)], output_path=out)

            healed = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
            assert len(healed) == 2
            assert {r["artist_normalized"] for r in healed} == {"of mice and men"}, (
                "the stale key should be re-derived, not left to split the track"
            )

    def test_a_repeated_play_inside_one_export_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec(1700000000)], output_path=out, mode="replace")
            total = ingest_from_records(
                [self._rec(1700000000), self._rec(1700000000)], output_path=out
            )
            assert total == 1

    def test_genuinely_new_plays_still_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.jsonl"
            ingest_from_records([self._rec(1700000000)], output_path=out, mode="replace")
            assert ingest_from_records([self._rec(1700007200)], output_path=out) == 2


class TestCliArgs:
    def test_default_is_append_without_shrink(self) -> None:
        args = _parse_args([])
        assert not args.replace and not args.allow_shrink

    def test_replace_flag(self) -> None:
        assert _parse_args(["--replace"]).replace

    def test_allow_shrink_flag(self) -> None:
        assert _parse_args(["--allow-shrink"]).allow_shrink


# ── Chain: partial ingest → dedupe → play-count integrity ──


class TestIntegrityAfterPartialExport:
    """Issue #38 AC 3. Phase 2 derives play_count by counting scrobbles, so a
    truncated log produces confidently wrong counts that every play-weighted
    chart inherits. Run the real chain and assert the invariant holds."""

    def _rec(self, uts, artist="Portishead", track="Roads"):
        return {
            "artist": {"#text": artist, "mbid": ""},
            "name": track,
            "album": {"#text": "Dummy", "mbid": ""},
            "date": {"uts": str(uts), "#text": ""},
        }

    def test_in_sync_after_a_partial_export_run(self) -> None:
        import app.data as data
        from app import metrics
        from pipeline.dedupe import dedupe

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            scrobbles = tmp_p / "scrobbles.jsonl"
            skeleton = tmp_p / "tracks_skeleton.jsonl"

            # An established history: 6 plays of Roads, 3 of Glory Box.
            history = ([self._rec(1700000000 + i * 3600) for i in range(6)]
                       + [self._rec(1700100000 + i * 3600, track="Glory Box")
                          for i in range(3)])
            ingest_from_records(history, output_path=scrobbles, mode="replace")

            with _run_log() as logp:
                dedupe(scrobbles_path=scrobbles, output_path=skeleton,
                       run_log_path=logp)
            with data.use_paths(skeleton, scrobbles):
                assert metrics.play_count_integrity()["in_sync"]
                before_total = metrics.play_count_integrity()["actual_total"]
            assert before_total == 9

            # A partial export: only the newest window, nothing from the history.
            export = _export_file(
                tmp_p / "export.json",
                [self._rec(1800000000), self._rec(1800003600, track="Glory Box")],
            )
            with _run_log() as logp:
                total = ingest(export_path=export, output_path=scrobbles,
                               run_log_path=logp)
            assert total == 11, "partial export must add to the history, not replace it"

            with _run_log() as logp:
                dedupe(scrobbles_path=scrobbles, output_path=skeleton,
                       run_log_path=logp)
            with data.use_paths(skeleton, scrobbles):
                report = metrics.play_count_integrity()
            assert report["in_sync"], report["worst"]
            assert report["actual_total"] == 11
            assert report["unmatched_scrobbles"] == 0
