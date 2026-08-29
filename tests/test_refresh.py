"""Tests for app.refresh — full-chain refresh orchestration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Unit: refresh() calls each step in order ──

class TestRefresh:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_calls_all_steps_in_order(self):
        call_order = []

        async def fake_sync(_path):
            call_order.append("sync")
            return {"new": 5, "fetched": 5, "total": 100, "pages_fetched": 1}

        def fake_pipeline(**kwargs):
            call_order.append("pipeline")
            assert kwargs["start_from"] == "2"
            assert kwargs["skip_tests"] is True
            assert kwargs["skip_pause"] is True
            return {"2": "ok", "3a": "ok"}

        def fake_export_pending():
            call_order.append("export_pending")
            return 12

        def fake_reload():
            call_order.append("reload")
            return {"tracks": 2730, "scrobbles": 50000}

        with (
            patch("app.refresh.lastfm_sync.sync", new=fake_sync),
            patch("app.refresh._pipeline_run", new=fake_pipeline),
            patch("app.refresh.export_tunemymusic.export_pending", new=fake_export_pending),
            patch("app.refresh.data.reload", new=fake_reload),
        ):
            from app.refresh import refresh
            result = self._run(refresh())

        assert call_order == ["sync", "pipeline", "export_pending", "reload"]

    def test_returns_combined_stats(self):
        async def fake_sync(_path):
            return {"new": 3, "fetched": 3, "total": 50, "pages_fetched": 1}

        def fake_pipeline(**kwargs):
            return {"2": "ok"}

        def fake_export_pending():
            return 7

        def fake_reload():
            return {"tracks": 100, "scrobbles": 500}

        with (
            patch("app.refresh.lastfm_sync.sync", new=fake_sync),
            patch("app.refresh._pipeline_run", new=fake_pipeline),
            patch("app.refresh.export_tunemymusic.export_pending", new=fake_export_pending),
            patch("app.refresh.data.reload", new=fake_reload),
        ):
            from app.refresh import refresh
            result = self._run(refresh())

        assert result["sync"]["new"] == 3
        assert result["pipeline"] == {"2": "ok"}
        assert result["pending_exportify"] == 7
        assert result["cache"]["tracks"] == 100

    def test_pipeline_receives_correct_kwargs(self):
        received = {}

        async def fake_sync(_path):
            return {"new": 0, "fetched": 0, "total": 0, "pages_fetched": 0}

        def fake_pipeline(**kwargs):
            received.update(kwargs)
            return {}

        with (
            patch("app.refresh.lastfm_sync.sync", new=fake_sync),
            patch("app.refresh._pipeline_run", new=fake_pipeline),
            patch("app.refresh.export_tunemymusic.export_pending", return_value=0),
            patch("app.refresh.data.reload", return_value={}),
        ):
            from app.refresh import refresh
            self._run(refresh())

        assert received["start_from"] == "2"
        assert received["skip_tests"] is True
        assert received["skip_pause"] is True

    def test_failed_phase_aborts_before_export_and_reload(self):
        """A FAILED phase must raise and skip export/reload; SKIPPED is benign."""
        side_effects = []

        async def fake_sync(_path):
            return {"new": 1, "fetched": 1, "total": 10, "pages_fetched": 1}

        def fake_pipeline(**kwargs):
            # 3c skipped (no Exportify CSV) is fine; 8 failed is not.
            return {"2": "ok", "3c": "skipped", "8": "failed"}

        def fake_export_pending():
            side_effects.append("export_pending")
            return 0

        def fake_reload():
            side_effects.append("reload")
            return {}

        with (
            patch("app.refresh.lastfm_sync.sync", new=fake_sync),
            patch("app.refresh._pipeline_run", new=fake_pipeline),
            patch("app.refresh.export_tunemymusic.export_pending", new=fake_export_pending),
            patch("app.refresh.data.reload", new=fake_reload),
        ):
            from app.refresh import refresh
            with pytest.raises(RuntimeError, match="8"):
                self._run(refresh())

        # Neither export nor cache reload ran off the broken pipeline.
        assert side_effects == []

    def test_skipped_only_phases_do_not_abort(self):
        """SKIPPED phases alone (e.g. no Exportify CSV) must not fail a refresh."""
        async def fake_sync(_path):
            return {"new": 0, "fetched": 0, "total": 0, "pages_fetched": 0}

        def fake_pipeline(**kwargs):
            return {"2": "ok", "3c": "skipped", "4b": "skipped"}

        with (
            patch("app.refresh.lastfm_sync.sync", new=fake_sync),
            patch("app.refresh._pipeline_run", new=fake_pipeline),
            patch("app.refresh.export_tunemymusic.export_pending", return_value=0),
            patch("app.refresh.data.reload", return_value={"tracks": 1, "scrobbles": 1}),
        ):
            from app.refresh import refresh
            result = self._run(refresh())

        assert result["cache"] == {"tracks": 1, "scrobbles": 1}

    def test_concurrent_refresh_raises_in_progress(self):
        """A second refresh while one holds the lock fails fast, no work done."""
        from app.refresh import refresh, _refresh_lock, RefreshInProgress

        async def scenario():
            async with _refresh_lock:  # simulate an in-flight refresh
                with pytest.raises(RefreshInProgress):
                    await refresh()

        self._run(scenario())


# ── F-05: refresh, direct sync, and reload share one lock ──

class TestMutationLockSharedAcrossOperations:
    """refresh, a direct Last.fm sync, and a manual cache reload must all go
    through the same lock — not each have their own — so any two of them
    racing (a sync fired mid-refresh, a reload racing a sync) fails fast
    instead of interleaving writes or reads."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_direct_sync_cannot_run_while_refresh_holds_the_lock(self):
        from app.refresh import _exclusive, _refresh_lock, RefreshInProgress

        async def scenario():
            async with _refresh_lock:  # simulate an in-flight refresh
                with pytest.raises(RefreshInProgress):
                    async with _exclusive("sync"):
                        pytest.fail("must not enter the sync section")

        self._run(scenario())

    def test_reload_cannot_run_while_refresh_holds_the_lock(self):
        from app.refresh import _exclusive, _refresh_lock, RefreshInProgress

        async def scenario():
            async with _refresh_lock:  # simulate an in-flight refresh
                with pytest.raises(RefreshInProgress):
                    async with _exclusive("reload"):
                        pytest.fail("must not enter the reload section")

        self._run(scenario())

    def test_refresh_cannot_run_while_a_direct_sync_holds_the_lock(self):
        from app.refresh import refresh, _refresh_lock, RefreshInProgress

        async def scenario():
            async with _refresh_lock:  # simulate an in-flight direct sync
                with pytest.raises(RefreshInProgress):
                    await refresh()

        self._run(scenario())

    def test_reload_cannot_run_while_a_direct_sync_holds_the_lock(self):
        from app.refresh import _exclusive, _refresh_lock, RefreshInProgress

        async def scenario():
            async with _refresh_lock:  # simulate an in-flight direct sync
                with pytest.raises(RefreshInProgress):
                    async with _exclusive("reload"):
                        pytest.fail("must not enter the reload section")

        self._run(scenario())

    def test_lock_releases_after_use_so_a_later_operation_can_proceed(self):
        """Guards the other half: contention only blocks while an operation
        is actually in flight, not forever once one has run."""
        from app.refresh import _exclusive

        async def scenario():
            async with _exclusive("sync"):
                pass
            # Would raise RefreshInProgress here if the first guard leaked.
            async with _exclusive("reload"):
                pass

        self._run(scenario())


