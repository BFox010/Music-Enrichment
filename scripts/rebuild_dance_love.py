"""Replace ### dance and ### love (locked) sections in taste_profile.md.

Sources are the owner's curated playlists:
  - C:/Users/Branden/Downloads/Dance.txt  -> ### dance (locked)
  - C:/Users/Branden/Downloads/Love.txt   -> ### love (locked)

Spot-check (2026-05-25) showed both audit-Dance (0/5 Y) and audit-Love (2/5 Y)
are bad signal — so unlike Sad, we do NOT merge any audit-source data here.
The playlists are taken as the sole source of truth, mirroring how Sad got
the Soaklist as authoritative reference.

Tracks are written using library's canonical (artist, track) when in
scrobble library; otherwise the playlist file's formatting is used.

Also preserves Dance.txt + Love.txt into inputs/ for posterity.
"""
from __future__ import annotations
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


def parse_playlist(path: Path, lib: dict[str, dict]) -> list[tuple[str, str, bool]]:
    """Return [(artist, track, in_library)], deduped, library-canonical when possible."""
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        a, t = (p.strip() for p in line.split(" - ", 1))
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
