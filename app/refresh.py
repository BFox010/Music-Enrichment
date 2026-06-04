"""Full-chain refresh: sync scrobbles → run pipeline → export pending → reload cache.

The refresh sequence is:
  1. Fetch new Last.fm scrobbles and append them (lastfm_sync.sync)
  2. Re-run the pipeline from Phase 2 onward (skip tests + manual pauses)
  3. Export tracks missing audio features to inputs/pending_exportify.csv
  4. Reload the in-memory data cache (data.reload)

Skipped phases are fine — the pipeline's FileNotFoundError guard handles
missing intermediates gracefully. Phase 3c (Exportify import) auto-runs
when the user drops inputs/exportify.csv into place.
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


# Process-level single-flight guard. A refresh appends scrobbles, rewrites
# intermediate JSONL files, tracks.jsonl and pending_exportify.csv, then reloads
# the cache — overlapping runs (duplicate clicks, multiple tabs) would interleave
# those writes, so a concurrent caller fails fast instead of racing.
_refresh_lock = asyncio.Lock()


async def refresh() -> dict:
    """Run the full refresh chain and return combined stats.

    Raises ``RefreshInProgress`` if another refresh is already running, and
    ``RuntimeError`` if any pipeline phase genuinely fails — in which case the
    cache is left untouched and nothing is exported, so a broken run can never
    masquerade as success.
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
            # Abort before exporting/reloading off a broken pipeline run.
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
