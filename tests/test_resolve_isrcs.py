"""Tests for Phase 5a — ISRC resolution (MusicBrainz -> Deezer).

Mirrors the structure of test_enrich_spotify_ids.py: exercise the pure
response-parsing logic, then the resolution cascade with a stubbed HTTP
client (no network — the sandbox this was built in has no outbound access to
musicbrainz.org or api.deezer.com either).
"""

from __future__ import annotations

import json

from pipeline import resolve_isrcs as ri
from pipeline.resolve_isrcs import (
    _best_deezer_match,
    _isrc_from_deezer_track,
    _isrc_from_musicbrainz_response,
    _resolve_deezer,
    _resolve_musicbrainz,
    enrich,
)


# ── MusicBrainz response parsing ──


class TestIsrcFromMusicbrainzResponse:
    def test_extracts_first_isrc(self) -> None:
        resp = {"isrcs": ["usabc1234567", "GBXYZ9876543"]}
        assert _isrc_from_musicbrainz_response(resp) == "USABC1234567"

    def test_empty_isrcs_list_is_none(self) -> None:
        assert _isrc_from_musicbrainz_response({"isrcs": []}) is None

    def test_missing_isrcs_key_is_none(self) -> None:
        assert _isrc_from_musicbrainz_response({"id": "abc"}) is None

    def test_error_response_is_none(self) -> None:
        assert _isrc_from_musicbrainz_response({"_error": "not_found"}) is None

    def test_non_dict_response_is_none(self) -> None:
        assert _isrc_from_musicbrainz_response(None) is None  # type: ignore[arg-type]

    def test_skips_non_string_entries(self) -> None:
        resp = {"isrcs": [123, "", "usabc1234567"]}
        assert _isrc_from_musicbrainz_response(resp) == "USABC1234567"


class _StubMBClient:
    def __init__(self, by_mbid: dict[str, dict]) -> None:
        self._by_mbid = by_mbid
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, params, cache_key):
        self.requests.append((url, params))
        mbid = url.rsplit("/", 1)[-1]
        return self._by_mbid.get(mbid, {"_error": "not_found"})


class TestResolveMusicbrainz:
    def test_resolves_isrc(self) -> None:
        client = _StubMBClient({"mb-1": {"isrcs": ["USABC1234567"]}})
        assert _resolve_musicbrainz(client, "mb-1") == "USABC1234567"

    def test_unmatched_returns_none(self) -> None:
        client = _StubMBClient({})
        assert _resolve_musicbrainz(client, "mb-unknown") is None

    def test_requests_the_isrcs_include(self) -> None:
        client = _StubMBClient({"mb-1": {"isrcs": ["USABC1234567"]}})
        _resolve_musicbrainz(client, "mb-1")
        _, params = client.requests[0]
        assert params["inc"] == "isrcs"
        assert params["fmt"] == "json"


# ── Deezer response parsing ──


def _deezer_search(*items: dict) -> dict:
    return {"data": list(items)}


def _deezer_item(track_id: int, title: str, artist: str) -> dict:
    return {"id": track_id, "title": title, "artist": {"name": artist}}


class TestBestDeezerMatch:
    def test_exact_match_found(self) -> None:
        resp = _deezer_search(_deezer_item(1, "Roads", "Portishead"))
        match = _best_deezer_match(resp, "portishead", "roads")
        assert match is not None and match["id"] == 1

    def test_no_match_returns_none(self) -> None:
        resp = _deezer_search(_deezer_item(1, "Glory Box", "Portishead"))
        assert _best_deezer_match(resp, "portishead", "roads") is None

    def test_wrong_artist_returns_none(self) -> None:
        resp = _deezer_search(_deezer_item(1, "Roads", "Cover Band"))
        assert _best_deezer_match(resp, "portishead", "roads") is None

    def test_empty_data(self) -> None:
        assert _best_deezer_match(_deezer_search(), "x", "y") is None

    def test_error_response(self) -> None:
        assert _best_deezer_match({"_error": "not_found"}, "x", "y") is None

    def test_non_dict_response(self) -> None:
        assert _best_deezer_match(None, "x", "y") is None  # type: ignore[arg-type]

    def test_skips_non_dict_items(self) -> None:
        resp = {"data": ["junk", _deezer_item(2, "Roads", "Portishead")]}
        match = _best_deezer_match(resp, "portishead", "roads")
        assert match is not None and match["id"] == 2


class TestIsrcFromDeezerTrack:
    def test_extracts_and_normalises(self) -> None:
        assert _isrc_from_deezer_track({"isrc": " gbaaa9400013 "}) == "GBAAA9400013"

    def test_missing_isrc_is_none(self) -> None:
        assert _isrc_from_deezer_track({"id": 1}) is None

    def test_blank_isrc_is_none(self) -> None:
        assert _isrc_from_deezer_track({"isrc": "   "}) is None

    def test_error_response_is_none(self) -> None:
        assert _isrc_from_deezer_track({"_error": "not_found"}) is None


class _StubDeezerClient:
    """Canned responses keyed by URL suffix (search query or track id)."""

    def __init__(self, search_by_query: dict[str, dict], track_by_id: dict[str, dict]) -> None:
        self._search_by_query = search_by_query
        self._track_by_id = track_by_id
        self.queries: list[str] = []

    def get(self, url, params, cache_key):
        if url.endswith("/search"):
            q = params["q"]
            self.queries.append(q)
            return self._search_by_query.get(q, _deezer_search())
        track_id = url.rsplit("/", 1)[-1]
        return self._track_by_id.get(track_id, {"_error": "not_found"})


