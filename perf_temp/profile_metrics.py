import time, cProfile, pstats, io, sys
sys.path.insert(0, ".")
import app.data as data
import app.metrics as m
import app.query as q

data.load()

calls = [
    ("overview", lambda: m.overview()),
    ("genres", lambda: m.genres()),
    ("moods", lambda: m.moods()),
    ("timeline_year", lambda: m.timeline("year")),
    ("timeline_month", lambda: m.timeline("month")),
    ("time_of_day", lambda: m.time_of_day()),
    ("albums", lambda: m.albums()),
    ("artist_trajectory", lambda: m.artist_trajectory()),
    ("top_items_artists", lambda: m.top_items("artists")),
    ("audio_features", lambda: m.audio_features()),
    ("saturation", lambda: m.saturation()),
    ("forgotten_favorites", lambda: m.forgotten_favorites()),
    ("tag_graph_discogs", lambda: m.tag_graph("discogs_styles")),
    ("tag_graph_moods", lambda: m.tag_graph("mood_tags")),
    ("tag_graph_lastfm", lambda: m.tag_graph("lastfm_tags")),
    ("query_tracks_nofilter", lambda: q.query_tracks(page=1, per_page=50)),
    ("query_tracks_genre", lambda: q.query_tracks(genre="rock", page=1, per_page=50)),
]

print(f"{'endpoint':28s} {'median_ms':>10s}  (best of 7)")
for name, fn in calls:
    ts = []
    for _ in range(7):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e3)
    ts.sort()
    print(f"{name:28s} {ts[len(ts)//2]:10.3f}  min={ts[0]:.3f}")

# cProfile aggregate over one pass of all endpoints
pr = cProfile.Profile()
pr.enable()
for _, fn in calls:
    fn()
pr.disable()
pr.dump_stats("perf_temp/metrics.prof")
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(15)
print("\n=== top cumulative frames (one pass all endpoints) ===")
print(s.getvalue())
