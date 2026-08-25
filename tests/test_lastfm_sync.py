"""Tests for app.lastfm_sync — mocks httpx, no network."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.lastfm_sync import fetch_recent_scrobbles, get_last_scrobble_ts


# ── helpers ──

def _raw_track(artist="Portishead", name="Roads", uts="1730606040"):
    return {
        "artist": {"#text": artist, "mbid": ""},
        "name": name,
        "album": {"#text": "Dummy", "mbid": ""},
        "date": {"uts": uts, "#text": "03 Nov 2024, 03:54"},
    }


def _api_page(tracks, page=1, total_pages=1):
    """Build a minimal Last.fm user.getRecentTracks JSON response."""
    return {
        "recenttracks": {
            "track": tracks,
            "@attr": {
                "user": "testuser",
                "page": str(page),
                "perPage": "200",
                "totalPages": str(total_pages),
                "total": str(len(tracks)),
            },
        }
    }


def _mock_client(pages: list[dict]):
    """Return a mock httpx.AsyncClient whose get() yields pages in order."""
    responses = []
    for page_body in pages:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=page_body)
        responses.append(mock_resp)

    mock_get = AsyncMock(side_effect=responses)
    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ── TestGetLastScrobbleTs ──

class TestGetLastScrobbleTs:
    def test_empty_returns_zero(self):
        assert get_last_scrobble_ts([]) == 0

    def test_single_scrobble(self):
        scrobbles = [{"scrobbled_at": "2024-11-03T03:54:00Z"}]
        ts = get_last_scrobble_ts(scrobbles)
        assert ts == 1730606040

    def test_picks_most_recent(self):
        scrobbles = [
            {"scrobbled_at": "2024-01-15T12:00:00Z"},
            {"scrobbled_at": "2024-11-03T03:54:00Z"},
            {"scrobbled_at": "2024-04-15T00:00:00Z"},
        ]
        ts = get_last_scrobble_ts(scrobbles)
        assert ts == 1730606040  # 2024-11-03

    def test_missing_field_returns_zero(self):
        assert get_last_scrobble_ts([{"artist": "x"}]) == 0


# ── TestFetchRecentScrobbles ──

class TestFetchRecentScrobbles:
    def test_single_page(self):
        tracks = [_raw_track(), _raw_track("Massive Attack", "Teardrop", "1705320000")]
        page = _api_page(tracks)
        with patch("httpx.AsyncClient", return_value=_mock_client([page])):
            result = asyncio.run(
                fetch_recent_scrobbles("testuser", "fakekey", since_ts=0)
            )
        assert len(result) == 2

    def test_paginates_all_pages(self):
        page1 = _api_page([_raw_track()], page=1, total_pages=2)
        page2 = _api_page(
            [_raw_track("Massive Attack", "Teardrop", "1705320000")],
            page=2, total_pages=2,
        )
        with patch("httpx.AsyncClient", return_value=_mock_client([page1, page2])):
            result = asyncio.run(
                fetch_recent_scrobbles("testuser", "fakekey", since_ts=0)
            )
        assert len(result) == 2

    def test_skips_nowplaying_stubs(self):
        nowplaying = {
            "artist": {"#text": "Portishead", "mbid": ""},
            "name": "Roads",
            "@attr": {"nowplaying": "true"},
            # no "date" key
        }
        real = _raw_track()
        page = _api_page([nowplaying, real])
        with patch("httpx.AsyncClient", return_value=_mock_client([page])):
            result = asyncio.run(
                fetch_recent_scrobbles("testuser", "fakekey", since_ts=0)
            )
        assert len(result) == 1

    def test_handles_single_track_as_dict(self):
        """Last.fm returns a dict (not list) when there's exactly one track."""
        body = {
            "recenttracks": {
                "track": _raw_track(),  # dict, not list
                "@attr": {"page": "1", "totalPages": "1", "total": "1"},
            }
        }
        with patch("httpx.AsyncClient", return_value=_mock_client([body])):
            result = asyncio.run(
                fetch_recent_scrobbles("testuser", "fakekey", since_ts=0)
            )
        assert len(result) == 1

    def test_raises_on_api_error(self):
        error_body = {"error": 6, "message": "User not found"}
        with patch("httpx.AsyncClient", return_value=_mock_client([error_body])):
            with pytest.raises(RuntimeError, match="Last.fm API error 6"):
                asyncio.run(
                    fetch_recent_scrobbles("baduser", "fakekey", since_ts=0)
                )

    def test_caps_total_pages(self):
        """C1 — a huge totalPages must be capped, not looped unbounded."""
        # Each page reports 100 total pages; with the cap patched to 2, only 2
        # requests should be made (and only 2 mock responses are provided, so an
        # uncapped loop would StopIteration).
        pages = [
            _api_page([_raw_track(uts=str(1730606040 + i))], page=i + 1, total_pages=100)
            for i in range(2)
        ]
        client = _mock_client(pages)
        with patch("httpx.AsyncClient", return_value=client), \
                patch("app.lastfm_sync._MAX_PAGES", 2):
            result = asyncio.run(
                fetch_recent_scrobbles("testuser", "fakekey", since_ts=0)
            )
        assert client.get.call_count == 2
        assert len(result) == 2
