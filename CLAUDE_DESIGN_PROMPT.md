# Claude Design handoff prompt
# Copy everything below the line into Claude Design.
# ─────────────────────────────────────────────────────────────────────────────

---

You are implementing features and visual improvements for a personal music analytics dashboard called **Listening Atlas**. This is a **React 18 + Babel-standalone SPA** — no build step, no npm, no webpack. Files are loaded directly as `<script type="text/babel" src="...">`. Everything you write must work in that environment.

## Repo layout

```
web/
  index.html          — loads React 18 + Babel from CDN, then the files below
  themes.css          — full design system (CSS variables, layout, all component classes)
  css/app.css         — supplemental styles
  dashboard.jsx       — App component: all state, routing (page useState), aggregations,
                        shell layout, every page's JSX, ScrobbleSync component
  charts.jsx          — Presentational React components: HBars, TrackList, HourChart,
                        DowChart, Seasons, MoodBars, GenreDonut, TagCloud, CoverageBars
                        (assigned to window.* at the bottom, used in dashboard.jsx)
  echarts-charts.jsx  — ECharts wrappers: TimelineChart, ArtistTrajectory, ListeningMap,
                        AudioFeaturesChart, SaturationChart, TagConstellation
                        (assigned to window.* at the bottom)
  explorer.jsx        — TrackTable + FilterBar (assigned to window.* at the bottom)
  tweaks-panel.jsx    — Accent-colour + density tweaks panel

app/
  main.py             — FastAPI: all /api/* routes + static mount of web/
  metrics.py          — All aggregation functions (reads tracks.jsonl, scrobbles.jsonl)
  data.py             — In-memory JSONL cache with reload()
  query.py            — Filtered track queries
  refresh.py          — Full-chain scrobble sync → pipeline → reload
```

## Design system (Midnight theme)

All component styles live in `web/themes.css`. Use only these CSS variables — do not hardcode colours:

```
--bg, --panel, --panel-2, --panel-hi    backgrounds (darkest to lightest)
--line, --line-soft                     borders / dividers
--text, --text-2, --muted-s, --faint    text hierarchy
--accent                                user-overridable accent (default #a78bfa, violet)
--accent-ink                            text on --accent backgrounds
--good (#4ade80), --warn (#fbbf24), --bad (#fb7185)
--track-bg                              bar track backgrounds

--font-display: 'Space Grotesk'
--font-body:    'Manrope'
--font-mono:    'Space Grotesk' (mono variant)
--radius: 14px  --radius-sm: 9px  --radius-pill: 999px
--card-border: 1px solid var(--line)
--card-shadow   (card box-shadow)
```

Key layout classes already in CSS: `.shell`, `.appbar`, `.sidebar`, `.sidenav`, `.sidenav-item`, `.main-content`, `.card`, `.card-head`, `.card-title`, `.card-meta`, `.block`, `.kpis`, `.kpi`, `.grid.g-2`, `.grid.g-32`, `.seg` (segmented control), `.slicer`, `.echart-wrap`, `.echart-wrap.tall`, `.echart-wrap.short`.

## Data shape

`tracks.jsonl` — one JSON object per line, ~2,730 rows. Relevant fields:
```
artist, track, album, release_year, play_count, peak_year,
first_scrobbled (ISO date), last_scrobbled (ISO date),
genres: string[], mood_tags: string[], lastfm_tags: string[],
discogs_styles: string[], audio_features: {energy, valence, danceability,
  acousticness, speechiness, tempo, loudness}, saturation_tier: 1|2|3|null,
spotify_id, musicbrainz_id, apple_music_available
```

`scrobbles.jsonl` — one JSON per line, ~13,700 rows:
```
artist, artist_normalized, track, track_normalized, album,
scrobbled_at (ISO datetime), year, month, hour, day_of_week (0=Mon),
season ("winter"|"spring"|"summer"|"fall")
```

## Existing API endpoints (all return JSON)

