"""Tests for the Last.fm name-variation retry in pipeline.enrich_metadata.

Uses a fake client (no network) to assert the retry cascade stops at the first
actionable variation, namespaces its cache keys, and falls back cleanly when
nothing is recoverable.
"""

from __future__ import annotations

from typing import Any

from pipeline.enrich_metadata import _lookup_with_variations

# A response Last.fm returns for a real, tagged track (MBID present = actionable).
HIT = {"track": {"mbid": "mbid-123", "toptags": {"tag": [{"name": "rap"}]},
                 "artist": {"mbid": "artist-1"}}}
# A thin page: name resolves but no MBID and no tags (non-actionable).
MISS = {"track": {"mbid": "", "toptags": {"tag": []}}}
NOT_FOUND = {"_error": "not_found"}


class FakeClient:
    """Records every .get call and returns a canned response keyed by (artist, track)."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []  # {artist, track, cache_key}

    def get(self, _url: str, params: dict, cache_key: str, *, classify=None) -> Any:
        self.calls.append({
            "artist": params["artist"],
            "track": params["track"],
            "cache_key": cache_key,
        })
        return self.responses.get((params["artist"], params["track"]), NOT_FOUND)


def _track(artist: str, title: str) -> dict:
    return {
        "artist": artist,
        "track": title,
        "artist_normalized": artist.lower(),
        "track_normalized": title.lower(),
    }


def test_original_hit_makes_no_extra_calls() -> None:
    track = _track("Radiohead", "Karma Police")
    client = FakeClient({("Radiohead", "Karma Police"): HIT})
    fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())

    assert label == "original"
    assert fields["musicbrainz_id"] == "mbid-123"
    assert len(client.calls) == 1
    assert client.calls[0]["cache_key"] == "radiohead|karma police"


def test_strip_feat_recovers_and_stops() -> None:
    track = _track("A$AP Mob", "Yamborghini High (feat. Juicy J)")
    client = FakeClient({("A$AP Mob", "Yamborghini High"): HIT})  # only stripped title hits
    fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())

    assert label == "strip_feat"
    assert fields["musicbrainz_id"] == "mbid-123"
    # Tried original first, then strip_feat — and stopped (no first_artist call).
    assert [c["track"] for c in client.calls] == [
        "Yamborghini High (feat. Juicy J)",
        "Yamborghini High",
    ]


def test_recovery_variation_uses_namespaced_cache_key() -> None:
    track = _track("070 Shake & Tame Impala", "Guilty Conscience (Tame Impala Remix)")
    # Only the first_artist variation (primary artist, full title) hits.
    client = FakeClient({("070 Shake", "Guilty Conscience (Tame Impala Remix)"): HIT})
    _fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())

    assert label == "first_artist"
    base = "070 shake & tame impala|guilty conscience (tame impala remix)"
    # Original keeps the bare cache key; the recovery variation is namespaced.
    assert client.calls[0]["cache_key"] == base
    hit_call = next(c for c in client.calls if c["artist"] == "070 Shake")
    assert hit_call["cache_key"] == f"{base}#first_artist"


def test_no_match_falls_back_to_original() -> None:
    track = _track("ichika Nito", "Some Obscure Instrumental")
    client = FakeClient({})  # everything returns NOT_FOUND
    fields, resp, label = _lookup_with_variations(client, "key", track, frozenset())

    assert label == "original"
    assert fields["musicbrainz_id"] is None
    assert fields["lastfm_tags"] == []
    assert resp == NOT_FOUND


def test_thin_page_is_not_actionable() -> None:
    # The bug we fixed: a bare page (no mbid, no tags) must NOT count as a hit,
    # so the cascade keeps trying past it.
    track = _track("A$AP Rocky", "1 Train (feat. Many People)")
    client = FakeClient({
        ("A$AP Rocky", "1 Train (feat. Many People)"): MISS,  # original: thin page
        ("A$AP Rocky", "1 Train"): HIT,                        # strip_feat: real data
    })
    _fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())
    assert label == "strip_feat"


# An MBID with no tags — the case #75 is about. Actionable under the old rule,
# so it ended the cascade on exactly the rows whose tags were missing.
MBID_ONLY = {"track": {"mbid": "mbid-123", "toptags": {"tag": []},
                       "artist": {"mbid": "artist-1"}}}
# Tags but no MBID.
TAGS_ONLY = {"track": {"mbid": "", "toptags": {"tag": [{"name": "soul"}]}}}


class TestAnMbidNoLongerSuppressesTheTagSearch:
    """#75: retries continue while the tags are empty, whatever the MBID says.

    Tags are what Phase 4c derives genres from, so a suppressed tag search shows
    up as a genre gap, not just a tag gap.
    """

    def test_tags_from_a_variation_join_the_mbid_from_the_original(self) -> None:
        track = _track("A$AP Rocky", "1 Train (feat. Many People)")
        client = FakeClient({
            ("A$AP Rocky", "1 Train (feat. Many People)"): MBID_ONLY,
            ("A$AP Rocky", "1 Train"): TAGS_ONLY,
        })
        fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())

        assert fields["musicbrainz_id"] == "mbid-123", "the original's MBID is kept"
        assert fields["lastfm_tags"] == ["soul"], "the variation's tags are picked up"
        assert label == "strip_feat"

    def test_stops_as_soon_as_tags_land(self) -> None:
        """Cost stays bounded: a row that already has tags does no extra lookups."""
        track = _track("A$AP Rocky", "1 Train (feat. Many People)")
        client = FakeClient({
            ("A$AP Rocky", "1 Train (feat. Many People)"): TAGS_ONLY,
        })
        _fields, _resp, label = _lookup_with_variations(client, "key", track, frozenset())

        assert label == "original"
        assert len(client.calls) == 1

    def test_mbid_only_everywhere_still_counts_as_a_match(self) -> None:
        track = _track("A$AP Rocky", "1 Train (feat. Many People)")
        client = FakeClient({
            ("A$AP Rocky", "1 Train (feat. Many People)"): MBID_ONLY,
        })
        fields, resp, label = _lookup_with_variations(client, "key", track, frozenset())

        assert fields["musicbrainz_id"] == "mbid-123"
        assert fields["lastfm_tags"] == []
        # Every variation was tried before giving up on the tags.
        assert len(client.calls) > 1
        assert label == "original"
        assert resp == MBID_ONLY, "the original response classifies the outcome"

    def test_an_mbid_recovered_by_a_variation_survives(self) -> None:
        """The original answered nothing; a later query supplied the MBID."""
        track = _track("A$AP Rocky", "1 Train (feat. Many People)")
        client = FakeClient({("A$AP Rocky", "1 Train"): MBID_ONLY})
        fields, _resp, _label = _lookup_with_variations(client, "key", track, frozenset())

        assert fields["musicbrainz_id"] == "mbid-123"
        assert fields["artist_mbid"] == "artist-1"