class TestApiReloadAndSyncReturn409WhenBusy:
    """API-level: /api/reload and /api/lastfm/sync must surface the same 409
    RefreshInProgress does for POST /api/refresh, not a 500 or a silent
    interleaved run."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks = Path(tmp) / "tracks.jsonl"
            scrobbles = Path(tmp) / "scrobbles.jsonl"
            tracks.write_text("")
            scrobbles.write_text("")
            import app.data as data_mod
            with data_mod.use_paths(tracks, scrobbles):
                from app.main import app
                yield TestClient(app)

    @staticmethod
    def _auth():
        from app.main import DASHBOARD_TOKEN
        return {"X-Dashboard-Token": DASHBOARD_TOKEN}

    @staticmethod
    def _busy_guard():
        """Stand-in for app.refresh._exclusive that always reports another
        operation is already running, without touching the real lock."""
        from app.refresh import RefreshInProgress

        class _Busy:
            def __init__(self, op_name):
                self.op_name = op_name

            async def __aenter__(self):
                raise RefreshInProgress(f"a {self.op_name} is already running")

            async def __aexit__(self, *exc_info):
                return False

        return _Busy

    def test_reload_returns_409_when_another_operation_is_running(self, client):
        with patch("app.refresh._exclusive", new=self._busy_guard()):
            r = client.post("/api/reload", headers=self._auth())
        assert r.status_code == 409
        assert "already running" in r.json()["detail"]

    def test_sync_returns_409_when_another_operation_is_running(self, client):
        with patch("app.refresh._exclusive", new=self._busy_guard()):
            r = client.post("/api/lastfm/sync", headers=self._auth())
        assert r.status_code == 409
        assert "already running" in r.json()["detail"]


class TestApiReloadDoesNotBlockTheEventLoop:
    """``/api/reload`` had to become a coroutine to hold the async mutation
    lock, which took it out of the threadpool FastAPI runs ``def`` handlers in.
    ``data.reload()`` re-parses both JSONL files synchronously, so calling it
    directly from the coroutine stalls the event loop — and with it every other
    in-flight request — for the whole parse. It has to be handed to a worker
    thread.
    """

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks = Path(tmp) / "tracks.jsonl"
            scrobbles = Path(tmp) / "scrobbles.jsonl"
            tracks.write_text("")
            scrobbles.write_text("")
            import app.data as data_mod
            with data_mod.use_paths(tracks, scrobbles):
                from app.main import app
                yield TestClient(app)

    @staticmethod
    def _auth():
        from app.main import DASHBOARD_TOKEN
        return {"X-Dashboard-Token": DASHBOARD_TOKEN}

    def test_reload_runs_off_the_event_loop_thread(self, client):
        observed = {}

        def fake_reload():
            # An event loop is only "running" in the thread that drives it, so
            # its absence here is exactly the property under test.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                observed["on_event_loop"] = False
            else:
                observed["on_event_loop"] = True
            return {"tracks": 0, "scrobbles": 0,
                    "skipped": {"tracks": 0, "scrobbles": 0}}

        with patch("app.data.reload", new=fake_reload):
            r = client.post("/api/reload", headers=self._auth())

        assert r.status_code == 200
        assert observed["on_event_loop"] is False

    def test_reload_still_returns_the_counts(self, client):
        r = client.post("/api/reload", headers=self._auth())
        assert r.status_code == 200
        body = r.json()
        assert body["tracks"] == 0
        assert body["scrobbles"] == 0


# ── API: POST /api/refresh ──

class TestApiRefresh:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks = Path(tmp) / "tracks.jsonl"
            scrobbles = Path(tmp) / "scrobbles.jsonl"
            tracks.write_text("")
            scrobbles.write_text("")
            import app.data as data_mod
            with data_mod.use_paths(tracks, scrobbles):
                from app.main import app
                yield TestClient(app)

    @staticmethod
    def _auth():
        from app.main import DASHBOARD_TOKEN
        return {"X-Dashboard-Token": DASHBOARD_TOKEN}

    def test_refresh_endpoint_returns_200(self, client):
        fake_result = {
            "sync": {"new": 2, "fetched": 2, "total": 50, "pages_fetched": 1},
            "pipeline": {"2": True},
            "pending_exportify": 5,
            "cache": {"tracks": 100, "scrobbles": 500},
        }
        with patch("app.refresh.refresh", new=AsyncMock(return_value=fake_result)):
            r = client.post("/api/refresh", headers=self._auth())
        assert r.status_code == 200
        body = r.json()
        assert body["sync"]["new"] == 2
        assert body["pending_exportify"] == 5

    def test_refresh_endpoint_propagates_runtime_error(self, client):
        async def explode():
            raise RuntimeError("LASTFM_API_KEY not set")

        with patch("app.refresh.refresh", new=explode):
            r = client.post("/api/refresh", headers=self._auth())
        assert r.status_code == 400
        assert "LASTFM_API_KEY" in r.json()["detail"]

    def test_refresh_endpoint_returns_409_when_in_progress(self, client):
        from app.refresh import RefreshInProgress

        async def busy():
            raise RefreshInProgress("a refresh is already running")

        with patch("app.refresh.refresh", new=busy):
            r = client.post("/api/refresh", headers=self._auth())
        assert r.status_code == 409
        assert "already running" in r.json()["detail"]


# ── Unit: export_pending() ──

class TestExportPending:
    def _make_tracks(self, path: Path, tracks: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for t in tracks:
                fh.write(json.dumps(t) + "\n")

    def test_exports_only_tracks_without_audio_features(self):
        from pipeline.export_tunemymusic import export_pending
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = Path(tmp) / "tracks.jsonl"
            out_path = Path(tmp) / "pending.csv"
            self._make_tracks(tracks_path, [
                {"artist": "A", "track": "T1", "album": "X", "audio_features": {"energy": 0.8}},
                {"artist": "B", "track": "T2", "album": "Y", "audio_features": None},
                {"artist": "C", "track": "T3", "album": "Z"},
            ])
            n = export_pending(tracks_path=tracks_path, output_path=out_path)
        assert n == 2

    def test_output_csv_has_correct_columns(self):
        from pipeline.export_tunemymusic import export_pending
        import csv as csv_mod
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = Path(tmp) / "tracks.jsonl"
            out_path = Path(tmp) / "pending.csv"
            self._make_tracks(tracks_path, [
                {"artist": "SZA", "track": "Kill Bill", "album": "SOS"},
            ])
            export_pending(tracks_path=tracks_path, output_path=out_path)
            with open(out_path, newline="", encoding="utf-8") as fh:
                rows = list(csv_mod.DictReader(fh))
        assert rows[0]["Artist"] == "SZA"
        assert rows[0]["Track"] == "Kill Bill"
        assert rows[0]["Album"] == "SOS"

    def test_returns_zero_when_all_have_features(self):
        from pipeline.export_tunemymusic import export_pending
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = Path(tmp) / "tracks.jsonl"
            out_path = Path(tmp) / "pending.csv"
            self._make_tracks(tracks_path, [
                {"artist": "A", "track": "T1", "audio_features": {"energy": 0.5}},
            ])
            n = export_pending(tracks_path=tracks_path, output_path=out_path)
        assert n == 0

    def test_returns_zero_when_tracks_file_missing(self):
        from pipeline.export_tunemymusic import export_pending
        with tempfile.TemporaryDirectory() as tmp:
            n = export_pending(
                tracks_path=Path(tmp) / "nonexistent.jsonl",
                output_path=Path(tmp) / "out.csv",
            )
        assert n == 0
