"""Tests for Phase B — Spotify ID resolution.

Mirrors the structure of test_check_apple_music: exercise the pure matching
logic, the credential loader's skip behaviour, the token provider, and the
resolution cascade with a stubbed HTTP client (no network).
"""

from __future__ import annotations

import json

import pytest

from pipeline import enrich_spotify_ids as esi
from pipeline.enrich_spotify_ids import (
    SpotifyAuth,
    _best_match,
    _resolve_one,
    load_credentials,
)


def _search(*items: dict) -> dict:
    """Build a Spotify search response envelope around track items."""
    return {"tracks": {"items": list(items)}}


def _track(track_id: str, name: str, *artists: str) -> dict:
    return {"id": track_id, "name": name, "artists": [{"name": a} for a in artists]}


# ── _best_match ────────────────────────────────────────────────────────────


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


# ── load_credentials ─────────────────────────────────────────────────────────


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


# ── SpotifyAuth ──────────────────────────────────────────────────────────────


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


# ── _resolve_one (cascade with a stubbed client) ─────────────────────────────


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
    def test_isrc_exact_short_circuits(self) -> None:
        client = _StubClient({"isrc:USABC1234567": _search(_track("byisrc", "Whatever", "Whoever"))})
        result = _resolve_one(client, _StubAuth(), "Artist", "Song", "USABC1234567")
        assert result == "byisrc"
        # ISRC hit means we never fall through to the name search.
        assert client.queries == ["isrc:USABC1234567"]

    def test_name_search_when_no_isrc(self) -> None:
        client = _StubClient({"Portishead Roads": _search(_track("abc", "Roads", "Portishead"))})
        result = _resolve_one(client, _StubAuth(), "Portishead", "Roads", None)
        assert result == "abc"

    def test_variation_retry_strips_feat(self) -> None:
        # Original query misses; the strip_feat variation ("Drake 1 Train") resolves it.
        client = _StubClient({
            "Drake 1 Train (feat. Kendrick Lamar)": _search(),  # original — miss
            "Drake 1 Train": _search(_track("v", "1 Train", "Drake")),  # strip_feat — hit
        })
        result = _resolve_one(client, _StubAuth(), "Drake", "1 Train (feat. Kendrick Lamar)", None)
        assert result == "v"
        assert client.queries == [
            "Drake 1 Train (feat. Kendrick Lamar)",
            "Drake 1 Train",
        ]

    def test_unmatched_returns_none(self) -> None:
        client = _StubClient({})
        assert _resolve_one(client, _StubAuth(), "Nobody", "Nothing", None) is None

    def test_sets_authorization_header(self) -> None:
        client = _StubClient({"A B": _search(_track("id", "B", "A"))})
        _resolve_one(client, _StubAuth(), "A", "B", None)
        assert client.session.headers.get("Authorization") == "Bearer tok"
