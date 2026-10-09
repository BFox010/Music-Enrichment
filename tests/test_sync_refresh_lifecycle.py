"""Sync and refresh lifecycle: #105 (Last.fm sync gaps) and #118 (refresh
reporting, credentials, cancellation). No network: Last.fm is faked at
``fetch_recent_scrobbles`` or at the httpx client."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.data as data
import app.lastfm_sync as ls
import app.refresh as refresh_mod
from pipeline.config import MissingCredentialsError
from pipeline.run_full_pipeline import FAILED, SKIPPED, _phase


def _uts(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


def _raw(stamp: str, track: str = "Roads") -> dict:
    return {
        "artist": {"#text": "Portishead", "mbid": ""},
        "name": track,
        "album": {"#text": "Dummy", "mbid": ""},
        "date": {"uts": str(_uts(stamp)), "#text": ""},
    }


def _stored(stamp: str, track: str = "Roads") -> dict:
    return {"artist": "Portishead", "track": track, "album": "Dummy",
            "artist_normalized": "portishead", "track_normalized": track.lower(),
            "scrobbled_at": stamp}


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


LASTFM_ENV = {"LASTFM_USERNAME": "u", "LASTFM_API_KEY": "k"}


# ── #105 ──


class TestLatePlaysAreFetched:
    """A play submitted late keeps its original timestamp, so a fetch strictly
    after the newest stored play could never return it."""

    def test_fetch_window_reaches_back_past_the_newest_stored_play(self):
        page = {"recenttracks": {"track": [], "@attr": {"totalPages": "1"}}}
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value=page)
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        newest = _uts("2026-08-15T12:00:00Z")
        with patch("httpx.AsyncClient", return_value=client):
            asyncio.run(ls.fetch_recent_scrobbles("u", "k", since_ts=newest))
        sent_from = client.get.call_args.kwargs["params"]["from"]
        assert sent_from == newest - ls._OVERLAP_SECONDS

    def test_a_late_submitted_play_is_ingested_by_the_next_sync(self, tmp_path):
        path = tmp_path / "scrobbles.jsonl"
        _write(path, [_stored("2026-08-15T12:00:00Z")])
        seen_since = {}

        async def fake_fetch(_u, _k, since_ts):
            seen_since["ts"] = since_ts
            # The overlap re-delivers the stored play alongside the late one.
            return [_raw("2026-08-15T12:00:00Z"), _raw("2026-08-12T09:00:00Z", "Glory Box")], 1, True

        with patch.dict(os.environ, LASTFM_ENV), patch.object(ls, "fetch_recent_scrobbles", new=fake_fetch):
            stats = asyncio.run(ls.sync(path))

        assert seen_since["ts"] == _uts("2026-08-15T12:00:00Z")
        assert stats["new"] == 1
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert [r["track"] for r in rows] == ["Glory Box", "Roads"]

    def test_since_comes_from_disk_when_no_snapshot_is_loaded(self, tmp_path):
        """``python -m app.refresh`` never loads the snapshot, so a since_ts
        taken from it was 0 and every CLI refresh fetched the full history."""
        path = tmp_path / "scrobbles.jsonl"
        _write(path, [_stored("2026-08-01T00:00:00Z"), _stored("2026-08-15T12:00:00Z", "Sour Times")])
        seen = {}

        async def fake_fetch(_u, _k, since_ts):
            seen["ts"] = since_ts
            return [], 1, True

        with patch.dict(os.environ, LASTFM_ENV), \
                patch.object(ls, "fetch_recent_scrobbles", new=fake_fetch), \
                patch("app.data.get_scrobbles", return_value=[]):
            asyncio.run(ls.sync(path))
        assert seen["ts"] == _uts("2026-08-15T12:00:00Z")


class TestCappedFetchIsReportedIncomplete:
    def test_sync_reports_complete_false(self, tmp_path):
        path = tmp_path / "scrobbles.jsonl"
        _write(path, [])

        async def fake_fetch(_u, _k, _since):
            return [_raw("2020-01-01T00:00:00Z")], 251, False

        with patch.dict(os.environ, LASTFM_ENV), patch.object(ls, "fetch_recent_scrobbles", new=fake_fetch):
            stats = asyncio.run(ls.sync(path))
        assert stats["complete"] is False


class TestNoOpSyncChangesNothing:
    """A sync that finds nothing new used to rewrite scrobbles.jsonl and reload,
    bumping the generation so every client re-downloaded the same bytes."""

    @pytest.fixture
    def paths(self, tmp_path):
        tracks = tmp_path / "tracks.jsonl"
        scrobbles = tmp_path / "scrobbles.jsonl"
        tracks.write_text("", encoding="utf-8")
        _write(scrobbles, [_stored("2026-08-15T12:00:00Z")])
        return tracks, scrobbles

    def test_generation_and_file_are_untouched(self, paths):
        tracks, scrobbles = paths
        before_bytes = scrobbles.read_bytes()
        before_mtime = scrobbles.stat().st_mtime_ns

        async def fake_fetch(_u, _k, _since):
            return [_raw("2026-08-15T12:00:00Z")], 1, True

        with data.use_paths(tracks, scrobbles), \
                patch.dict(os.environ, LASTFM_ENV), \
                patch.object(ls, "fetch_recent_scrobbles", new=fake_fetch), \
                patch("app.main.SCROBBLES_PATH", scrobbles):
            from app.main import DASHBOARD_TOKEN, app
            gen_before = data.get_snapshot().generation
            r = TestClient(app).post("/api/lastfm/sync", headers={"X-Dashboard-Token": DASHBOARD_TOKEN})
            gen_after = data.get_snapshot().generation

        assert r.status_code == 200, r.text
        assert r.json()["new"] == 0
        assert gen_after == gen_before
        assert scrobbles.read_bytes() == before_bytes
        assert scrobbles.stat().st_mtime_ns == before_mtime

    def test_a_sync_with_new_plays_still_reloads(self, paths):
        tracks, scrobbles = paths

        async def fake_fetch(_u, _k, _since):
            return [_raw("2026-08-16T08:00:00Z", "Glory Box")], 1, True

        with data.use_paths(tracks, scrobbles), \
                patch.dict(os.environ, LASTFM_ENV), \
                patch.object(ls, "fetch_recent_scrobbles", new=fake_fetch), \
                patch("app.main.SCROBBLES_PATH", scrobbles):
            from app.main import DASHBOARD_TOKEN, app
            gen_before = data.get_snapshot().generation
            r = TestClient(app).post("/api/lastfm/sync", headers={"X-Dashboard-Token": DASHBOARD_TOKEN})
            assert r.json()["new"] == 1
            assert data.get_snapshot().generation > gen_before
            assert len(data.get_scrobbles()) == 2


# ── #118 ──


class TestMissingCredentialsSkipAnOptionalPhase:
    @staticmethod
    def _no_creds():
        raise MissingCredentialsError("DISCOGS_TOKEN missing")

    def test_optional_phase_is_skipped(self):
        assert _phase("4b", "Discogs", self._no_creds, optional=True) == SKIPPED

    def test_required_phase_still_fails(self):
        assert _phase("4", "Last.fm", self._no_creds, optional=False) == FAILED

    def test_a_blank_discogs_token_raises_missing_credentials(self, monkeypatch):
        import pipeline.enrich_discogs as discogs
        monkeypatch.setattr(discogs, "load_dotenv", lambda *_a, **_k: None)
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        with pytest.raises(MissingCredentialsError):
            discogs.enrich()


class TestCancellationHoldsTheLock:
    """A thread can't be cancelled. With a bare ``await to_thread(...)``,
    cancelling the coroutine released the lock while the pipeline thread kept
    writing, so a second mutation could start on top of it."""

    def test_lock_is_held_until_the_thread_exits(self):
        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()

        def slow_pipeline():
            started.set()
            release.wait(5)
            finished.set()

        async def scenario():
            async def guarded():
                async with refresh_mod.exclusive_mutation("refresh"):
                    await refresh_mod.run_to_completion(slow_pipeline)

            task = asyncio.create_task(guarded())
            for _ in range(200):
                if started.is_set() or task.done():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set(), "the pipeline thread never started"
            task.cancel()
            await asyncio.sleep(0.05)
            # Cancelled, but the thread is still running: the lock must hold.
            assert refresh_mod._refresh_lock.locked()
            with pytest.raises(refresh_mod.RefreshInProgress):
                async with refresh_mod.exclusive_mutation("sync"):
                    pass
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
            assert not refresh_mod._refresh_lock.locked()

        asyncio.run(scenario())


class TestInProgressNamesTheRunningOperation:
    def test_409_message_names_what_holds_the_lock(self):
        async def scenario():
            async with refresh_mod.exclusive_mutation("refresh"):
                with pytest.raises(refresh_mod.RefreshInProgress, match="a refresh is already running"):
                    async with refresh_mod.exclusive_mutation("sync"):
                        pass

        asyncio.run(scenario())
