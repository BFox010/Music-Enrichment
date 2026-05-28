"""Tests for pipeline.normalize."""

from __future__ import annotations

from pipeline.normalize import (
    clean_artist_for_search,
    clean_track_for_search,
    join_key,
    normalize_artist,
    normalize_track,
    search_join_key,
)


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


class TestCleanTrackForSearch:
    def test_strips_feat_paren(self) -> None:
        assert clean_track_for_search("1 Train (feat. Kendrick Lamar, Joey Bada$$)") == "1 Train"

    def test_strips_feat_bracket(self) -> None:
        assert clean_track_for_search("Song [feat. X]") == "Song"

    def test_strips_inline_feat(self) -> None:
        assert clean_track_for_search("Smuckers ft Tyler The Creator and Lil Wayne") == "Smuckers"

    def test_strips_remaster(self) -> None:
        assert clean_track_for_search("1979 (Remastered 2012)") == "1979"
        assert clean_track_for_search("Song (2018 Remaster)") == "Song"

    def test_strips_radio_edit(self) -> None:
        assert clean_track_for_search("Song (Radio Edit)") == "Song"

    def test_strips_live(self) -> None:
        assert clean_track_for_search("Heart of Glass (Live from the iHeart Music Festival)") == "Heart of Glass"
        assert clean_track_for_search("Black Dresses (live)") == "Black Dresses"

    def test_strips_live_dash(self) -> None:
        assert clean_track_for_search("Song - Live at Madison Square Garden") == "Song"

    def test_strips_extended(self) -> None:
        assert clean_track_for_search("Song (Extended Version)") == "Song"
        assert clean_track_for_search("Song (Extended Mix)") == "Song"

    def test_keeps_remix(self) -> None:
        # Remixes identify a distinct version — must not be stripped
        result = clean_track_for_search("Guilty Conscience (Tame Impala Remix)")
        assert "Tame Impala Remix" in result

    def test_preserves_case(self) -> None:
        # clean functions must NOT lowercase — that's normalize_track's job
        assert clean_track_for_search("Roads (feat. Beth)") == "Roads"

    def test_empty(self) -> None:
        assert clean_track_for_search("") == ""

    def test_no_noise(self) -> None:
        assert clean_track_for_search("Roads") == "Roads"


class TestCleanArtistForSearch:
    def test_strips_ampersand_collab(self) -> None:
        assert clean_artist_for_search("Drake & 21 Savage") == "Drake"
        assert clean_artist_for_search("A$AP NAST & D33J") == "A$AP NAST"

    def test_strips_feat(self) -> None:
        assert clean_artist_for_search("A$AP Mob feat. A$AP Rocky") == "A$AP Mob"
        assert clean_artist_for_search("Artist ft. Other") == "Artist"

    def test_solo_artist_unchanged(self) -> None:
        assert clean_artist_for_search("Portishead") == "Portishead"
        assert clean_artist_for_search("The National") == "The National"

    def test_preserves_case(self) -> None:
        assert clean_artist_for_search("Baby Keem & Kendrick Lamar") == "Baby Keem"


class TestSearchJoinKey:
    def test_strips_feat_both_sides(self) -> None:
        # Library: "Song (feat. X)" and CSV: "Song" → same search key
        lib_key = search_join_key("A$AP Rocky", "West Side Highway (feat. James Fauntleroy)")
        csv_key = search_join_key("A$AP Rocky", "West Side Highway")
        assert lib_key == csv_key

    def test_strips_collab_artist(self) -> None:
        # Library artist "A$AP NAST & D33J" and CSV "A$AP NAST" → same key
        lib_key = search_join_key("A$AP NAST & D33J", "Designer Boi")
        csv_key = search_join_key("A$AP NAST", "Designer Boi")
        assert lib_key == csv_key

    def test_different_feat_credits_same_key(self) -> None:
        # "Song (feat. X & Y)" vs "Song (feat. X, Y)" → same search key
        k1 = search_join_key("Artist", "Song (feat. X & Y)")
        k2 = search_join_key("Artist", "Song (feat. X, Y)")
        assert k1 == k2