```
GET  /api/overview                     — coverage stats
GET  /api/timeline?by=year|month       — [{period, plays}]
GET  /api/time-of-day                  — {calendar: [[date,n]…], hour_weekday: [[h,dow,n]…]}
GET  /api/artist-trajectory?top=N      — {data: [[period, count, name]…]} (monthly)
GET  /api/audio-features               — {scatter: [{energy,valence,artist,track,play_count}…],
                                           histograms: {energy:[{bin_start,bin_end,count}]…}}
GET  /api/saturation                   — [{tier:"1"|"2"|"3"|"unranked", count}]
GET  /api/tag-graph?field=…&min_count  — {nodes:[{tag,count}…], edges:[{source,target,weight}…]}
GET  /api/genres?top=N                 — [{genre,count}]
GET  /api/moods                        — [{mood,count}]
GET  /api/top?dim=artists|tracks&n=N   — [{name,plays,…}]
GET  /api/tracks?genre&mood&artist&… &page&per_page
```

You may add new endpoints to `app/main.py` + `app/metrics.py` when a feature needs data that isn't exposed yet. Follow the existing patterns (FastAPI, `get_tracks()` / `get_scrobbles()`, return plain dicts/lists).

## Routing

Page routing is a `useState("overview")` in `App()` in `dashboard.jsx`. Each page is rendered as `<div style={{ display: page === "xxx" ? "" : "none" }}>`. Adding a page = add a sidebar button + a display-block div in the main content area.

---

## What to implement

Full details are in `CLAUDE_DESIGN_BRIEF.md` at the repo root. Summarised in priority order:

### 1 · Saturation — fold into Coverage page (quick win)

Remove "Saturation" from the sidebar. Move the donut chart into the Coverage page alongside the existing CoverageBars and TagCloud, with a one-line explanation of what a saturation tier means ("Tier 1 = heavy rotation, Tier 2 = regular plays, Tier 3 = deep cuts, Unranked = no play data yet"). Fix the mobile overlap (legend + donut stack vertically on screens < 600px).

### 2 · Seasonal overview drill-down + Seasonal Favorites page

**2a. Overview drill-down.** The Overview page has "When the music plays" (HourChart), "Weekly rhythm" (DowChart), and "Seasons of listening" (Seasons component). Make each interactive: clicking a season card, a day bar, or an hour column opens an expanding inline panel (or popover) below that card showing the top 5 genres and top 5 moods for that time slice. 

Data approach: the App already loads scrobbles and tracks. Build a helper that, given `{type: "season"|"dow"|"hour", value}`, cross-joins scrobbles→tracks on `(artist_normalized, track_normalized)` and counts genres/moods. This runs client-side on the in-memory arrays — no new endpoint needed.

**2b. Seasonal Favorites page.** Add a new "Seasonal" nav item under Listening. Four-panel layout (Winter/Spring/Summer/Fall), each showing: top 3 genres (with counts), top 3 moods, top 5 tracks (by seasonal play count). Same client-side join as 2a.

### 3 · Artist Trajectory — line chart + artist picker

The existing `ArtistTrajectory` in `echarts-charts.jsx` uses ECharts `themeRiver`. Replace/augment:

- Add a **toggle** ("Stream" / "Lines") above the chart. Stream keeps the current themeRiver. Lines switches to a multi-series line chart (ECharts `type: "line"`, smooth, one series per artist, x-axis = monthly periods, y-axis = plays, legend below).
- Replace the scroll legend with a **searchable artist multi-select** above the chart: an input box that filters a chip list of the top 20 artists (by lifetime plays); chips toggle inclusion; default = top 8 selected. Pass the selection as a query param: `/api/artist-trajectory?top=20` returns all 20, the component filters to the selected set client-side before rendering.
- On mobile, the chip list collapses to a `<select multiple>` or a scrollable row of toggle buttons.

### 4 · Albums page

Add an "Albums" nav item under Library.

