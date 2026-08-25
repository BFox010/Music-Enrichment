"""Tests for pipeline.apply_taste_profile."""

from __future__ import annotations

from pipeline.apply_taste_profile import (
    _parse_tier,
    _split_track_artist,
    apply_manifest,
    parse_taste_profile,
)


class TestParseTier:
    def test_arabic(self) -> None:
        assert _parse_tier("1") == 1
        assert _parse_tier("2") == 2
        assert _parse_tier("3") == 3

    def test_roman(self) -> None:
        assert _parse_tier("i") == 1
        assert _parse_tier("II") == 2
        assert _parse_tier("iii") == 3

    def test_out_of_range(self) -> None:
        assert _parse_tier("4") is None
        assert _parse_tier("0") is None
        assert _parse_tier("garbage") is None


class TestSplitTrackArtist:
    def test_quoted_by(self) -> None:
        assert _split_track_artist('"Roads" by Portishead') == ("Roads", "Portishead")

    def test_curly_quotes(self) -> None:
        assert _split_track_artist('“Roads” by Portishead') == ("Roads", "Portishead")

    def test_unquoted_by(self) -> None:
        assert _split_track_artist("Roads by Portishead") == ("Roads", "Portishead")

    def test_em_dash(self) -> None:
        assert _split_track_artist("Roads — Portishead") == ("Roads", "Portishead")

    def test_hyphen_with_spaces(self) -> None:
        assert _split_track_artist("Roads - Portishead") == ("Roads", "Portishead")

    def test_artist_only(self) -> None:
        assert _split_track_artist("Ed Sheeran") == (None, "Ed Sheeran")

    def test_a_ha_not_split(self) -> None:
        # 'a-ha' has no spaces around hyphen — must NOT be split
        assert _split_track_artist("a-ha") == (None, "a-ha")


class TestParseTasteProfile:
    def test_tiers(self) -> None:
        md = """
# Taste Profile

## Saturation Tiers

### Tier 1 — heavy rotation
- Tame Impala
- Kanye West

### Tier 2 — moderate
- Gorillaz

### Tier 3 — limited
- Frank Ocean
"""
        m = parse_taste_profile(md)
        assert m["tier_by_artist"]["tame impala"] == 1
        assert m["tier_by_artist"]["kanye west"] == 1
        assert m["tier_by_artist"]["gorillaz"] == 2
        assert m["tier_by_artist"]["frank ocean"] == 3

    def test_playlists(self) -> None:
        md = """
## Playlists

### soak (locked)
- "Roads" by Portishead
- "Glory Box" by Portishead

### night_drive (approved)
- "Crystalised" by The xx
"""
        m = parse_taste_profile(md)
        portishead_roads = ("portishead", "roads")
        portishead_glory = ("portishead", "glory box")
        thexx_crystalised = ("xx", "crystalised")
        assert m["playlists"][portishead_roads]["playlists"] == ["soak"]
        assert m["playlists"][portishead_roads]["curation_state"] == "locked"
        assert m["playlists"][portishead_glory]["curation_state"] == "locked"
        assert m["playlists"][thexx_crystalised]["curation_state"] == "approved"

    def test_track_in_multiple_playlists(self) -> None:
        md = """
## Playlists
### soak (approved)
- "Roads" by Portishead
### night_drive (locked)
- "Roads" by Portishead
"""
        m = parse_taste_profile(md)
        entry = m["playlists"][("portishead", "roads")]
        assert set(entry["playlists"]) == {"soak", "night_drive"}
        # locked outranks approved
        assert entry["curation_state"] == "locked"

    def test_empty_input(self) -> None:
        m = parse_taste_profile("")
        assert m["tier_by_artist"] == {}
        assert m["playlists"] == {}


