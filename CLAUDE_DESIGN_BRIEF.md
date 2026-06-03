# Claude Design brief — Listening Atlas dashboard, round 2

Context for the designer: this is a React 18 + Babel-standalone SPA (no build step) in
`web/`. Presentational components live in `web/charts.jsx`; ECharts wrappers in
`web/echarts-charts.jsx`; the shell/state in `web/dashboard.jsx`; styles in
`web/themes.css`. Data comes from a FastAPI layer (`app/main.py`, `app/metrics.py`) that
reads `tracks.jsonl` (one row per unique track: `play_count`, `genres`, `mood_tags`,
`audio_features`, `album`, `release_year`, `peak_year`, `first_scrobbled`,
`last_scrobbled`, `saturation_tier`, `discogs_styles`, `lastfm_tags`, `spotify_id`…) and
`scrobbles.jsonl` (one row per play: `artist`, `track`, `album`, `year`, `month`, `hour`,
`day_of_week`, `season`, `scrobbled_at`). ~2,730 tracks, ~13,700 scrobbles, 2010 + 2020–2026.

Already fixed directly (not for design): timeframe windows now compute real per-year /
per-month plays; constellation force-graph sizing race; "Last.fm" tab renamed "Genres";
audio-features axis titles re-placed. Everything below is the visual/UX work.

---

## A. Polish on existing pages

### A1. Drill-down from the overview charts
The Overview cards "When the music plays" (hour-of-day), "Weekly rhythm" (day-of-week),
and "Seasons of listening" are currently read-only bars. **Ask:** clicking a bar/season
should reveal the moods and/or genres that dominate that slice (e.g. click "Summer" →
top genres/moods for summer scrobbles; click the 11pm bar → late-night mood profile).
Data is available by joining scrobbles (which carry `season`/`hour`/`day_of_week`) to
track `genres`/`mood_tags`. Design direction: an expanding panel or popover under the
card with a small ranked list or mini-bar, not a full page change.

### A2. Tag Constellation — size + mobile
The force graph (`TagConstellation` in `echarts-charts.jsx`, `.echart-wrap.tall`,
min-height 560px) is too small and on mobile the card-head meta text
("force graph · shared-track connections") wraps and eats the right half of the screen,
squeezing the graph left. **Ask:** make the constellation noticeably larger (consider
full-bleed / taller on this page), and on mobile hide or move the `.card-meta` so the
graph gets full width. The explainer `<p>` under the head can stay but should collapse on
small screens.

---

## B. Reworks of existing pages

### B1. Artist Trajectory → line chart (Last.fm style), with easier artist picking
Currently a ThemeRiver (stacked stream) from `/api/artist-trajectory` (returns
`[period, count, name]` monthly). The user wants the **line-graph** form Last.fm uses in
its weekly "Tag Timeline" — curved multi-series lines of listening share over time — with
a **toggle** between line and the existing stacked/stream view. Also replace the scrolling
legend with a **better artist selector** (searchable chip multi-select, or a checklist with
top-N preselected). Reference: Last.fm Tag Timeline / weekly charts
(https://www.last.fm/charts , https://www.last.fm/features/category/charts-and-trends).

### B2. Listening Calendar — legibility
`ListeningMap` renders an ECharts calendar heatmap (`cellSize ['auto', 13]`, 3-stop accent
ramp) plus an hour×weekday grid. With years of daily data the cells are tiny and the
faded low-end colors make it unreadable. **Ask:** fewer/bigger cells and a higher-contrast
scale. Options: default to a **single-year view with a year switcher** (so cells are large),
bump min/mid contrast, and/or add a monthly-rollup variant. Keep the hour×weekday grid.

### B3. Saturation page — justify, fold in, or cut
Currently a lone donut from `/api/saturation` (tracks per enrichment tier 1/2/3/unranked).
The user doesn't understand it and isn't sure it deserves a page; on mobile the legend and
donut overlap. **Decision needed first**, then design:
- *Flesh out:* explain what a "saturation tier" means, and surround the donut with context —
  e.g. tier vs play_count, tier vs recency, example tracks per tier — so it tells a story.
- *Fold in:* move the donut into the Coverage page as one more panel.
- *Cut:* remove the nav item entirely.
Recommend folding into Coverage unless we can give the metric a clear narrative.

### B4. Tracks page — slicers
The Track Explorer (`explorer.jsx` / `TrackTable`) has search + sort + filter chips but no
**date slicers**. **Ask:** add date-range controls (release-year range and/or
first/last-scrobbled range) alongside the existing filters. Data: tracks carry
`release_year`, `first_scrobbled`, `last_scrobbled`. Consider also surfacing the timeframe
slicer here so play columns reflect a chosen window.

---

## C. New sections / features

### C1. Albums
**Ask:** a "most-played albums" view. An album's strength = many scrobbles spread across
several of its tracks (not one hit). Data: both `tracks.jsonl` and `scrobbles.jsonl` carry
`album`. Design direction inspired by Spotify Wrapped album scoring (total plays × how
evenly listening spreads across the album's tracks) — show album, artist, total plays,
track count, and a small "spread" indicator. New endpoint `/api/albums` would back it.

### C2. Forgotten favorites
**Ask:** a creative filter for tracks that were heavily played for a while then dropped off.
Data: per-track time series is derivable from `scrobbles.jsonl` (`year`/`month`); tracks
also carry `peak_year` and `last_scrobbled`. Surface tracks with a high historical peak but
low recent plays (e.g. peak window plays ≫ last-6-months plays). Present as a list/card row
with a tiny sparkline of the track's play history.

### C3. Seasonal favorites
**Ask:** favorite moods / genres / tracks per season. Data: scrobbles carry `season`; join
to track `mood_tags`/`genres`. Design direction: a four-up (winter/spring/summer/fall) panel
with each season's top genres, moods, and tracks — pairs naturally with the A1 drill-down.

### C4. Discovery (lower priority — needs a candidate source)
**Ask:** songs closely related (genre / mood / audio-feature similarity) to tracks you love
but with no/low plays. Caveat: every row in `tracks.jsonl` originates from a scrobble, so
there are no zero-play tracks in the current data. This feature needs an **external
candidate pool** (Last.fm similar-tracks, Apple Music, etc.) before it's buildable — flag
as research/dependency, design later.

### C5. Audio-feature "variance" / signature (Spotify-wrapped flavored)
**Ask:** do more with the audio-feature data. Ideas worth designing: a per-artist
audio-feature **spread** (how consistent vs varied an artist's catalog is across
energy/valence/danceability), and a personal **taste signature** radar of average features.
Reference: Spotify Wrapped methodology + audio-feature analysis writeups
(https://newsroom.spotify.com/2025-12-05/wrapped-methodology-explained/ ,
https://www.elastic.co/search-labs/blog/spotify-wrapped-data-analysis-visualization).

---

## Priority suggestion
1. B3 decision (saturation) — smallest, unblocks nav cleanup.
2. A1 + C3 (seasonal drill-down + seasonal favorites) — high value, data ready, reinforce each other.
3. B1 (artist trajectory line + picker) — most-requested, data ready.
4. C1 (albums) — clear win, needs one endpoint.
5. B2, A2, B4 — legibility/UX polish.
6. C2 (forgotten favorites), C5 (audio variance) — delightful extras.
7. C4 (discovery) — blocked on external data source.
