"""Emit ``inputs/claude_mood_results.jsonl`` from the committed Claude verdicts.

Phase 6 queues every track it cannot classify to ``inputs/claude_mood_batch.jsonl``;
Claude labels them and the verdicts are committed under ``docs/`` as
``claude_mood_verdicts_<date>.jsonl``. This replays those files into the results
file Phase 6 reads, applying them at ``mood_source="claude_batch"``,
``mood_confidence="high"``.

Verdicts are keyed by (artist_normalized, track_normalized), not by position in
the batch: ``inputs/`` is gitignored and the batch is regenerated on every Phase 6
run, so a positional key silently mislabels the whole library the next time the
queue changes size.

Later files win on a repeated key, so a re-review supersedes an older verdict.

    python scripts/write_mood_results.py            # replay every docs/ verdict file
    python scripts/write_mood_results.py --check    # validate only, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import MOOD_CATEGORIES  # noqa: E402

VERDICT_GLOB = "claude_mood_verdicts_*.jsonl"
DOCS_DIR = REPO_ROOT / "docs"
OUT = REPO_ROOT / "inputs" / "claude_mood_results.jsonl"


def load_verdicts(paths: list[Path]) -> tuple[dict[tuple[str, str], dict], list[str]]:
    """Merge verdict files in the order given. Returns (by_key, errors)."""
    by_key: dict[tuple[str, str], dict] = {}
    errors: list[str] = []
    valid = set(MOOD_CATEGORIES)

    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{lineno}: bad JSON ({exc})")
                continue

            artist = (row.get("artist_normalized") or "").strip()
            track = (row.get("track_normalized") or "").strip()
            moods = row.get("mood_tags") or []
            if not artist or not track:
                errors.append(f"{path.name}:{lineno}: missing normalized identity")
                continue
            unknown = [m for m in moods if m not in valid]
            if unknown:
                errors.append(f"{path.name}:{lineno}: unknown moods {unknown}")
                continue
            if not moods:
                errors.append(f"{path.name}:{lineno}: empty mood_tags")
                continue

            by_key[(artist, track)] = {
                "artist": row.get("artist"),
                "track": row.get("track"),
                "artist_normalized": artist,
                "track_normalized": track,
                "mood_tags": list(moods),
            }
    return by_key, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verdicts", type=Path, nargs="*",
                    help=f"verdict files (default: sorted docs/{VERDICT_GLOB})")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true",
                    help="validate only; do not write the results file")
    args = ap.parse_args(argv)

    paths = args.verdicts or sorted(DOCS_DIR.glob(VERDICT_GLOB))
    if not paths:
        print(f"No verdict files found in {DOCS_DIR}/{VERDICT_GLOB}", file=sys.stderr)
        return 1

    by_key, errors = load_verdicts(paths)
    for err in errors:
        print(f"ERROR {err}", file=sys.stderr)
    if errors:
        return 1

    counts = Counter(m for v in by_key.values() for m in v["mood_tags"])
    print(f"Verdict files : {', '.join(p.name for p in paths)}")
    print(f"Tracks labeled: {len(by_key)}")
    print("Mood counts   : " + ", ".join(f"{m}={n}" for m, n in counts.most_common()))

    if args.check:
        print("CHECK ONLY — nothing written.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for value in by_key.values():
            fh.write(json.dumps(value, ensure_ascii=False) + "\n")
    print(f"Wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
