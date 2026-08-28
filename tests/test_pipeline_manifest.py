"""Tests for pipeline_manifest.yaml integrity and orchestrator anti-drift.

Key invariant: the manifest is the single source of truth for phase execution
order. Any divergence between manifest and orchestrator must fail loudly here.
"""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

import pytest

from pipeline.config import REPO_ROOT, SCHEMA_VERSION
from pipeline.manifest import (
    find_phase_index,
    get_phase_ids,
    get_phases,
    load_manifest,
    validate_manifest,
)


# ── Fake phase callables for TestPhaseFailFast — referenced by dotted path
# ("tests.test_pipeline_manifest", "<name>") from synthetic manifest entries,
# so they must stay real, importable module-level functions.


def _ok(**kwargs):
    return "ok"


def _raise_file_not_found(**kwargs):
    raise FileNotFoundError("missing.jsonl")


def _raise_value_error(**kwargs):
    raise ValueError("boom")


def _fail_if_called(**kwargs):
    raise AssertionError("this phase must not run")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def phases(manifest):
    return get_phases(manifest)


# ── Manifest loads and is structurally valid ──


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


# ── Known phases are present ──


class TestExpectedPhases:
    # 5a sits before 4e on purpose: identity resolution clusters on `isrc`, so
    # it has to run after something resolves one. The ids are stable labels,
    # not an ordering — execution order is this list's order.
    EXPECTED_IDS = [
        "1", "2", "A", "B", "3a", "3b", "3c", "4", "4b", "4c", "4d",
        "5a", "4e", "5", "5b", "6", "7", "8",
    ]

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


# ── Anti-drift: README phase table matches manifest ──

_README_ROW_RE = re.compile(
    r"^\|\s*(\S+)\s*\|\s*(?:\[(\w+)\]\([^)]+\)|_\(manual[^)]*\)_)\s*\|",
)


def _parse_readme_phase_table() -> list[tuple[str, str | None]]:
    """Extract (phase_id, module_short_name) pairs from README's phase table.

    ``module_short_name`` is None for a manual phase's ``_(manual — owner)_``
    cell. Stops at the first blank line after the header, so a follow-on table
    elsewhere in the doc is never swept in by accident.
    """
    lines = (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, l in enumerate(lines) if l.startswith("| Phase | Module")
    )
    rows: list[tuple[str, str | None]] = []
    for line in lines[start + 2:]:  # skip header + separator row
        if not line.startswith("|"):
            break
        m = _README_ROW_RE.match(line)
        assert m, f"README phase table row doesn't parse: {line!r}"
        rows.append((m.group(1), m.group(2)))
    return rows


class TestReadmeAntiDrift:
    """The README's hand-maintained phase table must not drift from the
    manifest — the manifest is the single source of truth for phase order,
    modules, and dependencies (#44)."""

    def test_readme_lists_every_manifest_phase_in_order(self, manifest):
        readme_ids = [pid for pid, _module in _parse_readme_phase_table()]
        assert readme_ids == get_phase_ids(manifest), (
            f"README phase table has drifted from pipeline_manifest.yaml.\n"
            f"  Manifest: {get_phase_ids(manifest)}\n"
            f"  README:   {readme_ids}"
        )

    def test_readme_module_links_match_the_manifest(self, phases):
        readme_rows = dict(_parse_readme_phase_table())
        for phase in phases:
            pid = str(phase["id"])
            assert pid in readme_rows, f"Phase {pid!r} missing from README table"
            readme_module = readme_rows[pid]
            if phase.get("manual"):
                assert readme_module is None, (
                    f"Phase {pid!r} is manual in the manifest but README "
                    f"links a module ({readme_module!r})"
                )
            else:
                expected = phase["module"].rsplit(".", 1)[-1]
                assert readme_module == expected, (
                    f"Phase {pid!r}: README links {readme_module!r}, "
                    f"manifest module is {expected!r}"
                )


# ── Anti-drift: orchestrator execution order matches manifest ──


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


# ── Module importability ──


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


# ── Anti-drift: accepts_force matches the real signatures ──


