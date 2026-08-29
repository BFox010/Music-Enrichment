"""Phase 4e — merge recording identity across artist-credit variants.

Last.fm reports whatever credit the scrobbling client sent, so one recording
arrives under several names ("Clipse" vs "Clipse, Pharrell Williams, Pusha T &
Malice"). Downstream phases key on ``(artist_normalized, track_normalized)``, so
those become separate rows: enriched separately, classified separately (Slow on
one, not-Slow on the other), plays split between them.

Merges on strong evidence only, recording merged keys as ``identity_aliases``.
``scrobbles.jsonl`` is never rewritten — the aggregation layer resolves an alias
to its canonical row at read time.

Runs after enrichment, not after dedupe: the decisive evidence (ISRC, MusicBrainz
recording ID) doesn't exist until Phase 4/5a have fetched it. This is also where
``canonical_track_id`` is set, on a durable identifier rather than a normalized
name that moves whenever normalization improves.

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
    MOOD_SOURCE_RANK,
    REPO_ROOT,
    TRACKS_PATH,
    TRACKS_RESOLVED_PATH,
    TRACKS_WITH_GENRE_BACKFILL_PATH,
    TRACKS_WITH_ISRCS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.name_variations import strip_feat
from pipeline.normalize import normalize_track
from pipeline.schema import atomic_open, compute_canonical_track_id

log = get_logger(__name__)

INPUT_PATH: Path = TRACKS_WITH_ISRCS_PATH
OUTPUT_PATH: Path = TRACKS_RESOLVED_PATH

# Pairs the name test proposed but that were rejected, written out so the
# owner can eyeball what the conservative rule is declining to merge.
REVIEW_PATH: Path = REPO_ROOT / "identity_review.jsonl"

# Shortest artist name allowed to act as a prefix. Guards against a two-letter
# stage name swallowing unrelated artists that merely start with those letters.
MIN_PREFIX_LEN: int = 3

# Confidence ordering when two merged rows disagree about mood.
_CONFIDENCE_RANK: dict[str | None, int] = {"high": 3, "medium": 2, "low": 1, None: 0}


def _name_key(row: dict) -> tuple[str, str]:
    return (row.get("artist_normalized") or "", row.get("track_normalized") or "")


def _identity_title(row: dict) -> str:
    """Feat-stripped normalized title — "same recording" identity.

    Not ``track_normalized``, which keeps the guest credit (display identity).
    A guest credit that moved into the *title* ("FML" vs "FML (feat. The Weeknd)")
    leaves ``artist_normalized`` identical, so the title is the only place to strip it.
    """
    return normalize_track(strip_feat(row.get("track") or ""))


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


def _shape_matches(a: dict, b: dict) -> bool:
    """Do two rows *look* like the same recording under different credits?

    Matches on ``_identity_title``. Two accepted artist shapes:
    - word-boundary prefix ("clipse" ⊂ "clipse pharrell williams pusha t malice")
      — a featured-artist expansion
    - identical artists, differing titles — a feat-in-title split

    Ignores MBID/ISRC deliberately: those are a separate veto in the caller, so a
    conflict there stays distinguishable from "never looked like a variant".
    """
    title_a, title_b = _identity_title(a), _identity_title(b)
    if not title_a or title_a != title_b:
        return False

    x, y = a.get("artist_normalized") or "", b.get("artist_normalized") or ""
    if not x or not y:
        return False
    if x == y:
        # Same artist: only a variant if the raw titles actually differ
        # (otherwise this is a literal duplicate, not a credit variant).
        return a.get("track_normalized") != b.get("track_normalized")
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) < MIN_PREFIX_LEN:
        return False
    # Word boundary, so "the doors" never absorbs "the doors tribute".
    return long.startswith(short) and long[len(short)] == " "


def _conflicts(a: dict, b: dict, field: str) -> bool:
    va, vb = a.get(field), b.get(field)
    return bool(va and vb and va != vb)


# Sources whose ISRC is too fuzzy to veto a shape match. Deezer's comes from a
# name search: two title variants of one recording routinely land on different
# releases (disagreed with MusicBrainz on 8.7% of a 150-track sample), and letting
# that veto splits pairs the credit evidence got right.
# Unknown provenance counts as trusted — a wrong veto leaves a visible duplicate,
# a wrong merge silently folds two recordings and sums their plays.
_FUZZY_ISRC_SOURCES = frozenset({"deezer"})


def _isrc_vetoes_merge(a: dict, b: dict) -> bool:
    if not _conflicts(a, b, "isrc"):
        return False
    return not (
        a.get("isrc_source") in _FUZZY_ISRC_SOURCES
        or b.get("isrc_source") in _FUZZY_ISRC_SOURCES
    )


def is_credit_variant(a: dict, b: dict) -> bool:
    """``_shape_matches`` plus no decisive conflicting identifier.

    Conflicting ISRC from an exact join ⇒ two different recordings sharing a
    title. A Deezer ISRC lacks that weight; see ``_isrc_vetoes_merge``.

    A conflicting MBID is weaker — Last.fm hands out different recording MBIDs
    for one recording under different credit strings — so it depends on which
    credit changed:
    - same artist, guest only in the title: Last.fm never saw a different artist
      string, so the conflict is noise. Merge anyway.
    - different artist strings (Clipse-style expansion): the credit itself moved,
      so the conflict is informative. Caller routes it to identity_review.jsonl.
    """
    if not _shape_matches(a, b):
        return False
    if _isrc_vetoes_merge(a, b):
        return False
    x, y = a.get("artist_normalized") or "", b.get("artist_normalized") or ""
    if x != y and _conflicts(a, b, "musicbrainz_id"):
        return False
    return True


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

    # Then name-shape evidence, compared only within the same feat-stripped
    # title — this is what lets a guest credit that moved into the *title*
    # ("FML" vs "FML (feat. The Weeknd)") ever meet its pair at all.
    by_title: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(tracks):
        title = _identity_title(t)
        if title:
            by_title[title].append(i)

    rejected: list[dict] = []
    for members in by_title.values():
        if len(members) < 2:
            continue
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                ta, tb = tracks[i], tracks[j]
                if is_credit_variant(ta, tb):
                    uf.union(i, j)
                    continue
                if _shape_matches(ta, tb) and not _isrc_vetoes_merge(ta, tb):
                    # Everything about the credits lines up; only a
                    # disagreeing MBID stopped the merge. Demoted from a veto
                    # to a tiebreak — surfaced for the owner rather than
                    # merged blind.
                    reason = "same title, credit shape matches, but MusicBrainz IDs conflict"
                elif ta.get("artist_normalized") != tb.get("artist_normalized"):
                    reason = "same title, credits not a prefix variant"
                else:
                    continue
                rejected.append({
                    "track": ta.get("track"),
                    "artist_a": ta.get("artist"),
                    "artist_b": tb.get("artist"),
                    "plays_a": ta.get("play_count"),
                    "plays_b": tb.get("play_count"),
                    "reason": reason,
                })

    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tracks)):
        grouped[uf.find(i)].append(i)
    return list(grouped.values()), rejected


def _collect_aliases(rows: list[dict]) -> list[list[str]]:
    """Every name this cluster has been scrobbled under: own keys + inherited aliases.

    Inherited ones matter on re-runs — an absorbed row no longer exists to contribute
    its own name, so dropping its alias orphans every play logged under it.
    """
    aliases: list[list[str]] = []
    for r in rows:
        for key in [list(_name_key(r))] + [list(a) for a in (r.get("identity_aliases") or [])]:
            if key not in aliases:
                aliases.append(key)
    return aliases


def _deconflict_aliases(rows: list[dict]) -> int:
    """Ensure every name is claimed by exactly one row. Returns drops made.

    Contested two ways, both seen in practice: a live row owns the name outright
    (fresh metadata split a previously-merged pair), or two rows inherited the same
    alias from a row each absorbed at some point. Either way the aggregation layer
    would route one row's plays to the other.

    Awarded to the row whose own title matches, then most-played, then alphabetical
    (deterministic).
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


