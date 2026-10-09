"""Full-chain refresh: sync scrobbles → run pipeline → export pending → reload cache.

  1. lastfm_sync.sync — fetch and append new scrobbles
  2. run the pipeline from Phase 2 (tests + manual pauses skipped)
  3. export feature-less tracks to inputs/pending_exportify.csv
  4. data.reload — refresh the in-memory cache

SKIPPED phases are fine; only FAILED aborts the export (the cache is still
reloaded, so the server matches what the sync and Phase 8 wrote). Phase 3c
auto-runs once inputs/exportify.csv is present.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator

import app.data as data
from app import lastfm_sync
from pipeline.config import SCROBBLES_PATH
from pipeline import export_tunemymusic
from pipeline.run_full_pipeline import failed_phases, run as _pipeline_run


class RefreshInProgress(RuntimeError):
    """Raised when a mutating operation (refresh, direct Last.fm sync, or a
    manual cache reload) is requested while another one of these three is
    already running."""


# Single-flight guard shared by refresh, direct sync, and reload — not just
# refresh. A full refresh rewrites scrobbles, every intermediate, tracks.jsonl
# and pending_exportify.csv; a direct sync appends to scrobbles.jsonl and
# reloads the cache; a reload re-reads both canonical files. Any two of these
# racing (duplicate clicks, multiple tabs, a sync firing mid-refresh) can
# interleave writes or hand a reader a mix of old and new state, so they all
# go through the one lock below rather than each having their own.
_refresh_lock = asyncio.Lock()
# What holds the lock, for the 409: naming the *requested* operation ("a sync
# is already running" while a refresh was) sent the user looking for the wrong
# thing (#118).
_running_op: str | None = None


@contextlib.asynccontextmanager
async def exclusive_mutation(op_name: str) -> AsyncIterator[None]:
    """Fail fast with ``RefreshInProgress`` if another guarded operation holds
    the lock, instead of queuing behind it — a queued sync silently running
    minutes after the click that requested it would be more confusing than an
    immediate "try again" response."""
    global _running_op
    if _refresh_lock.locked():
        raise RefreshInProgress(
            f"a {_running_op or 'mutation'} is already running; try the {op_name} again when it finishes"
        )
    async with _refresh_lock:
        _running_op = op_name
        try:
            yield
        finally:
            _running_op = None


async def run_to_completion(fn, /, *args, **kwargs):
    """``asyncio.to_thread`` that keeps its caller — and so the lock — waiting
    until the thread has actually returned.

    A thread can't be cancelled. Cancelling a bare ``await to_thread(...)``
    unwound the ``async with`` and released the lock while the pipeline thread
    kept rewriting files, so a second mutation could start on top of it (#118).
    The cancellation still propagates, just not before the thread is done.
    """
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        while not fut.done():
            try:
                await asyncio.shield(fut)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        raise


async def refresh() -> dict:
    """Run the full refresh chain; returns combined stats.

    ``RefreshInProgress`` if a refresh, sync, or reload is already running.
    ``RuntimeError`` if a phase genuinely fails — nothing is exported, so a
    broken run can never masquerade as success.
    """
    async with exclusive_mutation("refresh"):
        sync_stats = await lastfm_sync.sync(SCROBBLES_PATH)

        pipeline_results = await run_to_completion(
            _pipeline_run,
            start_from="2",
            skip_tests=True,
            skip_pause=True,
        )

        failed = failed_phases(pipeline_results)
        if failed:
            # The sync has already appended to scrobbles.jsonl, and Phase 8 may
            # have rewritten tracks.jsonl before a later check failed. Skipping
            # the reload left /api/* on the old snapshot while /tracks.jsonl
            # served the new file (#118). Reload so the server matches disk;
            # only the export is withheld from a broken run.
            await run_to_completion(data.reload)
            raise RuntimeError(
                "pipeline phases failed: " + ", ".join(failed)
                + f" (the sync still added {sync_stats.get('new', 0)} scrobbles)"
            )

        # Both re-read multi-MB files from disk. refresh() is a coroutine, so
        # calling them directly ran that I/O on the event loop and stalled every
        # concurrent dashboard request for its duration — the same problem
        # api_reload already solved with to_thread.
        pending_count = await run_to_completion(export_tunemymusic.export_pending)
        cache_stats = await run_to_completion(data.reload)

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
