"""Tests for scripts/build_label_queue.py.

The queue exists because the classifier's honesty has a cost: moods it cannot
predict are left blank. Ranking by play count is what makes closing that gap
cheap — labeling effort should follow listening, not the alphabet.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_label_queue import build_queue, needs_label, to_payload  # noqa: E402


def _track(artist, plays, source, tags=None):
    return {
        "artist": artist,
        "track": "song",
        "artist_normalized": artist.lower(),
        "track_normalized": "song",
        "play_count": plays,
        "mood_tags": tags,
        "mood_source": source,
    }


class TestNeedsLabel:
    def test_owner_labels_are_not_queued(self):
        assert not needs_label(_track("a", 10, "audit", ["Fast"]))
        assert not needs_label(_track("a", 10, "claude_batch", ["Fast"]))

    def test_centroid_guesses_are_queued(self):
        """A guess is exactly what a real judgement should replace."""
        assert needs_label(_track("a", 10, "centroid", ["Slow"]))

    def test_untagged_tracks_are_queued(self):
        assert needs_label(_track("a", 10, None, None))


class TestBuildQueue:
    def test_ordered_by_plays_descending(self):
        tracks = [
            _track("quiet", 1, "centroid"),
            _track("loud", 106, "centroid"),
            _track("middling", 40, None),
        ]
        assert [t["artist"] for t in build_queue(tracks)] == ["loud", "middling", "quiet"]

    def test_owner_labeled_tracks_excluded_however_popular(self):
        tracks = [_track("already-judged", 999, "audit", ["Fast"]),
                  _track("guessed", 2, "centroid", ["Slow"])]
        assert [t["artist"] for t in build_queue(tracks)] == ["guessed"]

    def test_top_limits_the_queue(self):
        tracks = [_track(f"a{i}", i, "centroid") for i in range(20)]
        assert len(build_queue(tracks, top=5)) == 5

    def test_highest_leverage_track_comes_first(self):
        tracks = [_track(f"a{i}", i, "centroid") for i in range(20)]
        assert build_queue(tracks, top=5)[0]["play_count"] == 19

    def test_empty_library_is_safe(self):
        assert build_queue([]) == []


class TestPayload:
    def test_carries_current_guess_for_context(self):
        payload = to_payload(_track("a", 5, "centroid", ["Slow"]))
        assert payload["current_mood_tags"] == ["Slow"]
        assert payload["current_mood_source"] == "centroid"
        assert payload["play_count"] == 5

    def test_join_keys_survive_so_results_can_be_matched_back(self):
        payload = to_payload(_track("Danny Brown", 106, "centroid", ["Slow"]))
        assert payload["artist_normalized"] == "danny brown"
        assert payload["track_normalized"] == "song"
