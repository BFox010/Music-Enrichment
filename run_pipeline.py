"""Standalone overnight runner — execute outside of Claude Code.

Runs all completed pipeline phases in order with a shared log file.
Safe to re-run: each phase overwrites its output from scratch.

Usage (from the repo root, with venv active):
    .venv/Scripts/python run_pipeline.py

Or without activating venv:
    .venv/Scripts/python.exe run_pipeline.py
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.config import RUNS_DIR, configure_logging, get_logger


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    log_path = RUNS_DIR / f"full_run_{ts}.log"
    configure_logging(log_path)
    log = get_logger("run_pipeline")

    log.info("=" * 60)
    log.info("Full pipeline run started")
    log.info("Log: %s", log_path)
    log.info("=" * 60)

    phases: list[tuple[str, object]] = []

    # ── Phase 1: scrobble ingest ─────────────────────────────────────────
    try:
        from pipeline.ingest_scrobbles import ingest
        phases.append(("1: scrobble ingest", ingest))
    except ImportError as e:
        log.error("Could not import Phase 1: %s", e)

    # ── Phase 2: dedupe ──────────────────────────────────────────────────
    try:
        from pipeline.dedupe import dedupe
        phases.append(("2: dedupe", dedupe))
    except ImportError as e:
        log.error("Could not import Phase 2: %s", e)

    # ── Run phases ───────────────────────────────────────────────────────
    results: dict[str, str] = {}
    for name, fn in phases:
        log.info("-" * 40)
        log.info("Starting phase %s", name)
        try:
            n = fn(run_log_path=log_path)  # type: ignore[call-arg]
            msg = f"OK ({n} rows)"
            log.info("Phase %s complete: %s", name, msg)
            results[name] = msg
        except Exception:
            tb = traceback.format_exc()
            log.error("Phase %s FAILED:\n%s", name, tb)
            results[name] = "FAILED"

    # ── Summary ──────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Pipeline summary:")
    all_ok = True
    for name, status in results.items():
        symbol = "✓" if not status.startswith("FAILED") else "✗"
        log.info("  %s  Phase %s — %s", symbol, name, status)
        if status.startswith("FAILED"):
            all_ok = False

    log.info("Log written to: %s", log_path)
    log.info("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
