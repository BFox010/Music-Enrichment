"""Tests for scripts/apply_bass_labels (owner Heavy Bass overlay)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_bass_labels import BASS_MOOD, apply_bass_keeps  # noqa: E402
from pipeline.schema import fill_defaults, validate_dataset  # noqa: E402


def _row(artist: str, track: str, *, mood_tags, mood_source) -> dict:
    return fill_defaults({
        "artist": artist, "track": track,
        "artist_normalized": artist.lower(), "track_normalized": track.lower(),
        "mood_tags": mood_tags,
        "mood_source": mood_source,
        "mood_confidence": "medium" if mood_source == "centroid" else "high",
    })


def test_adds_heavy_bass_and_keeps_co_moods() -> None:
    rows = [_row("A", "x", mood_tags=["Love", "Happy"], mood_source="centroid")]
    cleaned, stats = apply_bass_keeps(rows, {("A", "x")})
    assert cleaned[0]["mood_tags"] == ["Love", "Happy", BASS_MOOD]
    assert cleaned[0]["mood_source"] == "manual"
    assert cleaned[0]["mood_confidence"] == "high"
    assert stats["added"] == 1 and stats["promoted"] == 1


def test_non_keep_rows_untouched() -> None:
    rows = [_row("A", "x", mood_tags=["Dance"], mood_source="centroid")]
    cleaned, stats = apply_bass_keeps(rows, set())
    assert cleaned[0]["mood_tags"] == ["Dance"]
    assert cleaned[0]["mood_source"] == "centroid"
    assert stats["matched"] == 0


def test_empty_row_gets_heavy_bass() -> None:
    rows = [_row("A", "x", mood_tags=None, mood_source=None)]
    cleaned, _ = apply_bass_keeps(rows, {("A", "x")})
    assert cleaned[0]["mood_tags"] == [BASS_MOOD]
    assert cleaned[0]["mood_source"] == "manual"


def test_idempotent() -> None:
    rows = [_row("A", "x", mood_tags=["Dance"], mood_source="centroid")]
    once, _ = apply_bass_keeps(rows, {("A", "x")})
    twice, stats2 = apply_bass_keeps(once, {("A", "x")})
    assert twice[0]["mood_tags"] == ["Dance", BASS_MOOD]
    assert stats2["added"] == 0 and stats2["already_had"] == 1
    assert stats2["promoted"] == 0  # already manual


def test_no_duplicate_when_already_heavy_bass() -> None:
    rows = [_row("A", "x", mood_tags=["Heavy Bass", "Dance"], mood_source="claude_batch")]
    cleaned, stats = apply_bass_keeps(rows, {("A", "x")})
    assert cleaned[0]["mood_tags"].count(BASS_MOOD) == 1
    assert stats["already_had"] == 1


def test_output_passes_schema_validation() -> None:
    rows = [
        _row("A", "x", mood_tags=["Love", "Happy"], mood_source="centroid"),
        _row("B", "y", mood_tags=None, mood_source=None),
        _row("C", "z", mood_tags=["Dance"], mood_source="audit"),
    ]
    cleaned, _ = apply_bass_keeps(rows, {("A", "x"), ("B", "y")})
    cleaned = [fill_defaults(r) for r in cleaned]
    assert validate_dataset(cleaned)["invalid_count"] == 0
