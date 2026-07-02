# Performance Footprint Map — Music Enrichment Pipeline

> **Profiling only — no fixes.** This document maps and quantifies the load-time and
> compute footprint of every section of the codebase. No application code was modified.
> Bottlenecks are recorded as **Observations** only.

**Confidence legend:** ✅ measured (real run) · ⚠️ estimated / derived (static or
extrapolated) · 🔴 could not measure (reason given).

> **Update (2026-06-08): a frontend load-time optimization sweep was implemented after
> the initial profiling.** The original report below documents the *pre-optimization*
> state. See [Frontend load optimization](#frontend-load-optimization-implemented) for
> the before/after results — this section supersedes the frontend observations below.

## Frontend load optimization (implemented)

**Goal:** reduce "time to see page". Measured with headless Chrome (Puppeteer) against
a uvicorn-served instance; CDN assets served from local mirrors so CPU costs (transpile,
mount, parse, render) are faithful — network download time (~0 on localhost) is reported
separately as gzipped bytes. Harnesses: `perf_temp/measure_load.js`, `build_frontend.mjs`.

### What changed
1. **Dropped in-browser Babel; added an esbuild build step** (`scripts/build_frontend.mjs`,
   `npm run build` / `npm run dev` watch). The 5 `.jsx` are pre-compiled to one minified
   `web/app.bundle.js` (101 KB / 27 KB gz). Removes the ~3 MB Babel download + 373 ms
   per-load transpile.
2. **Production React builds** (`react.production.min.js` / `react-dom.production.min.js`):
   dev 1.16 MB → prod 138 KB raw (228 KB → 41 KB gz for ReactDOM).
3. **ECharts lazy-loaded** — removed from `index.html` entirely; a singleton
   `ensureECharts()` injects the ~1 MB library on demand. The dashboard prefetches it
   during idle time after first paint and loads it immediately on first chart-view open,
   so it no longer competes with the initial data fetch and never blocks first paint.
   `useEChart` is resilient (waits for `window.echarts`), so charts still render. Verified:
   ECharts requested exactly once, ~686 ms in (after the ~160 ms FCP); all charts render.
4. All scripts `defer`; data transfer was already gzipped (see correction in Observations).

### Results (headless, instant local network — CPU-bound costs)

| Metric | Before (Babel) | After (esbuild + prod React) | Change | Conf |
|---|---|---|---|---|
| First Contentful Paint | **~5,000 ms** | **~155 ms** | **~32× faster** | ✅ |
| Library data visible | ~5,650 ms | ~880 ms | ~6.4× faster | ✅ |
| Chart tab open ("click load") | n/a (broke in headless) | ~64 ms | renders correctly | ✅ |
| Page navigation (view switch) | ~45 ms | ~45 ms | unchanged (already instant) | ✅ |
| Critical-path JS, gzipped | **~1,273 KB** (react-dom-dev 228 + babel 649 + echarts 327 + jsx ~42 + react-dev 27) | **~72 KB** (react 4 + react-dom 41 + app 27) | **~18× smaller** + 373 ms transpile gone; ECharts 327 KB gz now lazy | ✅ |

> Headless CPU differs from a real device, but the before/after is a consistent yardstick.
> On a real network the *additional* win is larger: the before-state also paid CDN
> download of ~1.3 MB gz of render-blocking JS before anything appeared.

### Files changed by the sweep
`web/index.html` (script tags), `web/echarts-charts.jsx` (`useEChart` resilient init),
`web/app.bundle.js` + `.map` (generated), `package.json` + `scripts/build_frontend.mjs`
(new build tooling), `.gitignore` (`node_modules/`). Build: `npm install && npm run build`.

### Further optional wins (not done)
- Self-host / subset Google Fonts (currently 5 render-blocking families).
- Code-split per view if the bundle grows.

*(Done — lazy ECharts: implemented as on-demand + idle prefetch; see item 3 above.)*

## Measurement environment

| Item | Value |
|---|---|
| Host | Linux 6.18.5, Python 3.11.15, Node v22.22.2 |
| Deps | Installed fresh from `requirements.txt` for this run (fastapi 0.136, uvicorn 0.49, httpx 0.28, pandas 3.0, pytest 9.0). ✅ |
| Dataset | `tracks.jsonl` = 4.32 MB / 2,730 records · `scrobbles.jsonl` = 3.86 MB / 13,669 records (the real committed data) |
| Credentials | **None** — `LASTFM_API_KEY`/`DISCOGS_TOKEN` unset, no `.cache/`, no `inputs/` files |
| Method | `python -X importtime`, `cProfile`, `tracemalloc`/`getrusage`, throwaway timing harnesses, Node V8 micro-bench. All scratch artifacts live in `perf_temp/`. |

All timings are best-of-N on a warm filesystem; treat absolute ms as representative of
*this* dataset (≈2.7k tracks). Costs scale with the complexity column, not the absolute
number, for larger libraries.

---

## 1. Component table

### Backend — Flask/API layer (FastAPI, `app/`)

| Section | Layer | Load cost | Compute (this dataset) | I/O & network | Memory | Complexity | Conf |
|---|---|---|---|---|---|---|---|
| `app/main.py` cold start (import + import-time `data.load()` @ line 38) | backend | **668 ms median** (636–795) full process import | — (startup only) | reads both JSONL at import (blocking) | RSS → ~142 MB | O(N+S) load | ✅ |
| ↳ `import fastapi` portion | backend | **286 ms** (cumulative 451 ms incl. app deps) | — | none | — | — | ✅ |
| `app/data.py` `load()` (parse both JSONL) | backend | 33.5 ms import | **123 ms** wall=cpu (107–134) | read 8.2 MB from disk | **42 MB** parsed objects (tracemalloc peak) | O(N+S) | ✅ |
| `app/metrics.py` `forgotten_favorites()` | backend | — | **21.8 ms** | in-mem | — | O(N·S/?)+sort; `_key` called 16,399× | ✅ |
| `app/metrics.py` `time_of_day()` | backend | — | 9.5 ms | in-mem | — | O(S) | ✅ |
| `app/metrics.py` `artist_trajectory()` | backend | — | 8.6 ms | in-mem | — | O(N·A) group+sort | ✅ |
| `app/metrics.py` `audio_features()` | backend | — | 7.7 ms | in-mem | — | O(N) + histograms | ✅ |
| `app/metrics.py` `overview()` | backend | 32.8 ms import | 7.5 ms | in-mem | — | O(N·F) coverage fields | ✅ |
| `app/metrics.py` `tag_graph()` (lastfm/moods/discogs) | backend | — | 5.3–7.4 ms each | in-mem | — | O(N·T²) co-occurrence | ✅ |
| `app/metrics.py` `albums()` | backend | — | 6.9 ms | in-mem | — | O(N) group | ✅ |
| `app/metrics.py` `timeline()` (month/year) | backend | — | 4.0 / 6.4 ms | in-mem | — | O(S) | ✅ |
| `app/metrics.py` `genres()` / `moods()` / `top_items()` / `saturation()` | backend | — | 0.8–1.8 ms | in-mem | — | O(N·G) Counter | ✅ |
| `app/query.py` `query_tracks()` (filter+paginate) | backend | 31 ms import | 0.16 ms (no filter) – 2.0 ms (genre) | in-mem | — | O(N·Filters) linear scan | ✅ |
| `app/refresh.py` import | backend | 141 ms import | runtime = pipeline P2–8 + sync (see below) | drives external APIs | — | — | ✅ import / 🔴 run |
| `app/lastfm_sync.py` import (httpx) | backend | 112 ms import | ⚠️ ~14 s full history (69 pages @ 5 req/s) | Last.fm `user.getRecentTracks`, 200/page | — | O(pages) | ✅ import / 🔴 run (no creds) |

### Backend — Pipeline stages (`pipeline/`)

| Stage (phase) | Layer | Load cost | Compute | I/O & network | Memory | Complexity | Conf |
|---|---|---|---|---|---|---|---|
| `dedupe` (P2) | backend | 29 ms import | **144 ms** wall | read `scrobbles.jsonl`, write skeleton | ~ dataset | O(S log S) group+sort | ✅ |
| `derive_genres` (P4c) | backend | 27 ms import | **128 ms** wall | read/write JSONL | ~ dataset | O(N·T) tag→genre map | ✅ |
| `apply_taste_profile` (P7) | backend | 32 ms import | **152 ms** wall | read `taste_profile.md` (112 KB) + JSONL | ~ dataset | O(N+M) parse+match | ✅ |
| `classify_moods` (P6) | backend | 34 ms import | **176 ms** wall (degraded path) | read/write JSONL | ~ dataset | O(N + T·D), D=9 | ⚠️ ran with **no audit CSV** → 0 centroids, all 2,730 = `no_match`; the centroid math path was not exercised |
| `enrich_metadata` (P4) | backend | 114 ms import (requests) | ⚠️ **~9–15 min** cold | Last.fm `track.getInfo` @ **5 req/s**, 1–4 calls/track (name variations), disk cache | streams | O(N·R) rate-bound | 🔴 no API key / no cache |
| `enrich_discogs` (P4b) | backend | 107 ms import | ⚠️ **~26 min** cold (1,578 unique albums @ 1 req/s) | Discogs search @ **1 req/s**, album-keyed cache | streams | O(albums) rate-bound | 🔴 no token / no cache |
| `check_apple_music` (P5) | backend | — | ⚠️ **~2.3 h** cold (2,730 @ 0.33 req/s) | iTunes Search @ **0.33 req/s**, 90-day cache | streams | O(N) rate-bound | 🔴 no cache (network) |
| `ingest_scrobbles` (P1) | backend | — | ⚠️ O(records) parse | needs `inputs/lastfm_export.json` | — | O(S log S) | 🔴 no input file |
| `enrich_apple_library` (PA) | backend | — | ⚠️ O(N+M) plist parse+merge | needs `inputs/apple_music_library.xml` | — | O(N+M) | 🔴 no input file |
| `merge_exportify` (P3c) | backend | — | ⚠️ O(N) CSV merge | needs `inputs/exportify.csv` | — | O(N) | 🔴 no input file |
| `run_full_pipeline` orchestrator | backend | 57 ms import (loads manifest @ import) | sum of phases | — | — | — | ✅ import |
| `_http.RateLimitedClient` | backend | 153 ms import | per-call `sleep(1/rate − elapsed)` + ≤5 retries, exp-backoff base 0.5 s cap 30 s | disk-backed JSON cache | cache in RAM | — | ✅ static |

### Frontend (`web/`) — browser React SPA, in-browser Babel

| Section | Layer | Load cost | Compute | I/O & network | Memory | Complexity | Conf |
|---|---|---|---|---|---|---|---|
| CDN: React 18.3.1 **dev** | frontend | ⚠️ ~350 KB download+parse | — | unpkg | — | — | ⚠️ |
| CDN: ReactDOM 18.3.1 **dev** | frontend | ⚠️ ~1 MB download+parse | — | unpkg | — | — | ⚠️ |
| CDN: `@babel/standalone` 7.29 | frontend | ⚠️ ~1.5 MB download+parse | — | unpkg | — | — | ⚠️ |
| CDN: ECharts 5 (min) | frontend | ⚠️ ~1 MB download+parse | chart init/layout | jsdelivr | — | — | ⚠️ |
| Google Fonts (5 families) | frontend | ⚠️ render-blocking CSS + WOFF2 | — | fonts.googleapis | — | — | ⚠️ |
| **In-browser Babel transform of 5 `.jsx`** (147 KB) | frontend | **373 ms** (V8): dashboard 204, echarts 90, charts 38, tweaks 24, explorer 18 | on every page load | — | — | O(source size) | ✅ (Node V8; browser comparable) |
| Local JSX/JS/CSS source | frontend | 244 KB total: `themes.css` 56K, `dashboard.jsx` 51K, `echarts-charts.jsx` 44K, `tweaks-panel.jsx` 24K, `charts.jsx` 21K, `app.css` 11K, `app.js` 9.3K, `explorer.jsx` 8K, `js/charts/*` ≤3.8K | — | same-origin | — | — | ✅ bytes |
| Dataset fetch `tracks.jsonl`+`scrobbles.jsonl` | frontend | **~975 KB gzip on wire** (511 KB + 450 KB; 8.17 MB raw, 8.4×) — GZipMiddleware active, verified | — | 2 same-origin fetches (or `/api`) | — | O(N+S) | ✅ measured |
| Client JSONL parse (`parseJSONL`) | frontend | — | **~80–100 ms** (V8) | — | heap ~34 MB | O(N+S) | ✅ |
| Client aggregation (`buildPlayWindows`+`buildDrill`) | frontend | — | **~36 ms** (V8) | — | included above | `buildDrill` ≈ O(scrobbles × tracks) cross-tab | ✅ (drill is the heaviest client path) |
| `web/data/library.js` | frontend | 474 B placeholder | — | — | — | — | ✅ |

---

## 2. Ranked heaviest paths

### A. Startup / load cost (one-time)

**Backend (process import):**
1. **`app/main.py` cold start — 668 ms** ✅ (fastapi import 286 ms + import-time `data.load()` 123 ms + remaining app deps). The data load is **synchronous at import** (`app/main.py:38`).
2. `pipeline/_http` import — 153 ms ✅ (pulls `requests`); shared by all API phases.
3. `app/refresh` 141 ms · `enrich_metadata` 114 ms · `lastfm_sync` 112 ms · `enrich_discogs` 107 ms ✅.
4. Stdlib-only modules (`metrics`/`query`/`data`/pipeline pure stages) — 27–37 ms each ✅ (bare interpreter baseline ≈ 2.5 ms).

**Frontend (time-to-interactive chain), heaviest first:**
1. **CDN payload ≈ 3.85 MB** ⚠️ (Babel 1.5 MB + ReactDOM-dev ~1 MB + ECharts ~1 MB + React-dev 350 KB) — download+parse before app runs.
2. **Dataset fetch 8.17 MB raw** ✅ (⚠️ ~975 KB if gzip enabled).
3. **In-browser Babel transform — 373 ms** ✅ (blocks first render; `dashboard.jsx` alone 204 ms).
4. **Client JSONL parse ~80–100 ms** + **aggregation ~36 ms** ✅ (synchronous on main thread).
5. ECharts init for up to 6 charts — ⚠️ ~100–300 ms (no browser run).

### B. Per-run compute cost

**Pipeline run (full, cold cache) — dominated by rate-limited network, not CPU:**
1. **`check_apple_music` (P5) — ⚠️ ~2.3 h** cold (0.33 req/s × 2,730). 🔴
2. **`enrich_discogs` (P4b) — ⚠️ ~26 min** cold (1 req/s × 1,578 albums). 🔴
3. **`enrich_metadata` (P4) — ⚠️ ~9–15 min** cold (5 req/s × 2,730 + variations). 🔴
4. CPU-only stages are negligible by comparison: `classify_moods` 176 ms ⚠️, `apply_taste_profile` 152 ms ✅, `dedupe` 144 ms ✅, `derive_genres` 128 ms ✅.

> The pipeline's per-run cost is **~99% rate-limit wait**, not computation. Warm caches collapse P4/P4b/P5 to near-zero (P5 has a 90-day cache).

**API server per-request compute (warm, in-memory):** every endpoint is **< 22 ms** ✅.
Ranked: `forgotten_favorites` 21.8 > `time_of_day` 9.5 > `artist_trajectory` 8.6 >
`audio_features` 7.7 > `overview` 7.5 > `tag_graph(lastfm)` 7.4 > `albums` 6.9 >
`timeline(month)` 6.4 > … > `query_tracks(no filter)` 0.16 ms.

---

## 3. Load-order / dependency map

```
SERVER STARTUP  (uvicorn app.main:app)
  import app.main            (668 ms total cold)
    ├─ import fastapi        (286 ms)  ── pulls pydantic, starlette, anyio
    ├─ import app.data       → at MODULE LEVEL calls _load_jsonl(tracks.jsonl)
    │                          and _load_jsonl(scrobbles.jsonl)  ← 123 ms BLOCKING
    │                          populates globals _tracks, _scrobbles (~42 MB)
    ├─ import app.metrics    (reads via app.data.get_tracks/get_scrobbles)
    ├─ import app.query
    └─ routes → app.refresh ─→ app.lastfm_sync (httpx)  +  pipeline.run_full_pipeline

PIPELINE STARTUP  (python -m pipeline.run_full_pipeline)
  import pipeline.run_full_pipeline
    └─ at MODULE LEVEL: load_manifest()  ← parses pipeline_manifest.yaml (14 phases)
  each phase imported dynamically via importlib in manifest order:
    1 ingest → 2 dedupe → A apple_library → 3c merge_exportify → 4 enrich_metadata
    → 4b enrich_discogs → 4c derive_genres → 4d genre_backfill → 5 check_apple_music
    → 6 classify_moods → 7 apply_taste_profile → 8 update_tracks
  shared infra: pipeline.config (paths, rate limits), pipeline._http (RateLimitedClient),
                pipeline.normalize / name_variations (hot inner helpers)

FRONTEND LOAD  (web/index.html)
  <link> themes.css + Google Fonts (render-blocking)
  <script> React-dev → ReactDOM-dev → @babel/standalone → data/library.js → ECharts
  Babel transforms 5 .jsx in-browser (373 ms) → React mounts <App>
    └─ effect: fetch tracks.jsonl + scrobbles.jsonl → parseJSONL (~90 ms)
       → processLibrary (buildPlayWindows + buildDrill ~36 ms) → setState → render
       → per-tab charts fetch /api/* (each ECharts component fetches on mount)
```

**Import-time side effects (work done merely by importing):**
- `app/data.py` — reads + parses both JSONL files (123 ms, 42 MB). *Heaviest import side effect.*
- `app/main.py:38` — calls `data.load()` at import.
- `pipeline/run_full_pipeline.py` — `load_manifest()` (YAML parse) at import.
- `pipeline/config.py` — `.env`/logging config (cheap).

---

## 4. Not measured / needs data

| Item | Why blocked | What would unblock |
|---|---|---|
| `enrich_metadata` (P4) real run | No `LASTFM_API_KEY`, no `.cache/lastfm.json` | Valid Last.fm key + network → run on a small slice |
| `enrich_discogs` (P4b) real run | No `DISCOGS_TOKEN`, no `.cache/discogs.json` | Discogs token + network |
| `check_apple_music` (P5) real run | No `.cache/apple_music.json`; iTunes API needs outbound network | Network egress to `itunes.apple.com` |
| `lastfm_sync` / `/api/lastfm/sync` / `/api/refresh` | Needs `LASTFM_API_KEY` + `LASTFM_USERNAME` | Credentials + network |
| `ingest_scrobbles` (P1) | Missing `inputs/lastfm_export.json` | Provide a raw Last.fm export |
| `enrich_apple_library` (PA) | Missing `inputs/apple_music_library.xml` | Provide an iTunes Library XML export |
| `merge_exportify` (P3c) | Missing `inputs/exportify.csv` | Provide an Exportify CSV |
| `classify_moods` centroid path | Ran but `inputs/existing_audit.csv` absent → 0 training rows, all `no_match`; the z-norm/centroid/Euclidean code (`compute_centroids`, `classify_track`) was **not exercised** | Provide the audit CSV → re-time the real classification path |
| Real-browser TTI, CDN download latency, ECharts init, React render | No headless browser in this environment | Run Lighthouse / a headless Chromium against a served instance. (Babel transform + JSONL parse + aggregation **were** measured via Node V8 as proxies.) |
| pandas-based scripts (`scripts/library_stats.py`, etc.) | Not on a pipeline hot path; not profiled | Run directly if needed |

---

## 5. Observations (no fixes applied — flagged only)

1. **Import-time blocking data load.** `app/data.py` parses both JSONL files (123 ms,
   42 MB) at *module import*, triggered by `app/main.py:38`. Server cold start is
   ~668 ms, of which the load is synchronous and blocks the event loop before the app
   is ready. (Observation only.)
2. **Frontend ships development React builds + in-browser Babel.** React-dev/ReactDOM-dev
   (~1.35 MB) and `@babel/standalone` (~1.5 MB) are downloaded on every load, and 5
   `.jsx` files are transformed in-browser (373 ms measured). This is the dominant TTI
   cost alongside the ~3.85 MB CDN payload. (Observation only.)
3. **Dataset transfer is already gzipped.** *(Correction — an earlier draft wrongly
   implied the raw files were uncompressed.)* FastAPI's `GZipMiddleware` compresses the
   raw `/tracks.jsonl` and `/scrobbles.jsonl` routes too — verified on the wire:
   `tracks.jsonl` ships as **511 KB** (vs 4.32 MB raw, 8.4×) with
   `content-encoding: gzip`. ✅ measured. So data transfer is **not** the load-time
   bottleneck; the JS/CDN bootstrap is.
4. **`buildDrill` is an O(scrobbles × tracks) client-side cross-tab** — cheap at this
   dataset (~36 ms) but the steepest-scaling client path. (Observation only.)
5. **Pipeline per-run cost is ~99% rate-limit wait** (P4/P4b/P5), not CPU. The
   disk-backed cache in `pipeline/_http.py` is what keeps re-runs fast; a cold run with
   no cache is hours. (Observation only.)
6. **API endpoints recompute aggregations per request** over the full in-memory lists
   with no precompute/index — fine at 2.7k tracks (<22 ms) but O(N)–O(N·T²) per call.
   (Observation only.)

---

*Reproduce:* harness scripts and raw outputs are under `perf_temp/`
(`time_dataload.py`, `profile_metrics.py` + `metrics.prof`, `time_pipeline.py` +
`taste.prof`, `fe_parse_bench.js`, `babel_bench.js`, `it_*.log` importtime traces).
*Caveat:* JS timings use Node V8 as a browser proxy; absolute backend ms reflect the
committed ~2.7k-track dataset and scale by the complexity column, not the raw count.
