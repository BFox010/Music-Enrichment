"""Tests for pipeline.enrich_discogs pure helpers.

Note: tests do NOT hit the Discogs API. They only exercise response parsing
and query-building, mirroring the test_enrich_metadata approach.
"""

from __future__ import annotations

from pipeline.enrich_discogs import _extract_discogs_styles, _query_for_track


class TestExtractDiscogsStyles:
    def test_first_result_with_styles(self) -> None:
        response = {
            "results": [
                {"title": "Artist - Album", "genre": ["Hip Hop"],
                 "style": ["Conscious", "Boom Bap"]},
                {"title": "Other", "style": ["Trap"]},
            ]
        }
        assert _extract_discogs_styles(response) == ["Conscious", "Boom Bap"]

    def test_skips_results_without_styles(self) -> None:
        response = {
            "results": [
                {"title": "no styles here", "genre": ["Rock"]},
                {"title": "has styles", "style": ["Shoegaze", "Dream Pop"]},
            ]
        }
        assert _extract_discogs_styles(response) == ["Shoegaze", "Dream Pop"]

    def test_dedupes_preserving_order(self) -> None:
        response = {"results": [{"style": ["House", "House", "Techno", "House"]}]}
        assert _extract_discogs_styles(response) == ["House", "Techno"]

    def test_caps_at_max_styles(self) -> None:
        many = [f"s{i}" for i in range(20)]
        response = {"results": [{"style": many}]}
        out = _extract_discogs_styles(response, max_styles=3)
        assert out == ["s0", "s1", "s2"]

    def test_filters_non_string_and_blank_styles(self) -> None:
        response = {"results": [{"style": ["Jazz", "", "  ", 42, None, "Funk"]}]}
        assert _extract_discogs_styles(response) == ["Jazz", "Funk"]

    def test_no_results(self) -> None:
        assert _extract_discogs_styles({"results": []}) == []

    def test_missing_results_key(self) -> None:
        assert _extract_discogs_styles({}) == []

    def test_error_response(self) -> None:
        assert _extract_discogs_styles({"_error": "not_found"}) == []

    def test_garbage_response(self) -> None:
        assert _extract_discogs_styles("nope") == []  # type: ignore[arg-type]

    def test_results_not_a_list(self) -> None:
        assert _extract_discogs_styles({"results": {"style": ["X"]}}) == []

    def test_all_results_styleless_returns_empty(self) -> None:
        response = {"results": [{"genre": ["Pop"]}, {"title": "x"}]}
        assert _extract_discogs_styles(response) == []


class TestQueryForTrack:
    TOKEN = "tok123"

    def test_album_search_when_album_present(self) -> None:
        track = {
            "artist": "Kendrick Lamar",
            "track": "DNA.",
            "artist_normalized": "kendrick lamar",
            "track_normalized": "dna",
            "album": "DAMN.",
        }
        params, key = _query_for_track(track, self.TOKEN)
        assert params["artist"] == "Kendrick Lamar"
        assert params["release_title"] == "DAMN."
        assert params["type"] == "release"
        assert params["token"] == self.TOKEN
        assert "track" not in params
        assert key == "kendrick lamar|album:damn."

    def test_track_search_when_no_album(self) -> None:
        track = {
            "artist": "Some Artist",
            "track": "Loose Single",
            "artist_normalized": "some artist",
            "track_normalized": "loose single",
            "album": "",
        }
        params, key = _query_for_track(track, self.TOKEN)
        assert params["track"] == "Loose Single"
        assert "release_title" not in params
        assert key == "some artist|track:loose single"

    def test_album_cache_key_dedupes_across_tracks(self) -> None:
        """Two tracks from the same album must produce the same cache key."""
        common = {"artist": "Tame Impala", "artist_normalized": "tame impala",
                  "album": "Currents"}
        t1 = {**common, "track": "Let It Happen", "track_normalized": "let it happen"}
        t2 = {**common, "track": "The Less I Know", "track_normalized": "the less i know"}
        _, k1 = _query_for_track(t1, self.TOKEN)
        _, k2 = _query_for_track(t2, self.TOKEN)
        assert k1 == k2

    def test_missing_album_key_treated_as_no_album(self) -> None:
        track = {
            "artist": "A", "track": "T",
            "artist_normalized": "a", "track_normalized": "t",
        }
        params, key = _query_for_track(track, self.TOKEN)
        assert "track" in params
        assert key == "a|track:t"
