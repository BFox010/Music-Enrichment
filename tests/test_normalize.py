"""Tests for pipeline.normalize."""

from __future__ import annotations

import json

from pipeline.normalize import join_key, normalize_artist, normalize_track


class TestNormalizeArtist:
    def test_lowercase(self) -> None:
        assert normalize_artist("PORTISHEAD") == "portishead"

    def test_strips_leading_the(self) -> None:
        assert normalize_artist("The Beatles") == "beatles"
        assert normalize_artist("THE NATIONAL") == "national"
        assert normalize_artist("the xx") == "xx"

    def test_does_not_strip_inner_the(self) -> None:
        assert normalize_artist("Edward Sharpe And The Magnetic Zeros") == (
            "edward sharpe and the magnetic zeros"
        )

    def test_punctuation_to_space(self) -> None:
        assert normalize_artist("a-ha") == "a ha"
        assert normalize_artist("AC/DC") == "ac dc"
        assert normalize_artist("$uicideboy$") == "uicideboy"

    def test_apostrophes_removed(self) -> None:
        assert normalize_artist("Guns N' Roses") == "guns n roses"
        assert normalize_artist("Sinéad O’Connor") == "sinead oconnor"

    def test_diacritics_stripped(self) -> None:
        assert normalize_artist("Beyoncé") == "beyonce"
        assert normalize_artist("Mötley Crüe") == "motley crue"
        assert normalize_artist("Sigur Rós") == "sigur ros"

    def test_feat_collapses(self) -> None:
        assert normalize_artist("Drake feat. Future") == "drake feat future"
        assert normalize_artist("Drake ft Future") == "drake feat future"
        assert normalize_artist("Drake featuring Future") == "drake feat future"
        assert normalize_artist("Drake FT. Future") == "drake feat future"
        assert normalize_artist("Drake Ft. Future") == "drake feat future"

    def test_ampersand_joins_with_spelled_out_and(self) -> None:
        assert normalize_artist("J Roddy Walston & The Business") == \
            normalize_artist("J Roddy Walston and The Business")
        assert normalize_artist("Big Data & Joywave") == "big data and joywave"

    def test_ampersand_collapses_surrounding_whitespace(self) -> None:
        assert normalize_artist("Simon&Garfunkel") == "simon and garfunkel"
        assert normalize_artist("Simon  &  Garfunkel") == "simon and garfunkel"

    def test_whitespace_collapsed(self) -> None:
        assert normalize_artist("  the   beatles   ") == "beatles"

    def test_empty(self) -> None:
        assert normalize_artist("") == ""

    def test_idempotent(self) -> None:
        once = normalize_artist("The Beatles feat. Yoko Onö")
        twice = normalize_artist(once)
        assert once == twice


class TestNormalizeTrack:
    def test_lowercase(self) -> None:
        assert normalize_track("ROADS") == "roads"

    def test_does_not_strip_leading_the(self) -> None:
        assert normalize_track("The Less I Know The Better") == (
            "the less i know the better"
        )

    def test_apostrophes_removed(self) -> None:
        assert normalize_track("Don't Stop Believin'") == "dont stop believin"

    def test_punctuation_to_space(self) -> None:
        assert normalize_track("Hey, Soul Sister") == "hey soul sister"
        assert normalize_track("(I Can't Get No) Satisfaction") == (
            "i cant get no satisfaction"
        )

    def test_feat_collapses(self) -> None:
        assert normalize_track("Roads (feat. Beth)") == "roads feat beth"

    def test_diacritics(self) -> None:
        assert normalize_track("Café") == "cafe"

    def test_empty(self) -> None:
        assert normalize_track("") == ""

    def test_idempotent(self) -> None:
        once = normalize_track("Don't Stop (feat. Café)")
        twice = normalize_track(once)
        assert once == twice


class TestJoinKey:
    def test_basic(self) -> None:
        assert join_key("Portishead", "Roads") == "portishead|roads"

    def test_handles_the_only_in_artist(self) -> None:
        assert join_key("The Beatles", "The End") == "beatles|the end"

    def test_stable_across_feat_variants(self) -> None:
        a = join_key("Drake feat. Future", "Jumpman")
        b = join_key("Drake ft Future", "Jumpman")
        c = join_key("Drake featuring Future", "Jumpman")
        assert a == b == c


class TestSymbolOnlyNames:
    """#106: a name that is nothing but punctuation normalized to "", which
    ingest kept and Phase 8 refused — failing every later run, since
    scrobbles.jsonl never shrinks."""

    def test_symbol_only_names_keep_a_key(self) -> None:
        assert normalize_artist("!!!") == "!!!"
        assert normalize_track("?") == "?"
        assert normalize_track("...") == "..."

    def test_distinct_symbol_names_stay_distinct(self) -> None:
        assert normalize_track("?") != normalize_track("!")

    def test_blank_is_still_blank(self) -> None:
        assert normalize_artist("   ") == ""
        assert normalize_track("") == ""

    def test_any_word_character_takes_the_normal_path(self) -> None:
        assert normalize_artist("AC/DC") == "ac dc"
        assert normalize_track("Hey!") == "hey"

    def test_symbol_only_scrobble_flows_through_to_tracks_jsonl(self, tmp_path) -> None:
        from pipeline.dedupe import dedupe
        from pipeline.ingest_scrobbles import ingest_from_records
        from pipeline.update_tracks import update

        scrobbles = tmp_path / "scrobbles.jsonl"
        skeleton = tmp_path / "tracks_skeleton.jsonl"
        tracks = tmp_path / "tracks.jsonl"
        ingest_from_records(
            [{
                "artist": {"#text": "!!!", "mbid": ""},
                "name": "?",
                "album": {"#text": "", "mbid": ""},
                "date": {"uts": "1730606040", "#text": ""},
            }],
            output_path=scrobbles, mode="replace",
        )
        dedupe(scrobbles, skeleton)
        update(input_path=skeleton, output_path=tracks)

        rows = [json.loads(l) for l in tracks.read_text(encoding="utf-8").splitlines() if l]
        assert [(r["artist"], r["track"]) for r in rows] == [("!!!", "?")]
        assert rows[0]["artist_normalized"] == "!!!"
