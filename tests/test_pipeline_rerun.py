"""#98: a re-run must process *this* run's rows.

Phases used to choose their own input as "the first of these files that
exists", and those lists included files that later phases, or a previous run,
had written. From the second run on, the chain could reprocess the previous
run's rows: new tracks never reached tracks.jsonl, play counts froze, and every
phase reported OK. The orchestrator now hands each tracks-chain phase the file
its nearest successful predecessor wrote in this run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pipeline import run_full_pipeline as rfp
from pipeline.config import REPO_ROOT


# ── Orchestrator hand-off, with phases stubbed ──


def _run_recording(monkeypatch, tmp_path, statuses=None, **run_kwargs):
    """Run the orchestrator with every phase stubbed; return the input each
    phase was handed. ``statuses`` overrides a phase's outcome (default OK)."""
    statuses = statuses or {}
    monkeypatch.setattr(rfp, "REPO_ROOT", tmp_path)
    # Phase 7's requires_file gate; inputs/exportify.csv stays absent, so 3c skips.
    (tmp_path / "taste_profile.md").write_text("")
    handed: dict[str, Path | None] = {}

    def fake_phase(pid, name, fn, *, optional, outputs=(), **kwargs):
        param = next((kwargs[p] for p in rfp._CHAIN_PARAMS if p in kwargs), None)
        handed[pid] = Path(param).name if param is not None else None
        return statuses.get(pid, rfp.OK)

    monkeypatch.setattr(rfp, "_phase", fake_phase)
    rfp.run(skip_tests=True, skip_pause=True, run_log_path=tmp_path / "run.log", **run_kwargs)
    return handed


class TestEachPhaseReadsThisRunsPredecessor:
    def test_phase_b_output_reaches_phase_4_when_3c_does_not_run(self, monkeypatch, tmp_path):
        # No inputs/exportify.csv under tmp_path, so 3c is SKIPPED at its gate.
        handed = _run_recording(monkeypatch, tmp_path, start_from="2")
        assert handed["B"] == "tracks_with_apple.jsonl"
        assert "3c" not in handed
        assert handed["4"] == "tracks_with_spotify.jsonl"

    def test_a_skipped_optional_phase_is_bridged(self, monkeypatch, tmp_path):
        handed = _run_recording(monkeypatch, tmp_path, statuses={"B": rfp.SKIPPED}, start_from="2")
        assert handed["4"] == "tracks_with_apple.jsonl"

    def test_a_failed_optional_phase_never_feeds_downstream(self, monkeypatch, tmp_path):
        # A previous run's Phase 6 output on disk must not be read.
        (tmp_path / "tracks_with_moods.jsonl").write_text("{}\n")
        handed = _run_recording(monkeypatch, tmp_path, statuses={"6": rfp.FAILED}, start_from="2")
        assert handed["7"] == "tracks_with_features.jsonl"
        assert handed["8"] == "tracks_with_taste.jsonl"

    def test_every_chain_phase_reads_its_manifest_predecessor(self, monkeypatch, tmp_path):
        handed = _run_recording(monkeypatch, tmp_path, start_from="2")
        assert handed == {
            "2": None,  # reads scrobbles.jsonl, not a tracks intermediate
            "A": "tracks_skeleton.jsonl",
            "B": "tracks_with_apple.jsonl",
            "3a": None,  # writes a CSV; not part of the tracks chain
            "4": "tracks_with_spotify.jsonl",
            "4b": "tracks_with_metadata.jsonl",
            "4c": "tracks_with_discogs.jsonl",
            "4d": "tracks_with_genres.jsonl",
            "5a": "tracks_with_genre_backfill.jsonl",
            "4e": "tracks_with_isrcs.jsonl",
            "5": "tracks_resolved.jsonl",
            "5b": "tracks_with_availability.jsonl",
            "6": "tracks_with_features.jsonl",
            "7": "tracks_with_moods.jsonl",
            "8": "tracks_with_taste.jsonl",
        }

    def test_resuming_mid_chain_seeds_from_the_newest_earlier_output(self, monkeypatch, tmp_path):
        import os
        old_discogs = tmp_path / "tracks_with_discogs.jsonl"
        metadata = tmp_path / "tracks_with_metadata.jsonl"
        old_discogs.write_text("{}\n")
        metadata.write_text("{}\n")
        # 4b's file is from an older run than 4's: it must not shadow it.
        os.utime(old_discogs, (1_000_000, 1_000_000))
        handed = _run_recording(monkeypatch, tmp_path, start_from="4c")
        assert handed["4c"] == "tracks_with_metadata.jsonl"


