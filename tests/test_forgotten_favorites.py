"""Forgotten Favorites: qualification rule, per-row evidence, and route bounds (#35).

The page states this rule to the viewer, so these tests pin the code to it.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

import app.data as data
from app import metrics
from app.main import app


def _scrobble(artist, track, stamp):
    return {
        "artist": artist,
        "track": track,
        "artist_normalized": artist.lower(),
        "track_normalized": track.lower(),
        "scrobbled_at": f"{stamp}T12:00:00Z",
        "year": int(stamp[:4]),
        "month": int(stamp[5:7]),
    }


def _plays(artist, track, stamp, n):
    return [_scrobble(artist, track, stamp) for _ in range(n)]


@contextmanager
def _library(scrobbles, tracks=()):
    with tempfile.TemporaryDirectory() as tmp:
        tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
        tp.write_text("".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8")
        sp.write_text("".join(json.dumps(s) + "\n" for s in scrobbles), encoding="utf-8")
        with data.use_paths(tp, sp):
            yield


def _by_track(rows):
    return {r["track"]: r for r in rows}


class TestQualification:
    def test_window_anchors_to_last_scrobble_year_not_today(self):
        """A library whose history ends in 2019 must not read as all-forgotten:
        the window is 2018-2019, so a track still played in 2019 stays out."""
        scrobbles = (
            _plays("A", "Faded", "2015-03-01", 8)
            + _plays("B", "Still On", "2015-03-01", 8)
            + _plays("B", "Still On", "2019-05-01", 8)
        )
        with _library(scrobbles):
            rows = _by_track(metrics.forgotten_favorites(min_peak=5, recent_years=2))
        assert set(rows) == {"Faded"}
        assert rows["Faded"]["recent_start"] == 2018
        assert rows["Faded"]["recent_end"] == 2019
        assert rows["Faded"]["as_of"] == "2019-05-01"

    def test_peak_below_min_peak_is_excluded(self):
        scrobbles = _plays("A", "Small", "2015-01-01", 4) + _plays("Z", "Anchor", "2020-01-01", 1)
        with _library(scrobbles):
            assert metrics.forgotten_favorites(min_peak=5) == []
            assert [r["track"] for r in metrics.forgotten_favorites(min_peak=4)] == ["Small"]

    def test_recent_years_widens_the_window(self):
        scrobbles = _plays("A", "Mid", "2017-01-01", 6) + _plays("Z", "Anchor", "2020-01-01", 1)
        with _library(scrobbles):
            assert [r["track"] for r in metrics.forgotten_favorites(recent_years=2)] == ["Mid"]
            # 2017 falls inside a four-year window ending 2020, so it is not "before" it.
            assert metrics.forgotten_favorites(recent_years=4) == []

    def test_fade_floor_is_compared_unrounded(self):
        """399 / 200 = 1.995 rounds to 2.0 but is still under the 2x floor."""
        scrobbles = (
            _plays("A", "Edge", "2015-01-01", 399)
            + _plays("A", "Edge", "2020-01-01", 200)
            + _plays("A", "Clear", "2015-01-01", 400)
            + _plays("A", "Clear", "2020-01-01", 200)
        )
        with _library(scrobbles):
            rows = _by_track(metrics.forgotten_favorites(min_peak=5, recent_years=1))
        assert set(rows) == {"Clear"}
        assert rows["Clear"]["score"] == 2.0

    def test_tied_peak_years_pick_the_later_year_in_any_file_order(self):
        tied = _plays("A", "Tied", "2014-01-01", 6) + _plays("A", "Tied", "2016-01-01", 6)
        anchor = _plays("Z", "Anchor", "2020-01-01", 1)
        for order in (tied, list(reversed(tied))):
            with _library(order + anchor):
                (row,) = metrics.forgotten_favorites(min_peak=5)
            assert row["peak_year"] == 2016

    def test_sorted_by_score_then_peak_plays(self):
        scrobbles = (
            _plays("A", "Ten", "2015-01-01", 10)
            + _plays("A", "Six", "2015-01-01", 6)
            + _plays("A", "Twelve-half", "2015-01-01", 12)
            + _plays("A", "Twelve-half", "2020-01-01", 2)
        )
        with _library(scrobbles):
            rows = metrics.forgotten_favorites(min_peak=5, recent_years=2)
        # Twelve-half and Six tie on fade (6x); the bigger peak goes first.
        assert [r["track"] for r in rows] == ["Ten", "Twelve-half", "Six"]
        assert [r["score"] for r in rows] == [10.0, 6.0, 6.0]


class TestEvidence:
    def test_row_carries_last_played_and_silence(self):
        scrobbles = (
            _plays("A", "Gone", "2016-02-01", 7)
            + [_scrobble("A", "Gone", "2017-03-10")]
            + _plays("Z", "Anchor", "2020-01-01", 1)
            + [_scrobble("Z", "Anchor", "2020-06-30")]
        )
        with _library(scrobbles):
            (row,) = metrics.forgotten_favorites(min_peak=5)
        assert row["peak_year"] == 2016
        assert row["peak_plays"] == 7
        assert row["recent_plays"] == 0
        assert row["last_heard"] == 2017
        assert row["last_played"] == "2017-03-10"
        assert row["as_of"] == "2020-06-30"
        # Silence is measured to the data's last scrobble, not to today.
        assert row["silence_days"] == 1208

    def test_missing_timestamps_leave_evidence_null_not_crash(self):
        scrobbles = (
            [{"artist": "A", "track": "Old", "year": 2015}] * 6
            + [{"artist": "Z", "track": "Anchor", "year": 2020}]
        )
        with _library(scrobbles):
            (row,) = metrics.forgotten_favorites(min_peak=5)
        assert row["last_played"] is None
        assert row["silence_days"] is None
        assert row["as_of"] is None
        assert row["recent_start"] == 2019


class TestRoute:
    def test_thresholds_reach_the_metric(self):
        scrobbles = _plays("A", "Small", "2015-01-01", 4) + _plays("Z", "Anchor", "2020-01-01", 1)
        with _library(scrobbles):
            client = TestClient(app)
            assert client.get("/api/forgotten-favorites").json() == []
            rows = client.get("/api/forgotten-favorites?min_peak=4&recent_years=1").json()
        assert [r["track"] for r in rows] == ["Small"]

    def test_out_of_range_thresholds_are_rejected(self):
        client = TestClient(app)
        for qs in ("min_peak=0", "min_peak=101", "recent_years=0", "recent_years=6", "top=0"):
            assert client.get(f"/api/forgotten-favorites?{qs}").status_code == 422, qs
