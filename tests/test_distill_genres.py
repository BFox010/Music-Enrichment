"""Tests for pipeline.distill_genres."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.distill_genres import (
    JUNK_TAGS,
    _filter_lastfm_tags,
    _merge_genres,
    distill,
)


# ── _filter_lastfm_tags ───────────────────────────────────────────────────────

class TestFilterLastfmTags:
    def test_drops_seen_live(self) -> None:
        assert _filter_lastfm_tags(["hip hop", "seen live"]) == ["hip hop"]

    def test_drops_decade_tags(self) -> None:
        assert _filter_lastfm_tags(["90s", "2000s", "alternative"]) == ["alternative"]

    def test_drops_favorites(self) -> None:
        assert _filter_lastfm_tags(["favorites", "my favorites", "indie"]) == ["indie"]

    def test_drops_artist_name_tags(self) -> None:
        assert _filter_lastfm_tags(["kanye west", "drake", "hip hop"]) == ["hip hop"]

    def test_drops_specific_year_tags(self) -> None:
        assert _filter_lastfm_tags(["2016", "2022", "trap"]) == ["trap"]

    def test_drops_nationality_tags(self) -> None:
        assert _filter_lastfm_tags(["british", "american", "indie rock"]) == ["indie rock"]

    def test_drops_reaction_noise(self) -> None:
        assert _filter_lastfm_tags(["masterpiece", "soty", "<3", "alternative"]) == ["alternative"]

    def test_drops_female_vocalists(self) -> None:
        assert _filter_lastfm_tags(["female vocalists", "pop"]) == ["pop"]

    def test_keeps_mood_tags(self) -> None:
        # User chose "keep mood-ish tags" — these must NOT be filtered
        tags = ["melancholic", "dark", "sad", "upbeat", "energetic", "happy"]
        assert _filter_lastfm_tags(tags) == tags

    def test_keeps_niche_genre_tags(self) -> None:
        tags = ["boom bap", "shoegaze", "cloud rap", "drill", "trip hop"]
        assert _filter_lastfm_tags(tags) == tags

    def test_case_insensitive_blocklist(self) -> None:
        # Blocklist comparison is lowercased
        assert _filter_lastfm_tags(["Favorites", "SEEN LIVE", "Rock"]) == ["Rock"]

    def test_empty_list(self) -> None:
        assert _filter_lastfm_tags([]) == []

    def test_all_junk(self) -> None:
        assert _filter_lastfm_tags(["80s", "90s", "favorites", "great"]) == []

    def test_junk_tags_are_lowercase(self) -> None:
        # Verify every entry in JUNK_TAGS is lowercase so the comparison works
        for tag in JUNK_TAGS:
            assert tag == tag.lower(), f"JUNK_TAG not lowercase: {tag!r}"


# ── _merge_genres ─────────────────────────────────────────────────────────────

class TestMergeGenres:
    def test_union_all_sources(self) -> None:
        genres = _merge_genres(
            discogs_styles=["Alternative Rock"],
            lastfm_tags=["indie", "90s"],         # "90s" filtered
            itunes_genre="Rock",
        )
        assert "Alternative Rock" in genres
        assert "indie" in genres
        assert "Rock" in genres
        assert "90s" not in genres

    def test_discogs_wins_on_duplicates(self) -> None:
        # "Hip-Hop" from Discogs vs "hip-hop" from Last.fm — Discogs spelling wins
        genres = _merge_genres(
            discogs_styles=["Hip-Hop"],
            lastfm_tags=["hip-hop", "rap"],
            itunes_genre=None,
        )
        assert genres[0] == "Hip-Hop"
        assert "rap" in genres
        # "hip-hop" deduped — only one entry despite different casing
        assert len([g for g in genres if g.lower() == "hip-hop"]) == 1

    def test_case_insensitive_dedup(self) -> None:
        genres = _merge_genres(
            discogs_styles=["Boom Bap"],
            lastfm_tags=["boom bap", "BOOM BAP"],
            itunes_genre=None,
        )
        assert genres.count("Boom Bap") == 1
        assert len(genres) == 1

    def test_no_sources_returns_empty(self) -> None:
        assert _merge_genres([], [], None) == []

    def test_only_itunes(self) -> None:
        genres = _merge_genres([], [], "Hip-Hop/Rap")
        assert genres == ["Hip-Hop/Rap"]

    def test_only_lastfm(self) -> None:
        genres = _merge_genres([], ["alternative rock", "indie"], None)
        assert genres == ["alternative rock", "indie"]

    def test_only_discogs(self) -> None:
        genres = _merge_genres(["Shoegaze", "Dream Pop"], [], None)
        assert genres == ["Shoegaze", "Dream Pop"]

    def test_itunes_junk_filtered_via_dedup_not_blocklist(self) -> None:
        # iTunes genre is NOT filtered through the junk blocklist —
        # it's a controlled Apple tag, added as-is.
        genres = _merge_genres([], [], "80s Pop")
        assert "80s Pop" in genres

    def test_source_priority_order_preserved(self) -> None:
        # Discogs first, then lastfm (new), then itunes (new)
        genres = _merge_genres(
            discogs_styles=["Boom Bap"],
            lastfm_tags=["conscious hip hop"],
            itunes_genre="Hip-Hop/Rap",
        )
        assert genres.index("Boom Bap") < genres.index("conscious hip hop")
        assert genres.index("conscious hip hop") < genres.index("Hip-Hop/Rap")


# ── distill() integration ─────────────────────────────────────────────────────

class TestDistill:
    def _make_track(
        self,
        discogs_styles=None,
        lastfm_tags=None,
        itunes_genre=None,
    ) -> dict:
        from pipeline.normalize import normalize_artist, normalize_track
        return {
            "artist": "Test Artist",
            "track": "Test Track",
            "artist_normalized": normalize_artist("Test Artist"),
            "track_normalized": normalize_track("Test Track"),
            "discogs_styles": discogs_styles or [],
            "lastfm_tags": lastfm_tags or [],
            "itunes_genre": itunes_genre,
            "genres": [],
        }

    def test_writes_output_with_genres(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        track = self._make_track(
            discogs_styles=["Alternative Rock"],
            lastfm_tags=["indie", "90s"],
            itunes_genre="Rock",
        )
        inp.write_text(json.dumps(track) + "\n", encoding="utf-8")

        stats = distill(input_path=inp, output_path=out, run_log_path=tmp_path / "run.log")

        assert out.exists()
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert "Alternative Rock" in rows[0]["genres"]
        assert "indie" in rows[0]["genres"]
        assert "Rock" in rows[0]["genres"]
        assert "90s" not in rows[0]["genres"]
        assert stats["with_genres"] == 1

    def test_empty_track_has_empty_genres(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        inp.write_text(json.dumps(self._make_track()) + "\n", encoding="utf-8")

        stats = distill(input_path=inp, output_path=out, run_log_path=tmp_path / "run.log")
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["genres"] == []
        assert stats["empty"] == 1

    def test_multiple_tracks(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        tracks = [
            self._make_track(discogs_styles=["Boom Bap"]),
            self._make_track(lastfm_tags=["shoegaze"]),
            self._make_track(),
        ]
        inp.write_text("\n".join(json.dumps(t) for t in tracks) + "\n", encoding="utf-8")

        stats = distill(input_path=inp, output_path=out, run_log_path=tmp_path / "run.log")
        assert stats["total"] == 3
        assert stats["with_genres"] == 2
        assert stats["empty"] == 1

    def test_limit_parameter(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        tracks = [self._make_track(discogs_styles=[f"Style{i}"]) for i in range(10)]
        inp.write_text("\n".join(json.dumps(t) for t in tracks) + "\n", encoding="utf-8")

        stats = distill(input_path=inp, output_path=out, run_log_path=tmp_path / "run.log", limit=3)
        assert stats["total"] == 3

    def test_missing_input_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            distill(
                input_path=tmp_path / "missing.jsonl",
                output_path=tmp_path / "out.jsonl",
                run_log_path=tmp_path / "run.log",
            )

    def test_existing_genres_overwritten(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        track = self._make_track(discogs_styles=["Hip Hop"])
        track["genres"] = ["stale_genre"]  # should be replaced
        inp.write_text(json.dumps(track) + "\n", encoding="utf-8")

        distill(input_path=inp, output_path=out, run_log_path=tmp_path / "run.log")
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["genres"] == ["Hip Hop"]
