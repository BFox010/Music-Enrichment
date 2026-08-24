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