# Fields whose provenance must travel with them when merge_cluster fills a gap
# from another row in the cluster.
_GAP_FILL_PROVENANCE: dict[str, tuple[str, ...]] = {
    "isrc": ("isrc_source", "isrc_retrieved_at"),
    "apple_music_available": ("apple_music_checked_at",),
}


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
        # Singletons get an id too. Leaving them blank defers to fill_defaults()
        # at Phase 8, which derives it from fields several phases have since
        # changed. This is the phase that decides identity; it must stamp every row.
        single["canonical_track_id"] = compute_canonical_track_id(single)
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

    # Fill scalar gaps from the other rows rather than losing them. A field with
    # its own provenance travels with it: an ISRC taken from another row keeps
    # that row's isrc_source, or the next run reads a Deezer name-search ISRC as
    # one of unknown (hence trusted) provenance and lets it veto a merge this
    # run made — splitting the cluster back apart and the plays with it.
    for field in ("musicbrainz_id", "isrc", "spotify_id", "apple_music_id",
                  "artist_mbid", "release_year", "duration_ms", "explicit",
                  "audio_features", "itunes_genre", "apple_music_available",
                  "album", "lastfm_listeners", "lastfm_playcount"):
        if merged.get(field) in (None, "", []):
            for r in ordered[1:]:
                if r.get(field) not in (None, "", []):
                    merged[field] = r[field]
                    for companion in _GAP_FILL_PROVENANCE.get(field, ()):
                        merged[companion] = r.get(companion)
                    break

    best_mood = max(
        (r for r in rows if r.get("mood_tags")),
        key=lambda r: (
            MOOD_SOURCE_RANK.get(r.get("mood_source"), 0),
            _CONFIDENCE_RANK.get(r.get("mood_confidence"), 0),
            int(r.get("play_count") or 0),
        ),
        default=None,
    )
    if best_mood is not None:
        merged["mood_tags"] = list(best_mood["mood_tags"])
        merged["mood_source"] = best_mood.get("mood_source")
        merged["mood_confidence"] = best_mood.get("mood_confidence")
        # mood_distance belongs to the same bundle — leaving the most-played
        # row's distance beside another row's tags describes a fit that was
        # never measured.
        merged["mood_distance"] = best_mood.get("mood_distance")

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

    with atomic_open(output_path) as fh:
        for row in merged_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if rejected:
        with atomic_open(review_path) as fh:
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
