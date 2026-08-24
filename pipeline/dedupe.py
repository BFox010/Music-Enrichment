"""Phase 2 — Dedupe scrobbles into unique track skeleton.

Groups scrobbles.jsonl by (artist_normalized, track_normalized) and emits
tracks_skeleton.jsonl — one row per unique track with play statistics.

Usage:
    python -m pipeline.dedupe
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.config import (
    SCROBBLES_PATH,
    TRACKS_SKELETON_PATH,
    configure_logging,
    get_logger,
)

log = get_logger(__name__)


def _most_common_value(values: list[str]) -> str:
    """Most frequently occurring non-empty string, or '' if all empty.

    Ties break on the value itself, not on scrobble arrival order.
    ``Counter.most_common`` breaks ties by insertion order, so the same input
    set could pick a different winner depending on the order rows happened to
    arrive — and for ``musicbrainz_id`` a tie can flip ``canonical_track_id``
    between runs, which cascades into every cross-phase join.
    """
    non_empty = [v for v in values if v]
    if not non_empty:
        return ""
    return sorted(Counter(non_empty).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _peak_year(years: list[int]) -> int:
    """Year with the most plays. Ties break on the year itself (earliest)."""
    return sorted(Counter(years).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _merge_skeleton_group(rows: list[dict]) -> dict:
    """Fold skeleton rows that share an export-supplied musicbrainz_id.

    Mirrors the strong-identifier merge Phase 4e already performs unconditionally
    on a shared musicbrainz_id — no review needed, an exact match is decisive.
    Doing it here too means Phases 3a/4 never see the redundant half at all.

    Records every source row's own (artist_normalized, track_normalized) as an
    ``identity_aliases`` entry. ``scrobbles.jsonl`` is never rewritten, so
    without this a play logged under the merged-away key would have nothing
    left to match — the app's scrobble→track index (app/metrics.py) resolves
    exactly through this field, same as it does for Phase 4e's later merges.
    """
    ordered = sorted(
        rows,
        key=lambda r: (-r["play_count"], r["artist_normalized"], r["track_normalized"]),
    )
    merged = dict(ordered[0])
    merged["play_count"] = sum(r["play_count"] for r in rows)
    merged["first_scrobbled"] = min(r["first_scrobbled"] for r in rows)
    merged["last_scrobbled"] = max(r["last_scrobbled"] for r in rows)
    merged["artist_mbid"] = _most_common_value(
        [r.get("artist_mbid") or "" for r in rows]
    ) or None
    merged["identity_aliases"] = sorted(
        {(r["artist_normalized"], r["track_normalized"]) for r in rows}
    )
    merged["identity_aliases"] = [list(pair) for pair in merged["identity_aliases"]]
    return merged


def _merge_by_export_mbid(skeletons: list[dict]) -> list[dict]:
    """Merge skeleton rows sharing a non-null export-supplied musicbrainz_id.

    The decisive evidence Phase 4e normally waits for (name-shape *and*
    fetched MBID) already exists here for free where the Last.fm export
    itself carried a recording MBID — no lookup required. Conservative by
    construction: only rows the export itself already tagged with the exact
    same recording ID ever merge; everything else is left for Phase 4e.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(skeletons):
        mbid = row.get("musicbrainz_id")
        if mbid:
            buckets[mbid].append(i)

    to_merge = {mbid: idxs for mbid, idxs in buckets.items() if len(idxs) > 1}
    if not to_merge:
        return skeletons

    merged_indices: set[int] = set()
    merged_rows: list[dict] = []
    for idxs in to_merge.values():
        merged_indices.update(idxs)
        merged_rows.append(_merge_skeleton_group([skeletons[i] for i in idxs]))

    kept = [row for i, row in enumerate(skeletons) if i not in merged_indices]
    return kept + merged_rows


def dedupe(
    scrobbles_path: Path = SCROBBLES_PATH,
    output_path: Path = TRACKS_SKELETON_PATH,
    run_log_path: Path | None = None,
) -> int:
    """Group scrobbles by join key, aggregate stats, write tracks_skeleton.jsonl.

    Returns count of unique tracks written.
    """
    run_log_path = configure_logging(run_log_path)
    log.info("=== Phase 2: dedupe ===")
    log.info("Input : %s", scrobbles_path)
    log.info("Output: %s", output_path)

    if not scrobbles_path.exists():
        log.error("scrobbles.jsonl not found: %s", scrobbles_path)
        raise FileNotFoundError(scrobbles_path)

    rows: list[dict] = []
    with open(scrobbles_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    log.info("Read %d scrobble rows", len(rows))

    # Group by composite join key
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = f"{row['artist_normalized']}|{row['track_normalized']}"
        groups[key].append(row)

    log.info("Unique (artist, track) pairs: %d", len(groups))

    skeletons: list[dict] = []
    for scrobbles in groups.values():
        # Use most-common display names to handle capitalisation drift
        artist = _most_common_value([s["artist"] for s in scrobbles])
        track = _most_common_value([s["track"] for s in scrobbles])
        album = _most_common_value([s["album"] for s in scrobbles])

        dates = sorted(s["scrobbled_at"] for s in scrobbles)
        years = [s["year"] for s in scrobbles]

        # Carry the MusicBrainz IDs the export supplied. Most-common wins for
        # the same reason display names do: individual scrobbles disagree, and
        # the majority reading is the safer identity. Phase 4e relies on these
        # to recognise the same recording under different artist credits.
        mbid = _most_common_value([s.get("musicbrainz_id") or "" for s in scrobbles])
        artist_mbid = _most_common_value([s.get("artist_mbid") or "" for s in scrobbles])

        skeletons.append(
            {
                "artist": artist,
                "track": track,
                "artist_normalized": scrobbles[0]["artist_normalized"],
                "track_normalized": scrobbles[0]["track_normalized"],
                "album": album,
                "musicbrainz_id": mbid or None,
                "artist_mbid": artist_mbid or None,
                "play_count": len(scrobbles),
                "first_scrobbled": dates[0][:10],   # YYYY-MM-DD
                "last_scrobbled": dates[-1][:10],
                "peak_year": _peak_year(years),
            }
        )

    before_mbid_merge = len(skeletons)
    skeletons = _merge_by_export_mbid(skeletons)
    if len(skeletons) < before_mbid_merge:
        log.info(
            "Merged %d row(s) sharing an export-supplied MBID",
            before_mbid_merge - len(skeletons),
        )

    # Stable sort → deterministic output and readable git diffs
    skeletons.sort(key=lambda r: (r["artist_normalized"], r["track_normalized"]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in skeletons:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("Wrote %d unique tracks → %s", len(skeletons), output_path)
    log.info("Run log: %s", run_log_path)
    return len(skeletons)


if __name__ == "__main__":
    import sys

    n = dedupe()
    print(f"Phase 2 done: {n} unique tracks.")
    sys.exit(0 if n > 0 else 1)
