"""Build a mood-labeling queue, ranked by how much listening each track explains.

The classifier declines to guess moods the features can't predict — honest, but
it leaves gaps, and those gaps are not evenly weighted: a track played 100 times
and one played once cost the same to label and are worth very different amounts.

Ranks unlabeled tracks by play count so a short session buys back the largest
share of listening, printing the share each slice covers as it goes.

Output mirrors ``classify_moods.write_claude_batch``, so results paste straight
back as ``inputs/claude_mood_results.jsonl``.

Usage:
    python scripts/build_label_queue.py [--top N] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.classify_moods import OWNER_LABEL_SOURCES  # noqa: E402
from pipeline.config import INPUTS_DIR, TRACKS_PATH  # noqa: E402

DEFAULT_OUT: Path = INPUTS_DIR / "mood_label_queue.jsonl"


def needs_label(track: dict) -> bool:
    """Anything not carrying an owner judgement is fair game for the queue.

    Centroid rows are included even though they have tags: those are the
    machine's guesses, and replacing one with a real judgement is exactly the
    point. Rows with no tags at all are included too.
    """
    return track.get("mood_source") not in OWNER_LABEL_SOURCES


def build_queue(tracks: list[dict], top: int | None = None) -> list[dict]:
    candidates = [t for t in tracks if needs_label(t)]
    candidates.sort(key=lambda t: -int(t.get("play_count") or 0))
    if top:
        candidates = candidates[:top]
    return candidates


def to_payload(track: dict) -> dict:
    """Fields a labeler needs, and nothing else."""
    return {
        "artist": track.get("artist"),
        "track": track.get("track"),
        "artist_normalized": track.get("artist_normalized"),
        "track_normalized": track.get("track_normalized"),
        "play_count": int(track.get("play_count") or 0),
        "current_mood_tags": track.get("mood_tags") or [],
        "current_mood_source": track.get("mood_source"),
        "audio_features": track.get("audio_features"),
        "genres": track.get("genres") or [],
        "lastfm_tags": track.get("lastfm_tags") or [],
        "discogs_styles": track.get("discogs_styles") or [],
        "release_year": track.get("release_year"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=100,
                        help="How many tracks to queue (0 = all).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    args = parser.parse_args()

    if not args.tracks.exists():
        print(f"No tracks file at {args.tracks}", file=sys.stderr)
        return 1

    tracks = [json.loads(l) for l in args.tracks.read_text(encoding="utf-8").splitlines() if l.strip()]
    total_plays = sum(int(t.get("play_count") or 0) for t in tracks)

    queue = build_queue(tracks, args.top or None)
    queued_plays = sum(int(t.get("play_count") or 0) for t in queue)
    unlabeled_plays = sum(
        int(t.get("play_count") or 0) for t in tracks if needs_label(t)
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for track in queue:
            fh.write(json.dumps(to_payload(track), ensure_ascii=False) + "\n")

    pct = (queued_plays / total_plays * 100) if total_plays else 0.0
    exposure = (unlabeled_plays / total_plays * 100) if total_plays else 0.0
    print(f"Queued {len(queue)} tracks → {args.out}")
    print(f"  covers {queued_plays} plays ({pct:.1f}% of all listening)")
    print(f"  machine-labeled or blank overall: {unlabeled_plays} plays ({exposure:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
