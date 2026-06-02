"""Tests for pipeline.derive_genres."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.derive_genres import (
    GENRE_CATEGORIES,
    GENRE_TAG_MAP,
    ITUNES_GENRE_MAP,
    derive,
    derive_genres_for_track,
)


class TestDeriveGenresForTrack:
    def test_lastfm_tags_basic(self) -> None:
        track = {"lastfm_tags": ["rap", "hip-hop"], "discogs_styles": [], "itunes_genre": None}
        assert "Hip-Hop / Rap" in derive_genres_for_track(track)

    def test_discogs_styles_basic(self) -> None:
        track = {"lastfm_tags": [], "discogs_styles": ["Contemporary R&B", "Neo Soul"], "itunes_genre": None}
        result = derive_genres_for_track(track)
        assert "R&B / Soul" in result

    def test_itunes_genre_priority(self) -> None:
        # itunes_genre comes first in the result list
        track = {
            "lastfm_tags": ["rock"],
            "discogs_styles": ["Indie Rock"],
            "itunes_genre": "Hip-Hop",
        }
        result = derive_genres_for_track(track)
        assert result[0] == "Hip-Hop / Rap"

    def test_no_sources_returns_empty(self) -> None:
        track = {"lastfm_tags": [], "discogs_styles": [], "itunes_genre": None}
        assert derive_genres_for_track(track) == []

    def test_missing_fields_handled(self) -> None:
        assert derive_genres_for_track({}) == []

    def test_deduplication(self) -> None:
        # "rap" and "hip-hop" both map to Hip-Hop / Rap; should appear once
        track = {"lastfm_tags": ["rap", "hip-hop", "trap"], "discogs_styles": [], "itunes_genre": None}
        result = derive_genres_for_track(track)
        assert result.count("Hip-Hop / Rap") == 1

    def test_multi_genre_tag(self) -> None:
        # "pop rap" maps to both Hip-Hop and Pop
        track = {"lastfm_tags": ["pop rap"], "discogs_styles": [], "itunes_genre": None}
        result = derive_genres_for_track(track)
        assert "Hip-Hop / Rap" in result
        assert "Pop" in result

    def test_indie_rock_maps_to_both(self) -> None:
        track = {"lastfm_tags": [], "discogs_styles": ["Indie Rock"], "itunes_genre": None}
        result = derive_genres_for_track(track)
        assert "Indie / Alternative" in result
        assert "Rock" in result

    def test_case_insensitive(self) -> None:
        track = {"lastfm_tags": ["RAP", "Hip-Hop"], "discogs_styles": [], "itunes_genre": None}
        assert "Hip-Hop / Rap" in derive_genres_for_track(track)

    def test_unknown_tags_ignored(self) -> None:
        track = {"lastfm_tags": ["my top songs", "beautiful", "2016"], "discogs_styles": [], "itunes_genre": None}
        assert derive_genres_for_track(track) == []


class TestGenreTaxonomy:
    def test_all_map_values_are_valid_categories(self) -> None:
        valid = set(GENRE_CATEGORIES)
        for tag, genres in GENRE_TAG_MAP.items():
            for g in genres:
                assert g in valid, f"Tag '{tag}' maps to unknown genre '{g}'"

    def test_itunes_map_values_are_valid_or_empty(self) -> None:
        valid = set(GENRE_CATEGORIES)
        for label, genres in ITUNES_GENRE_MAP.items():
            for g in genres:
                assert g in valid, f"iTunes '{label}' maps to unknown genre '{g}'"


class TestDerivePhase:
    def _make_jsonl(self, tracks: list[dict], path: Path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for t in tracks:
                fh.write(json.dumps(t) + "\n")

    def test_writes_output_and_returns_stats(self) -> None:
        tracks = [
            {"artist": "A", "track": "T1", "lastfm_tags": ["rap"], "discogs_styles": [], "itunes_genre": None},
            {"artist": "A", "track": "T2", "lastfm_tags": [], "discogs_styles": [], "itunes_genre": None},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            self._make_jsonl(tracks, inp)
            stats = derive(input_path=inp, output_path=out)
            assert stats["total"] == 2
            assert stats["with_genres"] == 1
            assert stats["no_sources"] == 1
            rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
            assert rows[0]["genres"] == ["Hip-Hop / Rap"]
            assert rows[1]["genres"] == []

    def test_existing_genres_field_overwritten(self) -> None:
        tracks = [{"artist": "A", "track": "T", "genres": ["stale"], "lastfm_tags": ["rock"],
                   "discogs_styles": [], "itunes_genre": None}]
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            self._make_jsonl(tracks, inp)
            derive(input_path=inp, output_path=out)
            row = json.loads(out.read_text().splitlines()[0])
            assert "Rock" in row["genres"]
            assert "stale" not in row["genres"]
