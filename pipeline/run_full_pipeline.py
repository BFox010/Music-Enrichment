"""Phase 9 — full-pipeline orchestrator.

Execution order is derived from pipeline_manifest.yaml — never hardcoded here.
Adding a phase requires a manifest entry; the anti-drift test in
tests/test_pipeline_manifest.py will catch any divergence.

Usage:
    python -m pipeline.run_full_pipeline
    python -m pipeline.run_full_pipeline --skip-tests
    python -m pipeline.run_full_pipeline --skip-pause
    python -m pipeline.run_full_pipeline --start-from 4
    python -m pipeline.run_full_pipeline --start-from 3c
    python -m pipeline.run_full_pipeline --force-errors
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline._http import FORCE_ALL, FORCE_ERRORS, FORCE_OFF
from pipeline.config import (
    INPUT_LASTFM_EXPORT,
    REPO_ROOT,
    RUNS_DIR,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.manifest import (
    find_phase_index,
    get_phases,
    load_manifest,
)

log = get_logger(__name__)

# Loaded once at import time so tests can inspect it without re-parsing.
_MANIFEST = load_manifest()
_PHASES = get_phases(_MANIFEST)


def get_execution_order() -> list[str]:
    """Return phase IDs in manifest execution order. Used by anti-drift tests."""
    return [str(p["id"]) for p in _PHASES]


def _run_pytest() -> bool:
    log.info("Running pytest before pipeline …")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    log.info("pytest stdout: %s", result.stdout.splitlines()[-3:] if result.stdout else "")
    if result.returncode != 0:
        log.error("pytest FAILED — aborting pipeline")
        log.error(result.stdout)
        log.error(result.stderr)
        return False
    log.info("pytest OK")
    return True


# Phase outcome states. "skipped" is benign (missing optional input / gated
# phase); only "failed" signals a genuine error worth aborting a refresh over.
OK = "ok"
SKIPPED = "skipped"
FAILED = "failed"


def _phase(phase_id: str, name: str, fn, *, optional: bool, outputs: list[str] = (), **kwargs) -> str:
    """Run a phase function; log the outcome. Returns OK / SKIPPED / FAILED.

    A missing input is only a benign SKIPPED for a phase the manifest marks
    ``optional`` — for a required phase it means the run can't safely proceed and
    is reported as FAILED instead. After a normal return, verify the phase's
    declared outputs actually landed; a phase that returns without producing them
    is also FAILED, not OK.
    """
    log.info("=" * 60)
    log.info("Phase %s: %s", phase_id, name)
    log.info("=" * 60)
    try:
        result = fn(**kwargs)
    except FileNotFoundError as e:
        if optional:
            log.warning("Phase %s SKIPPED — missing input: %s", phase_id, e)
            return SKIPPED
        log.error("Phase %s FAILED — required input missing: %s", phase_id, e, exc_info=True)
        return FAILED
    except Exception as e:
        log.error("Phase %s FAILED: %s", phase_id, e, exc_info=True)
        return FAILED

    missing = [f for f in outputs if not (REPO_ROOT / f).exists()]
    if missing:
        log.error(
            "Phase %s FAILED — returned normally but declared output(s) missing: %s",
            phase_id, missing,
        )
        return FAILED

    log.info("Phase %s OK: %s", phase_id, result)
    return OK


def failed_phases(results: dict[str, str]) -> list[str]:
    """Phase IDs that genuinely failed, excluding benign SKIPPED — an expected
    no-op like Phase 3c with no Exportify CSV. Only these count as errors.
    """
    return [pid for pid, status in results.items() if status == FAILED]


def _resolve_start_index(start_from: str) -> int:
    """--start-from value → 0-based manifest index. Accepts a phase ID ("3c", "A",
    "4"); an integer N tries phase ID str(N) first, then positional index N-1.
    """
    for i, phase in enumerate(_PHASES):
        if str(phase["id"]) == start_from:
            return i
    try:
        n = int(start_from)
        idx = n - 1
        if 0 <= idx < len(_PHASES):
            log.warning(
                "--start-from %d interpreted as positional index %d "
                "(phase %r). Prefer using phase IDs directly.",
                n, idx, _PHASES[idx]["id"],
            )
            return idx
    except ValueError:
        pass
    valid = [str(p["id"]) for p in _PHASES]
    raise SystemExit(
        f"--start-from {start_from!r} is not a valid phase ID or index. "
        f"Valid IDs: {valid}"
    )


def run(
    *,
    skip_tests: bool = False,
    skip_pause: bool = False,
    start_from: str = "1",
    force: str = FORCE_OFF,
    run_log_path: Path | None = None,
) -> dict[str, str]:
    """Run phases in manifest order; returns ``{phase_id: OK|SKIPPED|FAILED}``.
    Use ``failed_phases()`` — SKIPPED is an expected no-op, not a failure.

    ``force`` reaches only phases the manifest flags ``accepts_force``.
    """
    if run_log_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        run_log_path = RUNS_DIR / f"full_run_{ts}.log"
    configure_logging(run_log_path)
    log.info("Pipeline run started — log: %s", run_log_path)
    log.info("Execution order: %s", get_execution_order())
    if force != FORCE_OFF:
        log.warning("Cache force mode: %s — API phases will re-fetch", force)

    results: dict[str, str] = {}

    start_idx = _resolve_start_index(start_from)

    if not skip_tests and start_idx == 0:
        results["pytest"] = OK if _run_pytest() else FAILED
        if results["pytest"] == FAILED:
            return results
    else:
        results["pytest"] = OK

    for i, phase_def in enumerate(_PHASES):
        phase_id = str(phase_def["id"])

        if i < start_idx:
            results[phase_id] = OK  # assumed done
            continue

        optional = phase_def.get("optional", False)

        # ── Manual phase ──
        if phase_def.get("manual"):
            outputs = phase_def.get("outputs", [])
            output_exists = any((REPO_ROOT / f).exists() for f in outputs)
            if output_exists:
                results[phase_id] = OK
                continue
            if optional:
                log.info(
                    "Phase %s SKIPPED — manual+optional, no output present.", phase_id
                )
                results[phase_id] = SKIPPED
                continue
            if not skip_pause:
                log.info("PAUSE: Phase %s is manual.", phase_id)
                instructions = phase_def.get("instructions", "").strip()
                if instructions:
                    for line in instructions.splitlines():
                        log.info("  %s", line)
                log.info("Stopping pipeline — re-run with --start-from %s once complete.", phase_id)
                break
            # Required manual step, no output, and we've been told not to pause —
            # there is nothing to run and no operator to wait for. Fail rather
            # than silently marking a required phase SKIPPED and letting later
            # phases consume whatever stale intermediate is lying around.
            log.error(
                "Phase %s FAILED — manual step required but output missing, "
                "and --skip-pause was set.", phase_id,
            )
            results[phase_id] = FAILED
            log.error("Required phase %s failed — stopping pipeline.", phase_id)
            break

        # ── Conditional: required file gate ──
        req_file = phase_def.get("requires_file")
        if req_file and not (REPO_ROOT / req_file).exists():
            if optional:
                log.warning(
                    "Phase %s SKIPPED — required file missing: %s",
                    phase_id, req_file,
                )
                results[phase_id] = SKIPPED
                continue
            log.error("Phase %s FAILED — required file missing: %s", phase_id, req_file)
            results[phase_id] = FAILED
            log.error("Required phase %s failed — stopping pipeline.", phase_id)
            break

        # ── Dynamic import + execute ──
        module_path = phase_def.get("module")
        callable_name = phase_def.get("callable")

        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, callable_name)
        except (ImportError, AttributeError) as e:
            if optional:
                log.info("Phase %s SKIPPED — not yet implemented: %s", phase_id, e)
                results[phase_id] = SKIPPED
                continue
            log.error("Phase %s FAILED to import %s.%s: %s", phase_id, module_path, callable_name, e)
            results[phase_id] = FAILED
            log.error("Required phase %s failed — stopping pipeline.", phase_id)
            break

        kwargs = {}
        if force != FORCE_OFF and phase_def.get("accepts_force"):
            kwargs["force"] = force
        status = _phase(
            phase_id, phase_def["name"], fn,
            optional=optional, outputs=phase_def.get("outputs", []), **kwargs,
        )
        results[phase_id] = status
        if status == FAILED and not optional:
            log.error("Required phase %s failed — stopping pipeline.", phase_id)
            break

    # ── Summary ──
    log.info("=" * 60)
    log.info("Pipeline summary:")
    for phase_id, status in results.items():
        log.info("  %-7s phase %s", status, phase_id)
    log.info("Output: %s", TRACKS_PATH)
    log.info("Log   : %s", run_log_path)
    log.info("=" * 60)
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the full music-enrichment pipeline.")
    p.add_argument("--skip-tests", action="store_true",
                   help="Skip pytest run before the pipeline.")
    p.add_argument("--skip-pause", action="store_true",
                   help="Don't pause when manual steps are pending.")
    p.add_argument("--start-from", default="1",
                   help="Phase ID to start from (e.g. '1', '3c', 'A'). Default: 1.")
    force_group = p.add_mutually_exclusive_group()
    force_group.add_argument(
        "--force", dest="force", action="store_const", const=FORCE_ALL,
        default=FORCE_OFF,
        help="Bypass the HTTP cache entirely on API phases and re-fetch "
             "everything. Slow — see the per-phase ETA warning.",
    )
    force_group.add_argument(
        "--force-errors", dest="force", action="store_const", const=FORCE_ERRORS,
        help="Re-fetch only cached failures on API phases, ignoring their TTL. "
             "The cheap way to clear poisoned cache entries.",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    # argparse always hands back a string, so the guard is a plain equality;
    # it used to be `in ("1", "1")`, a two-slot tuple holding one value.
    if args.start_from == "1" and not INPUT_LASTFM_EXPORT.exists():
        log.error("Last.fm export not found at %s — cannot start from Phase 1.",
                  INPUT_LASTFM_EXPORT)
        sys.exit(1)
    results = run(
        skip_tests=args.skip_tests,
        skip_pause=args.skip_pause,
        start_from=args.start_from,
        force=args.force,
    )
    failed = failed_phases(results)
    if failed:
        log.error("Pipeline finished with failed phases: %s", ", ".join(failed))
    sys.exit(1 if failed else 0)
