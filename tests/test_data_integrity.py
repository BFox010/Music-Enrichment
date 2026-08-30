"""Assertions about the committed data, not just the code that produces it.

The suite was green throughout the drift #66 describes: `mood_audit.csv` and
`tracks.jsonl` disagreed about a quarter of the library and nothing failed,
because every other test builds its own fixtures. These read the real files.

Offline and self-contained — both files are git-tracked, so a fresh clone has
them. They are skipped rather than failed when absent, so the suite still runs
somewhere the data has been deliberately removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.classify_moods import _identity_keys, load_audit
from pipeline.config import MOOD_AUDIT_PATH, MOOD_CATEGORIES, TRACKS_PATH
from pipeline.schema import read_jsonl

# Audit rows naming a recording the library does not contain. 43 of them name an
# artist absent from the library entirely — the audit was made against a wider
# source than the scrobble history, so these are expected, not a defect.
#
# A ceiling, not an equality: ingesting more scrobbles can only resolve more of
# them. Growth means either the join regressed or rows were added for tracks
# that were never scrobbled, and both are worth a look.
MAX_UNMATCHED_AUDIT_ROWS = 88


def _load() -> tuple[list[dict], list[dict]]:
    if not MOOD_AUDIT_PATH.exists() or not TRACKS_PATH.exists():
        pytest.skip("committed mood_audit.csv / tracks.jsonl not present")
    return load_audit(MOOD_AUDIT_PATH), read_jsonl(TRACKS_PATH)


def _resolve() -> list[tuple[dict, dict]]:
    """Pair each audit row with the track it names, via key or merged-away alias."""
    audit_rows, tracks = _load()
    index = {(a["artist_normalized"], a["track_normalized"]): a for a in audit_rows}
    pairs = []
    for track in tracks:
        for key in _identity_keys(track):
            if key in index:
                pairs.append((index[key], track))
                break
    return pairs


class TestCommittedAudit:
    """`mood_audit.csv` is the canonical owner label file (#66)."""

    def test_every_resolved_audit_row_keeps_its_labels(self) -> None:
        """Subset, not equality: the bass overlay adds Heavy Bass on top."""
        offenders = [
            (a["artist_normalized"], a["track_normalized"], a["mood_tags"], t.get("mood_tags"))
            for a, t in _resolve()
            if not set(a["mood_tags"]).issubset(set(t.get("mood_tags") or []))
        ]
        assert offenders == [], f"{len(offenders)} audit rows lost labels: {offenders[:5]}"

    def test_no_model_source_on_an_audit_labelled_track(self) -> None:
        """A centroid or claude_batch source here means a model overwrote a hand label."""
        offenders = [
            (a["artist_normalized"], a["track_normalized"], t.get("mood_source"))
            for a, t in _resolve()
            if t.get("mood_source") in ("centroid", "claude_batch")
        ]
        assert offenders == [], f"{len(offenders)} owner labels overwritten: {offenders[:5]}"

    def test_unmatched_audit_rows_do_not_grow(self) -> None:
        audit_rows, _ = _load()
        matched = {(a["artist_normalized"], a["track_normalized"]) for a, _ in _resolve()}
        unmatched = [a for a in audit_rows
                     if (a["artist_normalized"], a["track_normalized"]) not in matched]
        assert len(unmatched) <= MAX_UNMATCHED_AUDIT_ROWS, (
            f"{len(unmatched)} audit rows match no track, up from "
            f"{MAX_UNMATCHED_AUDIT_ROWS}: {[ (a['artist_normalized'], a['track_normalized']) for a in unmatched[:5] ]}"
        )

    def test_audit_labels_are_inside_the_controlled_vocabulary(self) -> None:
        audit_rows, _ = _load()
        unknown = {m for a in audit_rows for m in a["mood_tags"] if m not in MOOD_CATEGORIES}
        assert unknown == set(), f"audit uses moods outside MOOD_CATEGORIES: {unknown}"

    def test_no_unfiltered_noise_tags_on_disk(self) -> None:
        """#69: the filter ran only in Phase 4, so Phase 4d stored artist tags raw.

        Both fields are checked — the point is that every tag field the pipeline
        writes goes through the same filter, not just the one that happened to.
        """
        from pipeline.tag_filter import build_artist_block, is_noise_tag

        _, tracks = _load()
        block = build_artist_block(tracks)
        offenders = [
            (t.get("artist"), field, tag)
            for t in tracks
            for field in ("lastfm_tags", "lastfm_artist_tags")
            for tag in (t.get(field) or [])
            if is_noise_tag(tag, block)
        ]
        assert offenders == [], f"{len(offenders)} unfiltered noise tags: {offenders[:5]}"

    def test_each_audit_row_resolves_to_at_most_one_track(self) -> None:
        """Two tracks answering to one audit key means an identity merge is wrong."""
        seen: dict[tuple[str, str], int] = {}
        for a, _ in _resolve():
            key = (a["artist_normalized"], a["track_normalized"])
            seen[key] = seen.get(key, 0) + 1
        assert [k for k, n in seen.items() if n > 1] == []


class TestIdentifierNormalization:
    """Identifiers are compared as plain strings everywhere — 4e clusters on
    them, `compute_canonical_track_id` keys on them, Phase 5b looks a provider's
    upper-cased answer back up by them. A code that differs only in case is a
    second identity for one recording, and nothing errors: the row just fails to
    cluster and never resolves audio features.

    Seven rows in the committed library carried a lower-case ISRC when this was
    written; they were repaired in the same change.
    """

    def test_every_isrc_is_upper_case_and_stripped(self) -> None:
        if not TRACKS_PATH.exists():
            pytest.skip("committed tracks.jsonl not present")
        offenders = [
            (t["artist"], t["track"], t["isrc"])
            for t in read_jsonl(TRACKS_PATH)
            if isinstance(t.get("isrc"), str) and t["isrc"] != t["isrc"].strip().upper()
        ]
        assert offenders == [], f"{len(offenders)} unnormalized ISRCs: {offenders[:5]}"

    def test_no_two_rows_share_an_isrc(self) -> None:
        """One ISRC is one recording. Two rows holding it means 4e failed to
        cluster them, and their plays are split across both."""
        if not TRACKS_PATH.exists():
            pytest.skip("committed tracks.jsonl not present")
        seen: dict[str, list[str]] = {}
        for t in read_jsonl(TRACKS_PATH):
            if t.get("isrc"):
                seen.setdefault(t["isrc"], []).append(f"{t['artist']} — {t['track']}")
        shared = {k: v for k, v in seen.items() if len(v) > 1}
        assert shared == {}, f"{len(shared)} ISRCs held by more than one row"
