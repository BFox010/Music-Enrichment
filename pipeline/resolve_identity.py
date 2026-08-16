"""Phase 4e — resolve recording identity across artist-credit variants.

Last.fm reports whatever artist credit the scrobbling client sent, so one
recording can arrive under several names: "Clipse" and "Clipse, Pharrell
Williams, Pusha T & Malice"; "Danger Mouse" and "Danger Mouse & Black Thought".
Every downstream phase keys on ``(artist_normalized, track_normalized)``, so
those land as separate rows, get enriched separately, and get classified
separately — the same recording ends up tagged Slow on one row and not-Slow on
the other, and its plays are split between them.

This phase merges those rows on strong evidence only, then records the merged
identities as ``identity_aliases`` so the scrobble log never has to be
rewritten: ``scrobbles.jsonl`` stays an immutable record of what was played,
and the aggregation layer resolves an alias to its canonical track at read
time.

Runs after enrichment (4d) rather than after dedupe, because the decisive
evidence — ISRC and MusicBrainz recording IDs — does not exist until Phase 4
has fetched it. Name-shape evidence alone would be a much weaker test.

Usage:
    python -m pipeline.resolve_identity
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import (
    REPO_ROOT,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.schema import compute_canonical_track_id

log = get_logger(__name__)

INPUT_PATH: Path = REPO_ROOT / "tracks_with_genre_backfill.jsonl"
OUTPUT_PATH: Path = REPO_ROOT / "tracks_resolved.jsonl"

# Pairs the name test proposed but that were rejected, written out so the
# owner can eyeball what the conservative rule is declining to merge.
REVIEW_PATH: Path = REPO_ROOT / "identity_review.jsonl"

# Shortest artist name allowed to act as a prefix. Guards against a two-letter
# stage name swallowing unrelated artists that merely start with those letters.
MIN_PREFIX_LEN: int = 3

# Confidence ordering when two merged rows disagree about mood.
_CONFIDENCE_RANK: dict[str | None, int] = {"high": 3, "medium": 2, "low": 1, None: 0}
_SOURCE_RANK: dict[str | None, int] = {
    "audit": 4, "claude_batch": 3, "centroid": 2, None: 0,
}


def _name_key(row: dict) -> tuple[str, str]:
    return (row.get("artist_normalized") or "", row.get("track_normalized") or "")


class _Union:
    """Minimal union-find over row indices."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def is_credit_variant(a: dict, b: dict) -> bool:
    """Do two rows look like the same recording under different credits?

    Requires the same normalized title *and* one artist name to be a
    word-boundary prefix of the other — the shape a featured-artist expansion
    takes ("clipse" ⊂ "clipse pharrell williams pusha t malice").

    Conflicting strong identifiers veto the merge: if both rows carry a
    MusicBrainz ID or both carry an ISRC and they disagree, these are two
    different recordings that happen to share a title, however similar the
    credits look.
    """
    if a.get("track_normalized") != b.get("track_normalized"):
        return False
    if not a.get("track_normalized"):
        return False

    for field in ("musicbrainz_id", "isrc"):
        va, vb = a.get(field), b.get(field)
        if va and vb and va != vb:
            return False

    x, y = a.get("artist_normalized") or "", b.get("artist_normalized") or ""
    if not x or not y or x == y:
        return False
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) < MIN_PREFIX_LEN:
        return False
    # Word boundary, so "the doors" never absorbs "the doors tribute".
    return long.startswith(short) and long[len(short)] == " "


