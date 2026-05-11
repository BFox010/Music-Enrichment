"""Tests for the v4 rich taste-profile parser (tables + inline lists + prose)."""

from __future__ import annotations

from pipeline.apply_taste_profile import (
    _blacklist_table_entries,
    _inline_dot_list,
    _is_rich_format,
    _playlist_prose_entries,
    _slice_section,
    _table_first_column,
    parse_rich_taste_profile,
    parse_taste_profile,
)


class TestSliceSection:
    def test_basic(self) -> None:
        text = "before ## A more content ## B even more"
        assert _slice_section(text, "## A", "## B").strip() == "more content"

    def test_no_end_marker(self) -> None:
        text = "before ## A all the rest"
        assert _slice_section(text, "## A", None).strip() == "all the rest"

    def test_start_missing(self) -> None:
        assert _slice_section("nothing here", "## X", None) == ""


class TestTableFirstColumn:
    def test_skips_header_and_separator(self) -> None:
        text = """
| Artist | Plays |
|---|---|
| Tame Impala | 747 |
| Kanye West | 654 |
"""
        assert _table_first_column(text) == ["Tame Impala", "Kanye West"]

    def test_empty_table(self) -> None:
        assert _table_first_column("no tables here") == []


class TestInlineDotList:
    def test_strips_parentheticals(self) -> None:
        text = "Tyler, The Creator (220) · UMO (213) · The Strokes (207)"
        assert _inline_dot_list(text) == ["Tyler, The Creator", "UMO", "The Strokes"]

    def test_comma_in_artist_not_split(self) -> None:
        text = "Tyler, The Creator (220) · Drake (37 but deep emotional impact)"
        result = _inline_dot_list(text)
        assert "Tyler, The Creator" in result
        assert "Drake" in result
        assert "The Creator" not in result

    def test_tier3_no_parens(self) -> None:
        text = "Kings of Leon · Clairo · Dehd · Beck · alt-J"
        assert _inline_dot_list(text) == [
            "Kings of Leon", "Clairo", "Dehd", "Beck", "alt-J"
        ]


class TestBlacklistTableEntries:
    def test_single_track_row(self) -> None:
        text = """
| Plays | Track |
|---|---|
| 105 | Danny Brown – "Grown Up" |
"""
        assert _blacklist_table_entries(text) == [("Danny Brown", "Grown Up")]

    def test_multi_track_row(self) -> None:
        text = """
| Plays | Track |
|---|---|
| 39 | Tame Impala – "Why Won't They Talk to Me?" / A$AP Rocky – "Rich N***a Problems" / Nappy Roots – "No Static" |
"""
        result = _blacklist_table_entries(text)
        assert len(result) == 3
        assert ("Tame Impala", "Why Won't They Talk to Me?") in result
        assert ("Nappy Roots", "No Static") in result

    def test_en_dash_em_dash_hyphen(self) -> None:
        text = """
| 1 | A – "T1" |
| 2 | B — "T2" |
| 3 | C - "T3" |
"""
        result = _blacklist_table_entries(text)
        artists = {a for a, _ in result}
        assert artists == {"A", "B", "C"}


class TestPlaylistProseEntries:
    def test_summer_locked(self) -> None:
        text = (
            '**Summer:** indie vibes. Locked: Dehd "Disappear," Pond "Tasmania," '
            'Wet Leg "Chaise Longue."'
        )
        result = _playlist_prose_entries(text)
        dehd_key = ("dehd", "disappear")
        assert dehd_key in result
        assert result[dehd_key]["playlists"] == ["summer"]
        assert result[dehd_key]["curation_state"] == "locked"

    def test_locked_and_rejected(self) -> None:
        text = (
            '**Night Drive:** intro. Locked: Chromatics "Shadow," Tourist "Run." '
            'Rejected: Khruangbin "A Hymn" (too static), SOHN "Ransom Notes" (holds tension).'
        )
        result = _playlist_prose_entries(text)
        chromatics_key = ("chromatics", "shadow")
        khruangbin_key = ("khruangbin", "a hymn")
        assert result[chromatics_key]["curation_state"] == "locked"
        assert result[khruangbin_key]["curation_state"] == "rejected"

    def test_spine_treated_as_locked(self) -> None:
        text = '**Workout (LIFT):** Spine: A$AP Rocky "Goldie" (opener) · Danny Brown "Grown Up."'
        result = _playlist_prose_entries(text)
        rocky_key = ("a ap rocky", "goldie")
        assert result[rocky_key]["curation_state"] == "locked"

    def test_unknown_playlist_ignored(self) -> None:
        text = '**Unknown Section:** Locked: Foo "Bar."'
        result = _playlist_prose_entries(text)
        assert result == {}


class TestIsRichFormat:
    def test_rich_yes(self) -> None:
        md = "## SATURATION TIERS\n**TIER 1** stuff\n**TIER 2** more\n**TIER 3** end\n|---|---|\n"
        assert _is_rich_format(md) is True

    def test_simple_no(self) -> None:
        md = "## Saturation Tiers\n### Tier 1\n- Tame Impala\n"
        assert _is_rich_format(md) is False


class TestParseRichEndToEnd:
    def test_full_minimal_doc(self) -> None:
        md = """
## SATURATION TIERS

**🔴 TIER 1 — DO NOT INCLUDE**
| Artist | Plays | Recency |
|---|---|---|
| Tame Impala | 747 | Active |
| Kanye West | 654 | Fading |

**🟡 TIER 2**
Tyler, The Creator (220) · UMO (213)

**🟢 TIER 3**
Kings of Leon · Clairo

## TRACK BLACKLIST — NEVER RECOMMEND
| Plays | Track |
|---|---|
| 105 | Danny Brown – "Grown Up" |

## EXISTING PLAYLIST DNA

**Summer:** mood. Locked: Dehd "Disappear," Pond "Tasmania."

**Night Drive:** mood. Rejected: SOHN "Ransom Notes" (holds tension).

## NEXT SECTION
end
"""
        manifest = parse_rich_taste_profile(md)
        assert manifest["tier_by_artist"]["tame impala"] == 1
        assert manifest["tier_by_artist"]["kanye west"] == 1
        assert manifest["tier_by_artist"]["tyler the creator"] == 2
        assert manifest["tier_by_artist"]["umo"] == 2
        assert manifest["tier_by_artist"]["kings of leon"] == 3
        assert manifest["tier_by_artist"]["clairo"] == 3
        assert ("danny brown", "grown up") in manifest["blacklist_tracks"]
        assert manifest["playlists"][("dehd", "disappear")]["curation_state"] == "locked"
        assert manifest["playlists"][("sohn", "ransom notes")]["curation_state"] == "rejected"

    def test_dispatcher_picks_rich(self) -> None:
        md = """
## SATURATION TIERS

**TIER 1**
| Artist |
|---|
| X |

**TIER 2**
A

**TIER 3**
B

|---|---|
"""
        manifest = parse_taste_profile(md)
        assert manifest["tier_by_artist"]["x"] == 1

    def test_dispatcher_falls_back_to_simple(self) -> None:
        md = """
## Saturation Tiers
### Tier 1 — heavy
- Tame Impala
"""
        manifest = parse_taste_profile(md)
        assert manifest["tier_by_artist"]["tame impala"] == 1