class TestAcceptsForceAntiDrift:
    """``--force`` is dispatched off the manifest flag, so the flag and the
    callable's signature must never disagree — a phase flagged but not taking
    ``force`` would blow up at runtime, and an unflagged one silently ignores
    the flag."""

    def _callables(self, phases):
        for phase in phases:
            if phase.get("manual"):
                continue
            mod = importlib.import_module(phase["module"])
            yield phase, getattr(mod, phase["callable"])

    def test_flagged_phases_accept_a_force_kwarg(self, phases):
        for phase, fn in self._callables(phases):
            if not phase.get("accepts_force"):
                continue
            params = inspect.signature(fn).parameters
            assert "force" in params, (
                f"Phase {phase['id']!r} is flagged accepts_force but "
                f"{phase['module']}.{phase['callable']} takes no 'force' parameter"
            )

    def test_phases_taking_force_are_flagged(self, phases):
        for phase, fn in self._callables(phases):
            if "force" not in inspect.signature(fn).parameters:
                continue
            assert phase.get("accepts_force"), (
                f"{phase['module']}.{phase['callable']} takes 'force' but phase "
                f"{phase['id']!r} is not flagged accepts_force — --force would "
                f"silently skip it"
            )

    def test_the_api_phases_are_the_flagged_ones(self, phases):
        flagged = {str(p["id"]) for p in phases if p.get("accepts_force")}
        assert flagged == {"4", "4b", "4d", "5", "5a", "5b"}

    def test_orchestrator_passes_force_only_to_flagged_phases(self, monkeypatch):
        """run() must not hand force= to a phase whose callable cannot take it."""
        from pipeline import run_full_pipeline as rfp

        seen: dict[str, dict] = {}

        def fake_phase(phase_id, name, fn, *args, **kwargs):
            seen[phase_id] = kwargs
            return rfp.OK

        monkeypatch.setattr(rfp, "_phase", fake_phase)
        monkeypatch.setattr(rfp, "_run_pytest", lambda: True)
        rfp.run(skip_tests=True, skip_pause=True, force="errors")

        flagged = {str(p["id"]) for p in rfp._PHASES if p.get("accepts_force")}
        for phase_id, kwargs in seen.items():
            if phase_id in flagged:
                assert kwargs.get("force") == "errors", phase_id
            else:
                assert "force" not in kwargs, phase_id

    def test_default_run_passes_no_force(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        seen: dict[str, dict] = {}
        monkeypatch.setattr(
            rfp, "_phase",
            lambda pid, name, fn, *a, **kw: (seen.__setitem__(pid, kw), rfp.OK)[1],
        )
        rfp.run(skip_tests=True, skip_pause=True)
        assert all("force" not in kwargs for kwargs in seen.values())


class TestDefaultInputMatchesManifest:
    """A phase's default input must be the file the manifest says it reads.

    The manifest documents the chain and the orchestrator derives order from it,
    but nothing bound a module's own input choice to that declaration. Phase 5
    kept ``tracks_with_genre_backfill.jsonl`` (4d) at the head of its priority
    list after 4e was inserted between them, so identity resolution was computed,
    written, and then silently ignored — leaving duplicate canonical_track_ids in
    the final merge. Order in the manifest is not enough; the read path has to
    agree with it.
    """

    def _modules_with_a_default(self, phases):
        for phase in phases:
            if phase.get("manual") or not phase.get("module"):
                continue
            mod = importlib.import_module(phase["module"])
            for attr in ("DEFAULT_INPUT", "INPUT_PATH"):
                default = getattr(mod, attr, None)
                if default is not None:
                    yield phase, mod, attr, Path(default)
                    break

    def test_default_input_is_declared_in_the_manifest(self, phases):
        for phase, mod, attr, default in self._modules_with_a_default(phases):
            declared = {Path(p).name for p in phase.get("inputs") or []}
            assert default.name in declared, (
                f"Phase {phase['id']!r} ({phase['module']}) reads {default.name!r} "
                f"as {attr} but the manifest declares inputs {sorted(declared)}. "
                f"A phase reading a shallower file silently discards the work of "
                f"every phase between them."
            )

    # Phase 3c is the one legitimate exception: the legacy Exportify merge is
    # re-runnable out of order (--start-from 3c) and deliberately reads the
    # deepest intermediate present, which is *later* in the chain than its own
    # manifest position. See CLAUDE.md's pipeline chain note.
    _READS_DEEPEST_BY_DESIGN = {"3c"}

    def test_input_priority_head_is_the_manifest_input(self, phases):
        """Modules that fall back through earlier outputs must still *prefer*
        the manifest's declared input."""
        for phase in phases:
            if phase.get("manual") or not phase.get("module"):
                continue
            if str(phase["id"]) in self._READS_DEEPEST_BY_DESIGN:
                continue
            mod = importlib.import_module(phase["module"])
            priority = getattr(mod, "_INPUT_PRIORITY", None)
            if not priority:
                continue
            declared = {Path(p).name for p in phase.get("inputs") or []}
            head = Path(priority[0]).name
            assert head in declared, (
                f"Phase {phase['id']!r} ({phase['module']}) prefers {head!r} but "
                f"the manifest declares inputs {sorted(declared)}"
            )


class TestManualOptionalPhaseDoesNotBlock:
    """Phase 3b (TuneMyMusic/Exportify) is manual + optional since #37 — the
    automated 5a/5b chain supersedes it, so a missing exportify.csv must no
    longer pause the whole run (the pre-#37 behaviour)."""

    def test_missing_output_skips_without_pausing(self, monkeypatch, tmp_path):
        from pipeline import run_full_pipeline as rfp

        # Point the run at an empty tree rather than asserting the real checkout
        # has no exportify.csv. The owner's working copy legitimately does — the
        # suite is meant to be self-contained, and reading inputs/ made this pass
        # in CI and on a fresh clone while failing on the one machine that
        # actually runs the pipeline.
        monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)

        seen: list[str] = []
        monkeypatch.setattr(
            rfp, "_phase",
            lambda pid, name, fn, *a, **kw: (seen.append(pid), rfp.OK)[1],
        )
        results = rfp.run(skip_tests=True, skip_pause=False)

        assert results["3b"] == rfp.SKIPPED
        # The run must not have stopped at 3b — later phases still ran.
        assert "8" in results

    def test_non_optional_manual_phase_still_pauses(self, monkeypatch):
        """Guard the other half: a manual phase without optional: true must
        still break the run when its output is missing and skip_pause=False —
        the behaviour this test's sibling is deliberately changing for 3b
        only, not for manual phases in general."""
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "p1", "module": "pipeline.config",
             "callable": "get_logger", "outputs": [], "manual": False,
             "depends_on": []},
            {"id": "2", "name": "manual step", "module": None, "callable": None,
             "outputs": ["this/path/does/not/exist.csv"], "manual": True,
             "depends_on": ["1"]},
            {"id": "3", "name": "p3", "module": "pipeline.config",
             "callable": "get_logger", "outputs": [], "manual": False,
             "depends_on": ["2"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        seen: list[str] = []
        monkeypatch.setattr(
            rfp, "_phase",
            lambda pid, name, fn, *a, **kw: (seen.append(pid), rfp.OK)[1],
        )
        results = rfp.run(skip_tests=True, skip_pause=False)

        assert "2" not in results  # broke before recording an outcome for it
        assert "3" not in results  # and never reached the next phase


class TestPhaseFailFast:
    """F-01: a required phase's failure must not be downgraded to SKIPPED, and
    must stop the run before any downstream phase runs against a stale or
    missing intermediate — possibly all the way through to Phase 8 rewriting
    tracks.jsonl off bad input."""

    # ── _phase() itself ──

    def test_required_file_not_found_is_failed_not_skipped(self):
        from pipeline import run_full_pipeline as rfp

        status = rfp._phase("x", "test", _raise_file_not_found, optional=False)
        assert status == rfp.FAILED

    def test_optional_file_not_found_is_skipped(self):
        from pipeline import run_full_pipeline as rfp

        status = rfp._phase("x", "test", _raise_file_not_found, optional=True)
        assert status == rfp.SKIPPED

    def test_required_exception_other_than_missing_file_is_failed(self):
        from pipeline import run_full_pipeline as rfp

        status = rfp._phase("x", "test", _raise_value_error, optional=False)
        assert status == rfp.FAILED

    def test_phase_producing_its_declared_output_is_ok(self, tmp_path):
        from pipeline import run_full_pipeline as rfp

        out = tmp_path / "out.jsonl"
        out.write_text("{}\n", encoding="utf-8")
        status = rfp._phase("x", "test", _ok, optional=False, outputs=[str(out)])
        assert status == rfp.OK

    def test_phase_missing_its_declared_output_is_failed(self, tmp_path):
        """A phase that returns normally without producing what the manifest
        says it outputs must not be trusted as OK."""
        from pipeline import run_full_pipeline as rfp

        status = rfp._phase(
            "x", "test", _ok, optional=False,
            outputs=[str(tmp_path / "never_written.jsonl")],
        )
        assert status == rfp.FAILED

    # ── rfp.run() — required failures stop the whole run ──

    def test_required_generic_exception_stops_the_run(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "boom", "module": "tests.test_pipeline_manifest",
             "callable": "_raise_value_error", "outputs": [], "depends_on": []},
            {"id": "2", "name": "must not run", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "2" not in results

    def test_required_missing_file_stops_the_run(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "boom", "module": "tests.test_pipeline_manifest",
             "callable": "_raise_file_not_found", "outputs": [], "depends_on": []},
            {"id": "2", "name": "must not run", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "2" not in results

    def test_optional_missing_file_does_not_stop_the_run(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "skip", "module": "tests.test_pipeline_manifest",
             "callable": "_raise_file_not_found", "outputs": [], "depends_on": [],
             "optional": True},
            {"id": "2", "name": "still runs", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.SKIPPED
        assert results["2"] == rfp.OK

    def test_required_import_failure_stops_the_run(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "bad import", "module": "pipeline.does_not_exist_xyz",
             "callable": "whatever", "outputs": [], "depends_on": []},
            {"id": "2", "name": "must not run", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "2" not in results

    def test_optional_import_failure_does_not_stop_the_run(self, monkeypatch):
        from pipeline import run_full_pipeline as rfp

        fake_phases = [
            {"id": "1", "name": "bad import", "module": "pipeline.does_not_exist_xyz",
             "callable": "whatever", "outputs": [], "depends_on": [], "optional": True},
            {"id": "2", "name": "still runs", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.SKIPPED
        assert results["2"] == rfp.OK

    def test_required_requires_file_gate_missing_fails_and_stops(self, monkeypatch, tmp_path):
        from pipeline import run_full_pipeline as rfp

        monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)
        fake_phases = [
            {"id": "1", "name": "gated", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": [], "depends_on": [],
             "requires_file": "no/such/file.csv"},
            {"id": "2", "name": "must not run", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "2" not in results

    def test_optional_requires_file_gate_missing_is_an_allowed_skip(self, monkeypatch, tmp_path):
        """Mirrors the real Phase 3c: an optional phase's requires_file gate is
        a deliberate, benign no-op, not a failure."""
        from pipeline import run_full_pipeline as rfp

        monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)
        fake_phases = [
            {"id": "1", "name": "gated", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": [], "depends_on": [],
             "requires_file": "no/such/file.csv", "optional": True},
            {"id": "2", "name": "still runs", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.SKIPPED
        assert results["2"] == rfp.OK

    def test_required_phase_missing_declared_output_stops_the_run(self, monkeypatch, tmp_path):
        from pipeline import run_full_pipeline as rfp

        monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)
        fake_phases = [
            {"id": "1", "name": "silent no-op", "module": "tests.test_pipeline_manifest",
             "callable": "_ok", "outputs": ["never_written.jsonl"], "depends_on": []},
            {"id": "2", "name": "must not run", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": [], "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "2" not in results

    def test_pre_existing_tracks_jsonl_survives_a_failure_before_phase_8(
        self, monkeypatch, tmp_path
    ):
        """End-to-end version of the F-01/F-04 story: a required phase fails
        partway through a run, and the canonical tracks.jsonl already on disk
        from a prior run is left byte-for-byte untouched — Phase 8 never runs
        to overwrite it."""
        from pipeline import run_full_pipeline as rfp
        from pipeline.schema import write_jsonl

        monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)
        tracks_path = tmp_path / "tracks.jsonl"
        write_jsonl([{"artist": "Existing", "track": "Track"}], tracks_path)
        original = tracks_path.read_bytes()

        fake_phases = [
            {"id": "1", "name": "boom", "module": "tests.test_pipeline_manifest",
             "callable": "_raise_value_error", "outputs": [], "depends_on": []},
            {"id": "8", "name": "final merge", "module": "tests.test_pipeline_manifest",
             "callable": "_fail_if_called", "outputs": ["tracks.jsonl"],
             "depends_on": ["1"]},
        ]
        monkeypatch.setattr(rfp, "_PHASES", fake_phases)
        results = rfp.run(skip_tests=True, skip_pause=True)

        assert results["1"] == rfp.FAILED
        assert "8" not in results
        assert tracks_path.read_bytes() == original


class TestForceCliParsing:
    def test_force_maps_to_all(self):
        from pipeline.run_full_pipeline import _parse_args
        assert _parse_args(["--force"]).force == "all"

    def test_force_errors_maps_to_errors(self):
        from pipeline.run_full_pipeline import _parse_args
        assert _parse_args(["--force-errors"]).force == "errors"

    def test_default_is_off(self):
        from pipeline.run_full_pipeline import _parse_args
        assert _parse_args([]).force == "off"

    def test_force_and_force_errors_are_mutually_exclusive(self):
        from pipeline.run_full_pipeline import _parse_args
        with pytest.raises(SystemExit):
            _parse_args(["--force", "--force-errors"])


# ── find_phase_index helper ──


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
