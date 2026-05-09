"""Tests for pipeline.enrich_metadata._extract_lastfm_fields.

Note: tests do NOT hit the Last.fm API. They only exercise pure response parsing.
"""

from __future__ import annotations

from pipeline.enrich_metadata import _extract_lastfm_fields


class TestExtractLastfmFields:
    def test_full_response(self) -> None:
        response = {
            "track": {
                "name": "Roads",
                "mbid": "abc-123",
                "artist": {"name": "Portishead", "mbid": "def-456"},
                "toptags": {
                    "tag": [
                        {"name": "trip hop", "url": "..."},
                        {"name": "90s", "url": "..."},
                        {"name": "melancholic", "url": "..."},
                    ]
                },
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["trip hop", "90s", "melancholic"]
        assert out["musicbrainz_id"] == "abc-123"
        assert out["artist_mbid"] == "def-456"

    def test_single_tag_as_dict(self) -> None:
        # Last.fm sometimes returns a dict instead of a list when there's only one tag
        response = {
            "track": {
                "mbid": "x",
                "artist": {"mbid": "y"},
                "toptags": {"tag": {"name": "rock", "url": "..."}},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["rock"]

    def test_missing_toptags(self) -> None:
        response = {"track": {"mbid": "x", "artist": {"mbid": "y"}}}
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == []
        assert out["musicbrainz_id"] == "x"

    def test_missing_mbids(self) -> None:
        response = {"track": {"toptags": {"tag": []}, "artist": {}}}
        out = _extract_lastfm_fields(response)
        assert out["musicbrainz_id"] is None
        assert out["artist_mbid"] is None

    def test_empty_string_mbid_becomes_none(self) -> None:
        response = {"track": {"mbid": "", "artist": {"mbid": ""}}}
        out = _extract_lastfm_fields(response)
        assert out["musicbrainz_id"] is None
        assert out["artist_mbid"] is None

    def test_error_response(self) -> None:
        response = {"_error": "not_found"}
        out = _extract_lastfm_fields(response)
        assert out == {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}

    def test_garbage_response(self) -> None:
        # Not even a dict
        out = _extract_lastfm_fields("nope")  # type: ignore[arg-type]
        assert out == {"lastfm_tags": [], "musicbrainz_id": None, "artist_mbid": None}

    def test_tag_without_name_skipped(self) -> None:
        response = {
            "track": {
                "toptags": {"tag": [{"name": "ok"}, {"url": "no-name"}, {"name": ""}]},
                "artist": {},
            }
        }
        out = _extract_lastfm_fields(response)
        assert out["lastfm_tags"] == ["ok"]
