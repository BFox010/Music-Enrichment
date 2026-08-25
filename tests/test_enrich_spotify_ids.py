"""Tests for Phase B — ISRC + Spotify ID resolution.

Mirrors the structure of test_check_apple_music: exercise the pure matching
logic, the credential loader's skip behaviour, the token provider, and the
resolution cascade with a stubbed HTTP client (no network).

The ISRC coverage at the bottom is the part that matters for issue #37 — that
identifier is what keeps the audio-feature source swappable, so the tests pin
both that we capture it and that we never clobber one we already hold.
"""

from __future__ import annotations

import json

import pytest

from pipeline import enrich_spotify_ids as esi
from pipeline.enrich_spotify_ids import (
    SpotifyAuth,
    _best_match,
    _extract_isrc,
    _resolve_one,
    enrich,
    load_credentials,
)


def _search(*items: dict) -> dict:
    """Build a Spotify search response envelope around track items."""
    return {"tracks": {"items": list(items)}}


def _track(track_id: str, name: str, *artists: str, isrc: str | None = None) -> dict:
    item = {"id": track_id, "name": name, "artists": [{"name": a} for a in artists]}
    if isrc is not None:
        item["external_ids"] = {"isrc": isrc}
    return item


# ── _best_match ──


class TestBestMatch:
    def test_exact_match_found(self) -> None:
        resp = _search(_track("abc", "Roads", "Portishead"))
        match = _best_match(resp, "portishead", "roads")
        assert match is not None and match["id"] == "abc"

    def test_no_match_returns_none(self) -> None:
        resp = _search(_track("abc", "Glory Box", "Portishead"))
        assert _best_match(resp, "portishead", "roads") is None

    def test_wrong_artist_returns_none(self) -> None:
        resp = _search(_track("abc", "Roads", "Some Cover Band"))
        assert _best_match(resp, "portishead", "roads") is None

    def test_matches_any_credited_artist(self) -> None:
        # Featured/secondary artist still counts as an exact match.
        resp = _search(_track("xyz", "Guilty Conscience", "070 Shake", "Tame Impala"))
        match = _best_match(resp, "tame impala", "guilty conscience")
        assert match is not None and match["id"] == "xyz"

    def test_normalization_match(self) -> None:
        # Leading "The " on the artist is dropped by normalize_artist.
        resp = _search(_track("end1", "The End", "The Beatles"))
        match = _best_match(resp, "beatles", "the end")
        assert match is not None and match["id"] == "end1"

    def test_diacritics_match(self) -> None:
        resp = _search(_track("halo1", "Halo", "Beyoncé"))
        match = _best_match(resp, "beyonce", "halo")
        assert match is not None and match["id"] == "halo1"

    def test_empty_items(self) -> None:
        assert _best_match(_search(), "x", "y") is None

    def test_error_response(self) -> None:
        assert _best_match({"_error": "not_found"}, "x", "y") is None

    def test_non_dict_response(self) -> None:
        assert _best_match(None, "x", "y") is None  # type: ignore[arg-type]
        assert _best_match([], "x", "y") is None  # type: ignore[arg-type]

    def test_skips_non_dict_items(self) -> None:
        resp = {"tracks": {"items": ["junk", _track("ok", "Roads", "Portishead")]}}
        match = _best_match(resp, "portishead", "roads")
        assert match is not None and match["id"] == "ok"


# ── load_credentials ──


