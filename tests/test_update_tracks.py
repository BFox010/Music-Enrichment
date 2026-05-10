"""Tests for pipeline.update_tracks merge logic."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

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

    def test_preserves_rejected_reason(self) -> None:
        new = {"artist": "x", "track": "y", "rejected_reason": None}
        existing = {"artist": "x", "track": "y", "rejected_reason": "too overplayed"}
        merged = _merge_with_existing(new, existing)
        assert merged["rejected_reason"] == "too overplayed"

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

    def test_preserves_playlists_when_locked(self) -> None:
        new = {"artist": "x", "track": "y", "playlists": [], "curation_state": None}
        existing = {"artist": "x", "track": "y",
                    "playlists": ["soak", "night_drive"],
                    "curation_state": "locked"}
        merged = _merge_with_existing(new, existing)
        assert merged["playlists"] == ["soak", "night_drive"]

    def test_does_not_preserve_playlists_when_unreviewed(self) -> None:
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
            rows[0]["rejected_reason"] = "kept for soak playlist"
            self._write_jsonl(out, rows)

            # Second run: same input
            stats = update(input_path=inp, output_path=out)
            assert stats["updated"] == 1
            rows = self._load_jsonl(out)
            assert rows[0]["curation_state"] == "locked"
            assert rows[0]["rejected_reason"] == "kept for soak playlist"