class TestResolveDeezer:
    def test_name_search_resolves(self) -> None:
        query = 'artist:"Portishead" track:"Roads"'
        client = _StubDeezerClient(
            {query: _deezer_search(_deezer_item(1, "Roads", "Portishead"))},
            {"1": {"isrc": "GBAAA9400013"}},
        )
        assert _resolve_deezer(client, "Portishead", "Roads") == "GBAAA9400013"

    def test_variation_retry_strips_feat(self) -> None:
        client = _StubDeezerClient(
            {
                'artist:"Drake" track:"1 Train (feat. Kendrick Lamar)"': _deezer_search(),
                'artist:"Drake" track:"1 Train"':
                    _deezer_search(_deezer_item(2, "1 Train", "Drake")),
            },
            {"2": {"isrc": "USCM51300289"}},
        )
        result = _resolve_deezer(client, "Drake", "1 Train (feat. Kendrick Lamar)")
        assert result == "USCM51300289"

    def test_unmatched_returns_none(self) -> None:
        client = _StubDeezerClient({}, {})
        assert _resolve_deezer(client, "Nobody", "Nothing") is None

    def test_match_without_isrc_keeps_trying_variations(self) -> None:
        """A matched Deezer track with no ISRC on file must not stop the
        cascade early — try the next name variation instead of giving up."""
        client = _StubDeezerClient(
            {
                'artist:"A" track:"B (feat. C)"':
                    _deezer_search(_deezer_item(1, "B (feat. C)", "A")),
                'artist:"A" track:"B"':
                    _deezer_search(_deezer_item(2, "B", "A")),
            },
            {"1": {"id": 1}, "2": {"isrc": "USXYZ0000001"}},
        )
        assert _resolve_deezer(client, "A", "B (feat. C)") == "USXYZ0000001"


# ── enrich() end-to-end ──


class TestEnrichPersistsIsrc:
    @staticmethod
    def _run(monkeypatch, tmp_path, tracks, mb_by_mbid=None, deezer_isrc_by_track=None):
        src = tmp_path / "in.jsonl"
        src.write_text("".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8")
        out = tmp_path / "out.jsonl"

        monkeypatch.setattr(
            ri, "RateLimitedClient",
            lambda *a, **k: type("C", (), {
                "flush": lambda self: None,
                "warn_if_forced": lambda self, n: None,
                "cache_summary": lambda self: "stub",
            })(),
        )
        monkeypatch.setattr(
            ri, "_resolve_musicbrainz",
            lambda client, mbid: (mb_by_mbid or {}).get(mbid),
        )
        monkeypatch.setattr(
            ri, "_resolve_deezer",
            lambda client, artist, track: (deezer_isrc_by_track or {}).get(track),
        )
        stats = ri.enrich(input_path=src, output_path=out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
        return stats, {r["track"]: r for r in rows}

    def test_musicbrainz_resolves_first(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "musicbrainz_id": "mb-1"}],
            mb_by_mbid={"mb-1": "USABC1234567"},
        )
        assert rows["B"]["isrc"] == "USABC1234567"
        assert rows["B"]["isrc_source"] == "musicbrainz"
        assert "isrc_retrieved_at" in rows["B"]
        assert stats["resolved_musicbrainz"] == 1
        assert stats["resolved_deezer"] == 0

    def test_falls_back_to_deezer_when_musicbrainz_misses(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "musicbrainz_id": "mb-1"}],
            mb_by_mbid={},  # miss
            deezer_isrc_by_track={"B": "GBAAA9400013"},
        )
        assert rows["B"]["isrc"] == "GBAAA9400013"
        assert rows["B"]["isrc_source"] == "deezer"
        assert stats["resolved_deezer"] == 1

    def test_no_mbid_goes_straight_to_deezer(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B"}],
            deezer_isrc_by_track={"B": "GBAAA9400013"},
        )
        assert rows["B"]["isrc_source"] == "deezer"

    def test_never_overwrites_an_existing_isrc(self, monkeypatch, tmp_path) -> None:
        """An Exportify-sourced ISRC was matched by another route; leave it —
        and never spend a MusicBrainz/Deezer request on it."""
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USORIGINAL01",
              "musicbrainz_id": "mb-1"}],
            mb_by_mbid={"mb-1": "USDIFFERENT9"},
        )
        assert rows["B"]["isrc"] == "USORIGINAL01"
        assert "isrc_source" not in rows["B"]
        assert stats["already_had"] == 1
        assert stats["resolved_musicbrainz"] == 0

    def test_unresolved_track_gains_nothing(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(monkeypatch, tmp_path, [{"artist": "A", "track": "B"}])
        assert not rows["B"].get("isrc")
        assert stats["unresolved"] == 1

    def test_does_not_touch_canonical_track_id(self, monkeypatch, tmp_path) -> None:
        """canonical_track_id is computed once, in Phase 4e, before this phase
        ever runs. A newly resolved ISRC must not re-key a track that Phase 4e
        already clustered under a norm:/mbid: id — CLAUDE.md invariant 4."""
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "musicbrainz_id": "mb-1",
              "canonical_track_id": "norm:a|b"}],
            mb_by_mbid={"mb-1": "USABC1234567"},
        )
        assert rows["B"]["isrc"] == "USABC1234567"
        assert rows["B"]["canonical_track_id"] == "norm:a|b"
