"""Tests for pipeline.update_tracks merge logic."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pipeline.update_tracks import (
    _enrichment_sources,
    _merge_with_existing,
    update,
)


class TestMergeWithExisting:
    def test_no_existing(self) -> None:
        new = {"artist": "x", "track": "y"}
        merged = _merge_with_existing(new, None)
        assert merged is new

    def test_preserves_curation_state(self) -> None:
        new = {"artist": "x", "track": "y", "curation_state": None}
        existing = {"artist": "x", "track": "y", "curation_state": "locked"}
        merged = _merge_with_existing(new, existing)
        assert merged["curation_state"] == "locked"

    def test_preserves_claude_batch_moods(self) -> None:
        new = {
            "artist": "x", "track": "y",
            "mood_tags": ["Slow"],
            "mood_source": "centroid",
            "mood_confidence": "medium",
        }
        existing = {
            "artist": "x", "track": "y",
            "mood_tags": ["Sad", "Slow", "Moody"],
            "mood_source": "claude_batch",
            "mood_confidence": "high",
        }
        merged = _merge_with_existing(new, existing)
        assert merged["mood_tags"] == ["Sad", "Slow", "Moody"]
        assert merged["mood_source"] == "claude_batch"
        assert merged["mood_confidence"] == "high"

    def test_centroid_overrides_centroid(self) -> None:
        # Two centroid runs — new wins (no human edit to preserve)
        new = {"mood_tags": ["Fast"], "mood_source": "centroid"}
        existing = {"mood_tags": ["Slow"], "mood_source": "centroid"}
        merged = _merge_with_existing(new, existing)
        assert merged["mood_tags"] == ["Fast"]

    def test_playlists_always_track_phase7_output(self) -> None:
        # Playlists are derived from taste_profile.md (Phase 7), not human-edited.
        # Even when curation_state is locked, new Phase 7 output wins — otherwise
        # tracks get stuck in playlist sections that no longer exist in markdown.
        new = {"artist": "x", "track": "y", "playlists": ["sad"], "curation_state": "locked"}
        existing = {"artist": "x", "track": "y",
                    "playlists": ["heavy_weather", "night_drive"],
                    "curation_state": "locked"}
        merged = _merge_with_existing(new, existing)
        assert merged["playlists"] == ["sad"]

    def test_playlists_clear_when_track_removed_from_markdown(self) -> None:
        # When Phase 7 emits no playlists for a track (removed from markdown),
        # tracks.jsonl reflects that — no stale memberships preserved.
        new = {"artist": "x", "track": "y", "playlists": [], "curation_state": None}
        existing = {"artist": "x", "track": "y",
                    "playlists": ["stale_playlist"],
                    "curation_state": None}
        merged = _merge_with_existing(new, existing)
        assert merged["playlists"] == []


class TestEnrichmentSources:
    def test_lastfm_only(self) -> None:
        row = {"lastfm_tags": ["rock"]}
        assert _enrichment_sources(row) == ["lastfm_tags"]

    def test_multiple_sources(self) -> None:
        row = {
            "lastfm_tags": ["rock"],
            "musicbrainz_id": "abc",
            "itunes_persistent_id": "xyz",
        }
        sources = _enrichment_sources(row)
        assert "lastfm_tags" in sources
        assert "musicbrainz" in sources
        assert "itunes_xml" in sources

    def test_empty_row(self) -> None:
        assert _enrichment_sources({}) == []

    def test_empty_list_does_not_count(self) -> None:
        # lastfm_tags=[] should NOT add the source
        assert _enrichment_sources({"lastfm_tags": []}) == []


class TestUpdate:
    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _load_jsonl(self, path: Path) -> list[dict]:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_first_run_creates_tracks_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "play_count": 47, "lastfm_tags": ["trip-hop"]},
            ])
            stats = update(input_path=inp, output_path=out)
            assert stats["total"] == 1
            assert stats["new"] == 1
            assert stats["updated"] == 0
            rows = self._load_jsonl(out)
            assert rows[0]["artist"] == "Portishead"
            assert "lastfm_tags" in rows[0]["enrichment_sources"]

    def test_second_run_preserves_curation_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            # First run
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "play_count": 47, "lastfm_tags": ["trip-hop"]},
            ])
            update(input_path=inp, output_path=out)

            # Manually edit curation_state on disk (simulates user edit)
            rows = self._load_jsonl(out)
            rows[0]["curation_state"] = "locked"
            self._write_jsonl(out, rows)

            # Second run: same input
            stats = update(input_path=inp, output_path=out)
            assert stats["updated"] == 1
            rows = self._load_jsonl(out)
            assert rows[0]["curation_state"] == "locked"

    def test_enriched_at_preserved_when_row_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "play_count": 47, "lastfm_tags": ["trip-hop"]},
            ])
            update(input_path=inp, output_path=out)
            # Backdate the stamp on disk to simulate a prior-day run.
            rows = self._load_jsonl(out)
            rows[0]["enriched_at"] = "2000-01-01"
            self._write_jsonl(out, rows)
            # Re-run with identical input: stamp preserved → no line churn.
            update(input_path=inp, output_path=out)
            assert self._load_jsonl(out)[0]["enriched_at"] == "2000-01-01"

    def test_enriched_at_bumped_when_row_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "play_count": 47, "lastfm_tags": ["trip-hop"]},
            ])
            update(input_path=inp, output_path=out)
            rows = self._load_jsonl(out)
            rows[0]["enriched_at"] = "2000-01-01"
            self._write_jsonl(out, rows)
            # Re-run with CHANGED data → stamp refreshes off the backdated value.
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "play_count": 99, "lastfm_tags": ["trip-hop"]},
            ])
            update(input_path=inp, output_path=out)
            assert self._load_jsonl(out)[0]["enriched_at"] != "2000-01-01"

    def test_duplicate_source_key_aborts_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "A", "track": "T",
                 "artist_normalized": "a", "track_normalized": "t"},
                {"artist": "A", "track": "T",
                 "artist_normalized": "a", "track_normalized": "t"},
            ])
            with pytest.raises(ValueError, match="duplicate source track key"):
                update(input_path=inp, output_path=out)
            assert not out.exists()

    def test_missing_source_key_fails_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "A", "track": "T", "artist_normalized": "a"},
            ])
            with pytest.raises(ValueError, match="source row 1 missing"):
                update(input_path=inp, output_path=out)


class TestMergeSurvivesRenormalization:
    """The merge keys on canonical_track_id, not the normalized name pair.

    Normalization is expected to improve, and each improvement rewrites
    artist_normalized/track_normalized. Keying the merge on those strings
    orphaned the existing row — the merge saw a brand-new track and dropped the
    human-edited fields that live only in tracks.jsonl. #27's feat-credit
    normalization did this to 183 rows, 54 of them losing curation_state.
    """

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _load_jsonl(self, path: Path) -> list[dict]:
        return [
            json.loads(l)
            for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]

    def test_renormalized_row_keeps_owner_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            cid = "mbid:1f1c4d0b-1e99-4524-a46b-2f89a2511aea"

            self._write_jsonl(inp, [
                {"artist": "Angels & Airwaves", "track": "Heaven",
                 "canonical_track_id": cid,
                 "artist_normalized": "angels and airwaves",
                 "track_normalized": "heaven"},
            ])
            update(input_path=inp, output_path=out)

            rows = self._load_jsonl(out)
            rows[0].update(
                mood_tags=["Fast", "Moody"],
                mood_source="audit",
                curation_state="locked",
            )
            self._write_jsonl(out, rows)

            # Same recording, same canonical id — only normalization changed.
            self._write_jsonl(inp, [
                {"artist": "Angels & Airwaves", "track": "Heaven",
                 "canonical_track_id": cid,
                 "artist_normalized": "angels airwaves",
                 "track_normalized": "heaven",
                 "mood_tags": ["Fast"], "mood_source": "centroid"},
            ])
            stats = update(input_path=inp, output_path=out)

            assert stats["updated"] == 1, "re-keyed row must merge, not re-add"
            assert stats["new"] == 0
            merged = self._load_jsonl(out)
            assert len(merged) == 1
            assert merged[0]["mood_source"] == "audit"
            assert merged[0]["mood_tags"] == ["Fast", "Moody"]
            assert merged[0]["curation_state"] == "locked"
            assert merged[0]["artist_normalized"] == "angels airwaves"

    def test_promoted_identity_still_merges_by_name(self) -> None:
        """Phase 5a resolving an ISRC moves the id *computed* from a source row.

        The source rows carry no canonical_track_id — the intermediates don't
        propagate it — so the merge computes one, and a freshly resolved ISRC
        makes that computed key jump from norm: to isrc:. The stored id is
        sticky (merge rule 4 keeps the existing value, and fill_defaults only
        computes when absent), so the two disagree and the name pair is what
        keeps the row from being orphaned.
        """
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads"},
            ])
            update(input_path=inp, output_path=out)
            assert self._load_jsonl(out)[0]["canonical_track_id"] == "norm:portishead|roads"

            rows = self._load_jsonl(out)
            rows[0]["curation_state"] = "locked"
            self._write_jsonl(out, rows)

            # Same track, now carrying an ISRC — identity chain promotes the id.
            self._write_jsonl(inp, [
                {"artist": "Portishead", "track": "Roads",
                 "artist_normalized": "portishead", "track_normalized": "roads",
                 "isrc": "GBAAA0000001"},
            ])
            stats = update(input_path=inp, output_path=out)

            assert stats["updated"] == 1, "promoted id must not orphan the row"
            assert stats["new"] == 0
            merged = self._load_jsonl(out)
            assert len(merged) == 1
            # Sticky by design: a stable identity is the point of invariant 4,
            # so the row keeps the id it was first written with.
            assert merged[0]["canonical_track_id"] == "norm:portishead|roads"
            assert merged[0]["isrc"] == "GBAAA0000001"
            assert merged[0]["curation_state"] == "locked"

    def test_one_existing_row_is_claimed_only_once(self) -> None:
        """Two source rows must not both merge onto the same existing track."""
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            self._write_jsonl(inp, [
                {"artist": "A", "track": "T", "canonical_track_id": "isrc:X",
                 "artist_normalized": "a", "track_normalized": "t"},
            ])
            update(input_path=inp, output_path=out)

            # Two distinct tracks that 5a mistakenly gave the same ISRC.
            self._write_jsonl(inp, [
                {"artist": "A", "track": "T", "isrc": "X",
                 "artist_normalized": "a", "track_normalized": "t"},
                {"artist": "B", "track": "U", "isrc": "X",
                 "artist_normalized": "b", "track_normalized": "u"},
            ])
            stats = update(input_path=inp, output_path=out)
            assert stats["total"] == 2
            assert stats["new"] == 1, "the second row must not re-claim the first"
            assert stats["updated"] == 1

    def test_legacy_row_without_canonical_id_still_merges_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.jsonl"
            out = Path(tmp) / "tracks.jsonl"
            row = {"artist": "Portishead", "track": "Roads",
                   "artist_normalized": "portishead", "track_normalized": "roads"}
            self._write_jsonl(inp, [row])
            update(input_path=inp, output_path=out)
            stats = update(input_path=inp, output_path=out)
            assert stats["updated"] == 1
            assert stats["new"] == 0
