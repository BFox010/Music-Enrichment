"""Tests for Phase 5b — ReccoBeats audio features, keyed by ISRC.

Pins the two-step shape (batch ISRC->track-id resolve, then per-track
audio-features fetch) against a stubbed client — no network.
"""

from __future__ import annotations

import json

from pipeline import enrich_audio_features as eaf
from pipeline.enrich_audio_features import (
    _fetch_audio_features,
    _parse_features,
    _resolve_track_ids,
    enrich,
)


# ── _parse_features ──


class TestParseFeatures:
    def test_extracts_known_keys(self) -> None:
        resp = {
            "danceability": 0.5, "energy": 0.8, "tempo": 120.0,
            "some_unrelated_field": "x",
        }
        features = _parse_features(resp)
        assert features == {"danceability": 0.5, "energy": 0.8, "tempo": 120.0}

    def test_time_signature_never_included(self) -> None:
        """ReccoBeats' audio-features endpoint doesn't carry time_signature
        per issue #37's survey — confirm the key map doesn't invent one."""
        resp = {"danceability": 0.5, "time_signature": 4}
        features = _parse_features(resp)
        assert "time_signature" not in features

    def test_error_response_is_none(self) -> None:
        assert _parse_features({"_error": "not_found"}) is None

    def test_non_dict_response_is_none(self) -> None:
        assert _parse_features(None) is None  # type: ignore[arg-type]

    def test_no_recognized_keys_is_none(self) -> None:
        assert _parse_features({"unrelated": "x"}) is None


# ── _resolve_track_ids ──


class _StubResolveClient:
    def __init__(self, by_batch_key: dict[str, dict]) -> None:
        self._by_batch_key = by_batch_key
        self.requests: list[dict] = []

    def get(self, url, params, cache_key, **_kw):
        self.requests.append(params)
        return self._by_batch_key.get(params.get("ids"), {"content": []})


class TestResolveTrackIds:
    def test_maps_isrc_to_track_id(self) -> None:
        client = _StubResolveClient({
            "USABC1234567": {"content": [{"id": "rb1", "isrc": "usabc1234567"}]},
        })
        result = _resolve_track_ids(client, ["USABC1234567"])
        assert result == {"USABC1234567": "rb1"}

    def test_partial_batch_match_is_not_an_error(self) -> None:
        client = _StubResolveClient({
            "USABC1234567,USXYZ0000001": {
                "content": [{"id": "rb1", "isrc": "USABC1234567"}],
            },
        })
        result = _resolve_track_ids(client, ["USABC1234567", "USXYZ0000001"])
        assert result == {"USABC1234567": "rb1"}
        assert "USXYZ0000001" not in result

    def test_error_response_yields_nothing(self) -> None:
        client = _StubResolveClient({})
        assert _resolve_track_ids(client, ["USNOPE00001"]) == {}

    def test_batches_large_input(self) -> None:
        isrcs = [f"US{i:011d}" for i in range(85)]  # > 2x _BATCH_SIZE
        client = _StubResolveClient({})
        _resolve_track_ids(client, isrcs)
        assert len(client.requests) == 3  # 40 + 40 + 5


# ── _fetch_audio_features ──


class _StubFeaturesClient:
    def __init__(self, by_track_id: dict[str, dict]) -> None:
        self._by_track_id = by_track_id

    def get(self, url, params, cache_key, **_kw):
        track_id = url.rsplit("/", 2)[-2]  # .../track/<id>/audio-features
        return self._by_track_id.get(track_id, {"_error": "not_found"})


class TestFetchAudioFeatures:
    def test_returns_parsed_features(self) -> None:
        client = _StubFeaturesClient({"rb1": {"danceability": 0.5, "energy": 0.8}})
        assert _fetch_audio_features(client, "rb1") == {"danceability": 0.5, "energy": 0.8}

    def test_missing_track_id_returns_none(self) -> None:
        client = _StubFeaturesClient({})
        assert _fetch_audio_features(client, "unknown") is None


# ── enrich() end-to-end ──


class TestEnrichPersistsFeatures:
    @staticmethod
    def _run(monkeypatch, tmp_path, tracks, track_ids=None, features_by_id=None):
        src = tmp_path / "in.jsonl"
        src.write_text("".join(json.dumps(t) + "\n" for t in tracks), encoding="utf-8")
        out = tmp_path / "out.jsonl"

        monkeypatch.setattr(
            eaf, "RateLimitedClient",
            lambda *a, **k: type("C", (), {
                "flush": lambda self: None,
                "warn_if_forced": lambda self, n: None,
                "cache_summary": lambda self: "stub",
            })(),
        )
        monkeypatch.setattr(
            eaf, "_resolve_track_ids",
            lambda client, isrcs: track_ids or {},
        )
        monkeypatch.setattr(
            eaf, "_fetch_audio_features",
            lambda client, track_id: (features_by_id or {}).get(track_id),
        )
        stats = eaf.enrich(input_path=src, output_path=out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
        return stats, {r["track"]: r for r in rows}

    def test_resolved_isrc_gets_features(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USABC1234567"}],
            track_ids={"USABC1234567": "rb1"},
            features_by_id={"rb1": {"danceability": 0.5, "energy": 0.8}},
        )
        af = rows["B"]["audio_features"]
        assert af["danceability"] == 0.5
        assert af["source"] == "reccobeats"
        assert "retrieved_at" in af
        assert stats["resolved"] == 1

    def test_never_overwrites_existing_audio_features(self, monkeypatch, tmp_path) -> None:
        """Exportify data was acquired at real cost and is not this phase's
        to second-guess."""
        existing = {"source": "exportify", "danceability": 0.1}
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USABC1234567",
              "audio_features": existing}],
            track_ids={"USABC1234567": "rb1"},
            features_by_id={"rb1": {"danceability": 0.99}},
        )
        assert rows["B"]["audio_features"] == existing
        assert stats["already_had"] == 1
        assert stats["resolved"] == 0

    def test_no_isrc_is_skipped_without_a_request(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(monkeypatch, tmp_path, [{"artist": "A", "track": "B"}])
        assert not rows["B"].get("audio_features")
        assert stats["already_had"] == 1
        assert stats["resolved"] == 0
        assert stats["unresolved"] == 0

    def test_isrc_that_does_not_resolve_is_unresolved(self, monkeypatch, tmp_path) -> None:
        stats, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USNOPE00001"}],
        )
        assert not rows["B"].get("audio_features")
        assert stats["unresolved"] == 1

    def test_records_enrichment_source(self, monkeypatch, tmp_path) -> None:
        _, rows = self._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "USABC1234567"}],
            track_ids={"USABC1234567": "rb1"},
            features_by_id={"rb1": {"danceability": 0.5}},
        )
        assert "reccobeats" in rows["B"]["enrichment_sources"]


class TestIsrcCaseIsNormalized:
    """ReccoBeats echoes ISRCs upper-cased and the resolve map is keyed that way.

    A row carrying a lower-case code — Exportify's CSV column before #37 — looked
    up nothing and silently never got features.
    """

    def test_lowercase_row_isrc_still_resolves(self, monkeypatch, tmp_path) -> None:
        stats, rows = TestEnrichPersistsFeatures._run(
            monkeypatch, tmp_path,
            [{"artist": "A", "track": "B", "isrc": "usabc1234567"}],
            track_ids={"USABC1234567": "rb1"},
            features_by_id={"rb1": {"danceability": 0.5}},
        )
        assert stats["resolved"] == 1
        assert rows["B"]["audio_features"]["danceability"] == 0.5
        assert rows["B"]["isrc"] == "USABC1234567"
