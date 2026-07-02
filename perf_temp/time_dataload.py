import time, resource, tracemalloc, statistics, sys
sys.path.insert(0, ".")
import app.data as data

# Warm one parse to populate, then measure repeated full reloads.
def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # KB->MB on linux

rss0 = rss_mb()
walls, cpus = [], []
for i in range(5):
    w0, c0 = time.perf_counter(), time.process_time()
    data.load()
    walls.append(time.perf_counter() - w0)
    cpus.append(time.process_time() - c0)

tracemalloc.start()
data.load()
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
rss1 = rss_mb()

t = data.get_tracks(); s = data.get_scrobbles()
print(f"tracks={len(t)} scrobbles={len(s)}")
print(f"load wall  min/median/max ms = {min(walls)*1e3:.1f} / {statistics.median(walls)*1e3:.1f} / {max(walls)*1e3:.1f}")
print(f"load cpu   min/median/max ms = {min(cpus)*1e3:.1f} / {statistics.median(cpus)*1e3:.1f} / {max(cpus)*1e3:.1f}")
print(f"tracemalloc current/peak MB = {cur/1e6:.1f} / {peak/1e6:.1f}")
print(f"RSS before/after MB = {rss0:.1f} / {rss1:.1f}  (delta {rss1-rss0:.1f})")