class TestLoadCredentials:
    def test_env_vars_win(self, monkeypatch) -> None:
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id_env")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret_env")
        assert load_credentials() == ("id_env", "secret_env")

    def test_json_file_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
        creds = tmp_path / "spotify_credentials.json"
        creds.write_text(json.dumps({"client_id": "id_f", "client_secret": "sec_f"}))
        monkeypatch.setattr(esi, "INPUT_SPOTIFY_CREDENTIALS", creds)
        assert load_credentials() == ("id_f", "sec_f")

    def test_missing_raises_filenotfound(self, monkeypatch, tmp_path) -> None:
        # FileNotFoundError is how the orchestrator detects a benign SKIP.
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
        monkeypatch.setattr(esi, "INPUT_SPOTIFY_CREDENTIALS", tmp_path / "nope.json")
        with pytest.raises(FileNotFoundError):
            load_credentials()

    def test_incomplete_json_raises_valueerror(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
        creds = tmp_path / "spotify_credentials.json"
        creds.write_text(json.dumps({"client_id": "only_id"}))
        monkeypatch.setattr(esi, "INPUT_SPOTIFY_CREDENTIALS", creds)
        with pytest.raises(ValueError):
            load_credentials()


# ── SpotifyAuth ──


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class TestSpotifyAuth:
    def test_fetches_and_caches_token(self, monkeypatch) -> None:
        calls = {"n": 0}

        def fake_post(url, **kwargs):
            calls["n"] += 1
            return _FakeResp({"access_token": "tok123", "expires_in": 3600})

        monkeypatch.setattr(esi.requests, "post", fake_post)
        auth = SpotifyAuth("id", "secret")
        assert auth.token() == "tok123"
        assert auth.token() == "tok123"  # cached, no second fetch
        assert calls["n"] == 1

    def test_refreshes_when_expired(self, monkeypatch) -> None:
        tokens = iter(["first", "second"])

        def fake_post(url, **kwargs):
            # expires_in=0 → always considered expired by the 60s guard
            return _FakeResp({"access_token": next(tokens), "expires_in": 0})

        monkeypatch.setattr(esi.requests, "post", fake_post)
        auth = SpotifyAuth("id", "secret")
        assert auth.token() == "first"
        assert auth.token() == "second"


# ── _resolve_one (cascade with a stubbed client) ──


class _StubSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _StubClient:
    """Minimal RateLimitedClient stand-in: canned responses keyed by query 'q'."""

    def __init__(self, by_query: dict[str, dict]) -> None:
        self.session = _StubSession()
        self._by_query = by_query
        self.queries: list[str] = []

    def get(self, url, params, cache_key):
        q = params["q"]
        self.queries.append(q)
        return self._by_query.get(q, _search())


class _StubAuth:
    def token(self) -> str:
        return "tok"


class TestResolveOne:
    def test_never_spends_a_call_on_an_isrc_lookup(self) -> None:
        """A held ISRC needs nothing from Spotify — ReccoBeats takes it directly.

        Regression guard for the dropped ISRC -> spotify_id short-circuit: the
        only query issued must be the name search.
        """
        client = _StubClient({"Artist Song": _search(_track("abc", "Song", "Artist"))})
        assert _resolve_one(client, _StubAuth(), "Artist", "Song") == ("abc", None)
        assert client.queries == ["Artist Song"]

    def test_name_search_resolves(self) -> None:
        client = _StubClient({"Portishead Roads": _search(_track("abc", "Roads", "Portishead"))})
        result = _resolve_one(client, _StubAuth(), "Portishead", "Roads")
        assert result == ("abc", None)

    def test_variation_retry_strips_feat(self) -> None:
        # Original query misses; the strip_feat variation ("Drake 1 Train") resolves it.
        client = _StubClient({
            "Drake 1 Train (feat. Kendrick Lamar)": _search(),  # original — miss
            "Drake 1 Train": _search(_track("v", "1 Train", "Drake")),  # strip_feat — hit
        })
        result = _resolve_one(client, _StubAuth(), "Drake", "1 Train (feat. Kendrick Lamar)")
        assert result == ("v", None)
        assert client.queries == [
            "Drake 1 Train (feat. Kendrick Lamar)",
            "Drake 1 Train",
        ]

    def test_unmatched_returns_none(self) -> None:
        client = _StubClient({})
        assert _resolve_one(client, _StubAuth(), "Nobody", "Nothing") == (None, None)

    def test_sets_authorization_header(self) -> None:
        client = _StubClient({"A B": _search(_track("id", "B", "A"))})
        _resolve_one(client, _StubAuth(), "A", "B")
        assert client.session.headers.get("Authorization") == "Bearer tok"


# ── ISRC capture ──
#
# The ISRC rides along free on a match Phase B already paid for. It is the
# identifier that keeps the audio-feature source swappable (ReccoBeats,
# MusicBrainz and AcousticBrainz all take it), so losing it silently — the
# behaviour before issue #37's review — is the failure these guard against.


class TestExtractIsrc:
    def test_extracts_and_normalises(self) -> None:
        assert _extract_isrc({"external_ids": {"isrc": " usabc1234567 "}}) == "USABC1234567"

    def test_missing_external_ids_is_none(self) -> None:
        assert _extract_isrc({"id": "x"}) is None

    def test_external_ids_without_isrc_is_none(self) -> None:
        assert _extract_isrc({"external_ids": {"upc": "123"}}) is None

    def test_blank_isrc_is_none(self) -> None:
        assert _extract_isrc({"external_ids": {"isrc": "   "}}) is None

    def test_non_dict_external_ids_is_none(self) -> None:
        assert _extract_isrc({"external_ids": "USABC1234567"}) is None

    def test_non_string_isrc_is_none(self) -> None:
        assert _extract_isrc({"external_ids": {"isrc": 12345}}) is None


class TestResolveOneCapturesIsrc:
    def test_returns_isrc_alongside_id(self) -> None:
        client = _StubClient({
            "Portishead Roads": _search(
                _track("abc", "Roads", "Portishead", isrc="GBAAA9400013")
            ),
        })
        assert _resolve_one(client, _StubAuth(), "Portishead", "Roads") == (
            "abc", "GBAAA9400013",
        )

    def test_id_without_isrc_still_resolves(self) -> None:
        """A catalogue entry with no ISRC must degrade, not fail the track."""
        client = _StubClient({"A B": _search(_track("id1", "B", "A"))})
        assert _resolve_one(client, _StubAuth(), "A", "B") == ("id1", None)

    def test_isrc_comes_from_the_variation_that_matched(self) -> None:
        client = _StubClient({
            "Drake 1 Train (feat. Kendrick Lamar)": _search(),
            "Drake 1 Train": _search(_track("v", "1 Train", "Drake", isrc="USCM51300289")),
        })
        assert _resolve_one(
            client, _StubAuth(), "Drake", "1 Train (feat. Kendrick Lamar)"
        ) == ("v", "USCM51300289")


class TestEnrichPersistsIsrc:
    """End-to-end over enrich(): what actually lands on the record."""

    @staticmethod
    def _run(monkeypatch, tmp_path, tracks, resolved):
        import pipeline.enrich_spotify_ids as mod

        src = tmp_path / "in.jsonl"
        src.write_text(
            "".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8"
        )
        out = tmp_path / "out.jsonl"

        monkeypatch.setattr(mod, "load_credentials", lambda: ("id", "secret"))
        monkeypatch.setattr(mod, "SpotifyAuth", lambda *a, **k: _StubAuth())
        monkeypatch.setattr(
            mod, "RateLimitedClient",
            lambda *a, **k: type("C", (), {"flush": lambda self: None})(),
        )
        monkeypatch.setattr(
            mod, "_resolve_one",
            lambda client, auth, artist, track: resolved.get(track, (None, None)),
        )
        stats = mod.enrich(input_path=src, output_path=out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
        return stats, {r["track"]: r for r in rows}

    def test_captures_isrc_on_a_new_resolution(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "Portishead", "track": "Roads"}],
            {"Roads": ("abc", "GBAAA9400013")},
        )
        assert rows["Roads"]["spotify_id"] == "abc"
        assert rows["Roads"]["isrc"] == "GBAAA9400013"
        assert stats["resolved"] == 1
        assert stats["isrc_captured"] == 1

    def test_never_overwrites_an_existing_isrc(self, monkeypatch, tmp_path) -> None:
        """An Exportify-sourced ISRC was matched by another route; leave it."""
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USORIGINAL01"}],
            {"B": ("newid", "USDIFFERENT9")},
        )
        assert rows["B"]["isrc"] == "USORIGINAL01"
        assert rows["B"]["spotify_id"] == "newid"
        assert stats["isrc_captured"] == 0

    def test_resolution_without_isrc_leaves_field_absent(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B"}],
            {"B": ("id1", None)},
        )
        assert rows["B"]["spotify_id"] == "id1"
        assert not rows["B"].get("isrc")
        assert stats["resolved"] == 1
        assert stats["isrc_captured"] == 0

    def test_unmatched_track_gains_neither(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path, [{"artist": "A", "track": "B"}], {},
        )
        assert not rows["B"].get("spotify_id")
        assert not rows["B"].get("isrc")
        assert stats["unmatched"] == 1
