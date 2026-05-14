"""Schema v5 migration + registry tests.

Verifies the rules in pipeline/schema.py:
- _schema_version is the FIRST field of every emitted record, value = 5
- Writers emit fields in stable, documented order (matches FIELD_DEFAULTS order)
- Readers preserve unknown fields (silent ignore = no crash, lossless)
- Registry maps version → dataclass
- canonical_track_id follows the documented priority chain
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import SCHEMA_VERSION
from pipeline.schema import (
    FIELD_DEFAULTS,
    SCHEMA_REGISTRY,
    TrackV5,
    _order_for_emit,
    compute_canonical_track_id,
    fill_defaults,
    get_schema,
    read_jsonl,
    write_jsonl,
)


# ── Registry + version constants ─────────────────────────────────────────


class TestSchemaRegistry:
    def test_current_version_is_five(self) -> None:
        assert SCHEMA_VERSION == 5

    def test_registry_has_v5(self) -> None:
        assert 5 in SCHEMA_REGISTRY
        assert SCHEMA_REGISTRY[5] is TrackV5

    def test_get_schema_returns_dataclass(self) -> None:
        assert get_schema() is TrackV5
        assert get_schema(5) is TrackV5

    def test_get_schema_unknown_version_raises(self) -> None:
        import pytest

        with pytest.raises(KeyError):
            get_schema(999)


# ── _schema_version FIRST field discipline ───────────────────────────────


class TestSchemaVersionFirstField:
    def test_fill_defaults_includes_schema_version(self) -> None:
        out = fill_defaults({"artist": "x", "track": "y"})
        assert out["_schema_version"] == 5

    def test_emit_order_puts_version_first(self) -> None:
        # Even when input dict has _schema_version last
        row = {"artist": "x", "track": "y", "_schema_version": 5}
        emitted = _order_for_emit(row)
        first_key = next(iter(emitted))
        assert first_key == "_schema_version"

    def test_emit_order_when_version_missing_in_input(self) -> None:
        emitted = _order_for_emit({"artist": "x", "track": "y"})
        first_key = next(iter(emitted))
        assert first_key == "_schema_version"
        assert emitted["_schema_version"] == 5

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
        row = {"track": "a", "artist": "b", "album": "c", "_schema_version": 5}
        a = list(_order_for_emit(row).keys())
        b = list(_order_for_emit(row).keys())
        assert a == b
        # And it should not match insertion order — should match FIELD_DEFAULTS order
        # _schema_version first, then canonical_track_id, then artist, track, ..., album
        assert a.index("artist") < a.index("track") < a.index("album")


# ── Unknown / future-version fields ───────────────────────────────────────


class TestUnknownFieldsIgnored:
    def test_v5_record_with_future_field_loads(self) -> None:
        """A v5 reader handed a record with a v6-style extra field must not crash."""
        row = {
            "_schema_version": 6,  # pretend future version
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

    def test_trackv5_from_dict_drops_unknowns_into_extras(self) -> None:
        row = {
            "artist": "x",
            "track": "y",
            "artist_normalized": "x",
            "track_normalized": "y",
            "future_unknown_field": "kept",
        }
        obj = TrackV5.from_dict(row)
        assert obj.artist == "x"
        assert obj._extras == {"future_unknown_field": "kept"}

    def test_trackv5_to_dict_preserves_extras_at_end(self) -> None:
        row = {
            "artist": "x",
            "track": "y",
            "artist_normalized": "x",
            "track_normalized": "y",
            "future_unknown_field": "kept",
        }
        obj = TrackV5.from_dict(row)
        out = obj.to_dict()
        assert out["future_unknown_field"] == "kept"
        # Unknown comes after all known fields
        keys = list(out.keys())
        assert keys.index("future_unknown_field") > keys.index("enrichment_sources")


# ── v5 roundtrip ──────────────────────────────────────────────────────────


class TestV5Roundtrip:
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
                "blacklisted": False,
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

    def test_roundtrip_via_trackv5_dataclass(self) -> None:
        row = fill_defaults({"artist": "x", "track": "y",
                             "artist_normalized": "x", "track_normalized": "y"})
        obj = TrackV5.from_dict(row)
        out = obj.to_dict()
        for key in FIELD_DEFAULTS:
            assert out[key] == row[key], f"mismatch on {key}"


# ── Legacy / v4 reader compat ─────────────────────────────────────────────


class TestLegacyRecordCompat:
    """A 'v4' record (current tracks.jsonl shape — no _schema_version) must
    still parse cleanly. The v5 reader stamps the version on read via
    fill_defaults; the schema is permissive about missing fields."""

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
        assert filled["_schema_version"] == 5
        assert filled["canonical_track_id"].startswith("norm:portishead|roads")

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
        })
        assert out["canonical_track_id"] == "mbid:already-set"
