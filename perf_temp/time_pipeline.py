import time, cProfile, pstats, io, sys
from pathlib import Path
sys.path.insert(0, ".")
from pipeline import config as cfg
import pipeline.dedupe as dedupe
import pipeline.derive_genres as derive_genres
import pipeline.apply_taste_profile as atp
import pipeline.classify_moods as moods

OUT = Path("perf_temp")
TRACKS = cfg.TRACKS_PATH  # real final dataset, used READ-ONLY as input

def bench(name, fn):
    # one run for cProfile, plus timing
    t0 = time.perf_counter(); c0 = time.process_time()
    res = fn()
    wall = (time.perf_counter() - t0) * 1e3
    cpu = (time.process_time() - c0) * 1e3
    print(f"{name:26s} wall={wall:8.1f}ms cpu={cpu:8.1f}ms  -> {res}")

# Phase 2: dedupe (input scrobbles.jsonl -> perf_temp skeleton)
def run_dedupe():
    out = OUT / "out_skeleton.jsonl"
    dedupe.dedupe(scrobbles_path=cfg.SCROBBLES_PATH, output_path=out)
    return f"{sum(1 for _ in open(out))} tracks"

# Phase 4c: derive_genres (feed real tracks.jsonl as input -> perf_temp out)
def run_derive():
    out = OUT / "out_genres.jsonl"
    derive_genres.derive(input_path=TRACKS, output_path=out)
    return f"{sum(1 for _ in open(out))} tracks"

# Phase 7: apply_taste_profile (real taste_profile.md + tracks.jsonl -> perf_temp out)
def run_taste():
    out = OUT / "out_taste.jsonl"
    atp.apply(profile_path=cfg.TASTE_PROFILE_PATH, input_path=TRACKS, output_path=out)
    return f"{sum(1 for _ in open(out))} tracks"

# Phase 6: classify_moods (no audit CSV present -> degraded path; feed tracks.jsonl)
def run_moods():
    out = OUT / "out_moods.jsonl"
    try:
        moods.classify(audit_path=cfg.INPUT_EXISTING_AUDIT, tracks_path=TRACKS, output_path=out)
        return f"{sum(1 for _ in open(out))} tracks (audit_exists={cfg.INPUT_EXISTING_AUDIT.exists()})"
    except Exception as e:
        return f"FAILED: {type(e).__name__}: {e}"

for name, fn in [("dedupe(P2)", run_dedupe), ("derive_genres(P4c)", run_derive),
                 ("apply_taste(P7)", run_taste), ("classify_moods(P6)", run_moods)]:
    try:
        bench(name, fn)
    except Exception as e:
        print(f"{name:26s} FAILED: {type(e).__name__}: {e}")

# cProfile the heaviest of the runnable ones for a frame breakdown
print("\n=== cProfile: apply_taste_profile ===")
pr = cProfile.Profile(); pr.enable(); run_taste(); pr.disable()
pr.dump_stats("perf_temp/taste.prof")
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(10)
print(s.getvalue())
