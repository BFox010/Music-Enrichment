"""Tests for pipeline.derive_genres."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pipeline.derive_genres import (
    GENRE_CATEGORIES,
    GENRE_TAG_MAP,
    ITUNES_GENRE_MAP,
    derive,
    derive_genres_for_track,
    normalize_tag_key,
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


class TestSeparatorInsensitiveLookup:
    """#71: Last.fm and Discogs spell the same genre with different punctuation,
    so an exact lookup missed whichever spelling the map didn't list.
    """

    def test_trip_hop_resolves_identically_either_way(self) -> None:
        hyphen = derive_genres_for_track({"lastfm_tags": ["trip-hop"]})
        space = derive_genres_for_track({"lastfm_tags": ["trip hop"]})
        assert hyphen == space == ["Electronic"]

    def test_separator_variants_of_mapped_tags_all_resolve(self) -> None:
        """The variants measured as unmapped on the committed data."""
        for tag, expected in [
            ("southern hip-hop", "Hip-Hop / Rap"),
            ("post hardcore", "Rock"),
            ("synth pop", "Electronic"),
            ("dance pop", "Electronic"),
            ("indie-pop", "Indie / Alternative"),
            ("conscious hip-hop", "Hip-Hop / Rap"),
            ("west coast hip-hop", "Hip-Hop / Rap"),
            ("singer songwriter", "Country / Folk"),
            ("Nu-metal", "Metal"),
            ("avant garde", "Experimental"),
        ]:
            got = derive_genres_for_track({"lastfm_tags": [tag]})
            assert expected in got, f"{tag!r} did not map to {expected!r} (got {got})"

    def test_lookup_is_case_and_punctuation_insensitive(self) -> None:
        assert derive_genres_for_track({"lastfm_tags": ["  TRIP...HOP  "]}) == ["Electronic"]

    def test_no_normalized_key_collisions_lose_a_mapping(self) -> None:
        """Folding is only safe while colliding keys agree on their value.

        Guards the `setdefault` that builds the normalized map: if someone adds
        "nu-metal": [METAL] beside a "nu metal": [ROCK], one silently wins.
        """
        by_normalized: dict[str, set[tuple[str, ...]]] = {}
        for tag, genres in GENRE_TAG_MAP.items():
            by_normalized.setdefault(normalize_tag_key(tag), set()).add(tuple(genres))
        conflicts = {k: v for k, v in by_normalized.items() if len(v) > 1}
        assert conflicts == {}, f"keys collide with different values: {conflicts}"

    def test_regional_hip_hop_family_is_mapped(self) -> None:
        """The densest genre in this library, absent from the map entirely."""
        for tag in ("underground hip-hop", "east coast hip hop", "Dirty South",
                    "memphis rap", "southern rap", "underground rap",
                    "alternative hip-hop", "Horrorcore"):
            got = derive_genres_for_track({"lastfm_tags": [tag]})
            assert "Hip-Hop / Rap" in got, f"{tag!r} still unmapped (got {got})"


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


class TestInputSelection:
    """Phase 4c is required but its predecessor (4b Discogs) is optional.

    Pinning the input to 4b's output halted every run made without a
    DISCOGS_TOKEN, and — worse, where an old tracks_with_discogs.jsonl was still
    lying around — silently re-derived genres from a stale file, dropping the
    tags Phase 4 had just fetched and every track ingested since.
    """

    def _write(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    def _redirect(self, monkeypatch, tmp: Path) -> None:
        import pipeline.derive_genres as dg

        monkeypatch.setattr(dg, "_INPUT_PRIORITY", [
            tmp / "tracks_with_discogs.jsonl",
            tmp / "tracks_with_metadata.jsonl",
        ])
        monkeypatch.setattr(dg, "DEFAULT_INPUT", tmp / "tracks_with_discogs.jsonl")

    def test_falls_back_to_metadata_when_discogs_is_absent(
        self, monkeypatch, tmp_path
    ) -> None:
        self._redirect(monkeypatch, tmp_path)
        self._write(tmp_path / "tracks_with_metadata.jsonl",
                    [{"artist": "A", "track": "T", "lastfm_tags": ["rap"]}])
        out = tmp_path / "out.jsonl"
        stats = derive(output_path=out)
        assert stats["with_genres"] == 1
        row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert row["genres"] == ["Hip-Hop / Rap"]

    def test_stale_discogs_file_loses_to_a_fresher_metadata_file(
        self, monkeypatch, tmp_path
    ) -> None:
        stale = tmp_path / "tracks_with_discogs.jsonl"
        fresh = tmp_path / "tracks_with_metadata.jsonl"
        self._redirect(monkeypatch, tmp_path)
        self._write(stale, [{"artist": "A", "track": "Old", "lastfm_tags": ["rock"]}])
        self._write(fresh, [
            {"artist": "A", "track": "Old", "lastfm_tags": ["rock"]},
            {"artist": "A", "track": "New", "lastfm_tags": ["rap"]},
        ])
        os.utime(stale, (1_600_000_000, 1_600_000_000))

        out = tmp_path / "out.jsonl"
        stats = derive(output_path=out)
        assert stats["total"] == 2, "the track ingested since the stale run was dropped"

    def test_explicit_input_path_still_wins(self, monkeypatch, tmp_path) -> None:
        self._redirect(monkeypatch, tmp_path)
        chosen = tmp_path / "explicit.jsonl"
        self._write(chosen, [{"artist": "A", "track": "T", "lastfm_tags": ["jazz"]}])
        out = tmp_path / "out.jsonl"
        derive(input_path=chosen, output_path=out)
        row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert row["genres"] == ["Jazz"]
