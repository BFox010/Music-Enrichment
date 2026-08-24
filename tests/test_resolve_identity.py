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

from pipeline.config import TRACKS_PATH
from pipeline.name_variations import strip_feat
from pipeline.normalize import normalize_artist, normalize_track
from pipeline.resolve_identity import (
    _deconflict_aliases,
    is_credit_variant,
    merge_cluster,
    resolve,
)


def _row(artist, track, **extra):
    row = {
        "artist": artist,
        "track": track,
        "artist_normalized": normalize_artist(artist),
        "track_normalized": normalize_track(track),
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

    def test_feat_in_title_merges_when_artist_is_unchanged(self):
        """The far more common shape: the guest credit never left the title,
        so the artist field is identical on both rows."""
        a = _row("kanye west", "FML")
        b = _row("kanye west", "FML (feat. The Weeknd)")
        assert is_credit_variant(a, b)
        assert is_credit_variant(b, a)

    def test_feat_in_title_merges_across_bracket_style(self):
        a = _row("a$ap rocky", "Highjack")
        b = _row("a$ap rocky", "Highjack (feat. Jessica Pratt)")
        assert is_credit_variant(a, b)

    def test_feat_in_title_still_requires_matching_stripped_title(self):
        a = _row("kanye west", "FML")
        b = _row("kanye west", "Waves (feat. Chris Brown)")
        assert not is_credit_variant(a, b)

    def test_conflicting_isrc_vetoes_a_feat_in_title_merge(self):
        a = _row("kanye west", "FML", isrc="AAA111")
        b = _row("kanye west", "FML (feat. The Weeknd)", isrc="BBB222")
        assert not is_credit_variant(a, b)

    def test_conflicting_mbid_is_demoted_from_veto_to_tiebreak(self):
        """MusicBrainz recording IDs are demoted from a hard veto to a
        tiebreak: Last.fm routinely returns different recording MBIDs for the
        same recording under different credit strings, so a disagreement here
        should route to review, not silently block a merge the credit shape
        otherwise supports."""
        a = _row("clipse", "so far ahead", musicbrainz_id="aaa")
        b = _row("clipse pharrell williams pusha t malice", "so far ahead",
                  musicbrainz_id="bbb")
        assert not is_credit_variant(a, b)  # still doesn't auto-merge...
        from pipeline.resolve_identity import _shape_matches
        assert _shape_matches(a, b)          # ...but the shape is recognised

    def test_conflicting_mbid_does_not_block_a_same_artist_feat_in_title_merge(self):
        """The artist field never changed, so Last.fm had no different string
        to hash a different MBID from — a conflict here is expected noise,
        not evidence of a different recording."""
        a = _row("kendrick lamar", "Money Trees", musicbrainz_id="aaa")
        b = _row("kendrick lamar", "Money Trees (feat. Jay Rock)", musicbrainz_id="bbb")
        assert is_credit_variant(a, b)


class TestIsrcVetoRespectsProvenance:
    """Only an exact-join ISRC may overrule matching credit shape.

    Phase 5a now runs before this phase, so most rows carry a Deezer ISRC from a
    name search — and searching two title variants of one recording lands on two
    different releases. Treating that as decisive split three recordings the
    credit evidence had correctly paired ("DARE" vs "DARE (feat. Shaun Ryder &
    Roses Gabor)"). A MusicBrainz ISRC is an MBID join and still vetoes.
    """

    def _pair(self, **extra_b):
        a = _row("Gorillaz", "DARE", isrc="GBAAA0000001",
                 isrc_source=extra_b.pop("a_source", None))
        b = _row("Gorillaz", "DARE (feat. Shaun Ryder)", **extra_b)
        return a, b

    def test_conflicting_musicbrainz_isrcs_still_veto(self):
        a, b = self._pair(a_source="musicbrainz", isrc="GBAAA0000002",
                          isrc_source="musicbrainz")
        assert not is_credit_variant(a, b)

    def test_conflicting_deezer_isrcs_do_not_veto(self):
        a, b = self._pair(a_source="deezer", isrc="GBAAA0000002",
                          isrc_source="deezer")
        assert is_credit_variant(a, b)

    def test_one_deezer_side_is_enough_to_lift_the_veto(self):
        a, b = self._pair(a_source="musicbrainz", isrc="GBAAA0000002",
                          isrc_source="deezer")
        assert is_credit_variant(a, b)

    def test_unknown_provenance_is_treated_as_decisive(self):
        """A wrong veto leaves a visible duplicate; a wrong merge sums plays."""
        a, b = self._pair(isrc="GBAAA0000002")
        assert not is_credit_variant(a, b)

    def test_matching_isrcs_never_veto(self):
        a, b = self._pair(a_source="deezer", isrc="GBAAA0000001",
                          isrc_source="musicbrainz")
        assert is_credit_variant(a, b)


class TestCanonicalIdIsStampedOnEveryRow:
    """4e is the phase that decides identity, so it must set it on every row.

    Only merged clusters used to get one — 163 of 3255 rows on 2026-08-24 —
    leaving the rest to fill_defaults() at Phase 8, which derived the id from
    fields several later phases had already changed. Invariant 4 asks every
    phase to preserve canonical_track_id; this is where it comes from.
    """

    def test_singleton_row_gets_an_id(self):
        merged = merge_cluster([_row("portishead", "roads", play_count=5)])
        assert merged["canonical_track_id"] == "norm:portishead|roads"

    def test_merged_cluster_gets_an_id(self):
        rows = [_row("clipse", "so far ahead", play_count=2),
                _row("clipse pharrell", "so far ahead", play_count=22)]
        assert merge_cluster(rows)["canonical_track_id"]

    def test_mbid_outranks_the_name_for_a_singleton(self):
        merged = merge_cluster(
            [_row("portishead", "roads", play_count=5, musicbrainz_id="abc-123")]
        )
        assert merged["canonical_track_id"] == "mbid:abc-123"

    def test_every_resolved_row_carries_one(self, tmp_path):
        rows = [
            _row("portishead", "roads", play_count=5),
            _row("clipse", "so far ahead", play_count=2),
            _row("clipse pharrell", "so far ahead", play_count=22),
            _row("boards of canada", "roygbiv", play_count=9),
        ]
        src = tmp_path / "in.jsonl"
        src.write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        out = tmp_path / "out.jsonl"
        resolve(input_path=src, output_path=out,
                review_path=tmp_path / "review.jsonl",
                run_log_path=tmp_path / "run.log")
        written = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert written, "phase produced no rows"
        blank = [r for r in written if not r.get("canonical_track_id")]
        assert not blank, f"{len(blank)} row(s) left without a canonical_track_id"


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

    def test_feat_in_title_split_collapses(self):
        """The shape the audit found most: same artist, guest credit only in
        the title, never entered the comparison loop before because the two
        rows landed in different title buckets."""
        rows = [_row("kendrick lamar", "Money Trees", play_count=8),
                _row("kendrick lamar", "Money Trees (feat. Jay Rock)", play_count=25)]
        stats, written = self._run(rows)
        assert stats["output"] == 1
        assert written[0]["play_count"] == 33

    def test_feat_in_title_split_collapses_regardless_of_which_row_has_it(self):
        rows = [_row("kanye west", "Waves", play_count=20),
                _row("kanye west", "Waves (feat. Chris Brown & Kid Cudi)", play_count=7)]
        stats, written = self._run(rows)
        assert stats["output"] == 1
        assert written[0]["play_count"] == 27

    def test_mbid_conflict_on_a_matching_shape_goes_to_review_not_a_merge(self):
        """Prefix-artist case: the credit itself changed, so a conflicting
        MBID is informative — held for review rather than merged blind."""
        rows = [_row("clipse", "so far ahead", play_count=2, musicbrainz_id="aaa"),
                _row("clipse pharrell williams pusha t malice", "so far ahead",
                     play_count=22, musicbrainz_id="bbb")]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "in.jsonl", Path(tmp) / "out.jsonl"
            review = Path(tmp) / "review.jsonl"
            src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            stats = resolve(input_path=src, output_path=out, review_path=review)
            assert stats["output"] == 2  # not merged
            assert "MusicBrainz IDs conflict" in review.read_text(encoding="utf-8")

    def test_mbid_conflict_on_a_same_artist_feat_split_still_merges(self):
        """Same-artist case: the artist field never changed, so a conflicting
        MBID is expected Last.fm noise, not evidence of a different
        recording — merges anyway rather than sitting in review forever."""
        rows = [_row("kendrick lamar", "Money Trees", play_count=8, musicbrainz_id="aaa"),
                _row("kendrick lamar", "Money Trees (feat. Jay Rock)", play_count=25,
                     musicbrainz_id="bbb")]
        stats, written = self._run(rows)
        assert stats["output"] == 1
        assert written[0]["play_count"] == 33

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


class TestIdempotence:
    """Re-running the phase must converge, not accumulate.

    Fresh metadata can reveal that two rows merged on an earlier pass are
    actually distinct — a newly-fetched MBID that disagrees, for instance. If
    the stale alias list survives that split, both rows go on claiming the same
    name and the aggregation layer routes one row's plays to the other.
    """

    def test_inherited_aliases_survive_a_rerun(self):
        """An absorbed row no longer exists to contribute its own name, so the
        alias it left behind is the only thing keeping its plays reachable."""
        previously_merged = _row("clipse pharrell", "so far ahead", play_count=24)
        previously_merged["identity_aliases"] = [
            ["clipse pharrell", "so far ahead"],
            ["clipse", "so far ahead"],
        ]
        merged = merge_cluster([previously_merged])
        assert ["clipse", "so far ahead"] in merged["identity_aliases"]

    def test_stale_claim_is_dropped_when_a_live_row_owns_the_name(self):
        """Fresh metadata can split a previously-merged pair. De-confliction is
        a whole-file question — one row cannot know what the others own — so it
        happens in resolve(), not in merge_cluster()."""
        a = _row("run the jewels", "blockbuster night part 1",
                 musicbrainz_id="aaa", play_count=1)
        a["identity_aliases"] = [
            ["run the jewels", "blockbuster night part 1"],
            ["run the jewels", "blockbuster night pt 1"],
        ]
        b = _row("run the jewels", "blockbuster night pt 1",
                 musicbrainz_id="bbb", play_count=2)
        rows = [a, b]
        _deconflict_aliases(rows)
        assert rows[0]["identity_aliases"] == [
            ["run the jewels", "blockbuster night part 1"]
        ]
        assert rows[1]["identity_aliases"] == [
            ["run the jewels", "blockbuster night pt 1"]
        ]

    def test_single_row_always_declares_its_own_name(self):
        merged = merge_cluster([_row("artist", "song")])
        assert merged["identity_aliases"] == [["artist", "song"]]

    def test_conflicting_mbids_split_a_previously_merged_pair(self):
        a = _row("run the jewels", "blockbuster night part 1",
                 musicbrainz_id="aaa", play_count=1)
        b = _row("run the jewels", "blockbuster night pt 1",
                 musicbrainz_id="bbb", play_count=2)
        assert not is_credit_variant(a, b)

    def test_every_alias_is_claimed_exactly_once(self):
        """The invariant that failed in practice: no name may appear on two
        rows, or play counts get misrouted between them."""
        rows = [
            _row("clipse", "so far ahead", play_count=2),
            _row("clipse pharrell", "so far ahead", play_count=22),
            _row("run the jewels", "blockbuster night part 1",
                 musicbrainz_id="aaa", play_count=1),
            _row("run the jewels", "blockbuster night pt 1",
                 musicbrainz_id="bbb", play_count=2),
        ]
        import json as _json
        import tempfile as _tf
        from pathlib import Path as _P
        with _tf.TemporaryDirectory() as tmp:
            src, out = _P(tmp) / "in.jsonl", _P(tmp) / "out.jsonl"
            src.write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            resolve(input_path=src, output_path=out, review_path=_P(tmp) / "r.jsonl")
            written = [_json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
        seen = [tuple(a) for t in written for a in t["identity_aliases"]]
        assert len(seen) == len(set(seen)), "an alias is claimed by more than one row"


class TestAliasUniqueness:
    """A name may be claimed by exactly one row.

    Two rows can inherit the same alias when each merged with the absorbed row
    at some point in its history. Left alone, the aggregation layer routes one
    row's plays to the other — which is how it surfaced: two Run the Jewels
    rows, one over-counted by a play and one under-counted by the same play.
    """

    def test_contested_alias_goes_to_the_matching_title(self):
        a = _row("run the jewels", "blockbuster night part 1", play_count=1)
        a["identity_aliases"] = [["run the jewels killer mike el p",
                                  "blockbuster night pt 1"]]
        b = _row("run the jewels", "blockbuster night pt 1", play_count=2)
        b["identity_aliases"] = [["run the jewels killer mike el p",
                                  "blockbuster night pt 1"]]
        rows = [a, b]
        _deconflict_aliases(rows)
        assert ["run the jewels killer mike el p", "blockbuster night pt 1"] \
            in b["identity_aliases"]
        assert ["run the jewels killer mike el p", "blockbuster night pt 1"] \
            not in a["identity_aliases"]

    def test_no_alias_is_claimed_twice(self):
        a = _row("x", "one", play_count=5)
        a["identity_aliases"] = [["ghost", "gone"]]
        b = _row("y", "two", play_count=9)
        b["identity_aliases"] = [["ghost", "gone"]]
        rows = [a, b]
        _deconflict_aliases(rows)
        claimed = [tuple(al) for r in rows for al in r["identity_aliases"]]
        assert len(claimed) == len(set(claimed))

    def test_result_is_deterministic(self):
        def build():
            a = _row("x", "one", play_count=5)
            a["identity_aliases"] = [["ghost", "gone"]]
            b = _row("y", "two", play_count=9)
            b["identity_aliases"] = [["ghost", "gone"]]
            return [a, b]

        first, second = build(), build()
        _deconflict_aliases(first)
        _deconflict_aliases(second)
        assert [r["identity_aliases"] for r in first] == \
               [r["identity_aliases"] for r in second]

    def test_own_name_is_never_surrendered(self):
        a = _row("artist", "song", play_count=1)
        b = _row("other", "thing", play_count=99)
        b["identity_aliases"] = [["artist", "song"]]
        rows = [a, b]
        _deconflict_aliases(rows)
        assert a["identity_aliases"] == [["artist", "song"]]
        assert ["artist", "song"] not in b["identity_aliases"]


class TestCommittedLibraryHasNoResidualSplits:
    """Regression guard for the 87-cluster feat-in-title split (#61).

    ``tracks.jsonl`` is Phase 8 output — it should already reflect a Phase 4e
    run under the current rules. If two rows share an
    (artist_normalized, feat-stripped title) pair, either the merge rule
    regressed or the committed file is stale relative to the pipeline.
    """

    @pytest.mark.skipif(not TRACKS_PATH.exists(), reason="tracks.jsonl not present")
    def test_no_two_rows_share_artist_and_feat_stripped_title(self):
        seen: dict[tuple[str, str], str] = {}
        dupes: list[str] = []
        with open(TRACKS_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = (
                    row.get("artist_normalized") or "",
                    normalize_track(strip_feat(row.get("track") or "")),
                )
                if key == ("", ""):
                    continue
                if key in seen:
                    dupes.append(f"{key} — {seen[key]!r} / {row.get('track')!r}")
                else:
                    seen[key] = row.get("track")
        assert not dupes, f"{len(dupes)} unresolved credit-variant split(s): {dupes[:5]}"
