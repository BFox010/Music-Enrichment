"""Tests for pipeline.enrich_discogs.

All tests are offline — no Discogs API calls are made.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.enrich_discogs import (
    MATCH_THRESHOLD,
    _artist_similarity,
    _extract_discogs_fields,
    _parse_artist_from_title,
    enrich,
)


# ── _parse_artist_from_title ──────────────────────────────────────────────────

class TestParseArtistFromTitle:
    def test_en_dash(self) -> None:
        assert _parse_artist_from_title("Radiohead – The Bends") == "Radiohead"

    def test_plain_hyphen(self) -> None:
        assert _parse_artist_from_title("Kendrick Lamar - good kid, m.A.A.d city") == "Kendrick Lamar"

    def test_em_dash(self) -> None:
        assert _parse_artist_from_title("Nine Inch Nails — The Downward Spiral") == "Nine Inch Nails"

    def test_no_separator(self) -> None:
        # Returns the whole string as artist when no separator found
        assert _parse_artist_from_title("NoSeparator") == "NoSeparator"

    def test_first_separator_wins(self) -> None:
        # Only the first separator splits; album title may contain dashes
        assert _parse_artist_from_title("The Knife - Deep Cuts - Remastered") == "The Knife"


# ── _artist_similarity ────────────────────────────────────────────────────────

class TestArtistSimilarity:
    def test_exact_match(self) -> None:
        result = {"title": "Radiohead - OK Computer"}
        # "radiohead" normalized vs "radiohead"
        ratio = _artist_similarity(result, "radiohead")
        assert ratio >= MATCH_THRESHOLD

    def test_clear_mismatch(self) -> None:
        result = {"title": "Various Artists - Compilation"}
        ratio = _artist_similarity(result, "radiohead")
        assert ratio < MATCH_THRESHOLD

    def test_missing_title(self) -> None:
        ratio = _artist_similarity({}, "radiohead")
        # Empty string vs "radiohead" — low ratio, should not exceed threshold
        assert ratio < MATCH_THRESHOLD


# ── _extract_discogs_fields ───────────────────────────────────────────────────

class TestExtractDiscogsFields:
    def _result(self, title: str, styles: list[str]) -> dict:
        return {"title": title, "style": styles, "genre": ["Rock"]}

    def test_clear_match_returns_styles(self) -> None:
        response = {
            "results": [self._result("Radiohead – The Bends", ["Alternative Rock", "Britpop"])]
        }
        out = _extract_discogs_fields(response, "radiohead")
        assert out["discogs_styles"] == ["Alternative Rock", "Britpop"]

    def test_artist_mismatch_returns_empty(self) -> None:
        response = {
            "results": [self._result("Various Artists – Hits Vol. 1", ["Pop"])]
        }
        out = _extract_discogs_fields(response, "radiohead")
        assert out["discogs_styles"] == []

    def test_no_results_returns_empty(self) -> None:
        out = _extract_discogs_fields({"results": []}, "radiohead")
        assert out["discogs_styles"] == []

    def test_error_response_returns_empty(self) -> None:
        out = _extract_discogs_fields({"_error": "not_found"}, "radiohead")
        assert out["discogs_styles"] == []

    def test_non_dict_response_returns_empty(self) -> None:
        out = _extract_discogs_fields(None, "radiohead")  # type: ignore[arg-type]
        assert out["discogs_styles"] == []

    def test_no_style_field_returns_empty_list(self) -> None:
        # Release exists but has no style tags
        response = {"results": [{"title": "Radiohead – Pablo Honey"}]}
        out = _extract_discogs_fields(response, "radiohead")
        assert out["discogs_styles"] == []

    def test_style_as_scalar_coerced_to_list(self) -> None:
        # Defensive: handle a stray string instead of a list
        response = {"results": [{"title": "Radiohead – Pablo Honey", "style": "Grunge"}]}
        out = _extract_discogs_fields(response, "radiohead")
        assert out["discogs_styles"] == ["Grunge"]

    def test_hip_hop_artist_match(self) -> None:
        response = {
            "results": [self._result("Kendrick Lamar – good kid, m.A.A.d city", ["Hip Hop", "Conscious"])]
        }
        out = _extract_discogs_fields(response, "kendrick lamar")
        assert "Hip Hop" in out["discogs_styles"]


# ── enrich() integration (fully mocked) ──────────────────────────────────────

class TestEnrich:
    """Test enrich() end-to-end with mocked HTTP client and env vars."""

    def _make_track(self, artist: str = "Radiohead", track: str = "Creep") -> dict:
        from pipeline.normalize import normalize_artist, normalize_track
        return {
            "artist": artist,
            "track": track,
            "artist_normalized": normalize_artist(artist),
            "track_normalized": normalize_track(track),
            "discogs_styles": [],
            "lastfm_tags": [],
        }

    def _mock_client_get(self, url, params, cache_key):
        return {
            "results": [
                {"title": "Radiohead – Pablo Honey", "style": ["Alternative Rock"]}
            ]
        }

    def test_writes_output_file(self, tmp_path: Path) -> None:
        input_file = tmp_path / "tracks_in.jsonl"
        output_file = tmp_path / "tracks_out.jsonl"
        track = self._make_track()
        input_file.write_text(json.dumps(track) + "\n", encoding="utf-8")

        env = {"DISCOGS_TOKEN": "fake-token"}
        with patch.dict(os.environ, env):
            with patch("pipeline.enrich_discogs.RateLimitedClient") as MockClient:
                inst = MagicMock()
                inst.cache = {}
                inst.get.side_effect = self._mock_client_get
                MockClient.return_value = inst

                stats = enrich(
                    input_path=input_file,
                    output_path=output_file,
                    run_log_path=tmp_path / "run.log",
                )

        assert output_file.exists()
        rows = [json.loads(l) for l in output_file.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["discogs_styles"] == ["Alternative Rock"]
        assert stats["matched"] == 1
        assert stats["total"] == 1

    def test_no_match_leaves_styles_empty(self, tmp_path: Path) -> None:
        input_file = tmp_path / "tracks_in.jsonl"
        output_file = tmp_path / "tracks_out.jsonl"
        track = self._make_track()
        input_file.write_text(json.dumps(track) + "\n", encoding="utf-8")

        def _no_match(url, params, cache_key):
            return {"results": []}

        env = {"DISCOGS_TOKEN": "fake-token"}
        with patch.dict(os.environ, env):
            with patch("pipeline.enrich_discogs.RateLimitedClient") as MockClient:
                inst = MagicMock()
                inst.cache = {}
                inst.get.side_effect = _no_match
                MockClient.return_value = inst

                stats = enrich(
                    input_path=input_file,
                    output_path=output_file,
                    run_log_path=tmp_path / "run.log",
                )

        rows = [json.loads(l) for l in output_file.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["discogs_styles"] == []
        assert stats["no_match"] == 1

    def test_missing_token_raises(self, tmp_path: Path) -> None:
        input_file = tmp_path / "tracks_in.jsonl"
        input_file.write_text(json.dumps(self._make_track()) + "\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            with patch("pipeline.enrich_discogs.load_dotenv"):  # prevent .env load
                with pytest.raises(RuntimeError, match="DISCOGS_TOKEN"):
                    enrich(
                        input_path=input_file,
                        output_path=tmp_path / "out.jsonl",
                        run_log_path=tmp_path / "run.log",
                    )

    def test_missing_input_raises_file_not_found(self, tmp_path: Path) -> None:
        env = {"DISCOGS_TOKEN": "fake-token"}
        with patch.dict(os.environ, env):
            with pytest.raises(FileNotFoundError):
                enrich(
                    input_path=tmp_path / "nonexistent.jsonl",
                    output_path=tmp_path / "out.jsonl",
                    run_log_path=tmp_path / "run.log",
                )

    def test_limit_parameter(self, tmp_path: Path) -> None:
        input_file = tmp_path / "tracks_in.jsonl"
        output_file = tmp_path / "tracks_out.jsonl"
        lines = [json.dumps(self._make_track(track=f"Track {i}")) for i in range(5)]
        input_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        env = {"DISCOGS_TOKEN": "fake-token"}
        with patch.dict(os.environ, env):
            with patch("pipeline.enrich_discogs.RateLimitedClient") as MockClient:
                inst = MagicMock()
                inst.cache = {}
                inst.get.return_value = {"results": []}
                MockClient.return_value = inst

                stats = enrich(
                    input_path=input_file,
                    output_path=output_file,
                    run_log_path=tmp_path / "run.log",
                    limit=2,
                )

        assert stats["total"] == 2
