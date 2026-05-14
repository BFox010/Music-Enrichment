"""Tests for pipeline_manifest.yaml integrity and orchestrator anti-drift.

Key invariant: the manifest is the single source of truth for phase execution
order. Any divergence between manifest and orchestrator must fail loudly here.
"""

from __future__ import annotations

import importlib

import pytest

from pipeline.config import SCHEMA_VERSION
from pipeline.manifest import (
    find_phase_index,
    get_phase_ids,
    get_phases,
    load_manifest,
    validate_manifest,
)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def phases(manifest):
    return get_phases(manifest)


# ── Manifest loads and is structurally valid ──────────────────────────────


class TestManifestStructure:
    def test_manifest_loads(self, manifest):
        assert "phases" in manifest
        assert "schema_version" in manifest

    def test_has_phases(self, phases):
        assert len(phases) > 0

    def test_schema_version_matches_config(self, manifest):
        assert manifest["schema_version"] == SCHEMA_VERSION

    def test_validate_manifest_returns_no_errors(self, manifest):
        errors = validate_manifest(manifest)
        assert errors == [], f"Manifest validation errors:\n" + "\n".join(errors)

    def test_phase_ids_are_unique(self, phases):
        ids = [str(p["id"]) for p in phases]
        assert len(ids) == len(set(ids)), f"Duplicate phase IDs: {ids}"

    def test_all_phases_have_id_and_name(self, phases):
        for phase in phases:
            assert phase.get("id"), f"Phase missing 'id': {phase}"
            assert phase.get("name"), f"Phase {phase['id']!r} missing 'name'"

    def test_non_manual_phases_have_module_and_callable(self, phases):
        for phase in phases:
            if not phase.get("manual"):
                pid = phase["id"]
                assert phase.get("module"), f"Phase {pid!r}: missing 'module'"
                assert phase.get("callable"), f"Phase {pid!r}: missing 'callable'"

    def test_manual_phases_have_null_module(self, phases):
        for phase in phases:
            if phase.get("manual"):
                assert phase.get("module") is None, (
                    f"Phase {phase['id']!r} is manual but has a module — "
                    "set module to null for manual phases"
                )

    def test_dependencies_reference_prior_phases(self, phases):
        seen: set[str] = set()
        for phase in phases:
            pid = str(phase["id"])
            for dep in phase.get("depends_on", []):
                assert str(dep) in seen, (
                    f"Phase {pid!r} depends_on {dep!r} which hasn't been "
                    f"defined yet (must precede it in the manifest)"
                )
            seen.add(pid)

    def test_resumable_field_is_bool(self, phases):
        for phase in phases:
            assert isinstance(phase.get("resumable"), bool), (
                f"Phase {phase['id']!r}: 'resumable' must be a boolean"
            )


# ── Known phases are present ──────────────────────────────────────────────


class TestExpectedPhases:
    EXPECTED_IDS = ["1", "2", "A", "3a", "3b", "3c", "4", "5", "6", "7", "8"]

    def test_all_expected_phase_ids_present(self, manifest):
        ids = get_phase_ids(manifest)
        for expected in self.EXPECTED_IDS:
            assert expected in ids, f"Expected phase {expected!r} not found in manifest"

    def test_execution_order_is_correct(self, manifest):
        ids = get_phase_ids(manifest)
        assert ids == self.EXPECTED_IDS, (
            f"Manifest phase order has changed.\n"
            f"  Expected: {self.EXPECTED_IDS}\n"
            f"  Got:      {ids}"
        )


# ── Anti-drift: orchestrator execution order matches manifest ─────────────


class TestOrchestratorAntiDrift:
    def test_orchestrator_order_matches_manifest(self, manifest):
        """The orchestrator's get_execution_order() must equal manifest order.

        If a phase is added to the manifest but the orchestrator has a bug
        that skips it, this test fails. If someone hardcodes a phase ID in
        the orchestrator outside of the manifest loop, this test catches it.
        """
        from pipeline.run_full_pipeline import get_execution_order

        manifest_ids = get_phase_ids(manifest)
        orchestrator_ids = get_execution_order()
        assert orchestrator_ids == manifest_ids, (
            f"Orchestrator and manifest have diverged!\n"
            f"  Manifest:      {manifest_ids}\n"
            f"  Orchestrator:  {orchestrator_ids}"
        )

    def test_orchestrator_uses_manifest_phases(self):
        """_PHASES in run_full_pipeline comes from the manifest, not hardcoded."""
        from pipeline.run_full_pipeline import _PHASES, _MANIFEST
        from pipeline.manifest import get_phases
        assert _PHASES is get_phases(_MANIFEST)


# ── Module importability ──────────────────────────────────────────────────


class TestModuleImports:
    def test_all_non_manual_modules_are_importable(self, phases):
        """Every module referenced in the manifest must be importable.

        This catches: typos in module paths, modules that have been renamed
        without updating the manifest, modules that fail to import cleanly.
        """
        failures: list[str] = []
        for phase in phases:
            if phase.get("manual"):
                continue
            module_path = phase["module"]
            try:
                importlib.import_module(module_path)
            except ImportError as e:
                failures.append(f"Phase {phase['id']!r} — {module_path}: {e}")
        assert not failures, (
            "The following manifest modules could not be imported:\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_all_callables_exist_on_modules(self, phases):
        """Every callable referenced in the manifest must exist on its module."""
        failures: list[str] = []
        for phase in phases:
            if phase.get("manual"):
                continue
            module_path = phase["module"]
            callable_name = phase["callable"]
            try:
                mod = importlib.import_module(module_path)
                if not hasattr(mod, callable_name):
                    failures.append(
                        f"Phase {phase['id']!r} — {module_path}.{callable_name}: "
                        f"attribute not found"
                    )
            except ImportError:
                pass  # already caught by test_all_non_manual_modules_are_importable
        assert not failures, (
            "The following manifest callables could not be resolved:\n"
            + "\n".join(f"  {f}" for f in failures)
        )


# ── find_phase_index helper ───────────────────────────────────────────────


class TestFindPhaseIndex:
    def test_finds_phase_by_id(self, phases):
        assert find_phase_index(phases, "1") == 0
        assert find_phase_index(phases, "8") == len(phases) - 1

    def test_finds_alpha_phase(self, phases):
        idx = find_phase_index(phases, "A")
        assert idx > 0

    def test_finds_compound_phase(self, phases):
        idx_3a = find_phase_index(phases, "3a")
        idx_3c = find_phase_index(phases, "3c")
        assert idx_3a < idx_3c

    def test_raises_for_unknown_phase(self, phases):
        with pytest.raises(ValueError, match="not found in manifest"):
            find_phase_index(phases, "999")
