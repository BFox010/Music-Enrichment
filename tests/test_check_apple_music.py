"""Tests for pipeline.check_apple_music helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline.check_apple_music import _best_match, _is_stale


class TestBestMatch:
    def test_exact_match_found(self) -> None:
        response = {
            "results": [
                {"artistName": "Some Other Artist", "trackName": "Roads", "trackId": 1},
                {"artistName": "Portishead", "trackName": "Roads", "trackId": 999},
            ]
        }
        match = _best_match(response, "portishead", "roads")
        assert match is not None
        assert match["trackId"] == 999

    def test_no_match_returns_none(self) -> None:
        response = {"results": [{"artistName": "Foo", "trackName": "Bar", "trackId": 1}]}
        assert _best_match(response, "portishead", "roads") is None

    def test_empty_results(self) -> None:
        assert _best_match({"results": []}, "x", "y") is None

    def test_normalization_match(self) -> None:
        # iTunes returns "The Beatles" with leading "the" — should normalize equal
        response = {"results": [{"artistName": "The Beatles", "trackName": "The End", "trackId": 5}]}
        match = _best_match(response, "beatles", "the end")
        assert match is not None
        assert match["trackId"] == 5

    def test_diacritics_match(self) -> None:
        response = {"results": [{"artistName": "Beyoncé", "trackName": "Halo", "trackId": 7}]}
        match = _best_match(response, "beyonce", "halo")
        assert match is not None

    def test_error_response(self) -> None:
        assert _best_match({"_error": "not_found"}, "x", "y") is None

    def test_non_dict_response(self) -> None:
        assert _best_match(None, "x", "y") is None  # type: ignore[arg-type]
        assert _best_match([], "x", "y") is None  # type: ignore[arg-type]

    def test_skips_non_dict_results(self) -> None:
        response = {"results": ["garbage", None, {"artistName": "X", "trackName": "Y", "trackId": 1}]}
        match = _best_match(response, "x", "y")
        assert match is not None
        assert match["trackId"] == 1


class TestIsStale:
    def test_none_is_stale(self) -> None:
        assert _is_stale(None) is True

    def test_invalid_format_is_stale(self) -> None:
        assert _is_stale("not-a-date") is True

    def test_recent_is_fresh(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        assert _is_stale(recent) is False

    def test_old_is_stale(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d")
        assert _is_stale(old) is True