class TestApplyManifest:
    def _track(self, artist: str, track: str, **extra) -> dict:
        from pipeline.normalize import normalize_artist, normalize_track
        return {
            "artist": artist,
            "track": track,
            "artist_normalized": normalize_artist(artist),
            "track_normalized": normalize_track(track),
            **extra,
        }

    def test_apply_tier(self) -> None:
        manifest = {
            "tier_by_artist": {"tame impala": 1},
            "playlists": {},
        }
        tracks = [self._track("Tame Impala", "Let It Happen")]
        stats = apply_manifest(tracks, manifest)
        assert tracks[0]["saturation_tier"] == 1
        assert stats["tiered"] == 1

    def test_apply_playlists(self) -> None:
        manifest = {
            "tier_by_artist": {},
            "playlists": {
                ("portishead", "roads"): {"playlists": ["soak"], "curation_state": "locked"},
            },
        }
        tracks = [self._track("Portishead", "Roads")]
        apply_manifest(tracks, manifest)
        assert tracks[0]["playlists"] == ["soak"]
        assert tracks[0]["curation_state"] == "locked"

    def test_no_match_clears_playlist_fields(self) -> None:
        """apply_manifest recomputes fully from scratch each run — a track
        carrying stale playlists/curation_state from before a Phase 4e merge
        must not keep them once its identities no longer match anything."""
        manifest = {"tier_by_artist": {}, "playlists": {}}
        tracks = [self._track("Portishead", "Roads",
                               playlists=["stale"], curation_state="locked")]
        apply_manifest(tracks, manifest)
        assert tracks[0]["playlists"] == []
        assert tracks[0]["curation_state"] is None

    def test_playlist_matches_via_identity_alias(self) -> None:
        manifest = {
            "tier_by_artist": {},
            "playlists": {("clipse", "so far ahead"):
                          {"playlists": ["throwbacks"], "curation_state": "locked"}},
        }
        tracks = [self._track(
            "Clipse, Pharrell Williams, Pusha T & Malice", "So Far Ahead",
            identity_aliases=[
                ["clipse pharrell williams pusha t malice", "so far ahead"],
                ["clipse", "so far ahead"],
            ],
        )]
        apply_manifest(tracks, manifest)
        assert tracks[0]["playlists"] == ["throwbacks"]

    def test_tier_matches_via_alias_artist(self) -> None:
        manifest = {"tier_by_artist": {"clipse": 1}, "playlists": {}}
        tracks = [self._track(
            "Clipse, Pharrell Williams, Pusha T & Malice", "So Far Ahead",
            identity_aliases=[["clipse", "so far ahead"]],
        )]
        apply_manifest(tracks, manifest)
        assert tracks[0]["saturation_tier"] == 1


class TestUnmatchedProfileEntries:
    """#65: Phase 7 counted what it set, never what matched nothing, so a typo
    in taste_profile.md was indistinguishable from a track never scrobbled and
    the profile could rot indefinitely with no signal.
    """

    @staticmethod
    def _manifest(**playlists) -> dict:
        return {"tier_by_artist": {}, "playlists": dict(playlists)}

    def test_feat_suffix_in_profile_is_recovered(self) -> None:
        from pipeline.apply_taste_profile import resolve_playlist_entries

        entry = {"playlists": ["uplifting"], "curation_state": "locked",
                 "raw_artist": "Gorillaz", "raw_track": "Some Kind of Nature (feat. Lou Reed)"}
        manifest = {"tier_by_artist": {},
                    "playlists": {("gorillaz", "some kind of nature feat lou reed"): entry}}
        known = {("gorillaz", "some kind of nature")}

        resolved, unmatched = resolve_playlist_entries(manifest, known)
        assert unmatched == []
        assert resolved[("gorillaz", "some kind of nature")] == entry

    def test_collab_credit_falls_back_to_the_primary_artist(self) -> None:
        from pipeline.apply_taste_profile import resolve_playlist_entries

        entry = {"playlists": ["moody"], "curation_state": None,
                 "raw_artist": "La Roux & Gamper & Dadoni", "raw_track": "Bulletproof"}
        manifest = {"tier_by_artist": {},
                    "playlists": {("la roux and gamper and dadoni", "bulletproof"): entry}}
        known = {("la roux", "bulletproof")}

        resolved, unmatched = resolve_playlist_entries(manifest, known)
        assert unmatched == []
        assert ("la roux", "bulletproof") in resolved

    def test_a_genuine_miss_is_reported_not_invented(self) -> None:
        """A track the owner never scrobbled must stay unmatched, not be
        force-fitted onto some other row."""
        from pipeline.apply_taste_profile import resolve_playlist_entries

        entry = {"playlists": ["uplifting"], "curation_state": "locked",
                 "raw_artist": "AWOLNATION", "raw_track": "Miracle Man"}
        manifest = {"tier_by_artist": {},
                    "playlists": {("awolnation", "miracle man"): entry}}
        known = {("oliver tree", "miracle man")}   # same title, different artist

        resolved, unmatched = resolve_playlist_entries(manifest, known)
        assert resolved == {}
        assert len(unmatched) == 1
        assert unmatched[0]["artist"] == "AWOLNATION"
        assert unmatched[0]["playlists"] == ["uplifting"]
        assert unmatched[0]["curation_state"] == "locked"

    def test_a_direct_hit_is_never_rerouted(self) -> None:
        from pipeline.apply_taste_profile import resolve_playlist_entries

        entry = {"playlists": ["moody"], "curation_state": None,
                 "raw_artist": "Gorillaz", "raw_track": "Feel Good Inc. (feat. De La Soul)"}
        key = ("gorillaz", "feel good inc feat de la soul")
        manifest = {"tier_by_artist": {}, "playlists": {key: entry}}
        # Both the exact key and a variation exist; the exact key must win.
        known = {key, ("gorillaz", "feel good inc")}

        resolved, unmatched = resolve_playlist_entries(manifest, known)
        assert list(resolved) == [key]
        assert unmatched == []

    def test_unmatched_review_file_is_written_and_cleared(self, tmp_path) -> None:
        import json as _json
        from pipeline.apply_taste_profile import _write_unmatched

        path = tmp_path / "unmatched.jsonl"
        _write_unmatched([{"artist": "A", "track": "B"}], path)
        assert [_json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] == [
            {"artist": "A", "track": "B"}
        ]
        # A stale file must not outlive the problem it described.
        _write_unmatched([], path)
        assert not path.exists()


