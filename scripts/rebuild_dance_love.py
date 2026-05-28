"""Replace owner-curated playlist sections in taste_profile.md.

Sections rebuilt from owner's authoritative playlists:
  - C:/Users/Branden/Downloads/Dance.txt  -> ### dance (locked)
  - C:/Users/Branden/Downloads/Love.txt   -> ### love (locked)
  - C:/Users/Branden/Downloads/Slow.csv   -> ### slow (locked)

Spot-check (2026-05-25) verdicts for these three categories:
  Dance: audit 0/5 Y, centroid 1/5 Y — AUDIT DRIFT
  Love : audit 2/5 Y, centroid 1/5 Y — AUDIT DRIFT
  Slow : audit 1/5 Y, centroid 0/5 Y — AUDIT DRIFT

For all three, the audit signal drifted too far from current taste to merge
any of it (unlike Sad, where audit-Sad was 5/5 Y on keeps). The owner's
curated playlists are taken as sole source of truth.

Tracks are written using library's canonical (artist, track) when in
scrobble library; otherwise the playlist file's formatting is used.

Supports both formats:
  - .txt: lines of `Artist - Track`
  - .csv: header row, then `Artist,Track[,Album,...]` per row

Also preserves source playlists into inputs/ for posterity (gitignored).
"""
from __future__ import annotations
import csv
import json
import re
import shutil
from pathlib import Path

REPO = Path(r"C:\Users\Branden\OneDrive\Documents\Music Enrichment\Music-Enrichment")
DESKTOP_TASTE = Path(r"C:\Users\Branden\OneDrive\Desktop\taste_profile.md")
REPO_TASTE = REPO / "taste_profile.md"
TRACKS = REPO / "tracks.jsonl"

PLAYLIST_SOURCES = [
    ("dance", Path(r"C:\Users\Branden\Downloads\Dance.txt")),
    ("love", Path(r"C:\Users\Branden\Downloads\Love.txt")),
    ("slow", Path(r"C:\Users\Branden\Downloads\Slow.csv")),
]


def tight(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_library() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for line in TRACKS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        idx[f"{tight(o['artist'])}|{tight(o['track'])}"] = o
    return idx


def _read_pairs(path: Path) -> list[tuple[str, str]]:
    """Read playlist into (artist, track) pairs. Auto-detects .csv vs .txt."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return []
            # Map column names case-insensitively
            cols = {c.strip().lower(): i for i, c in enumerate(header)}
            def _find(*names: str) -> int | None:
                for n in names:
                    if n in cols:
                        return cols[n]
                return None
            ai = _find("artist", "artist name", "artist name(s)")
            ti = _find("track", "track name", "title", "name")
            if ai is None or ti is None:
                raise SystemExit(f"CSV {path} missing Artist/Track columns; saw: {header}")
            return [(row[ai].strip(), row[ti].strip()) for row in reader
                    if len(row) > max(ai, ti) and row[ai].strip() and row[ti].strip()]
    # .txt format
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        a, t = (p.strip() for p in line.split(" - ", 1))
        pairs.append((a, t))
    return pairs


def parse_playlist(path: Path, lib: dict[str, dict]) -> list[tuple[str, str, bool]]:
    """Return [(artist, track, in_library)], deduped, library-canonical when possible."""
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for a, t in _read_pairs(path):
        k = f"{tight(a)}|{tight(t)}"
        if k in seen:
            continue
        seen.add(k)
        lib_track = lib.get(k)
        if lib_track:
            out.append((lib_track["artist"], lib_track["track"], True))
        else:
            out.append((a, t, False))
    return out


def patch_section(text: str, section: str, body_lines: list[str]) -> str:
    """Replace `### {section} (locked)` ... up to next `### ` heading."""
    lines = text.split("\n")
    target = f"### {section} (locked)"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == target), None)
    if start is None:
        raise SystemExit(f"Could not find '{target}' in taste_profile.md")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")),
        len(lines),
    )
    new_block = [target] + body_lines + [""]
    return "\n".join(lines[:start] + new_block + lines[end:])


def main() -> None:
    lib = load_library()
    print(f"Library: {len(lib)} tracks")

    desktop_text = DESKTOP_TASTE.read_text(encoding="utf-8")
    repo_text = REPO_TASTE.read_text(encoding="utf-8")

    for section, src in PLAYLIST_SOURCES:
        tracks = parse_playlist(src, lib)
        in_lib = sum(1 for _, _, ok in tracks if ok)
        print(f"  {section}: {len(tracks)} tracks ({in_lib} in library, {len(tracks)-in_lib} not)")
        tracks.sort(key=lambda r: (r[0].lower(), r[1].lower()))
        body = [f'- "{t}" by {a}' for a, t, _ in tracks]
        desktop_text = patch_section(desktop_text, section, body)
        repo_text = patch_section(repo_text, section, body)

        # Preserve original playlist file
        dest = REPO / "inputs" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)

    DESKTOP_TASTE.write_text(desktop_text, encoding="utf-8")
    REPO_TASTE.write_text(repo_text, encoding="utf-8")
    print(f"Patched desktop ({DESKTOP_TASTE.stat().st_size} bytes)")
    print(f"Patched repo    ({REPO_TASTE.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
