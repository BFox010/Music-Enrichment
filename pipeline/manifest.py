"""Manifest loader for pipeline_manifest.yaml.

The manifest is the single source of truth for phase execution order.
run_full_pipeline.py derives its phase list from here; tests use the same
functions to verify the orchestrator and manifest never drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pipeline.config import REPO_ROOT, SCHEMA_VERSION

MANIFEST_PATH: Path = REPO_ROOT / "pipeline_manifest.yaml"


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load and return the raw manifest dict."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_phases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered list of phase dicts from the manifest."""
    return manifest["phases"]


def get_phase_ids(manifest: dict[str, Any]) -> list[str]:
    """Return phase IDs in execution order."""
    return [str(p["id"]) for p in manifest["phases"]]


def find_phase_index(phases: list[dict[str, Any]], phase_id: str) -> int:
    """Return the 0-based index of `phase_id` in the phases list.

    Raises ValueError if not found.
    """
    for i, phase in enumerate(phases):
        if str(phase["id"]) == phase_id:
            return i
    valid = [str(p["id"]) for p in phases]
    raise ValueError(
        f"Phase {phase_id!r} not found in manifest. Valid IDs: {valid}"
    )


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of error strings. Empty = manifest is valid."""
    errors: list[str] = []

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"manifest schema_version {manifest.get('schema_version')!r} "
            f"!= config.SCHEMA_VERSION {SCHEMA_VERSION!r}"
        )

    phases = manifest.get("phases", [])
    if not phases:
        errors.append("manifest has no phases")
        return errors

    ids: set[str] = set()
    for phase in phases:
        pid = str(phase.get("id", ""))
        if not pid:
            errors.append(f"phase missing 'id' field: {phase}")
            continue
        if pid in ids:
            errors.append(f"duplicate phase id: {pid!r}")
        ids.add(pid)

        if not phase.get("manual"):
            if not phase.get("module"):
                errors.append(f"phase {pid!r}: missing 'module'")
            if not phase.get("callable"):
                errors.append(f"phase {pid!r}: missing 'callable'")

        for dep in phase.get("depends_on", []):
            if str(dep) not in ids:
                # deps must reference phases that appear earlier in the list
                errors.append(
                    f"phase {pid!r}: depends_on {dep!r} which hasn't been "
                    f"defined yet (dependency must precede dependent)"
                )

    return errors