def _cluster(tracks: list[dict]) -> tuple[list[list[int]], list[dict]]:
    """Group row indices into identity clusters. Returns (clusters, rejected)."""
    uf = _Union(len(tracks))

    # Strong identifiers first — these merge regardless of how the credits read.
    for field in ("isrc", "musicbrainz_id"):
        buckets: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(tracks):
            value = t.get(field)
            if value:
                buckets[str(value)].append(i)
        for members in buckets.values():
            for j in members[1:]:
                uf.union(members[0], j)

    # Then name-shape evidence, compared only within the same title.
    by_title: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(tracks):
        title = t.get("track_normalized")
        if title:
            by_title[title].append(i)

    rejected: list[dict] = []
    for members in by_title.values():
        if len(members) < 2:
            continue
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                if is_credit_variant(tracks[i], tracks[j]):
                    uf.union(i, j)
                elif tracks[i].get("artist_normalized") != tracks[j].get("artist_normalized"):
                    rejected.append({
                        "track": tracks[i].get("track"),
                        "artist_a": tracks[i].get("artist"),
                        "artist_b": tracks[j].get("artist"),
                        "plays_a": tracks[i].get("play_count"),
                        "plays_b": tracks[j].get("play_count"),
                        "reason": "same title, credits not a prefix variant",
                    })

    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tracks)):
        grouped[uf.find(i)].append(i)
    return list(grouped.values()), rejected


def _collect_aliases(rows: list[dict]) -> list[list[str]]:
    """Every name the recordings in this cluster have ever been scrobbled under.

    Accumulates each row's own key *and* any aliases it already carries. The
    inherited ones matter on re-runs: once a row has been absorbed it no longer
    exists to contribute its own name, so dropping its alias would orphan every
    play logged under it.
    """
    aliases: list[list[str]] = []
    for r in rows:
        for key in [list(_name_key(r))] + [list(a) for a in (r.get("identity_aliases") or [])]:
            if key not in aliases:
                aliases.append(key)
    return aliases


def _deconflict_aliases(rows: list[dict]) -> int:
    """Ensure every name is claimed by exactly one row. Returns drops made.

    Two ways a name ends up contested, both seen in practice:

    1. A live row owns the name outright — fresh metadata split a pair that
       merged on an earlier run, so the other half must stop claiming it.
    2. Two rows inherit the same alias from an absorbed row, because each
       merged with it at some point in their history.

    Either way the aggregation layer would route one row's plays to the other.
    A contested alias is awarded to the row whose own title matches it, then to
    the most-played, then alphabetically so the outcome is deterministic.
    """
    owned = {_name_key(r): r for r in rows}
    dropped = 0

    claims: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        own = _name_key(r)
        seen: set[tuple[str, str]] = {own}
        for alias in r.get("identity_aliases") or []:
            key = (alias[0], alias[1]) if len(alias) == 2 else None
            if key is None or key in seen:
                continue
            seen.add(key)
            claims[key].append(r)

    winner: dict[tuple[str, str], int] = {}
    for key, claimants in claims.items():
        if key in owned:
            winner[key] = id(owned[key])
            dropped += len(claimants)
            continue
        best = sorted(
            claimants,
            key=lambda r: (
                r.get("track_normalized") != key[1],
                -int(r.get("play_count") or 0),
                r.get("artist_normalized") or "",
            ),
        )[0]
        winner[key] = id(best)
        dropped += len(claimants) - 1

    for r in rows:
        own = _name_key(r)
        kept: list[list[str]] = [list(own)]
        for alias in r.get("identity_aliases") or []:
            key = (alias[0], alias[1]) if len(alias) == 2 else None
            if key is None or key == own or list(key) in kept:
                continue
            if winner.get(key) == id(r):
                kept.append(list(key))
        r["identity_aliases"] = kept
    return dropped


def _union_lists(rows: list[dict], field: str) -> list:
    out: list = []
    for row in rows:
        for value in row.get(field) or []:
            if value not in out:
                out.append(value)
    return out