class TestProfileBomHandling:
    """#65 / audit §4.3: the file carries a UTF-8 BOM, which utf-8 hands to the
    parser as part of line 1. Harmless while the H1 is not a section marker —
    it would silently swallow a section if the document were reordered.
    """

    def test_bom_does_not_reach_the_parser(self, tmp_path) -> None:
        path = tmp_path / "taste_profile.md"
        path.write_bytes(
            b"\xef\xbb\xbf"
            b"## Saturation Tiers\n\n### Tier 1\n\n- Tame Impala\n"
        )
        manifest = parse_taste_profile(path.read_text(encoding="utf-8-sig"))
        assert manifest["tier_by_artist"] == {"tame impala": 1}

    def test_committed_profile_h1_parses_without_the_bom(self) -> None:
        from pipeline.config import TASTE_PROFILE_PATH

        if not TASTE_PROFILE_PATH.exists():
            import pytest
            pytest.skip("taste_profile.md not present")
        first = TASTE_PROFILE_PATH.read_text(encoding="utf-8-sig").splitlines()[0]
        assert not first.startswith("﻿")
        assert first.startswith("# ")


class TestCollapsingProfileEntries:
    """Two profile entries can resolve onto one library row — a bare title and
    its feat. variant. The later must not erase the earlier's playlists.
    """

    def test_entries_collapsing_onto_one_row_are_merged(self) -> None:
        from pipeline.apply_taste_profile import resolve_playlist_entries

        manifest = {"tier_by_artist": {}, "playlists": {
            ("gorillaz", "dare feat shaun ryder"): {
                "playlists": ["uplifting"], "curation_state": "approved",
                "raw_artist": "Gorillaz", "raw_track": "DARE (feat. Shaun Ryder)"},
            ("gorillaz", "dare remastered"): {
                "playlists": ["moody"], "curation_state": "locked",
                "raw_artist": "Gorillaz", "raw_track": "DARE (Remastered)"},
        }}
        resolved, unmatched = resolve_playlist_entries(manifest, {("gorillaz", "dare")})

        assert unmatched == []
        entry = resolved[("gorillaz", "dare")]
        assert sorted(entry["playlists"]) == ["moody", "uplifting"]
        # Strongest state wins, as the parser does for duplicate keys.
        assert entry["curation_state"] == "locked"

    def test_merging_does_not_mutate_the_parsed_manifest(self) -> None:
        from pipeline.apply_taste_profile import resolve_playlist_entries

        first = {"playlists": ["uplifting"], "curation_state": "approved",
                 "raw_artist": "Gorillaz", "raw_track": "DARE (feat. Shaun Ryder)"}
        manifest = {"tier_by_artist": {}, "playlists": {
            ("gorillaz", "dare feat shaun ryder"): first,
            ("gorillaz", "dare remastered"): {
                "playlists": ["moody"], "curation_state": "locked",
                "raw_artist": "Gorillaz", "raw_track": "DARE (Remastered)"},
        }}
        resolve_playlist_entries(manifest, {("gorillaz", "dare")})
        assert first["playlists"] == ["uplifting"]
