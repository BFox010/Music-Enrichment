"""Invariants for play-weighted tag mass.

These guard the property that makes the dashboard's tag charts truthful: a
scrobble contributes exactly 1.0 of mass, no matter how many tags its track
carries, and tag totals describe listening rather than library composition.

Fixtures are written to temp files and injected with ``data.use_paths()``, the
same pattern as ``tests/test_app_api.py``.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

import app.data as data
from app import metrics


def _track(artist, track, moods, *, canonical=None, **extra):
    row = {
        "artist": artist,
        "track": track,
        "artist_normalized": artist.lower(),
        "track_normalized": track.lower(),
        "mood_tags": moods,
        "genres": ["Rock"],
        "play_count": extra.pop("play_count", 1),
    }
    if canonical:
        row["canonical_track_id"] = canonical
    row.update(extra)
    return row


def _scrobble(artist, track, stamp, **extra):
    year, month = int(stamp[:4]), int(stamp[5:7])
    row = {
        "artist": artist,
        "track": track,
        "artist_normalized": artist.lower(),
        "track_normalized": track.lower(),
        "scrobbled_at": f"{stamp}T12:00:00Z",
        "year": year,
        "month": month,
    }
    row.update(extra)
    return row


@contextmanager
def _library(tracks, scrobbles):
    with tempfile.TemporaryDirectory() as tmp:
        tp, sp = Path(tmp) / "t.jsonl", Path(tmp) / "s.jsonl"
        tp.write_text("".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8")
        sp.write_text("".join(json.dumps(s) + "\n" for s in scrobbles), encoding="utf-8")
        with data.use_paths(tp, sp):
            yield


class TestMassConservation:
    def test_one_play_is_one_unit_regardless_of_tag_count(self):
        """A 4-tag track must not outvote a 1-tag track on the same play count.

        This is the unequal-franchise bug: tags per track vary by which pipeline
        labeled them, so counting each tag fully let labeling accidents shape
        the chart.
        """
        tracks = [
            _track("A", "one", ["Fast", "Moody", "Dance", "Slow"]),
            _track("B", "two", ["Fast"]),
        ]
        scrobbles = [_scrobble("A", "one", "2025-01-01"),
                     _scrobble("B", "two", "2025-01-02")]
        with _library(tracks, scrobbles):
            mass, cov = metrics.tag_mass("mood_tags")
            assert sum(mass.values()) == pytest.approx(2.0)
            assert cov["tagged_plays"] == 2
            # Each track contributed 1.0 total: A gave Fast 0.25, B gave 1.0.
            assert mass["Fast"] == pytest.approx(1.25)
            assert mass["Slow"] == pytest.approx(0.25)

    def test_repeated_plays_accumulate(self):
        """Listening 3 times weighs 3x — the whole point of play-weighting."""
        tracks = [_track("A", "one", ["Fast"])]
        scrobbles = [_scrobble("A", "one", f"2025-01-0{i}") for i in (1, 2, 3)]
        with _library(tracks, scrobbles):
            mass, cov = metrics.tag_mass("mood_tags")
            assert mass["Fast"] == pytest.approx(3.0)
            assert cov["tagged_plays"] == 3

    def test_mass_equals_tagged_plays_in_every_window(self):
        tracks = [_track("A", "one", ["Fast", "Moody"]),
                  _track("B", "two", ["Slow"])]
        scrobbles = [
            _scrobble("A", "one", "2024-03-01"),
            _scrobble("B", "two", "2025-07-01"),
            _scrobble("A", "one", "2025-07-02"),
        ]
        with _library(tracks, scrobbles):
            for window in (None, "2024", "2025", "2025-summer", "2025-07"):
                mass, cov = metrics.tag_mass("mood_tags", window)
                assert sum(mass.values()) == pytest.approx(cov["tagged_plays"]), window

    def test_untagged_plays_counted_but_carry_no_mass(self):
        """Coverage must be visible: an untagged play is real listening that
        contributes nothing to the shares, so the denominator has to say so."""
        tracks = [_track("A", "one", ["Fast"]), _track("B", "two", None)]
        scrobbles = [_scrobble("A", "one", "2025-01-01"),
                     _scrobble("B", "two", "2025-01-02")]
        with _library(tracks, scrobbles):
            mass, cov = metrics.tag_mass("mood_tags")
            assert cov["plays"] == 2
            assert cov["tagged_plays"] == 1
            assert sum(mass.values()) == pytest.approx(1.0)

    def test_duplicate_tags_on_a_track_do_not_inflate(self):
        tracks = [_track("A", "one", ["Fast", "Fast", "Moody"])]
        with _library(tracks, [_scrobble("A", "one", "2025-01-01")]):
            mass, _ = metrics.tag_mass("mood_tags")
            assert sum(mass.values()) == pytest.approx(1.0)
            assert mass["Fast"] == pytest.approx(0.5)


class TestIdentityJoin:
    def test_canonical_id_on_tracks_only_still_joins(self):
        """Regression: tracks.jsonl carries canonical_track_id and
        scrobbles.jsonl does not. Keying the index solely on the canonical ID
        matched nothing and silently reported zero tagged plays."""
        tracks = [_track("A", "one", ["Fast"], canonical="mbid:abc-123")]
        with _library(tracks, [_scrobble("A", "one", "2025-01-01")]):
            _, cov = metrics.tag_mass("mood_tags")
            assert cov["tagged_plays"] == 1

    def test_canonical_id_on_both_sides_joins(self):
        tracks = [_track("A", "one", ["Fast"], canonical="mbid:abc-123")]
        scrobble = _scrobble("A", "one", "2025-01-01",
                             canonical_track_id="mbid:abc-123")
        with _library(tracks, [scrobble]):
            _, cov = metrics.tag_mass("mood_tags")
            assert cov["tagged_plays"] == 1

    def test_canonical_id_wins_over_a_stale_name(self):
        """Once identity is resolved, a renamed credit still joins by ID."""
        tracks = [_track("Artist Full Credit", "one", ["Fast"],
                         canonical="mbid:abc-123")]
        scrobble = _scrobble("Artist", "one", "2025-01-01",
                             canonical_track_id="mbid:abc-123")
        with _library(tracks, [scrobble]):
            _, cov = metrics.tag_mass("mood_tags")
            assert cov["tagged_plays"] == 1


class TestPlayCountIntegrity:
    """tracks.jsonl caches a play count that Phase 2 derives from scrobbles.

    The two files drift apart whenever scrobbles change without the track rows
    being rebuilt — the exact condition a fresh Last.fm export creates. Every
    play-weighted chart inherits that error silently, so it needs a hard check.
    """

    def test_in_sync_library_reports_clean(self):
        tracks = [_track("A", "one", ["Fast"], play_count=2)]
        scrobbles = [_scrobble("A", "one", "2025-01-01"),
                     _scrobble("A", "one", "2025-01-02")]
        with _library(tracks, scrobbles):
            r = metrics.play_count_integrity()
            assert r["in_sync"]
            assert r["declared_total"] == r["actual_total"] == 2

    def test_stale_play_count_is_caught(self):
        """New scrobbles ingested without rebuilding track rows."""
        tracks = [_track("A", "one", ["Fast"], play_count=2)]
        scrobbles = [_scrobble("A", "one", f"2025-01-0{i}") for i in (1, 2, 3)]
        with _library(tracks, scrobbles):
            r = metrics.play_count_integrity()
            assert not r["in_sync"]
            assert r["mismatched_tracks"] == 1
            assert r["worst"][0]["delta"] == 1

    def test_orphaned_scrobbles_are_counted(self):
        """A play whose track has no row at all — invisible to every tag chart."""
        tracks = [_track("A", "one", ["Fast"], play_count=1)]
        scrobbles = [_scrobble("A", "one", "2025-01-01"),
                     _scrobble("Ghost", "missing", "2025-01-02")]
        with _library(tracks, scrobbles):
            r = metrics.play_count_integrity()
            assert r["unmatched_scrobbles"] == 1
            assert not r["in_sync"]

    def test_merged_aliases_do_not_read_as_drift(self):
        """After identity resolution one row absorbs several credits; its
        play_count covers all of them and must not look like a mismatch."""
        track = _track("A B", "song", ["Fast"], play_count=2)
        track["identity_aliases"] = [["a", "song"], ["a b", "song"]]
        scrobbles = [_scrobble("A", "song", "2025-01-01"),
                     _scrobble("A B", "song", "2025-01-02")]
        with _library([track], scrobbles):
            assert metrics.play_count_integrity()["in_sync"]


class TestAliasResolution:
    def test_plays_under_a_merged_credit_still_join(self):
        """After identity resolution one row represents several credits.

        scrobbles.jsonl is never rewritten — it stays the immutable record of
        what was played — so plays logged under the old credit have to resolve
        through identity_aliases or they vanish from every chart.
        """
        track = _track("Clipse, Pharrell Williams", "So Far Ahead", ["Moody"])
        track["identity_aliases"] = [
            ["clipse", "so far ahead"],
            ["clipse, pharrell williams", "so far ahead"],
        ]
        scrobbles = [
            _scrobble("Clipse", "So Far Ahead", "2025-01-01"),
            _scrobble("Clipse, Pharrell Williams", "So Far Ahead", "2025-01-02"),
        ]
        with _library([track], scrobbles):
            mass, cov = metrics.tag_mass("mood_tags")
            assert cov["tagged_plays"] == 2
            assert mass["Moody"] == pytest.approx(2.0)

    def test_alias_does_not_shadow_a_real_track(self):
        """An alias must never displace a row that owns that name outright."""
        merged = _track("A B", "song", ["Fast"])
        merged["identity_aliases"] = [["a", "song"], ["a b", "song"]]
        real = _track("A", "song", ["Slow"])
        with _library([real, merged], [_scrobble("A", "song", "2025-01-01")]):
            mass, _ = metrics.tag_mass("mood_tags")
            assert mass["Slow"] == pytest.approx(1.0)
            assert "Fast" not in mass


class TestBlacklistNeverFilters:
    def test_blacklisted_plays_are_still_counted(self):
        """A dashboard must report music that was demonstrably played.

        ``blacklisted`` is a playlist-generation exclusion inherited from this
        project's origin as a recommender. Wiring it into a count would delete
        real listening history from the record.
        """
        tracks = [
            _track("A", "one", ["Fast"], blacklisted=True),
            _track("B", "two", ["Slow"]),
        ]
        scrobbles = [_scrobble("A", "one", "2025-01-01"),
                     _scrobble("B", "two", "2025-01-02")]
        with _library(tracks, scrobbles):
            mass, cov = metrics.tag_mass("mood_tags")
            assert cov["plays"] == 2
            assert mass["Fast"] == pytest.approx(1.0)


class TestCoverageFields:
    def test_saturation_tier_is_not_an_enrichment_metric(self):
        """saturation_tier is a curation choice, not missing data. Counting it
        as coverage understated completeness for every track whose artist the
        owner simply hasn't tiered."""
        assert "saturation_tier" not in dict(metrics._COVERAGE_FIELDS)