# ── End to end: real phases, HTTP stubbed, two runs ──


_DRIVER = textwrap.dedent('''
    import json, os, plistlib, sys
    from pathlib import Path

    root, mode, fail_phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    sys.path.insert(0, str(root))
    os.chdir(root)
    for k in ("DISCOGS_TOKEN", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        os.environ.pop(k, None)
    os.environ.update(LASTFM_API_KEY="k", LASTFM_USERNAME="u")

    import requests
    def _no_network(*a, **k):
        raise requests.ConnectionError("network disabled in test")
    requests.sessions.Session.request = _no_network
    from pipeline import _http
    _http.RateLimitedClient.get = lambda self, url, params, cache_key, **kw: {"_error": "not_found"}

    def play(track, stamp):
        return {"artist": "Band", "track": track, "album": "LP",
                "artist_normalized": "band", "track_normalized": track.lower(),
                "scrobbled_at": stamp, "year": int(stamp[:4]), "month": int(stamp[5:7]),
                "day_of_week": 0, "hour": 12, "season": "spring"}

    plays = [play("Song One", "2026-03-01T12:00:00Z"), play("Song Two", "2026-03-02T12:00:00Z")]
    if mode == "run2":
        plays += [play("Song One", "2026-03-05T12:00:00Z"), play("Song Three", "2026-03-06T12:00:00Z")]
    (root / "scrobbles.jsonl").write_text("".join(json.dumps(p) + "\\n" for p in plays))
    (root / "inputs").mkdir(exist_ok=True)
    with open(root / "inputs/apple_music_library.xml", "wb") as fh:
        plistlib.dump({"Tracks": {}}, fh)

    if fail_phase:
        import importlib, yaml
        manifest = yaml.safe_load(open(root / "pipeline_manifest.yaml"))
        ph = next(p for p in manifest["phases"] if str(p["id"]) == fail_phase)
        def boom(*a, **k):
            raise RuntimeError("phase " + fail_phase + " exploded")
        setattr(importlib.import_module(ph["module"]), ph["callable"], boom)

    from pipeline.run_full_pipeline import run
    results = run(start_from="2", skip_tests=True, skip_pause=True,
                  run_log_path=root / "runs" / (mode + ".log"))
    rows = [json.loads(l) for l in open(root / "tracks.jsonl")]
    print(json.dumps({"results": results,
                      "tracks": {r["track"]: r.get("play_count") for r in rows}}))
''')


@pytest.fixture
def tree(tmp_path):
    """A throwaway copy of the code and its committed inputs, so the real
    phases can write every intermediate without touching the checkout."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "pipeline", root / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pipeline_manifest.yaml", "mood_audit.csv", "taste_profile.md"):
        shutil.copy(REPO_ROOT / name, root / name)
    (tmp_path / "driver.py").write_text(_DRIVER)
    return root


def _run(tree: Path, mode: str, fail_phase: str = "") -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", str(tree.parent / "driver.py"), str(tree), mode, fail_phase],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


EXPECTED_RUN2 = {"Song One": 2, "Song Two": 1, "Song Three": 1}


class TestTwoRuns:
    def test_run_two_picks_up_new_tracks_and_plays(self, tree):
        first = _run(tree, "run1")
        assert first["tracks"] == {"Song One": 1, "Song Two": 1}
        assert _run(tree, "run2")["tracks"] == EXPECTED_RUN2

    def test_a_stale_intermediate_from_an_old_run_is_not_read(self, tree):
        """Phase 4 preferred any tracks_with_audio.jsonl on disk, so one left by
        an old Exportify run froze the library at that run's rows."""
        _run(tree, "run1")
        shutil.copy(tree / "tracks_with_apple.jsonl", tree / "tracks_with_audio.jsonl")
        assert _run(tree, "run2")["tracks"] == EXPECTED_RUN2

    def test_a_failing_optional_phase_cannot_feed_last_runs_file_downstream(self, tree):
        """With Phase 6 failing, Phase 7 used to read run 1's
        tracks_with_moods.jsonl, and Phase 8 wrote it over the new library."""
        _run(tree, "run1")
        second = _run(tree, "run2", fail_phase="6")
        assert second["results"]["6"] == "failed"
        assert second["tracks"] == EXPECTED_RUN2
