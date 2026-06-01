# Handoff — Frontend Phase, Slice 1 (Dashboard Foundation)

> **Canonical, versioned handoff for the Frontend phase.** Lives here (not at the
> repo-root `HANDOFF.md`, which `.gitignore` keeps local-only) so it travels to
> remote/mobile Claude Code sessions. The planning-mode plan file
> (`~/.claude/plans/…`) is local-only and does NOT travel — this file embeds it.

**Branch:** `feat/dashboard-foundation` (branched from `chore/ci-and-readme`, which carries the new CI workflow).
**Date handed off:** 2026-05-31.

---

## Where the project stands

- **Backend phase = done.** The enrichment pipeline produces `tracks.jsonl` (2,730 tracks, 43 fields; genres 99%, moods 98%, audio-features 81%) and `scrobbles.jsonl` (~3,700 plays, each with `scrobbled_at`, `year`, `month`, `day_of_week`, `hour`, `season`). Both are git-tracked.
- **Now starting the Frontend phase.** Core goal of the whole project: a personalized, **natural-language-prompted playlist builder** that pushes to Spotify/Apple, fronted by a polished **HTML dashboard** of metrics.

## Key architectural decision (locked)

**JSONL stays the git-tracked source of truth** (`tracks.jsonl` + the human-edited `taste_profile.md`). We do **not** migrate storage to a DB. Instead we add a **serving layer** in front for interactive reads (dashboard now, builder later). The pipeline keeps its invariant: *markdown is truth, JSONL is the derived index, regenerated each run.*

## Roadmap (slices)

1. **Slice 1 — serving layer + dashboard foundation** ← *current work*
2. **Slice 2 — NL playlist builder:** Claude API turns a prompt into structured constraints (timeframe ← scrobble dates/`play_count`, genre, `mood_tags`, intensity ← `audio_features.energy`, vibe), ranks tracks, **reuses the saturation density-cap in `pipeline/apply_taste_profile.py`**. Add DuckDB-over-JSONL here for ad-hoc querying.
3. **Slice 3 — push** generated playlist to Spotify (and Apple).
4. **Slice 4 — curation write-back** from the dashboard → edits `taste_profile.md` (preserves the "markdown is truth" invariant), pipeline re-derives.

## Locked decisions for Slice 1

| Decision | Choice |
|---|---|
| Runtime | Local, single-user web app |
| Stack | **FastAPI** JSON backend + hand-built **vanilla HTML/JS** frontend (no framework) |
| Charts | **Apache ECharts** (native `graph`/force, `themeRiver`, `calendar`/polar — covers the flagship visuals) |
| Data structure | In-memory `list[dict]` + `collections.Counter`, matching the repo idiom (NOT pandas) |
| Storage | JSONL primary; serving layer is a derived, read-only view |

---

## Slice 1 — implementation plan

### Target layout
```
app/
  __init__.py
  main.py        # FastAPI app: /api routes + mount web/ as static
  data.py        # load tracks.jsonl + scrobbles.jsonl once; cache; reload(); use_paths() for tests
  metrics.py     # aggregations returning JSON-able dicts
  query.py       # filterable/paginated track table
web/
  index.html     # single page, sidebar nav switching sections
  css/app.css    # dark, polished theme
  js/
    api.js       # fetch wrappers
    filters.js   # shared filter-bar state (genre/mood/year/intensity/artist)
    app.js       # nav + bootstrap
    charts/*.js  # one module per ECharts viz
```

### Reuse (do not rewrite from scratch)
- **`scripts/library_stats.py`** already computes: coverage per field, top artists/tracks by `play_count`, tag/genre/mood counts, release-year distribution, and listening patterns by hour / day-of-week / season. **Refactor these to return dicts instead of printing.**
- **`scripts/make_view.py`** has the nested-record → flat-column model (`COLUMNS`, `_flatten`) for the track table.
- **`pipeline/config.py`** has `TRACKS_PATH`, `SCROBBLES_PATH`, `MOOD_CATEGORIES`, `SEASON_BY_MONTH`. Use these.

### Backend endpoints (`app/main.py`)
- `GET /api/overview` — counts, scrobble range, coverage gauges
- `GET /api/genres`, `GET /api/moods` — distributions
- `GET /api/timeline?by=year|month` — scrobbles over time
- `GET /api/time-of-day` — hour×weekday matrix + daily calendar counts (listening map)
- `GET /api/artist-trajectory?top=N` — per-artist plays by period (themeRiver)
- `GET /api/top?dim=artists|tracks&n=` — ranked plays
- `GET /api/audio-features` — per-feature histograms + energy×valence scatter
- `GET /api/saturation` — tier counts (donut)
- `GET /api/tracks?…filters` — filterable, paginated table
- `POST /api/reload` — re-read JSONL (the "updatable" requirement)

Register `/api` routes **before** mounting `StaticFiles(web, html=True)` at `/` so the API wins.

### Frontend (`web/`)
- Vanilla-JS SPA, sidebar nav. ECharts via CDN to start (can vendor later). Dark theme, smooth animations.
- **Flagship creative visuals (native ECharts):**
  - **Genre bubbles** — `graph` series, force layout (bubbles sized by track count).
  - **Artist listening trajectory** — `themeRiver` (streamgraph) over time.
  - **Time-of-day listening map** — `calendar` heatmap + polar/radial "clock" heatmap.
- **Foundation cards:** overview + coverage gauges, genre & mood charts, listening timeline, top artists/tracks, audio-feature histograms + energy×valence scatter, saturation donut, filterable track table.
- **Shared filter bar** (`filters.js`) — built reusable; Slice 2's builder will drive the same constraint model.

### Tests / CI
- Add `tests/test_app_api.py` using FastAPI `TestClient`. **Self-contained** (tiny temp-JSONL fixture via `data.use_paths()`, no network/secrets) so the CI workflow (`.github/workflows/ci.yml`, runs full pytest on push/PR) stays green.

### Verification
1. `pip install -r requirements.txt`
2. `uvicorn app.main:app --reload` → open `http://127.0.0.1:8000`
3. Confirm: coverage gauges, genre bubbles float, themeRiver shows artist trajectories, time-of-day map populates, filter bar narrows the track table live.
4. `POST /api/reload` reflects new data without restart.
5. `pytest` green (incl. the existing manifest anti-drift guard).

---

## Progress so far (what this branch already contains)

- ✅ Created branch `feat/dashboard-foundation`.
- ✅ `requirements.txt` — added `fastapi`, `uvicorn[standard]`, `httpx` (httpx is for FastAPI `TestClient`).
- ⬜ Everything else in the plan above is **not yet built** — `app/` and `web/` do not exist yet.

## Next session: start here
1. `pip install -r requirements.txt` (the env is missing deps).
2. Build `app/data.py` → `app/metrics.py` (port from `library_stats.py`) → `app/query.py` → `app/main.py`.
3. Build `web/` frontend with the ECharts modules.
4. Add `tests/test_app_api.py`; run `pytest`.
5. Verify in the browser per the steps above.

## Out of scope for Slice 1
Playlist builder, Claude API, DuckDB, Spotify/Apple push, curation write-back — Slices 2–4.
