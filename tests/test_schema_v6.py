"""Schema v6 tests — the current schema.

Verifies the rules in pipeline/schema.py:
- _schema_version is the FIRST field of every emitted record, value = 6
- Writers emit fields in stable, documented order (matches FIELD_DEFAULTS order)
- Readers preserve unknown fields (silent ignore = no crash, lossless)
- Registry maps version → dataclass
- canonical_track_id follows the documented priority chain

v6 (issue #63) dropped `blacklisted` and `rejected_reason` — playlist-generator
leftovers computed on every run but rendered nowhere on the dashboard — and
added `isrc_source`/`isrc_retrieved_at` (issue #37) as provenance for Phase
5a's resolved ISRCs. See tests/test_schema_v5.py for the legacy-read
compat tests covering the fields this version removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config import SCHEMA_VERSION
from pipeline.schema import (
    FIELD_DEFAULTS,
    SCHEMA_REGISTRY,
    TrackV6,
    _order_for_emit,
    compute_canonical_track_id,
    fill_defaults,
    get_schema,
    read_jsonl,
    write_jsonl,
)


# ── Registry + version constants ─────────────────────────────────────────


class TestSchemaRegistry:
    def test_current_version_is_six(self) -> None:
        assert SCHEMA_VERSION == 6

    def test_registry_has_v6(self) -> None:
        assert 6 in SCHEMA_REGISTRY
        assert SCHEMA_REGISTRY[6] is TrackV6

    def test_get_schema_returns_dataclass(self) -> None:
        assert get_schema() is TrackV6
        assert get_schema(6) is TrackV6

    def test_get_schema_unknown_version_raises(self) -> None:
        with pytest.raises(KeyError):
            get_schema(999)


# ── #63: blacklisted / rejected_reason are gone; isrc provenance added ────


class TestV6FieldRemovals:
    def test_blacklisted_not_in_field_defaults(self) -> None:
        assert "blacklisted" not in FIELD_DEFAULTS

    def test_rejected_reason_not_in_field_defaults(self) -> None:
        assert "rejected_reason" not in FIELD_DEFAULTS

    def test_blacklisted_not_a_trackv6_field(self) -> None:
        assert not hasattr(TrackV6(), "blacklisted")

    def test_rejected_reason_not_a_trackv6_field(self) -> None:
        assert not hasattr(TrackV6(), "rejected_reason")

    def test_fill_defaults_does_not_add_removed_fields(self) -> None:
        out = fill_defaults({})
        assert "blacklisted" not in out
        assert "rejected_reason" not in out

    def test_isrc_provenance_fields_default_to_none(self) -> None:
        out = fill_defaults({})
        assert out["isrc_source"] is None
        assert out["isrc_retrieved_at"] is None


# ── _schema_version FIRST field discipline ───────────────────────────────


class TestSchemaVersionFirstField:
    def test_fill_defaults_includes_schema_version(self) -> None:
        out = fill_defaults({"artist": "x", "track": "y"})
        assert out["_schema_version"] == 6

    def test_emit_order_puts_version_first(self) -> None:
        # Even when input dict has _schema_version last
        row = {"artist": "x", "track": "y", "_schema_version": 6}
        emitted = _order_for_emit(row)
        first_key = next(iter(emitted))
        assert first_key == "_schema_version"

    def test_emit_order_when_version_missing_in_input(self) -> None:
        emitted = _order_for_emit({"artist": "x", "track": "y"})
        first_key = next(iter(emitted))
        assert first_key == "_schema_version"
        assert emitted["_schema_version"] == 6

    def test_write_jsonl_emits_version_first(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        write_jsonl([{"artist": "x", "track": "y"}], path)
        with open(path, encoding="utf-8") as fh:
            line = fh.readline()
        # First key in the serialized JSON line
        parsed = json.loads(line)
        assert next(iter(parsed)) == "_schema_version"
        # And the raw line text starts with the schema_version key
        assert line.lstrip().startswith('{"_schema_version":')


# ── Stable field order ────────────────────────────────────────────────────


class TestStableFieldOrder:
    def test_emit_order_matches_field_defaults(self) -> None:
        row = {k: v for k, v in FIELD_DEFAULTS.items()}
        emitted = _order_for_emit(row)
        assert list(emitted.keys()) == list(FIELD_DEFAULTS.keys())

    def test_emit_order_deterministic_across_calls(self) -> None:
        row = {"track": "a", "artist": "b", "album": "c", "_schema_version": 6}
        a = list(_order_for_emit(row).keys())
        b = list(_order_for_emit(row).keys())
        assert a == b
        # And it should not match insertion order — should match FIELD_DEFAULTS order
        # _schema_version first, then canonical_track_id, then artist, track, ..., album
        assert a.index("artist") < a.index("track") < a.index("album")


# ── Unknown / future-version fields ───────────────────────────────────────


class TestUnknownFieldsIgnored:
    def test_v6_record_with_future_field_loads(self) -> None:
        """A v6 reader handed a record with a v7-style extra field must not crash."""
        row = {
            "_schema_version": 7,  # pretend future version
            "artist": "x",
            "track": "y",
            "artist_normalized": "x",
            "track_normalized": "y",
            "future_unknown_field": {"some": "blob"},
        }
        # fill_defaults must not throw, must preserve the unknown field
        out = fill_defaults(row)
        assert out["future_unknown_field"] == {"some": "blob"}
        assert out["artist"] == "x"

    def test_trackv6_from_dict_drops_unknowns_into_extras(self) -> None:
        row = {
            "artist": "x",
            "track": "y",
            "artist_normalized": "x",
            "track_normalized": "y",
            "future_unknown_field": "kept",
        }
        obj = TrackV6.from_dict(row)
        assert obj.artist == "x"
        assert obj._extras == {"future_unknown_field": "kept"}

    def test_trackv6_to_dict_preserves_extras_at_end(self) -> None:
        row = {
            "artist": "x",
            "track": "y",
            "artist_normalized": "x",
            "track_normalized": "y",
            "future_unknown_field": "kept",
        }
        obj = TrackV6.from_dict(row)
        out = obj.to_dict()
        assert out["future_unknown_field"] == "kept"
        # Unknown comes after all known fields
        keys = list(out.keys())
        assert keys.index("future_unknown_field") > keys.index("enrichment_sources")


# ── v6 roundtrip ──────────────────────────────────────────────────────────


class TestV6Roundtrip:
    def test_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        row = fill_defaults(
            {
                "artist": "Portishead",
                "track": "Roads",
                "artist_normalized": "portishead",
                "track_normalized": "roads",
                "musicbrainz_id": "abc-123",
                "play_count": 47,
                "genres": ["trip-hop", "downtempo"],
                "isrc_source": "musicbrainz",
                "audio_features": {"valence": 0.21, "energy": 0.34},
                "future_unknown_field": ["x", "y"],
            }
        )
        path = tmp_path / "rt.jsonl"
        write_jsonl([row], path)
        [restored] = read_jsonl(path)
        # All known fields equal
        for key in FIELD_DEFAULTS:
            assert restored[key] == row[key], f"mismatch on {key}"
        # Unknown field preserved
        assert restored["future_unknown_field"] == ["x", "y"]

    def test_roundtrip_via_trackv6_dataclass(self) -> None:
        row = fill_defaults({"artist": "x", "track": "y",
                             "artist_normalized": "x", "track_normalized": "y"})
        obj = TrackV6.from_dict(row)
        out = obj.to_dict()
        for key in FIELD_DEFAULTS:
            assert out[key] == row[key], f"mismatch on {key}"


# ── Legacy / v4 reader compat ─────────────────────────────────────────────


class TestLegacyRecordCompat:
    """A 'v4' record (pre-_schema_version tracks.jsonl shape) must still parse
    cleanly. The v6 reader stamps the version on read via fill_defaults; the
    schema is permissive about missing fields."""

    def test_record_without_schema_version_loads(self, tmp_path: Path) -> None:
        legacy_row = {
            "artist": "Portishead",
            "track": "Roads",
            "artist_normalized": "portishead",
            "track_normalized": "roads",
            "play_count": 47,
            # NOTE: no _schema_version, no canonical_track_id
        }
        path = tmp_path / "legacy.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy_row) + "\n")
        [restored] = read_jsonl(path)
        # Reader doesn't auto-stamp — it returns raw rows. But fill_defaults does.
        assert "_schema_version" not in restored  # raw read preserves absence
        filled = fill_defaults(restored)
        assert filled["_schema_version"] == 6
        assert filled["canonical_track_id"].startswith("norm:portishead|roads")

    def test_invalid_jsonl_reports_line_number(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"artist": "ok"}\n{not json}\n', encoding="utf-8")
        with pytest.raises(ValueError, match=r"bad\.jsonl:2: invalid JSONL row"):
            read_jsonl(path)

    def test_non_object_jsonl_row_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('["not", "an", "object"]\n', encoding="utf-8")
        with pytest.raises(ValueError, match="expected JSON object row"):
            read_jsonl(path)

    def test_existing_schema_exports_still_present(self) -> None:
        """Regression: existing imports in update_tracks.py must still resolve."""
        from pipeline.schema import (  # noqa: F401
            HUMAN_EDITED_FIELDS,
            fill_defaults,
            validate_dataset,
        )


# ── canonical_track_id priority chain ─────────────────────────────────────


class TestCanonicalTrackId:
    def test_mbid_wins(self) -> None:
        cid = compute_canonical_track_id({
            "musicbrainz_id": "MB-001",
            "isrc": "ISRC-002",
            "artist_normalized": "x",
            "track_normalized": "y",
        })
        assert cid == "mbid:MB-001"

    def test_isrc_when_no_mbid(self) -> None:
        cid = compute_canonical_track_id({
            "musicbrainz_id": None,
            "isrc": "ISRC-002",
            "artist_normalized": "x",
            "track_normalized": "y",
        })
        assert cid == "isrc:ISRC-002"

    def test_normalized_when_no_mbid_or_isrc(self) -> None:
        cid = compute_canonical_track_id({
            "artist_normalized": "portishead",
            "track_normalized": "roads",
        })
        assert cid == "norm:portishead|roads"

    def test_fallback_hash_when_no_normalized(self) -> None:
        cid = compute_canonical_track_id({
            "artist": "X Y Z",
            "track": "Some Track",
        })
        assert cid.startswith("hash:")
        assert len(cid) == len("hash:") + 16

    def test_empty_when_no_identity_at_all(self) -> None:
        assert compute_canonical_track_id({}) == ""

    def test_fill_defaults_populates_canonical_id(self) -> None:
        out = fill_defaults({
            "artist": "Portishead",
            "track": "Roads",
            "artist_normalized": "portishead",
            "track_normalized": "roads",
        })
        assert out["canonical_track_id"] == "norm:portishead|roads"

    def test_fill_defaults_does_not_overwrite_existing_id(self) -> None:
        out = fill_defaults({
            "canonical_track_id": "mbid:already-set",
            "artist": "x", "track": "y",
            "artist_normalized": "x", "track_normalized": "y",
            "musicbrainz_id": "different-mbid",
        })
        assert out["canonical_track_id"] == "mbid:already-set"
