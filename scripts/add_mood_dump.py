"""Merge an `Artist - Track` text dump into existing_audit.csv as a new mood.

For when a mood category was missing from the original TuneMyMusic export
(e.g. Sadlist) and you have it as a plain text dump.

Usage:
    python scripts/add_mood_dump.py --mood Sad --input inputs/sadlist_dump.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import INPUT_EXISTING_AUDIT, MOOD_CATEGORIES  # noqa: E402


def _parse_line(line: str) -> tuple[str, str] | None:
    """Split 'Artist - Track' on the FIRST ' - ' occurrence."""
    line = line.strip()
    if not line:
        return None
    idx = line.find(" - ")
    if idx == -1:
        return None
    return line[:idx].strip(), line[idx + 3:].strip()


def add(input_path: Path, mood: str, audit_path: Path = INPUT_EXISTING_AUDIT) -> dict[str, int]:
    if mood not in MOOD_CATEGORIES:
        raise ValueError(f"Mood {mood!r} not in canonical 14: {MOOD_CATEGORIES}")
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    # Load existing audit
    rows: list[dict] = []
    if audit_path.exists():
        with open(audit_path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))

    # Index by (artist_lower, track_lower) for matching
    index: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["artist"].lower().strip(), r["track"].lower().strip())
        index[key] = r

    appended = 0
    new_entries = 0
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            parsed = _parse_line(line)
            if parsed is None:
                continue
            artist, track = parsed
            key = (artist.lower().strip(), track.lower().strip())
            existing = index.get(key)
            if existing is not None:
                tags = [t.strip() for t in (existing.get("mood_tags") or "").split(",") if t.strip()]
                if mood not in tags:
                    tags.append(mood)
                    existing["mood_tags"] = ", ".join(sorted(tags))
                    appended += 1
            else:
                new_row = {"artist": artist, "track": track, "mood_tags": mood}
                rows.append(new_row)
                index[key] = new_row
                new_entries += 1

    # Write back
    rows.sort(key=lambda r: (r["artist"].lower(), r["track"].lower()))
    with open(audit_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["artist", "track", "mood_tags"])
        for r in rows:
            writer.writerow([r["artist"], r["track"], r.get("mood_tags", "")])

    print(f"Mood: {mood}")
    print(f"  Appended to existing entries : {appended}")
    print(f"  New entries created          : {new_entries}")
    print(f"  Total audit rows now         : {len(rows)}")
    return {"appended": appended, "new": new_entries, "total": len(rows)}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Add a mood category from a text dump.")
    p.add_argument("--mood", required=True, choices=list(MOOD_CATEGORIES))
    p.add_argument("--input", type=Path, required=True,
                   help="Text file with one 'Artist - Track' per line")
    p.add_argument("--audit", type=Path, default=INPUT_EXISTING_AUDIT)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    add(args.input, args.mood, args.audit)
