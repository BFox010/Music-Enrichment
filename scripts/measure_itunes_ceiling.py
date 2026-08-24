"""Measure how much iTunes-library coverage Phase A could possibly reach.

Issue #42 asks for the achievable ceiling to be measured before any matching
work is built. The XML is owner-provided (`inputs/` is gitignored), so this has
to run on the owner's machine.

Three questions, in order:

1. **Is there anything to join on besides the name?** #42 proposes matching on
   `apple_music_id`. The iTunes export has no catalogue-ID field
   (`Persistent ID` is a local library UUID — see docs/apple-music-xml.md), so
   the script dumps every per-track key present with its frequency. If some
   ID-bearing key does exist in this export, it shows up here.
2. **What is the ceiling?** No matcher can beat the size of the overlap between
   the XML and the library, so the XML's audio-track count is the hard cap and
   the loosest sane matcher below is the practical one.
3. **Where do today's misses come from?** Each relaxed key recovers a slice; the
   size of each slice says whether the fix is normalization or acquisition.

Usage:
    python scripts/measure_itunes_ceiling.py
    python scripts/measure_itunes_ceiling.py --xml path/to/library.xml --limit 40
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import INPUT_APPLE_MUSIC_LIBRARY, TRACKS_PATH  # noqa: E402
from pipeline.enrich_apple_library import _is_audio_track, _record_to_apple_block  # noqa: E402
from pipeline.normalize import normalize_artist, normalize_track  # noqa: E402

# Trailing "(...)"/"[...]" — remaster/live/edit/version tags that iTunes keeps
# and Last.fm usually doesn't (or vice versa).
_PAREN_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
# Everything from a " - " separator onwards: "Song - 2011 Remaster".
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+.*$")
# A normalized "feat ..." tail, after normalize_track has folded ft/featuring.
_FEAT_TAIL_RE = re.compile(r"\s+feat\b.*$")


def loose_track(title: str) -> str:
    """Normalized title with version/credit tails removed.

    Deliberately more lossy than `normalize_track`: this is a diagnostic key for
    counting recoverable misses, not a join key for the pipeline.
    """
    stripped = _DASH_SUFFIX_RE.sub("", _PAREN_RE.sub("", title or ""))
    return _FEAT_TAIL_RE.sub("", normalize_track(stripped or title or "")).strip()


def loose_artist(artist: str) -> str:
    """Normalized artist with any credit tail and the first separator onwards removed."""
    primary = re.split(r"\s*[,/;]\s*|\s+&\s+|\s+and\s+", artist or "", maxsplit=1)[0]
    return _FEAT_TAIL_RE.sub("", normalize_artist(primary or artist or "")).strip()


def load_library(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def parse_xml(path: Path) -> tuple[list[dict], Counter, int]:
    """Return (audio blocks, per-track key frequencies, total entries)."""
    with open(path, "rb") as fh:
        plist = plistlib.load(fh)
    entries = list((plist.get("Tracks") or {}).values())

    key_freq: Counter[str] = Counter()
    blocks: list[dict] = []
    for record in entries:
        key_freq.update(record.keys())
        if not _is_audio_track(record):
            continue
        block = _record_to_apple_block(record)
        if block is not None:
            blocks.append(block)
    return blocks, key_freq, len(entries)


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "n/a"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xml", type=Path, default=INPUT_APPLE_MUSIC_LIBRARY)
    ap.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    ap.add_argument("--limit", type=int, default=25,
                    help="How many example rows to print per section.")
    args = ap.parse_args(argv)

    if not args.xml.exists():
        print(f"iTunes XML not found: {args.xml}", file=sys.stderr)
        return 1
    if not args.tracks.exists():
        print(f"Track library not found: {args.tracks}", file=sys.stderr)
        return 1

    blocks, key_freq, total_entries = parse_xml(args.xml)
    tracks = load_library(args.tracks)

    print("=== XML ===")
    print(f"Total entries      : {total_entries}")
    print(f"Audio entries      : {len(blocks)}")
    print(f"Unique strict keys : {len({(b['artist_normalized'], b['track_normalized']) for b in blocks})}")

    print("\n=== Per-track keys present (frequency) ===")
    print("Anything here that identifies an Apple *catalogue* track would make "
          "the #42 ID-join possible; Persistent ID does not.")
    for key, n in key_freq.most_common():
        print(f"  {n:6d}  {key}")

    strict = {(t.get("artist_normalized"), t.get("track_normalized")): t for t in tracks}
    loose: dict[tuple[str, str], dict] = {}
    title_only: dict[str, list[dict]] = {}
    for t in tracks:
        loose.setdefault((loose_artist(t.get("artist") or ""), loose_track(t.get("track") or "")), t)
        title_only.setdefault(loose_track(t.get("track") or ""), []).append(t)

    strict_hits: list[dict] = []
    loose_only: list[tuple[dict, dict]] = []
    title_only_hits: list[tuple[dict, dict]] = []
    misses: list[dict] = []
    for b in blocks:
        if (b["artist_normalized"], b["track_normalized"]) in strict:
            strict_hits.append(b)
            continue
        match = loose.get((loose_artist(b["artist"]), loose_track(b["track"])))
        if match is not None:
            loose_only.append((b, match))
            continue
        candidates = title_only.get(loose_track(b["track"])) or []
        if candidates:
            title_only_hits.append((b, candidates[0]))
            continue
        misses.append(b)

    n = len(blocks)
    print("\n=== XML rows, by how they join to the library ===")
    print(f"Strict key (what Phase A does today) : {len(strict_hits):5d}  {_pct(len(strict_hits), n)}")
    print(f"Recovered by relaxed artist/title    : {len(loose_only):5d}  {_pct(len(loose_only), n)}")
    print(f"Title-only (needs eyeballing)        : {len(title_only_hits):5d}  {_pct(len(title_only_hits), n)}")
    print(f"No counterpart in the library        : {len(misses):5d}  {_pct(len(misses), n)}")

    covered_strict = {id(strict[(b['artist_normalized'], b['track_normalized'])]) for b in strict_hits}
    covered_loose = covered_strict | {id(m) for _, m in loose_only}
    covered_all = covered_loose | {id(m) for _, m in title_only_hits}
    total_tracks = len(tracks)
    print("\n=== Library rows that would carry iTunes fields ===")
    print(f"Today (strict)                 : {len(covered_strict):5d}  {_pct(len(covered_strict), total_tracks)}")
    print(f"+ relaxed artist/title         : {len(covered_loose):5d}  {_pct(len(covered_loose), total_tracks)}")
    print(f"+ title-only (upper bound)     : {len(covered_all):5d}  {_pct(len(covered_all), total_tracks)}")
    ceiling = min(n, total_tracks)
    print(f"Hard ceiling (all audio in XML): {ceiling:5d}  {_pct(ceiling, total_tracks)}")

    if loose_only:
        print(f"\n=== Recoverable by better normalization (first {args.limit}) ===")
        for b, m in loose_only[:args.limit]:
            print(f"  XML: {b['artist']} — {b['track']}")
            print(f"  LIB: {m.get('artist')} — {m.get('track')}")
    if misses:
        print(f"\n=== In the XML, absent from the library (first {args.limit}) ===")
        for b in misses[:args.limit]:
            print(f"  {b['artist']} — {b['track']}  [{b.get('itunes_kind')}]")

    print("\n=== Kind breakdown of XML audio rows ===")
    for kind, count in Counter(b.get("itunes_kind") for b in blocks).most_common():
        print(f"  {count:6d}  {kind}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
