"""Tests for pipeline.enrich_genre_backfill (phase 4d).

The cascade is exercised with a fake client (no network): Last.fm artist tags
first, MusicBrainz artist genres only as a fallback, gap tracks only.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pipeline.enrich_genre_backfill as mod
from pipeline.enrich_genre_backfill import (
    _names_from_lastfm_toptags,
    _names_from_musicbrainz_artist,
)


class TestExtractHelpers:
    def test_lastfm_toptags_names(self) -> None:
        resp = {"toptags": {"tag": [{"name": "Hip-Hop"}, {"name": "rap"}]}}
        assert _names_from_lastfm_toptags(resp) == ["Hip-Hop", "rap"]

    def test_lastfm_single_tag_as_dict(self) -> None:
        resp = {"toptags": {"tag": {"name": "soul"}}}
        assert _names_from_lastfm_toptags(resp) == ["soul"]

    def test_lastfm_error_returns_empty(self) -> None:
        assert _names_from_lastfm_toptags({"_error": "not_found"}) == []

    def test_mb_genres_before_tags(self) -> None:
        resp = {"genres": [{"name": "rock"}], "tags": [{"name": "indie"}]}
        assert _names_from_musicbrainz_artist(resp) == ["rock", "indie"]

    def test_mb_error_returns_empty(self) -> None:
        assert _names_from_musicbrainz_artist({"_error": "max_retries"}) == []


# ── Cascade (fake client, no network) ──

NOT_FOUND = {"_error": "not_found"}


class FakeClient:
    """Replaces RateLimitedClient; returns canned responses keyed by cache_key."""

    responses: dict[str, dict] = {}
    all_calls: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def get(self, _url: str, _params: dict, cache_key: str) -> dict:
        FakeClient.all_calls.append(cache_key)
        return FakeClient.responses.get(cache_key, NOT_FOUND)

    def flush(self) -> None:
        pass

    def warn_if_forced(self, _n_requests: int) -> None:
        pass

    def cache_summary(self) -> str:
        return "cache (fake)"


def _run(tracks: list[dict], responses: dict[str, dict], monkeypatch) -> tuple[list[dict], dict]:
    FakeClient.responses = responses
    FakeClient.all_calls = []
    monkeypatch.setattr(mod, "RateLimitedClient", FakeClient)
    monkeypatch.setenv("LASTFM_API_KEY", "test-key")
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.jsonl"
        out = Path(tmp) / "out.jsonl"
        with open(inp, "w", encoding="utf-8") as fh:
            for t in tracks:
                fh.write(json.dumps(t) + "\n")
        stats = mod.enrich(input_path=inp, output_path=out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows, stats


def _track(artist, norm, genres=None, artist_mbid=None) -> dict:
    t = {"artist": artist, "track": "T", "artist_normalized": norm,
         "track_normalized": "t", "genres": genres or []}
    if artist_mbid:
        t["artist_mbid"] = artist_mbid
    return t


def test_passthrough_leaves_tagged_tracks_untouched(monkeypatch) -> None:
    rows, stats = _run([_track("X", "x", genres=["Rock"])], {}, monkeypatch)
    assert rows[0]["genres"] == ["Rock"]
    assert stats["gap"] == 0
    assert FakeClient.all_calls == []  # no API calls for already-tagged tracks


def test_lastfm_artist_recovers_and_skips_mb(monkeypatch) -> None:
    track = _track("A$AP Rocky", "asap rocky", artist_mbid="mbid-1")
    responses = {"artisttags|asap rocky": {"toptags": {"tag": [{"name": "Hip-Hop"}]}}}
    rows, stats = _run([track], responses, monkeypatch)
    assert "Hip-Hop / Rap" in rows[0]["genres"]
    assert rows[0]["lastfm_artist_tags"] == ["Hip-Hop"]
    assert stats["recovered_lastfm_artist"] == 1
    # MusicBrainz must NOT be consulted once Last.fm yielded a genre.
    assert not any(c.startswith("mbartist|") for c in FakeClient.all_calls)


def test_primary_artist_retry_on_collab(monkeypatch) -> None:
    # Full credit finds nothing; primary artist (first_artist) carries the genre.
    track = _track("A$AP NAST & D33J", "asap nast & d33j")
    responses = {
        "artisttags|asap nast & d33j": {"toptags": {"tag": []}},      # collab: empty
        # primary artist cache key is normalize_artist("A$AP NAST") == "a ap nast"
        "artisttags|a ap nast": {"toptags": {"tag": [{"name": "Hip-Hop"}]}},
    }
    rows, stats = _run([track], responses, monkeypatch)
    assert "Hip-Hop / Rap" in rows[0]["genres"]
    assert stats["recovered_lastfm_artist"] == 1
    assert stats["recovered_via_first_artist"] == 1


def test_no_primary_retry_for_single_artist(monkeypatch) -> None:
    # A single (non-collab) artist must not trigger a second lookup.
    track = _track("Radiohead", "radiohead")
    responses = {"artisttags|radiohead": {"toptags": {"tag": [{"name": "rock"}]}}}
    rows, stats = _run([track], responses, monkeypatch)
    assert "Rock" in rows[0]["genres"]
    assert stats["recovered_via_first_artist"] == 0
    # Only one artist-tag call was made.
    assert sum(1 for c in FakeClient.all_calls if c.startswith("artisttags|")) == 1


def test_musicbrainz_fallback_when_lastfm_empty(monkeypatch) -> None:
    track = _track("Obscure", "obscure", artist_mbid="mbid-9")
    responses = {
        "artisttags|obscure": {"toptags": {"tag": []}},          # nothing usable
        "mbartist|mbid-9": {"genres": [{"name": "jazz"}]},        # MB has it
    }
    rows, stats = _run([track], responses, monkeypatch)
    assert "Jazz" in rows[0]["genres"]
    assert rows[0]["musicbrainz_genres"] == ["jazz"]
    assert stats["recovered_musicbrainz"] == 1
    assert stats["recovered_lastfm_artist"] == 0


def test_no_artist_mbid_skips_mb(monkeypatch) -> None:
    track = _track("Nobody", "nobody")  # no artist_mbid
    responses = {"artisttags|nobody": {"toptags": {"tag": [{"name": "seen live"}]}}}
    rows, stats = _run([track], responses, monkeypatch)
    assert rows[0]["genres"] == []
    assert stats["still_empty"] == 1
    assert not any(c.startswith("mbartist|") for c in FakeClient.all_calls)


def test_still_empty_when_all_sources_miss(monkeypatch) -> None:
    track = _track("Ghost", "ghost", artist_mbid="mbid-0")
    rows, stats = _run([track], {}, monkeypatch)  # everything NOT_FOUND
    assert rows[0]["genres"] == []
    assert stats["still_empty"] == 1


class TestBackfillProvenance:
    """#73: Phase 4d recorded nothing about where an artist-level genre came
    from, so "4d looked and found nothing" and "4d never saw this row" were
    indistinguishable — the ambiguity that blocks diagnosing the genre gap.
    """

    def test_lastfm_route_records_its_provenance(self, monkeypatch) -> None:
        track = _track("A$AP Rocky", "asap rocky")
        responses = {"artisttags|asap rocky": {"toptags": {"tag": [{"name": "Hip-Hop"}]}}}
        rows, _ = _run([track], responses, monkeypatch)
        block = rows[0]["genre_backfill"]
        assert block["source"] == "lastfm_artist"
        assert block["pipeline_phase"] == "4d"
        assert block["confidence"] == "medium"
        assert block["retrieved_at"]

    def test_musicbrainz_route_records_its_provenance(self, monkeypatch) -> None:
        track = _track("Obscure", "obscure", artist_mbid="mbid-9")
        responses = {
            "artisttags|obscure": {"toptags": {"tag": []}},
            "mbartist|mbid-9": {"genres": [{"name": "jazz"}]},
        }
        rows, _ = _run([track], responses, monkeypatch)
        assert rows[0]["genre_backfill"]["source"] == "musicbrainz_artist"

    def test_primary_artist_retry_is_marked_lower_confidence(self, monkeypatch) -> None:
        """That retry drops the rest of a collab credit, so the genre is the
        lead artist's and not necessarily this track's."""
        track = _track("A$AP NAST & D33J", "asap nast & d33j")
        responses = {
            "artisttags|asap nast & d33j": {"toptags": {"tag": []}},
            "artisttags|a ap nast": {"toptags": {"tag": [{"name": "Hip-Hop"}]}},
        }
        rows, _ = _run([track], responses, monkeypatch)
        assert rows[0]["genre_backfill"]["confidence"] == "low"

    def test_examined_but_empty_is_distinguishable_from_never_seen(self, monkeypatch) -> None:
        """The acceptance criterion: a null source is a verdict, an absent
        block means 4d never visited the row."""
        examined = _track("Ghost", "ghost", artist_mbid="mbid-0")
        never_seen = _track("Tagged", "tagged", genres=["Rock"])
        rows, _ = _run([examined, never_seen], {}, monkeypatch)
        by = {r["artist"]: r for r in rows}

        assert by["Ghost"]["genre_backfill"]["source"] is None
        assert by["Ghost"]["genre_backfill"]["pipeline_phase"] == "4d"
        assert "genre_backfill" not in by["Tagged"]

    def test_enrichment_sources_distinguishes_the_two_routes(self) -> None:
        from pipeline.update_tracks import _enrichment_sources

        lastfm_row = {"genre_backfill": {"source": "lastfm_artist"}}
        mb_row = {"genre_backfill": {"source": "musicbrainz_artist"}}
        examined = {"genre_backfill": {"source": None}}

        assert "lastfm_artist_tags" in _enrichment_sources(lastfm_row)
        assert "musicbrainz_artist" in _enrichment_sources(mb_row)
        # Examined-and-empty contributed nothing, so it earns no marker.
        assert _enrichment_sources(examined) == []

    def test_backfill_fields_survive_the_schema_write_order(self) -> None:
        from pipeline.schema import FIELD_DEFAULTS, _order_for_emit, fill_defaults

        for name in ("lastfm_artist_tags", "musicbrainz_genres", "genre_backfill"):
            assert name in FIELD_DEFAULTS, f"{name} missing from FIELD_DEFAULTS"

        row = {
            "artist": "X", "track": "Y",
            "artist_normalized": "x", "track_normalized": "y",
            "musicbrainz_genres": ["jazz"], "lastfm_artist_tags": ["rap"],
            "genre_backfill": {"source": "musicbrainz_artist", "retrieved_at": "2026-08-25",
                               "pipeline_phase": "4d", "confidence": "medium"},
        }
        out = _order_for_emit(fill_defaults(row))
        assert out["musicbrainz_genres"] == ["jazz"]
        assert out["lastfm_artist_tags"] == ["rap"]
        assert out["genre_backfill"]["source"] == "musicbrainz_artist"
