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
from pipeline.run_full_pipeline import run as _pipeline_run


async def refresh() -> dict:
    """Run the full refresh chain and return combined stats."""
    sync_stats = await lastfm_sync.sync(SCROBBLES_PATH)

    pipeline_results = await asyncio.to_thread(
        _pipeline_run,
        start_from="2",
        skip_tests=True,
        skip_pause=True,
    )

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
