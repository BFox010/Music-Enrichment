"""Tests for pipeline.name_variations.

Examples are drawn from the real 308 unmatched tracks the match-variation
diagnostic measured (A$AP feat. strings, 070 Shake collaborations, remixes).
"""

from __future__ import annotations

from pipeline.name_variations import (
    first_artist,
    lookup_variations,
    strip_feat,
    strip_parens,
)


class TestStripFeat:
    def test_strips_paren_feat(self) -> None:
        assert strip_feat("Yamborghini High (feat. Juicy J)") == "Yamborghini High"

    def test_strips_long_feat_string(self) -> None:
        title = ("1 Train (feat. Kendrick Lamar, Joey Bada$$, Yelawolf, "
                 "Danny Brown, Action Bronson & Big K.R.I.T.)")
        assert strip_feat(title) == "1 Train"

    def test_strips_bracket_feat(self) -> None:
        assert strip_feat("Song [feat. Someone]") == "Song"

    def test_case_insensitive(self) -> None:
        assert strip_feat("Song (FEAT. X)") == "Song"
        assert strip_feat("Song (Feat X)") == "Song"

    def test_no_feat_unchanged(self) -> None:
        assert strip_feat("Guilty Conscience") == "Guilty Conscience"

    def test_keeps_non_feat_parens(self) -> None:
        # strip_feat only targets feat.; other parens are strip_parens' job
        assert strip_feat("Guilty Conscience (Tame Impala Remix)") == \
            "Guilty Conscience (Tame Impala Remix)"


class TestStripParens:
    def test_removes_remix_paren(self) -> None:
        assert strip_parens("Guilty Conscience (Tame Impala Remix)") == "Guilty Conscience"

    def test_removes_brackets(self) -> None:
        assert strip_parens("Song [Extended]") == "Song"

    def test_removes_multiple(self) -> None:
        assert strip_parens("Song (Remix) [Clean]") == "Song"

    def test_no_parens_unchanged(self) -> None:
        assert strip_parens("Plain Title") == "Plain Title"


class TestFirstArtist:
    def test_ampersand_split(self) -> None:
        assert first_artist("070 Shake & Tame Impala") == "070 Shake"

    def test_jayz_kanye(self) -> None:
        assert first_artist("JAY-Z & Kanye West") == "JAY-Z"

    def test_comma_split(self) -> None:
        assert first_artist("A$AP Rocky, Tyler, The Creator") == "A$AP Rocky"

    def test_x_split_before_capital(self) -> None:
        assert first_artist("Artist X Other") == "Artist"

    def test_single_artist_unchanged(self) -> None:
        assert first_artist("A$AP Rocky") == "A$AP Rocky"

    def test_does_not_split_ampersand_in_name(self) -> None:
        # No surrounding spaces → not a separator
        assert first_artist("Earth, Wind & Fire").startswith("Earth")


class TestLookupVariations:
    def test_first_is_always_original(self) -> None:
        vs = lookup_variations("A$AP Rocky", "A$AP Forever (feat. Moby)")
        assert vs[0] == ("original", "A$AP Rocky", "A$AP Forever (feat. Moby)")

    def test_feat_track_yields_strip_feat(self) -> None:
        vs = lookup_variations("A$AP Mob", "Yamborghini High (feat. Juicy J)")
        labels = {label for label, _, _ in vs}
        assert "strip_feat" in labels
        sf = next(v for v in vs if v[0] == "strip_feat")
        assert sf[1:] == ("A$AP Mob", "Yamborghini High")

    def test_collaboration_yields_first_artist(self) -> None:
        vs = lookup_variations("070 Shake & Tame Impala", "Guilty Conscience (Tame Impala Remix)")
        labels = {label for label, _, _ in vs}
        assert "first_artist" in labels
        fa = next(v for v in vs if v[0] == "first_artist")
        assert fa[1] == "070 Shake"

    def test_plain_track_yields_only_original(self) -> None:
        # No feat., no parens, single artist → nothing to vary, no wasted calls
        vs = lookup_variations("Radiohead", "Karma Police")
        assert vs == [("original", "Radiohead", "Karma Police")]

    def test_no_duplicate_query_pairs(self) -> None:
        vs = lookup_variations("A$AP Mob", "Walk On Water (feat. A$AP Twelvyy & Playboi Carti)")
        pairs = [(a.casefold(), t.casefold()) for _, a, t in vs]
        assert len(pairs) == len(set(pairs))

    def test_clean_artist_never_emitted(self) -> None:
        # The $→S rule recovered 0 and is deliberately excluded; the artist
        # string is never mangled (A$AP stays A$AP, autocorrect handles it).
        vs = lookup_variations("A$AP Rocky", "Praise The Lord (feat. Skepta)")
        labels = {label for label, _, _ in vs}
        assert not any("clean" in label for label in labels)
        assert all("$" in artist for _, artist, _ in vs)
