"""Tag Constellation scenes and drill-through (#92): pairing strength, the
deterministic Louvain clustering, the link threshold, and ``tag_detail``.

The fixture has two obvious scenes, three rock tags heard together and three
hip-hop tags heard together, joined by one weak bridge track.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

import app.data as data
from app import metrics


def _track(artist: str, track: str, styles: list[str], genres=None, moods=None) -> dict:
    return {
        "artist": artist, "track": track, "discogs_styles": styles,
        "genres": genres or [], "mood_tags": moods or [],
    }


def _plays(artist: str, track: str, n: int, hour: int = 20) -> list[dict]:
    return [
        {"artist": artist, "track": track, "scrobbled_at": f"2024-01-{i + 1:02d}T{hour:02d}:00:00Z",
         "year": 2024, "month": 1, "day_of_week": 0, "hour": hour, "season": "winter"}
        for i in range(n)
    ]


_TRACKS = [
    _track("Band A", "Riff", ["Indie Rock", "Alt Rock", "Garage"], ["Rock"], ["Fast"]),
    _track("Band B", "Chorus", ["Indie Rock", "Alt Rock"], ["Rock"], ["Uplifting"]),
    _track("Band C", "Fuzz", ["Garage", "Alt Rock"], ["Rock"]),
    _track("MC D", "Bars", ["Trap", "Boom Bap", "Conscious"], ["Hip-Hop"], ["Heavy Bass"]),
    _track("MC E", "Flow", ["Trap", "Boom Bap"], ["Hip-Hop"]),
    _track("MC F", "Verse", ["Conscious", "Boom Bap"], ["Hip-Hop"]),
    # The weak bridge: one track, heard once, carrying a tag from each scene.
    _track("Crossover", "Both", ["Indie Rock", "Trap"], ["Rock", "Hip-Hop"]),
]
_SCROBBLES = (
    _plays("Band A", "Riff", 10) + _plays("Band B", "Chorus", 8) + _plays("Band C", "Fuzz", 6)
    + _plays("MC D", "Bars", 12, hour=23) + _plays("MC E", "Flow", 9, hour=23)
    + _plays("MC F", "Verse", 7, hour=23) + _plays("Crossover", "Both", 1)
)

ROCK = {"Indie Rock", "Alt Rock", "Garage"}
HIPHOP = {"Trap", "Boom Bap", "Conscious"}


@pytest.fixture(scope="module", autouse=True)
def library():
    with tempfile.TemporaryDirectory() as tmp:
        tp, sp = Path(tmp) / "tracks.jsonl", Path(tmp) / "scrobbles.jsonl"
        tp.write_text("".join(json.dumps(t) + "\n" for t in _TRACKS), encoding="utf-8")
        sp.write_text("".join(json.dumps(s) + "\n" for s in _SCROBBLES), encoding="utf-8")
        with data.use_paths(tp, sp):
            yield


def _scene_members(g: dict) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for n in g["nodes"]:
        out.setdefault(n["scene"], set()).add(n["tag"])
    return out


class TestPairStrength:
    def test_is_the_cosine_of_the_two_play_sets(self):
        assert metrics._pair_strength(4, 16, 4) == pytest.approx(4 / math.sqrt(64))

    def test_a_tag_heard_only_alongside_another_is_full_strength(self):
        assert metrics._pair_strength(5, 5, 5) == 1.0

    def test_zero_plays_is_zero_not_a_division_error(self):
        assert metrics._pair_strength(0, 0, 3) == 0.0


class TestLouvain:
    def test_two_disconnected_triangles_are_two_communities(self):
        w = {("a", "b"): 1.0, ("b", "c"): 1.0, ("a", "c"): 1.0,
             ("x", "y"): 1.0, ("y", "z"): 1.0, ("x", "z"): 1.0}
        comm = metrics._louvain(["a", "b", "c", "x", "y", "z"], w)
        assert comm["a"] == comm["b"] == comm["c"]
        assert comm["x"] == comm["y"] == comm["z"]
        assert comm["a"] != comm["x"]

    def test_a_weak_bridge_does_not_merge_two_strong_groups(self):
        w = {("a", "b"): 1.0, ("b", "c"): 1.0, ("a", "c"): 1.0,
             ("x", "y"): 1.0, ("y", "z"): 1.0, ("x", "z"): 1.0, ("c", "x"): 0.05}
        comm = metrics._louvain(["a", "b", "c", "x", "y", "z"], w)
        assert comm["c"] == comm["a"] and comm["x"] == comm["z"] and comm["c"] != comm["x"]

    def test_no_edges_leaves_every_node_alone(self):
        comm = metrics._louvain(["a", "b", "c"], {})
        assert len(set(comm.values())) == 3

    def test_same_graph_same_answer(self):
        w = {("a", "b"): 0.9, ("b", "c"): 0.4, ("c", "d"): 0.9, ("a", "d"): 0.1}
        first = metrics._louvain(["a", "b", "c", "d"], w)
        assert all(metrics._louvain(["a", "b", "c", "d"], w) == first for _ in range(5))


class TestTagGraphScenes:
    def test_the_two_planted_scenes_are_found(self):
        g = metrics.tag_graph(field="discogs_styles", min_count=1)
        assert sorted(_scene_members(g).values(), key=min) == sorted([ROCK, HIPHOP], key=min)

    def test_scenes_are_ranked_by_plays_and_named_after_their_top_two_tags(self):
        g = metrics.tag_graph(field="discogs_styles", min_count=1)
        first, second = g["scenes"]
        # Hip-hop: 12 + 9 + 7 track plays across its tags beats rock's 10 + 8 + 6.
        assert first["plays"] > second["plays"]
        assert first["name"] == "Boom Bap · Trap"
        assert first["tags"] == 3 and second["tags"] == 3
        assert [s["id"] for s in g["scenes"]] == [0, 1]

    def test_every_node_points_at_a_listed_scene(self):
        g = metrics.tag_graph(field="discogs_styles", min_count=1)
        ids = {s["id"] for s in g["scenes"]}
        assert all(n["scene"] in ids for n in g["nodes"])

    def test_edges_carry_strength_between_zero_and_one(self):
        g = metrics.tag_graph(field="discogs_styles", min_count=1)
        assert g["edges"] and all(0 < e["strength"] <= 1 for e in g["edges"])
        assert [e["strength"] for e in g["edges"]] == sorted((e["strength"] for e in g["edges"]), reverse=True)

    def test_the_bridge_is_the_weakest_link(self):
        g = metrics.tag_graph(field="discogs_styles", min_count=1)
        bridge = next(e for e in g["edges"] if {e["source"], e["target"]} == {"Indie Rock", "Trap"})
        assert bridge["strength"] == min(e["strength"] for e in g["edges"])

    def test_min_strength_thins_links_but_never_moves_a_tag_between_scenes(self):
        everything = metrics.tag_graph(field="discogs_styles", min_count=1)
        strong = metrics.tag_graph(field="discogs_styles", min_count=1, min_strength=0.3)
        assert len(strong["edges"]) < len(everything["edges"])
        assert all(e["strength"] >= 0.3 for e in strong["edges"])
        assert strong["nodes"] == everything["nodes"]
        assert strong["scenes"] == everything["scenes"]

    def test_deterministic(self):
        assert metrics.tag_graph(field="discogs_styles", min_count=1) == metrics.tag_graph(
            field="discogs_styles", min_count=1
        )


class TestTagDetail:
    def test_breaks_the_tag_down_the_way_drillpanel_reads_a_slice(self):
        d = metrics.tag_detail("discogs_styles", "Indie Rock")
        assert d["total"] == 10 + 8 + 1
        assert d["tracks"] == {"Band A — Riff": 10, "Band B — Chorus": 8, "Crossover — Both": 1}
        assert d["genres"] == {"Rock": 19, "Hip-Hop": 1}
        assert d["moods"] == {"Fast": 10, "Uplifting": 8}
        assert len(d["byHour"]) == 24 and d["byHour"][20] == 19

    def test_neighbours_are_ranked_by_strength(self):
        d = metrics.tag_detail("discogs_styles", "Indie Rock")
        names = [n["tag"] for n in d["neighbours"]]
        assert names[0] == "Alt Rock"
        assert names[-1] == "Trap"  # the bridge
        strengths = [n["strength"] for n in d["neighbours"]]
        assert strengths == sorted(strengths, reverse=True)

    def test_an_unknown_tag_is_empty_not_an_error(self):
        d = metrics.tag_detail("discogs_styles", "Polka")
        assert d["total"] == 0 and d["tracks"] == {} and d["neighbours"] == []

    def test_an_unknown_field_falls_back_like_tag_graph(self):
        assert metrics.tag_detail("not_a_field", "Trap")["field"] == "discogs_styles"
