"""Tests for pipeline.resolve_identity — credit-variant merging.

The merge must be conservative in both directions: fold genuine duplicates so
one recording is not classified twice and its plays are not split, but never
collapse two distinct recordings that merely share a title.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pipeline.resolve_identity import is_credit_variant, merge_cluster, resolve


def _row(artist, track, **extra):
    row = {
        "artist": artist,
        "track": track,
        "artist_normalized": artist.lower(),
        "track_normalized": track.lower(),
        "play_count": extra.pop("play_count", 1),
    }
    row.update(extra)
    return row


class TestIsCreditVariant:
    def test_featured_artist_expansion_merges(self):
        a = _row("clipse", "so far ahead")
        b = _row("clipse pharrell williams pusha t malice", "so far ahead")
        assert is_credit_variant(a, b)
        assert is_credit_variant(b, a)

    def test_different_titles_never_merge(self):
        a = _row("clipse", "so far ahead")
        b = _row("clipse pharrell", "different song")
        assert not is_credit_variant(a, b)

    def test_requires_word_boundary(self):
        """"the door" must not absorb "the doorsmen" on a raw prefix match."""
        a = _row("the door", "x")
        b = _row("the doorsmen", "x")
        assert not is_credit_variant(a, b)

    def test_conflicting_mbids_veto_the_merge(self):
        """Two different recordings can share a title and similar credits.

        A disagreeing strong identifier is decisive — it means the metadata
        already knows these are distinct.
        """
        a = _row("artist", "song", musicbrainz_id="aaa")
        b = _row("artist featuring someone", "song", musicbrainz_id="bbb")
        assert not is_credit_variant(a, b)

    def test_conflicting_isrcs_veto_the_merge(self):
        a = _row("artist", "song", isrc="AAA111")
        b = _row("artist featuring someone", "song", isrc="BBB222")
        assert not is_credit_variant(a, b)

    def test_matching_mbid_still_allows_name_merge(self):
        a = _row("artist", "song", musicbrainz_id="same")
        b = _row("artist featuring someone", "song", musicbrainz_id="same")
        assert is_credit_variant(a, b)

    def test_short_names_are_not_prefixes(self):
        a = _row("m", "song")
        b = _row("m people", "song")
        assert not is_credit_variant(a, b)

    def test_identical_artists_are_not_variants(self):
        a = _row("artist", "song")
        assert not is_credit_variant(a, dict(a))


class TestMergeCluster:
    def test_plays_are_summed(self):
        rows = [_row("clipse", "so far ahead", play_count=2),
                _row("clipse pharrell", "so far ahead", play_count=22)]
        assert merge_cluster(rows)["play_count"] == 24

    def test_owner_label_beats_a_louder_centroid_row(self):
        """A hand-made judgement on the quieter variant must win.

        Before merging, the same recording could be Slow on one row and
        not-Slow on the other; picking by play count alone would let a guess
        overrule the owner.
        """
        rows = [
            _row("clipse", "x", play_count=2,
                 mood_tags=["Moody"], mood_source="audit", mood_confidence="high"),
            _row("clipse pharrell", "x", play_count=99,
                 mood_tags=["Slow"], mood_source="centroid", mood_confidence="medium"),
        ]
        merged = merge_cluster(rows)
        assert merged["mood_tags"] == ["Moody"]
        assert merged["mood_source"] == "audit"

    def test_display_name_comes_from_the_most_played_row(self):
        rows = [_row("clipse", "x", play_count=2),
                _row("clipse pharrell", "x", play_count=22)]
        assert merge_cluster(rows)["artist"] == "clipse pharrell"

    def test_enrichment_is_unioned_not_replaced(self):
        rows = [_row("a", "x", play_count=9, genres=["Rock"], lastfm_tags=["indie"]),
                _row("a b", "x", play_count=1, genres=["Pop"], lastfm_tags=["indie", "uk"])]
        merged = merge_cluster(rows)
        assert set(merged["genres"]) == {"Rock", "Pop"}
        assert set(merged["lastfm_tags"]) == {"indie", "uk"}

    def test_scalar_gaps_are_filled_from_other_rows(self):
        rows = [_row("a", "x", play_count=9, isrc=None, release_year=None),
                _row("a b", "x", play_count=1, isrc="AAA111", release_year=2011)]
        merged = merge_cluster(rows)
        assert merged["isrc"] == "AAA111"
        assert merged["release_year"] == 2011

    def test_aliases_record_every_name_played(self):
        rows = [_row("clipse", "x"), _row("clipse pharrell", "x")]
        aliases = merge_cluster(rows)["identity_aliases"]
        assert ["clipse", "x"] in aliases
        assert ["clipse pharrell", "x"] in aliases

    def test_scrobble_dates_span_the_cluster(self):
        rows = [_row("a", "x", first_scrobbled="2021-01-01", last_scrobbled="2021-06-01"),
                _row("a b", "x", first_scrobbled="2020-01-01", last_scrobbled="2023-01-01")]
        merged = merge_cluster(rows)
        assert merged["first_scrobbled"] == "2020-01-01"
        assert merged["last_scrobbled"] == "2023-01-01"

    def test_single_row_passes_through(self):
        row = _row("a", "x", play_count=5)
        assert merge_cluster([row])["play_count"] == 5


class TestResolve:
    def _run(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            review = Path(tmp) / "review.jsonl"
            src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            stats = resolve(input_path=src, output_path=out, review_path=review)
            written = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
            return stats, written

    def test_merges_credit_variants(self):
        rows = [_row("clipse", "so far ahead", play_count=2),
                _row("clipse pharrell williams", "so far ahead", play_count=22),
                _row("unrelated", "other song", play_count=5)]
        stats, written = self._run(rows)
        assert stats["output"] == 2
        assert stats["merged_rows"] == 1

    def test_shared_isrc_merges_across_different_titles(self):
        """Title annotations ("(Slowed)", featured-artist lists) differ while
        the recording is the same; a shared ISRC settles it."""
        rows = [_row("artist", "song", play_count=2, isrc="AAA111"),
                _row("artist", "song slowed", play_count=6, isrc="AAA111")]
        stats, _ = self._run(rows)
        assert stats["output"] == 1

    def test_distinct_recordings_sharing_a_title_stay_apart(self):
        rows = [_row("nirvana", "something in the way", play_count=10),
                _row("the beatles", "something in the way", play_count=3)]
        stats, _ = self._run(rows)
        assert stats["output"] == 2

    def test_total_plays_are_conserved(self):
        rows = [_row("clipse", "x", play_count=2),
                _row("clipse pharrell", "x", play_count=22),
                _row("other", "y", play_count=7)]
        _, written = self._run(rows)
        assert sum(r["play_count"] for r in written) == 31

    def test_near_misses_are_written_for_review(self):
        """Same title, credits that are not a prefix variant — surfaced rather
        than merged, so the conservative rule stays auditable."""
        rows = [_row("nirvana", "something in the way", play_count=10),
                _row("the beatles", "something in the way", play_count=3)]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "in.jsonl", Path(tmp) / "out.jsonl"
            review = Path(tmp) / "review.jsonl"
            src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            resolve(input_path=src, output_path=out, review_path=review)
            assert review.exists()
            assert "something in the way" in review.read_text(encoding="utf-8")
