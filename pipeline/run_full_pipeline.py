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
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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


def _phase(phase_id: str, name: str, fn, *args, **kwargs) -> bool:
    """Run a phase function; log success/failure. Returns True on success."""
    log.info("=" * 60)
    log.info("Phase %s: %s", phase_id, name)
    log.info("=" * 60)
    try:
        result = fn(*args, **kwargs)
        log.info("Phase %s OK: %s", phase_id, result)
        return True
    except FileNotFoundError as e:
        log.warning("Phase %s SKIPPED — missing input: %s", phase_id, e)
        return False
    except Exception as e:
        log.error("Phase %s FAILED: %s", phase_id, e, exc_info=True)
        return False


def _resolve_start_index(start_from: str) -> int:
    """Convert --start-from value to a 0-based manifest index.

    Accepts a phase ID ("3c", "A", "4") or an integer string ("4").
    Integer N maps directly to phase ID str(N) first; if not found,
    falls back to positional index N-1 for backwards compat.
    """
    # Try as a direct phase ID first
    for i, phase in enumerate(_PHASES):
        if str(phase["id"]) == start_from:
            return i
    # Numeric fallback: --start-from 4 → 0-based index 3
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
    run_log_path: Path | None = None,
) -> dict[str, bool]:
    """Run pipeline phases in manifest order; return dict of phase_id → success."""
    if run_log_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        run_log_path = RUNS_DIR / f"full_run_{ts}.log"
    configure_logging(run_log_path)
    log.info("Pipeline run started — log: %s", run_log_path)
    log.info("Execution order: %s", get_execution_order())

    results: dict[str, bool] = {}

    start_idx = _resolve_start_index(start_from)

    if not skip_tests and start_idx == 0:
        results["pytest"] = _run_pytest()
        if not results["pytest"]:
            return results
    else:
        results["pytest"] = True

    for i, phase_def in enumerate(_PHASES):
        phase_id = str(phase_def["id"])

        if i < start_idx:
            results[phase_id] = True  # assumed done
            continue

        # ── Manual phase ────────────────────────────────────────────────
        if phase_def.get("manual"):
            outputs = phase_def.get("outputs", [])
            output_exists = any((REPO_ROOT / f).exists() for f in outputs)
            if not output_exists and not skip_pause:
                log.info("PAUSE: Phase %s is manual.", phase_id)
                instructions = phase_def.get("instructions", "").strip()
                if instructions:
                    for line in instructions.splitlines():
                        log.info("  %s", line)
                log.info("Stopping pipeline — re-run with --start-from %s once complete.", phase_id)
                break
            results[phase_id] = output_exists or skip_pause
            continue

        # ── Conditional: required file gate ─────────────────────────────
        req_file = phase_def.get("requires_file")
        if req_file and not (REPO_ROOT / req_file).exists():
            log.warning(
                "Phase %s SKIPPED — required file missing: %s",
                phase_id, req_file,
            )
            results[phase_id] = False
            continue

        # ── Dynamic import + execute ─────────────────────────────────────
        module_path = phase_def.get("module")
        callable_name = phase_def.get("callable")
        optional = phase_def.get("optional", False)

        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, callable_name)
        except (ImportError, AttributeError) as e:
            if optional:
                log.info("Phase %s SKIPPED — not yet implemented: %s", phase_id, e)
                results[phase_id] = False
            else:
                log.error("Phase %s FAILED to import %s.%s: %s", phase_id, module_path, callable_name, e)
                results[phase_id] = False
            continue

        results[phase_id] = _phase(phase_id, phase_def["name"], fn)

    # ── Summary ──────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Pipeline summary:")
    for phase_id, ok in results.items():
        log.info("  %s  phase %s", "OK " if ok else "-- ", phase_id)
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
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    if args.start_from in ("1", "1") and not INPUT_LASTFM_EXPORT.exists():
        log.error("Last.fm export not found at %s — cannot start from Phase 1.",
                  INPUT_LASTFM_EXPORT)
        sys.exit(1)
    results = run(
        skip_tests=args.skip_tests,
        skip_pause=args.skip_pause,
        start_from=args.start_from,
    )
    sys.exit(1 if any(not ok for p, ok in results.items() if p == "pytest") else 0)
