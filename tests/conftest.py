"""Pytest configuration — fails fast and clearly on an unsupported interpreter.

CI pins Python 3.13 (.github/workflows/ci.yml). Running the suite under an
older interpreter doesn't fail cleanly: Python 3.9 in particular surfaces a
cascade of confusing `RuntimeError: There is no current event loop in thread
'MainThread'` failures in test_refresh.py that have nothing to do with the
code under test. One clear error here beats nine misleading ones there.
"""

from __future__ import annotations

import sys

import pytest

_MIN_PYTHON = (3, 13)


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
