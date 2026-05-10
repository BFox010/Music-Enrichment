"""Phase 9 — full-pipeline orchestrator.

Chains the entire pipeline end-to-end. Runs pytest first; aborts on failure.
Each phase is invoked through its module, sharing one timestamped log file.
Manual phases (3b: TuneMyMusic + Exportify) pause until their input is provided
or skip if --skip-pause is set.

Usage:
    python -m pipeline.run_full_pipeline
    python -m pipeline.run_full_pipeline --skip-tests
    python -m pipeline.run_full_pipeline --skip-pause
    python -m pipeline.run_full_pipeline --start-from 4
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import (
    INPUT_EXPORTIFY_CSV,
    INPUT_LASTFM_EXPORT,
    REPO_ROOT,
    RUNS_DIR,
    TASTE_PROFILE_PATH,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)


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


def _phase(num: str, name: str, fn, *args, **kwargs) -> bool:
    """Run a phase function; log success/failure. Returns True if it ran cleanly."""
    log.info("=" * 60)
    log.info("Phase %s: %s", num, name)
    log.info("=" * 60)
    try:
        result = fn(*args, **kwargs)
        log.info("Phase %s OK: %s", num, result)
        return True
    except FileNotFoundError as e:
        log.warning("Phase %s SKIPPED — missing input: %s", num, e)
        return False
    except Exception as e:
        log.error("Phase %s FAILED: %s", num, e, exc_info=True)
        return False


def run(
    *,
    skip_tests: bool = False,
    skip_pause: bool = False,
    start_from: int = 1,
    run_log_path: Path | None = None,
) -> dict[str, bool]:
    """Run all pipeline phases in order; return dict of phase → success."""
    if run_log_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        run_log_path = RUNS_DIR / f"full_run_{ts}.log"
    configure_logging(run_log_path)
    log.info("Pipeline run started — log: %s", run_log_path)

    if not skip_tests and start_from <= 1:
        if not _run_pytest():
            return {"pytest": False}

    results: dict[str, bool] = {"pytest": True}

    # Phase 1 — scrobble ingest
    if start_from <= 1:
        from pipeline.ingest_scrobbles import ingest
        results["1"] = _phase("1", "scrobble ingest", ingest)
    else:
        results["1"] = True

    # Phase 2 — dedupe
    if start_from <= 2:
        from pipeline.dedupe import dedupe
        results["2"] = _phase("2", "dedupe", dedupe)
    else:
        results["2"] = True

    # Phase A — iTunes XML enrichment (between skeleton and audio)
    if start_from <= 2:
        from pipeline.enrich_apple_library import enrich as enrich_apple
        results["A"] = _phase("A", "iTunes XML enrichment", enrich_apple)
    else:
        results["A"] = True

    # Phase 3a — TuneMyMusic export
    if start_from <= 3:
        from pipeline.export_tunemymusic import export
        results["3a"] = _phase("3a", "TuneMyMusic CSV export", export)

    # Phase 3b — manual step (pause)
    if not INPUT_EXPORTIFY_CSV.exists() and not skip_pause and start_from <= 3:
        log.info(
            "PAUSE: Phase 3b is manual.\n"
            "  1. Upload inputs/tunemymusic_upload.csv to TuneMyMusic\n"
            "  2. Create a Spotify playlist from it\n"
            "  3. Run Exportify (https://exportify.net) on that playlist\n"
            "  4. Save the result as inputs/exportify.csv\n"
            "  5. Re-run with --start-from 3"
        )
        log.info("Skipping the rest of Phase 3 — Exportify CSV not present.")

    # Phase 3c — Exportify merge
    if start_from <= 3 and INPUT_EXPORTIFY_CSV.exists():
        from pipeline.merge_exportify import merge as merge_exportify
        results["3c"] = _phase("3c", "Exportify merge", merge_exportify)
    elif start_from <= 3:
        log.warning("Phase 3c skipped — no Exportify CSV at %s", INPUT_EXPORTIFY_CSV)
        results["3c"] = False

    # Phase 4 — Last.fm metadata
    if start_from <= 4:
        from pipeline.enrich_metadata import enrich as enrich_metadata
        results["4"] = _phase("4", "Last.fm + MusicBrainz", enrich_metadata)

    # Phase 5 — Apple Music availability
    if start_from <= 5:
        from pipeline.check_apple_music import check
        results["5"] = _phase("5", "Apple Music availability", check)

    # Phase 6 — mood classification (skipped if not implemented yet)
    if start_from <= 6:
        try:
            from pipeline.classify_moods import classify
        except ImportError:
            log.info("Phase 6 SKIPPED — classify_moods not yet implemented")
            results["6"] = False
        else:
            results["6"] = _phase("6", "mood classification", classify)

    # Phase 7 — saturation/curation from taste_profile.md
    if start_from <= 7:
        if not TASTE_PROFILE_PATH.exists():
            log.info("Phase 7 SKIPPED — %s not present", TASTE_PROFILE_PATH)
            results["7"] = False
        else:
            try:
                from pipeline.apply_taste_profile import apply
            except ImportError:
                log.info("Phase 7 SKIPPED — apply_taste_profile not yet implemented")
                results["7"] = False
            else:
                results["7"] = _phase("7", "saturation + curation", apply)

    # Phase 8 — final merge
    if start_from <= 8:
        from pipeline.update_tracks import update
        results["8"] = _phase("8", "final merge → tracks.jsonl", update)

    # Summary
    log.info("=" * 60)
    log.info("Pipeline summary:")
    for phase, ok in results.items():
        symbol = "OK " if ok else "-- "
        log.info("  %s phase %s", symbol, phase)
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
    p.add_argument("--start-from", type=int, default=1,
                   help="Phase number to start from (default: 1).")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    if not INPUT_LASTFM_EXPORT.exists() and args.start_from <= 1:
        log.error("Last.fm export not found at %s — cannot start from Phase 1.",
                  INPUT_LASTFM_EXPORT)
        sys.exit(1)
    results = run(
        skip_tests=args.skip_tests,
        skip_pause=args.skip_pause,
        start_from=args.start_from,
    )
    failed = [p for p, ok in results.items() if not ok and p != "pytest"]
    sys.exit(1 if any(not ok for p, ok in results.items() if p == "pytest") else 0)