**New endpoint** in `app/metrics.py` + `app/main.py`:
```
GET /api/albums?top=50
```
Implementation:
```python
def albums(top: int = 50) -> list[dict]:
    from collections import defaultdict, Counter
    albums = defaultdict(lambda: {"tracks": [], "plays": 0, "artist": ""})
    for t in get_tracks():
        if not t.get("album"):
            continue
        key = (t["artist"].lower(), (t["album"] or "").lower())
        albums[key]["tracks"].append(t.get("play_count") or 0)
        albums[key]["plays"] += t.get("play_count") or 0
        albums[key]["artist"] = t["artist"]
        albums[key]["album"] = t["album"]
    result = []
    for (_, _), v in albums.items():
        n = len(v["tracks"])
        if n < 2:
            continue
        total = v["plays"]
        # spread = 1 when plays are perfectly even, approaches 0 when one track dominates
        if total > 0:
            shares = [p / total for p in v["tracks"]]
            import math
            entropy = -sum(s * math.log(s + 1e-9) for s in shares)
            max_entropy = math.log(n)
            spread = round(entropy / max_entropy, 3) if max_entropy else 0
        else:
            spread = 0
        result.append({"album": v["album"], "artist": v["artist"],
                        "track_count": n, "plays": total, "spread": spread})
    result.sort(key=lambda x: -x["plays"])
    return result[:top]
```

**UI**: a ranked card list, each row showing album art placeholder (a coloured initial square), album name, artist, play count, track count, and a small horizontal "spread" indicator bar (0 = one track dominates, 1 = perfectly even). Add a toggle to sort by plays vs spread. Consider a `min_tracks` slider (default 3) to filter out single-track "albums".

### 5 · Listening Calendar — legibility

In `echarts-charts.jsx`, `ListeningMap` renders a multi-year calendar heatmap. The cells are too small and low-contrast. Fix:

- Default to showing **one year at a time** with a year picker (prev/next arrows, or a segmented control of available years derived from the API data). Pass `?year=YYYY` to `/api/time-of-day` (add optional `year` param to the endpoint that filters scrobbles before building the calendar array).
- Increase cell size: `cellSize: [16, 16]` or `["auto", 16]`.
- Use a 3-stop colour scale with more contrast: `["#1a1a2e", "#7c3aed", "#a78bfa"]` (dark → mid violet → accent).
- Keep the hour×weekday heatmap below, unchanged.

### 6 · Track Explorer — date slicers

In `dashboard.jsx`, the `explorer` page renders `<FilterBar>` and `<TrackTable>`. Add two new filter controls to `FilterBar` (defined in `explorer.jsx`):

- **Release decade** — already partially supported in `filters.decade`; make it a segmented control of available decades (60s, 70s, 80s, 90s, 00s, 10s, 20s) derived from the track data, not hardcoded.
- **First-heard range** — a pair of year dropdowns ("From … To …") filtering on `first_scrobbled` year. Add `firstFrom`/`firstTo` to the `filters` state shape in `dashboard.jsx` and apply them in the `filtered` useMemo.
- **"This year" quick button** — a single chip that sets firstFrom = firstTo = current year ("New this year"), for quickly surfacing recently discovered tracks.

---

## Constraints & notes

- **No build step.** No `import`/`export` syntax. All components must be assigned to `window.*` at the bottom of their file, exactly as the existing code does, and consumed as `window.ComponentName` in `dashboard.jsx`.
- **ECharts** is loaded from CDN (`window.echarts`). Use `useEChart()` + `chart.current.setOption(...)` as the existing charts do.
- **Mobile.** The app has a responsive sidebar (becomes a horizontal scroll bar at 768px). All new pages must be usable at 375px width.
- **CSS.** Extend `themes.css` with any new classes. Use CSS variables for all colours. Do not add inline `style` colour values.
- **API additions** go in `app/metrics.py` (the pure function) + `app/main.py` (the FastAPI route). Follow the existing patterns exactly.
- **Do not touch** `pipeline_manifest.yaml`, any `pipeline/*.py`, or `scripts/*.py`.
- The repo's active development branch is `claude/handoff-md-visibility-GGjFj`. Commit all changes there.
