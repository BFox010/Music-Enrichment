"""Tests for scripts/cleanup_centroid_moods (retroactive centroid policy)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cleanup_centroid_moods import clean_centroid_moods  # noqa: E402
from pipeline.classify_moods import apply_centroid_policy  # noqa: E402
from pipeline.schema import fill_defaults, validate_dataset  # noqa: E402


def _features(**kwargs) -> dict:
    base = {
        "danceability": 0.5, "energy": 0.5, "valence": 0.5,
        "speechiness": 0.05, "acousticness": 0.3,
        "instrumentalness": 0.0, "liveness": 0.1,
        "tempo": 120.0, "loudness": -10.0,
    }
    base.update(kwargs)
    return base


def _row(artist: str, track: str, *, mood_tags, mood_source, tempo=120.0) -> dict:
    return fill_defaults({
        "artist": artist, "track": track,
        "artist_normalized": artist.lower(), "track_normalized": track.lower(),
        "mood_tags": mood_tags,
        "mood_source": mood_source,
        "mood_confidence": "medium" if mood_source == "centroid" else "high",
        "audio_features": _features(tempo=tempo),
    })


def test_heavy_bass_removed_from_centroid_row() -> None:
    rows = [_row("A", "x", mood_tags=["Heavy Bass", "Dance"], mood_source="centroid")]
    cleaned, stats = clean_centroid_moods(rows)
    assert cleaned[0]["mood_tags"] == ["Dance"]
    assert cleaned[0]["mood_source"] == "centroid"
    assert stats["removed_by_mood"]["Heavy Bass"] == 1


def test_slow_fast_tempo_removed() -> None:
    rows = [_row("A", "x", mood_tags=["Slow", "Dance"], mood_source="centroid", tempo=160)]
    cleaned, _ = clean_centroid_moods(rows)
    assert "Slow" not in cleaned[0]["mood_tags"]
    assert "Dance" in cleaned[0]["mood_tags"]


def test_slow_kept_when_actually_slow() -> None:
    rows = [_row("A", "x", mood_tags=["Slow"], mood_source="centroid", tempo=80)]
    cleaned, _ = clean_centroid_moods(rows)
    assert cleaned[0]["mood_tags"] == ["Slow"]


def test_row_reduced_to_empty_clears_triple() -> None:
    rows = [_row("A", "x", mood_tags=["Heavy Bass"], mood_source="centroid")]
    cleaned, stats = clean_centroid_moods(rows)
    assert cleaned[0]["mood_tags"] is None
    assert cleaned[0]["mood_source"] is None
    assert cleaned[0]["mood_confidence"] is None
    assert stats["rows_cleared"] == 1


def test_audit_and_claude_rows_untouched() -> None:
    rows = [
        _row("A", "x", mood_tags=["Heavy Bass", "Slow"], mood_source="audit", tempo=160),
        _row("B", "y", mood_tags=["Heavy Bass"], mood_source="claude_batch"),
    ]
    cleaned, stats = clean_centroid_moods(rows)
    assert cleaned[0]["mood_tags"] == ["Heavy Bass", "Slow"]
    assert cleaned[1]["mood_tags"] == ["Heavy Bass"]
    assert stats["rows_changed"] == 0


def test_idempotent() -> None:
    rows = [_row("A", "x", mood_tags=["Heavy Bass", "Moody"], mood_source="centroid", tempo=160)]
    once, _ = clean_centroid_moods(rows)
    twice, stats2 = clean_centroid_moods(once)
    assert stats2["rows_changed"] == 0
    assert twice[0]["mood_tags"] == once[0]["mood_tags"]


def test_output_passes_schema_validation() -> None:
    rows = [
        _row("A", "x", mood_tags=["Heavy Bass", "Slow"], mood_source="centroid", tempo=160),
        _row("B", "y", mood_tags=["Heavy Bass"], mood_source="centroid"),
        _row("C", "z", mood_tags=["Dance"], mood_source="audit"),
    ]
    cleaned, _ = clean_centroid_moods(rows)
    cleaned = [fill_defaults(r) for r in cleaned]
    result = validate_dataset(cleaned)
    assert result["invalid_count"] == 0


def test_parity_with_classifier_policy() -> None:
    """Cleanup result on a centroid row == apply_centroid_policy on the same input."""
    tags = ["Heavy Bass", "Slow", "Moody", "Dance"]
    feats = _features(tempo=140)
    rows = [_row("A", "x", mood_tags=list(tags), mood_source="centroid", tempo=140)]
    cleaned, _ = clean_centroid_moods(rows)
    expected = apply_centroid_policy(tags, feats)
    assert cleaned[0]["mood_tags"] == expected