def merge_cluster(rows: list[dict]) -> dict:
    """Fold a cluster into one row.

    The most-played row supplies display names and scalar metadata; plays are
    summed; list-valued enrichment is unioned so nothing learned about a
    variant is thrown away. Mood follows the most authoritative source rather
    than the most-played row, so a hand-made judgement on the quieter variant
    still wins over a centroid guess on the louder one.
    """
    if len(rows) == 1:
        single = dict(rows[0])
        single["identity_aliases"] = _collect_aliases(rows)
        return single

    ordered = sorted(rows, key=lambda r: -int(r.get("play_count") or 0))
    merged = dict(ordered[0])

    merged["play_count"] = sum(int(r.get("play_count") or 0) for r in rows)

    firsts = [r.get("first_scrobbled") for r in rows if r.get("first_scrobbled")]
    lasts = [r.get("last_scrobbled") for r in rows if r.get("last_scrobbled")]
    if firsts:
        merged["first_scrobbled"] = min(firsts)
    if lasts:
        merged["last_scrobbled"] = max(lasts)

    for field in ("genres", "lastfm_tags", "discogs_styles", "lastfm_artist_tags",
                  "enrichment_sources", "playlists"):
        values = _union_lists(rows, field)
        if values:
            merged[field] = values

    # Fill scalar gaps from the other rows rather than losing them.
    for field in ("musicbrainz_id", "isrc", "spotify_id", "apple_music_id",
                  "artist_mbid", "release_year", "duration_ms", "audio_features",
                  "itunes_genre", "apple_music_available"):
        if merged.get(field) in (None, "", []):
            for r in ordered[1:]:
                if r.get(field) not in (None, "", []):
                    merged[field] = r[field]
                    break

    best_mood = max(
        (r for r in rows if r.get("mood_tags")),
        key=lambda r: (
            _SOURCE_RANK.get(r.get("mood_source"), 0),
            _CONFIDENCE_RANK.get(r.get("mood_confidence"), 0),
            int(r.get("play_count") or 0),
        ),
        default=None,
    )
    if best_mood is not None:
        merged["mood_tags"] = list(best_mood["mood_tags"])
        merged["mood_source"] = best_mood.get("mood_source")
        merged["mood_confidence"] = best_mood.get("mood_confidence")

    merged["identity_aliases"] = _collect_aliases(rows)
    merged["canonical_track_id"] = compute_canonical_track_id(merged)
    return merged


def resolve(
    input_path: Path | None = None,
    output_path: Path = OUTPUT_PATH,
    review_path: Path = REVIEW_PATH,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Merge credit variants. Returns ``{total, merged_rows, clusters, output}``."""
    configure_logging(run_log_path)
    log.info("=== Phase 4e: identity resolution ===")

    if input_path is None:
        for candidate in (INPUT_PATH, REPO_ROOT / "tracks_with_genres.jsonl", TRACKS_PATH):
            if candidate.exists():
                input_path = candidate
                break
    if input_path is None or not input_path.exists():
        raise FileNotFoundError("No tracks file found for identity resolution")
    log.info("Input : %s", input_path)

    tracks: list[dict] = []
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))
    log.info("Loaded %d rows", len(tracks))

    clusters, rejected = _cluster(tracks)
    merged_rows = [merge_cluster([tracks[i] for i in sorted(c)]) for c in clusters]
    merged_rows.sort(key=lambda r: (r.get("artist_normalized") or "",
                                    r.get("track_normalized") or ""))

    stale = _deconflict_aliases(merged_rows)
    if stale:
        log.info("Dropped %d stale alias claim(s) now owned by a live row", stale)

    collapsed = len(tracks) - len(merged_rows)
    log.info("Clusters: %d  (collapsed %d rows)", len(clusters), collapsed)

    plays_before = sum(int(t.get("play_count") or 0) for t in tracks)
    plays_after = sum(int(t.get("play_count") or 0) for t in merged_rows)
    if plays_before != plays_after:
        # Merging redistributes plays; it must never create or destroy them.
        log.error("Play count changed in merge: %d → %d", plays_before, plays_after)
        raise ValueError("identity resolution altered total play count")
    log.info("Play total preserved: %d", plays_after)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in merged_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if rejected:
        with open(review_path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rejected:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        log.info("Wrote %d near-miss pairs → %s", len(rejected), review_path)

    log.info("Wrote %d rows → %s", len(merged_rows), output_path)
    return {
        "total": len(tracks),
        "clusters": len(clusters),
        "merged_rows": collapsed,
        "output": len(merged_rows),
    }


if __name__ == "__main__":
    resolve()
    sys.exit(0)
