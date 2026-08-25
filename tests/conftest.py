"""Pytest configuration — fails fast and clearly on an unsupported interpreter.

CI pins Python 3.13 (.github/workflows/ci.yml). Running the suite under an
older interpreter doesn't fail cleanly: Python 3.9 in particular surfaces a
cascade of confusing `RuntimeError: There is no current event loop in thread
'MainThread'` failures in test_refresh.py that have nothing to do with the
code under test. One clear error here beats nine misleading ones there.
"""

from __future__ import annotations

import logging
import sys

import pytest

_MIN_PYTHON = (3, 13)


def close_run_log_handlers() -> None:
    """Release run-log handles so Windows can delete the temp dir holding them.

    configure_logging() closes stale FileHandlers when it is *next* called, so
    the handler opened by the final call stays open. On POSIX an open file
    unlinks happily and nothing notices; on Windows deleting it raises
    PermissionError and fails the test that just passed.

    Call this inside the tempdir's own scope — an autouse fixture runs too late,
    after `with TemporaryDirectory()` has already tried to clean up.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)


@pytest.fixture(autouse=True)
def _close_log_file_handlers():
    """Backstop for tests using pytest's tmp_path, whose cleanup is deferred."""
    yield
    close_run_log_handlers()


def pytest_configure(config: pytest.Config) -> None:
    if sys.version_info < _MIN_PYTHON:
        got = ".".join(map(str, sys.version_info[:3]))
        want = ".".join(map(str, _MIN_PYTHON))
        pytest.exit(
            f"\nThis suite requires Python >= {want} (the version CI pins in "
            f".github/workflows/ci.yml). You're running {got}.\n"
            f"Fix: py -3.13 -m pip install -r requirements.txt "
            f"&& py -3.13 -m pytest tests/ -q\n",
            returncode=1,
        )
