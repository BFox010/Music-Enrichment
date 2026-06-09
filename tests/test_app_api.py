"""Self-contained API tests for the FastAPI music dashboard.

Uses a tiny temp-JSONL fixture via ``app.data.use_paths()`` so no real
tracks.jsonl / scrobbles.jsonl is needed. No network or secrets required.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.data as data
from app.main import DASHBOARD_TOKEN, app

# Mutating endpoints (reload/refresh/sync) require this header; the SPA reads the
# token from GET /api/config. Tests send it directly.
AUTH = {"X-Dashboard-Token": DASHBOARD_TOKEN}

# ── Fixture data ──────────────────────────────────────────────────────────────

_TRACK = {
    "artist": "Portishead",
    "track": "Roads",
    "album": "Dummy",
    "play_count": 10,
    "release_year": 1994,
    "genres": ["trip-hop", "electronic"],
    "mood_tags": ["Moody", "Dark"],
    "mood_source": "claude_batch",
    "mood_confidence": "high",
    "audio_features": {
        "energy": 0.4,
        "valence": 0.2,
        "danceability": 0.5,
        "tempo": 85.0,
        "loudness": -8.0,
        "acousticness": 0.6,
        "speechiness": 0.05,
        "instrumentalness": 0.1,
        "liveness": 0.1,
    },
    "discogs_styles": ["Trip Hop", "Downtempo"],
    "saturation_tier": 1,
    "blacklisted": False,
    "playlists": [],
    "curation_state": None,
    "rejected_reason": None,
    "enriched_at": "2026-01-01T00:00:00Z",
    "first_scrobbled": "2020-06-15",
    "last_scrobbled": "2020-06-15",
}

_SCROBBLE = {
    "artist": "Portishead",
    "track": "Roads",
    "album": "Dummy",
    "scrobbled_at": "2020-06-15T22:30:00Z",
    "year": 2020,
    "month": 6,
    "day_of_week": 0,
    "hour": 22,
    "season": "summer",
}


@pytest.fixture(scope="module")
def client():
    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp) / "tracks.jsonl"
        sp = Path(tmp) / "scrobbles.jsonl"
        tp.write_text(json.dumps(_TRACK) + "\n", encoding="utf-8")
        sp.write_text(json.dumps(_SCROBBLE) + "\n", encoding="utf-8")
        with data.use_paths(tp, sp):
            yield TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOverview:
    def test_status(self, client):
        r = client.get("/api/overview")
        assert r.status_code == 200

    def test_counts(self, client):
        body = client.get("/api/overview").json()
        assert body["track_count"] == 1
        assert body["scrobble_count"] == 1

    def test_scrobble_range(self, client):
        body = client.get("/api/overview").json()
        rng = body["scrobble_range"]
        assert rng["first"] == 2020
        assert rng["last"] == 2020

    def test_coverage_keys(self, client):
        body = client.get("/api/overview").json()
        cov = body["coverage"]
        assert isinstance(cov, dict)
        for v in cov.values():
            assert "n" in v and "pct" in v


class TestGenres:
    def test_status(self, client):
        assert client.get("/api/genres").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/genres").json()
        assert isinstance(body, list)
        assert len(body) >= 1
        for item in body:
            assert "genre" in item
            assert "count" in item
            assert isinstance(item["count"], int)

    def test_contains_trip_hop(self, client):
        body = client.get("/api/genres").json()
        genres = {item["genre"] for item in body}
        assert "trip-hop" in genres

    def test_top_param(self, client):
        body = client.get("/api/genres?top=1").json()
        assert len(body) <= 1


class TestMoods:
    def test_status(self, client):
        assert client.get("/api/moods").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/moods").json()
        assert isinstance(body, list)
        for item in body:
            assert "mood" in item and "count" in item

    def test_contains_moody(self, client):
        body = client.get("/api/moods").json()
        moods = {item["mood"] for item in body}
        assert "Moody" in moods
        assert "Dark" in moods


class TestTimeline:
    def test_by_year(self, client):
        body = client.get("/api/timeline?by=year").json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["period"] == "2020"
        assert body[0]["plays"] == 1

    def test_by_month(self, client):
        body = client.get("/api/timeline?by=month").json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["period"] == "2020-06"
        assert body[0]["plays"] == 1

    def test_default_is_year(self, client):
        r = client.get("/api/timeline")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["period"] == "2020"


class TestTimeOfDay:
    def test_status(self, client):
        assert client.get("/api/time-of-day").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/time-of-day").json()
        assert "hour_weekday" in body
        assert "calendar" in body
        assert isinstance(body["hour_weekday"], list)
        assert isinstance(body["calendar"], list)

    def test_hour_weekday_entry(self, client):
        body = client.get("/api/time-of-day").json()
        # sample scrobble: hour=22, day_of_week=0
        entries = {(row[0], row[1]): row[2] for row in body["hour_weekday"]}
        assert entries.get((22, 0)) == 1

    def test_calendar_entry(self, client):
        body = client.get("/api/time-of-day").json()
        cal = {row[0]: row[1] for row in body["calendar"]}
        assert cal.get("2020-06-15") == 1


class TestArtistTrajectory:
    def test_status(self, client):
        assert client.get("/api/artist-trajectory").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/artist-trajectory").json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_portishead_present(self, client):
        body = client.get("/api/artist-trajectory?top=1").json()
        assert len(body["data"]) >= 1
        # each entry: [period, count, artist]
        entry = body["data"][0]
        assert len(entry) == 3
        assert entry[2] == "Portishead"
        assert entry[1] == 1
        assert entry[0] == "2020-06-01"


class TestTop:
    def test_artists(self, client):
        body = client.get("/api/top?dim=artists&n=5").json()
        assert isinstance(body, list)
        assert body[0]["name"] == "Portishead"
        assert body[0]["plays"] == 10

    def test_tracks(self, client):
        body = client.get("/api/top?dim=tracks&n=5").json()
        assert isinstance(body, list)
        assert body[0]["track"] == "Roads"
        assert body[0]["plays"] == 10

    def test_status_both_dims(self, client):
        assert client.get("/api/top?dim=artists").status_code == 200
        assert client.get("/api/top?dim=tracks").status_code == 200


class TestAudioFeatures:
    def test_status(self, client):
        assert client.get("/api/audio-features").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/audio-features").json()
        assert "histograms" in body
        assert "scatter" in body
        assert isinstance(body["histograms"], dict)
        assert isinstance(body["scatter"], list)

    def test_scatter_entry(self, client):
        body = client.get("/api/audio-features").json()
        assert len(body["scatter"]) == 1
        pt = body["scatter"][0]
        assert pt["artist"] == "Portishead"
        assert abs(pt["energy"] - 0.4) < 0.001
        assert abs(pt["valence"] - 0.2) < 0.001

    def test_histogram_keys(self, client):
        body = client.get("/api/audio-features").json()
        for key in ("energy", "valence", "danceability"):
            assert key in body["histograms"]
            assert isinstance(body["histograms"][key], list)


class TestSaturation:
    def test_status(self, client):
        assert client.get("/api/saturation").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/saturation").json()
        assert isinstance(body, list)
        for item in body:
            assert "tier" in item and "count" in item

    def test_tier1_present(self, client):
        body = client.get("/api/saturation").json()
        tiers = {item["tier"]: item["count"] for item in body}
        assert tiers.get("1") == 1


class TestTracks:
    def test_no_filter(self, client):
        body = client.get("/api/tracks").json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert len(body["tracks"]) == 1

    def test_genre_filter_match(self, client):
        body = client.get("/api/tracks?genre=trip-hop").json()
        assert body["total"] == 1

    def test_genre_filter_no_match(self, client):
        body = client.get("/api/tracks?genre=jazz").json()
        assert body["total"] == 0

    def test_genre_filter_case_insensitive(self, client):
        body = client.get("/api/tracks?genre=TRIP-HOP").json()
        assert body["total"] == 1

    def test_mood_filter_match(self, client):
        body = client.get("/api/tracks?mood=moody").json()
        assert body["total"] == 1

    def test_mood_filter_no_match(self, client):
        body = client.get("/api/tracks?mood=happy").json()
        assert body["total"] == 0

    def test_artist_filter_match(self, client):
        body = client.get("/api/tracks?artist=portis").json()
        assert body["total"] == 1

    def test_artist_filter_no_match(self, client):
        body = client.get("/api/tracks?artist=radiohead").json()
        assert body["total"] == 0

    def test_year_filter_match(self, client):
        body = client.get("/api/tracks?year=1994").json()
        assert body["total"] == 1

    def test_year_filter_no_match(self, client):
        body = client.get("/api/tracks?year=2000").json()
        assert body["total"] == 0

    def test_energy_filter(self, client):
        body = client.get("/api/tracks?min_energy=0.3&max_energy=0.5").json()
        assert body["total"] == 1

    def test_energy_filter_excludes(self, client):
        body = client.get("/api/tracks?min_energy=0.8").json()
        assert body["total"] == 0

    def test_pagination_beyond_results(self, client):
        body = client.get("/api/tracks?page=2&per_page=1").json()
        assert body["total"] == 1
        assert body["page"] == 2
        assert body["tracks"] == []

    def test_track_fields(self, client):
        body = client.get("/api/tracks").json()
        t = body["tracks"][0]
        assert t["artist"] == "Portishead"
        assert t["track"] == "Roads"
        assert t["play_count"] == 10


class TestTagGraph:
    def test_status(self, client):
        assert client.get("/api/tag-graph").status_code == 200

    def test_structure(self, client):
        body = client.get("/api/tag-graph?field=discogs_styles&min_count=1").json()
        assert "nodes" in body and "edges" in body
        assert isinstance(body["nodes"], list)
        assert isinstance(body["edges"], list)

    def test_nodes_have_required_fields(self, client):
        body = client.get("/api/tag-graph?field=discogs_styles&min_count=1").json()
        assert len(body["nodes"]) >= 1
        for node in body["nodes"]:
            assert "tag" in node and "count" in node
            assert isinstance(node["count"], int)

    def test_edges_connect_known_tags(self, client):
        body = client.get("/api/tag-graph?field=discogs_styles&min_count=1").json()
        node_names = {n["tag"] for n in body["nodes"]}
        # sample track has ["Trip Hop", "Downtempo"] → edge between them
        assert len(body["edges"]) >= 1
        e = body["edges"][0]
        assert "source" in e and "target" in e and "weight" in e
        assert e["source"] in node_names
        assert e["target"] in node_names

    def test_min_count_cutoff(self, client):
        # min_count higher than any tag count in the 1-track fixture → empty
        body = client.get("/api/tag-graph?field=discogs_styles&min_count=500").json()
        assert body["nodes"] == []
        assert body["edges"] == []

    def test_mood_tags_field(self, client):
        body = client.get("/api/tag-graph?field=mood_tags&min_count=1").json()
        assert body["field"] == "mood_tags"
        node_names = {n["tag"] for n in body["nodes"]}
        assert "Moody" in node_names


class TestReload:
    def test_status(self, client):
        r = client.post("/api/reload", headers=AUTH)
        assert r.status_code == 200

    def test_returns_counts(self, client):
        body = client.post("/api/reload", headers=AUTH).json()
        assert body["tracks"] == 1
        assert body["scrobbles"] == 1

    def test_reports_skipped_counts(self, client):
        body = client.post("/api/reload", headers=AUTH).json()
        assert body["skipped"] == {"tracks": 0, "scrobbles": 0}


class TestAuthGate:
    def test_reload_without_token_is_403(self, client):
        assert client.post("/api/reload").status_code == 403

    def test_reload_with_wrong_token_is_403(self, client):
        r = client.post("/api/reload", headers={"X-Dashboard-Token": "nope"})
        assert r.status_code == 403

    def test_config_exposes_token(self, client):
        body = client.get("/api/config").json()
        assert body["token"] == DASHBOARD_TOKEN

    def test_no_cors_headers(self, client):
        # CORS middleware was removed — no access-control-* headers should appear.
        r = client.get("/api/overview", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


class TestTracksMinJsonl:
    def test_only_ships_ui_fields(self, client):
        r = client.get("/tracks.min.jsonl")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        row = json.loads(r.text.strip().splitlines()[0])
        # kept fields
        assert row["artist"] == "Portishead"
        assert row["audio_features"] == {
            "energy": 0.4, "valence": 0.2, "danceability": 0.5, "acousticness": 0.6,
        }
        # id-presence flags collapsed to booleans
        assert row["musicbrainz_id"] is False and row["spotify_id"] is False
        # trimmed fields must be absent
        for gone in ("enriched_at", "rejected_reason", "blacklisted", "curation_state"):
            assert gone not in row

    def test_conditional_304(self, client):
        etag = client.get("/tracks.min.jsonl").headers["etag"]
        r = client.get("/tracks.min.jsonl", headers={"If-None-Match": etag})
        assert r.status_code == 304


class TestStaticCaching:
    def test_static_assets_have_cache_control(self, client):
        r = client.get("/index.html")
        assert r.status_code == 200
        assert "max-age" in r.headers.get("cache-control", "")


class TestMalformedRows:
    """B1 — a row missing hot fields must degrade, not 500."""

    def test_aggregations_survive_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp) / "tracks.jsonl"
            sp = Path(tmp) / "scrobbles.jsonl"
            # tracks row with no artist; scrobble row with no year/hour/day_of_week
            tp.write_text(json.dumps({"track": "Orphan", "play_count": 3}) + "\n", encoding="utf-8")
            sp.write_text(json.dumps({"scrobbled_at": "2021-01-01T00:00:00Z"}) + "\n", encoding="utf-8")
            with data.use_paths(tp, sp):
                c = TestClient(app)
                for path in ("/api/overview", "/api/timeline", "/api/time-of-day",
                             "/api/artist-trajectory", "/api/albums", "/api/top"):
                    assert c.get(path).status_code == 200, path


class TestLastFmStatus:
    def test_status_ok(self, client):
        r = client.get("/api/lastfm/status")
        assert r.status_code == 200

    def test_response_shape(self, client):
        body = client.get("/api/lastfm/status").json()
        assert "scrobble_count" in body
        assert "last_scrobbled_at" in body
        assert "first_scrobbled_at" in body
        assert isinstance(body["configured"], bool)

    def test_scrobble_count_matches_fixture(self, client):
        body = client.get("/api/lastfm/status").json()
        assert body["scrobble_count"] == 1

    def test_last_scrobbled_at_is_iso(self, client):
        body = client.get("/api/lastfm/status").json()
        assert body["last_scrobbled_at"] == "2020-06-15T22:30:00Z"

    def test_not_configured_without_env(self, client, monkeypatch):
        monkeypatch.delenv("LASTFM_USERNAME", raising=False)
        monkeypatch.delenv("LASTFM_API_KEY", raising=False)
        body = client.get("/api/lastfm/status").json()
        assert body["configured"] is False
