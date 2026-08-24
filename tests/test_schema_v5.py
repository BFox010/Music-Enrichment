"""Schema v5 legacy-read compat tests.

v5 is superseded by v6 (issue #63 dropped `blacklisted` and `rejected_reason`
— playlist-generator leftovers computed on every run but rendered nowhere on
the dashboard). These tests guard that the registry and reader still load a
pre-migration v5-shaped row without crashing or silently losing data; the
actual field removal is a one-shot pass
(`scripts/archive/migrate_v5_to_v6.py`), not something `fill_defaults()` does
implicitly — `fill_defaults()` preserves unknown fields by design, so a v5 row
handed to it keeps carrying the removed fields as extras.

See tests/test_schema_v6.py for the current schema's tests (field order,
canonical_track_id, general JSONL I/O, etc).
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.schema import (
    SCHEMA_REGISTRY,
    TrackV5,
    fill_defaults,
    get_schema,
    read_jsonl,
)


class TestSchemaRegistryLegacy:
    def test_registry_has_v5(self) -> None:
        assert 5 in SCHEMA_REGISTRY
        assert SCHEMA_REGISTRY[5] is TrackV5

    def test_get_schema_v5_returns_trackv5(self) -> None:
        assert get_schema(5) is TrackV5

    def test_trackv5_schema_version_defaults_to_five_not_the_live_constant(self) -> None:
        """TrackV5's default must stay hardcoded at 5 — it must not silently
        track pipeline.config.SCHEMA_VERSION, which now means 6."""
        assert TrackV5()._schema_version == 5


class TestV5RecordStillLoads:
    def test_v5_record_with_removed_fields_reads_cleanly(self, tmp_path: Path) -> None:
        """A pre-migration row still carries these fields; read_jsonl must not
        choke on them."""
        row = {
            "_schema_version": 5,
            "artist": "Portishead", "track": "Roads",
            "artist_normalized": "portishead", "track_normalized": "roads",
            "blacklisted": True,
            "rejected_reason": "too overplayed",
        }
        path = tmp_path / "legacy_v5.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        [restored] = read_jsonl(path)
        assert restored["blacklisted"] is True
        assert restored["rejected_reason"] == "too overplayed"

    def test_fill_defaults_preserves_removed_fields_as_extras(self, tmp_path: Path) -> None:
        """fill_defaults() doesn't migrate schemas — it just fills gaps and
        preserves whatever it doesn't recognize. A v5 row's now-removed
        fields ride along as unknown extras rather than vanishing silently;
        actually dropping them is scripts/archive/migrate_v5_to_v6.py's job."""
        row = {
            "_schema_version": 5,
            "artist": "x", "track": "y",
            "artist_normalized": "x", "track_normalized": "y",
            "blacklisted": True,
            "rejected_reason": "kept for soak playlist",
        }
        filled = fill_defaults(row)
        assert filled["blacklisted"] is True
        assert filled["rejected_reason"] == "kept for soak playlist"
        assert filled["_schema_version"] == 5

    def test_trackv5_from_dict_still_parses_the_removed_fields(self) -> None:
        row = {
            "artist": "x", "track": "y",
            "artist_normalized": "x", "track_normalized": "y",
            "blacklisted": True,
            "rejected_reason": "kept for soak playlist",
        }
        obj = TrackV5.from_dict(row)
        assert obj.blacklisted is True
        assert obj.rejected_reason == "kept for soak playlist"

    def test_trackv5_to_dict_roundtrips_the_removed_fields(self) -> None:
        row = {
            "artist": "x", "track": "y",
            "artist_normalized": "x", "track_normalized": "y",
            "blacklisted": True,
            "rejected_reason": "too overplayed",
        }
        out = TrackV5.from_dict(row).to_dict()
        assert out["blacklisted"] is True
        assert out["rejected_reason"] == "too overplayed"
