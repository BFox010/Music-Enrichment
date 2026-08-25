"""Full-chain refresh: sync scrobbles → run pipeline → export pending → reload cache.

  1. lastfm_sync.sync — fetch and append new scrobbles
  2. run the pipeline from Phase 2 (tests + manual pauses skipped)
  3. export feature-less tracks to inputs/pending_exportify.csv
  4. data.reload — refresh the in-memory cache

SKIPPED phases are fine; only FAILED aborts. Phase 3c auto-runs once
inputs/exportify.csv is present.
"""

from __future__ import annotations

import asyncio

import app.data as data
from app import lastfm_sync
from pipeline.config import SCROBBLES_PATH
from pipeline import export_tunemymusic
from pipeline.run_full_pipeline import failed_phases, run as _pipeline_run


class RefreshInProgress(RuntimeError):
    """Raised when a refresh is requested while another is still running."""


# Single-flight guard. A refresh rewrites scrobbles, every intermediate,
# tracks.jsonl and pending_exportify.csv — overlapping runs (duplicate clicks,
# multiple tabs) would interleave those writes, so a second caller fails fast.
_refresh_lock = asyncio.Lock()


async def refresh() -> dict:
    """Run the full refresh chain; returns combined stats.

    ``RefreshInProgress`` if one is already running. ``RuntimeError`` if a phase
    genuinely fails — the cache is left untouched and nothing is exported, so a
    broken run can never masquerade as success.
    """
    if _refresh_lock.locked():
        raise RefreshInProgress("a refresh is already running")

    async with _refresh_lock:
        sync_stats = await lastfm_sync.sync(SCROBBLES_PATH)

        pipeline_results = await asyncio.to_thread(
            _pipeline_run,
            start_from="2",
            skip_tests=True,
            skip_pause=True,
        )

        failed = failed_phases(pipeline_results)
        if failed:
            # Never export or reload off a broken run.
            raise RuntimeError("pipeline phases failed: " + ", ".join(failed))

        pending_count = export_tunemymusic.export_pending()
        cache_stats = data.reload()

    return {
        "sync": sync_stats,
        "pipeline": pipeline_results,
        "pending_exportify": pending_count,
        "cache": cache_stats,
    }


def refresh_sync() -> dict:
    """Synchronous wrapper for CLI use: python -m app.refresh"""
    return asyncio.run(refresh())


if __name__ == "__main__":
    import json
    result = refresh_sync()
    print(json.dumps(result, indent=2, default=str))
