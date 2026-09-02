"""Regressions for ``pipeline.config.configure_logging`` (2026-09-02 audit).

Two separate defects, both about *which file* the logging config points at:
the API key leaking into it, and the file being replaced once per phase.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx
import pytest

from pipeline.config import configure_logging
from tests.conftest import close_run_log_handlers


class TestSecretsDoNotReachTheRunLog:
    """``configure_logging`` sets the ROOT logger to DEBUG with a FileHandler.
    urllib3 was pinned to INFO because Last.fm takes its API key as a query
    parameter — but ``app.lastfm_sync`` talks over **httpx**, which logs
    ``HTTP Request: GET <full url>`` at INFO, key and all. The first
    ``/api/refresh`` installs this config in the *server* process, so from then
    on every sync wrote the key into runs/*.log and onto the console.
    """

    SECRET = "SECRETKEY123abc"

    def _emit_one_request(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
        with httpx.Client(transport=transport) as client:
            client.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={"method": "user.getRecentTracks", "api_key": self.SECRET},
            )

    def test_httpx_request_lines_stay_out_of_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "run.log"
            configure_logging(log_path)
            try:
                self._emit_one_request()
                logging.shutdown()
                assert self.SECRET not in log_path.read_text(encoding="utf-8")
            finally:
                close_run_log_handlers()

    @pytest.mark.parametrize("name", ["httpx", "httpcore", "urllib3"])
    def test_the_chatty_transports_are_pinned(self, name) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(Path(tmp) / "run.log")
            try:
                # DEBUG must not pass; the pipeline's own DEBUG output still does.
                assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)
                assert logging.getLogger("pipeline.enrich_metadata").isEnabledFor(logging.DEBUG)
            finally:
                close_run_log_handlers()


class TestTheFullRunLogIsNotFragmented:
    """Every phase entry point calls ``configure_logging()`` with its own
    ``None`` default. That used to tear down the log the orchestrator had just
    opened and start a new one, once per phase: ``full_run_<ts>.log`` kept only
    the header, each phase went to its own file, and the summary block landed in
    whichever file the last phase happened to open.
    """

    def test_a_none_path_keeps_the_installed_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_log = Path(tmp) / "full_run.log"
            configure_logging(run_log)
            try:
                assert configure_logging() == run_log        # a phase starting up
                logging.getLogger("pipeline.phase").info("phase output")
                logging.shutdown()
                assert "phase output" in run_log.read_text(encoding="utf-8")
            finally:
                close_run_log_handlers()

    def test_repeated_phase_starts_all_land_in_the_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_log = Path(tmp) / "full_run.log"
            configure_logging(run_log)
            try:
                for phase in ("4", "4c", "5a", "8"):
                    configure_logging()
                    logging.getLogger("pipeline.phase").info("phase %s ran", phase)
                logging.shutdown()
                body = run_log.read_text(encoding="utf-8")
                for phase in ("4", "4c", "5a", "8"):
                    assert f"phase {phase} ran" in body
            finally:
                close_run_log_handlers()

    def test_an_explicit_path_still_reconfigures(self) -> None:
        """Tests rely on this, and so does starting a genuinely new run."""
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.log", Path(tmp) / "b.log"
            configure_logging(first)
            try:
                assert configure_logging(second) == second
                logging.getLogger("pipeline.phase").info("into b")
                logging.shutdown()
                assert "into b" in second.read_text(encoding="utf-8")
            finally:
                close_run_log_handlers()

    def test_with_no_handler_installed_it_opens_a_fresh_one(self) -> None:
        close_run_log_handlers()
        path = configure_logging()
        try:
            assert path.exists()
        finally:
            close_run_log_handlers()
