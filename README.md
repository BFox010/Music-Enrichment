# Listening Atlas

A personal scrobble-history dashboard. It turns a Last.fm export into an
annotated track library and renders it — listening timelines, artist
trajectories, genre and mood structure, seasonal patterns, and the tracks you
used to play constantly and stopped.

A React SPA over a FastAPI serving layer, backed by an enrichment pipeline.
`tracks.jsonl` and `scrobbles.jsonl` are the source of truth; everything else is
derived.

> **Agents:** start with [CLAUDE.md](CLAUDE.md) — invariants, conventions, and
> gotchas in a fraction of the reading.

---

## Run it

```bash
pip install -r requirements.txt
npm install && npm run build     # compile web/*.jsx → web/app.bundle.js
uvicorn app.main:app             # http://127.0.0.1:8000
```

Only `LASTFM_API_KEY` and `LASTFM_USERNAME` are needed to serve the committed
data and sync new scrobbles. See [.env.example](.env.example) for the rest.

## Dashboard

| Group | View | What it shows |
|---|---|---|
| — | Overview | KPIs, coverage gauges, top artists/tracks, genre and mood distribution |
| Library | Genre & Moods | Genre and mood breakdown with cross-filtering |
| Library | Albums | Album-level aggregation, gated on a minimum track count |
| Library | Tag Constellation | Tag/style co-occurrence graph, force-laid-out |
| Library | Audio Features | Feature histograms and the energy × valence scatter |
| Library | Coverage | Per-field enrichment coverage and saturation tiers |
| Listening | Timeline | Scrobbles over time, by year or month |
| Listening | Listening Map | Hour × weekday matrix and a calendar heatmap |
| Listening | Artists | Per-artist plays over time (themeRiver) |
| Listening | Seasonal | Per-season favourites with a tracks-and-time drill-down |
| Listening | Forgotten | Tracks with a peak-then-fade listening arc |
| Browse | Tracks | Filterable, paginated track table |
| Data | Scrobble Sync | Pull new scrobbles from Last.fm; run a full refresh |

Most views accept a listening window (`2025`, `2025-03`, `2025-summer`, or
`from:to`) and cross-filter each other.

### Frontend build

The `.jsx` sources are **pre-compiled** into a single minified
`web/app.bundle.js` by [scripts/build_frontend.mjs](scripts/build_frontend.mjs)
using esbuild. There is no in-browser transpile.

```bash
npm run build     # one-shot
npm run dev       # watch mode
```

**After editing any `web/*.jsx`, rebuild and commit the regenerated bundle** —
otherwise the change simply does not appear.

`web/data-processing.js` and `web/data-worker.js` are **not** part of the bundle.
They hold the pure parse/aggregate transforms as plain JS so a Web Worker can run
the initial-load parse off the main thread; the bundle references them as
globals. Edit directly, no rebuild needed.

On first paint the dashboard fetches `/tracks.min.jsonl` — a slimmed projection
of just the fields the UI renders. The full `tracks.jsonl` stays available for
download and through `/api/*`.

Load-time profile: [PERFORMANCE_MAP.md](PERFORMANCE_MAP.md).
Visual-polish backlog: [docs/WOW_FACTOR_IDEAS.md](docs/WOW_FACTOR_IDEAS.md).

## API

Served by [app/main.py](app/main.py). API routes register before the static mount
so `/api/*` always wins.

**Reads:** `/api/overview` · `/api/genres` · `/api/moods` · `/api/timeline` ·
`/api/time-of-day` · `/api/albums` · `/api/artist-trajectory` · `/api/top` ·
`/api/audio-features` · `/api/saturation` · `/api/tracks` ·
`/api/forgotten-favorites` · `/api/tag-graph` · `/api/integrity` · `/api/config` ·
`/api/lastfm/status`

**Mutations** (require `X-Dashboard-Token`): `POST /api/reload` ·
`POST /api/lastfm/sync` · `POST /api/refresh`

**Raw data:** `/tracks.jsonl` · `/scrobbles.jsonl` · `/tracks.min.jsonl` — all
served with ETag-based conditional GETs so repeat loads are a cheap `304`.

The token comes from same-origin `GET /api/config`. There is **no CORS
middleware, deliberately**: the dashboard is same-origin, and allowing all
origins would let any site the user visits read their full listening history over
the public tunnel. Set `DASHBOARD_TOKEN` to keep the token stable across
restarts.

Responses above 1 KB are gzipped.

---

## Pipeline

Phase execution order is derived from [pipeline_manifest.yaml](pipeline_manifest.yaml),
which is the single source of truth. Adding a phase means adding a manifest entry
*and* implementing the module — the anti-drift test in
[tests/test_pipeline_manifest.py](tests/test_pipeline_manifest.py) catches either
half being missing.

| Phase | Module | Input | Output |
|---|---|---|---|
| 1 | [ingest_scrobbles](pipeline/ingest_scrobbles.py) | `inputs/lastfm_export.json` | `scrobbles.jsonl` |
| 2 | [dedupe](pipeline/dedupe.py) | `scrobbles.jsonl` | `tracks_skeleton.jsonl` |
| A | [enrich_apple_library](pipeline/enrich_apple_library.py) | + `inputs/apple_music_library.xml` | `tracks_with_apple.jsonl` |
| B | [enrich_spotify_ids](pipeline/enrich_spotify_ids.py) | `tracks_with_apple.jsonl` | `tracks_with_spotify.jsonl` |
| 3a | [export_tunemymusic](pipeline/export_tunemymusic.py) | `tracks_skeleton.jsonl` | `inputs/tunemymusic_upload.csv` |
| 3b | _(manual — owner)_ | `inputs/tunemymusic_upload.csv` | `inputs/exportify.csv` |
| 3c | [merge_exportify](pipeline/merge_exportify.py) | + `inputs/exportify.csv` | `tracks_with_audio.jsonl` |
| 4 | [enrich_metadata](pipeline/enrich_metadata.py) | `tracks_with_audio.jsonl` | `tracks_with_metadata.jsonl` |
| 4b | [enrich_discogs](pipeline/enrich_discogs.py) | `tracks_with_metadata.jsonl` | `tracks_with_discogs.jsonl` |
| 4c | [derive_genres](pipeline/derive_genres.py) | `tracks_with_discogs.jsonl` | `tracks_with_genres.jsonl` |
| 4d | [enrich_genre_backfill](pipeline/enrich_genre_backfill.py) | `tracks_with_genres.jsonl` | `tracks_with_genre_backfill.jsonl` |
| 4e | [resolve_identity](pipeline/resolve_identity.py) | `tracks_with_genre_backfill.jsonl` | `tracks_resolved.jsonl` |
| 5 | [check_apple_music](pipeline/check_apple_music.py) | `tracks_resolved.jsonl` | `tracks_with_availability.jsonl` |
| 6 | [classify_moods](pipeline/classify_moods.py) | + `inputs/existing_audit.csv` | `tracks_with_moods.jsonl` |
| 7 | [apply_taste_profile](pipeline/apply_taste_profile.py) | + [taste_profile.md](taste_profile.md) | `tracks_with_taste.jsonl` |
| 8 | [update_tracks](pipeline/update_tracks.py) | `tracks_with_taste.jsonl` | `tracks.jsonl` |

**Optional phases** (`B`, `4b`, `4d`, `6`, `7`) skip gracefully when their
credentials or input files are absent. **Phase 3b is blocked** on owner action;
[#37](https://github.com/BFox010/Music-Enrichment/issues/37) plans to replace the
TuneMyMusic/Exportify round-trip with a chain that doesn't need it.

Phases that read a JSONL pick the **deepest existing intermediate** rather than a
fixed path, so skipping an optional phase doesn't silently drop the fields a
later one added.

```bash
python -m pipeline.run_full_pipeline
python -m pipeline.run_full_pipeline --start-from 4
python -m pipeline.run_full_pipeline --skip-tests --skip-pause
```

### Phase 1 is additive

The owner's export workflow is a partial pull covering "today back to the last
pull", so ingest merges into `scrobbles.jsonl` and dedupes on
`(scrobbled_at, artist_normalized, track_normalized)` rather than overwriting.

A write that would leave *fewer* rows than are already on disk is **refused**.
`scrobbles.jsonl` is the base record every play-weighted number derives from, and
Phase 2 recomputes `play_count`, `first_scrobbled`, `last_scrobbled` and
`peak_year` from whatever survives.

```bash
python -m pipeline.ingest_scrobbles                            # merge (default)
python -m pipeline.ingest_scrobbles --replace                  # rebuild; still refuses to shrink
python -m pipeline.ingest_scrobbles --replace --allow-shrink   # genuinely drop history
```

For incremental updates without a manual export, `POST /api/lastfm/sync`
([app/lastfm_sync.py](app/lastfm_sync.py)) fetches from the last stored timestamp
and appends through the same guarded path.

### On Phase B

Its real output is the **ISRC** — an open standard accepted by ReccoBeats,
MusicBrainz and AcousticBrainz alike, which keeps the audio-feature source
swappable. `spotify_id` is stored because ReccoBeats also accepts it, not as the
identifier of record.

B is the **last-resort resolver**: cheaper routes (MusicBrainz `musicbrainz_id` →
ISRC, needing no auth; then Deezer) should run first, and B should be retired
outright if they reach adequate coverage. See the module docstring.

### On phases 3a/3c and 7

These look like playlist machinery. They aren't:

- **3a/3b/3c** exist to acquire *audio features*. The playlist round-trip is the
  means, not a feature.
- **Phase 7** produces `saturation_tier`, `blacklisted` and `curation_state` —
  dashboard-facing curation metadata. Its `playlists` field is a grouping label
  read out of `taste_profile.md`, not a generated playlist.

An earlier iteration aimed at a natural-language playlist builder that pushed to
Spotify/Apple. That direction was dropped.

## API reliability

Every phase that hits an external API:

- Caches every response — **successes and failures** — to `.cache/<api>.json`,
  and skips anything already cached.
- **Expires negative entries.** A genuine `not_found` after
  `HTTP_NEGATIVE_TTL_SECONDS` (30 days); anything transient (`max_retries`,
  `invalid_json`) after `HTTP_TRANSIENT_TTL_SECONDS` (6 hours). A network blip
  heals itself on the next run instead of freezing that track permanently. Cache
  entries written before this format carry no timestamp and count as expired.
- **Is crash-safe** — flushes its cache from a `finally`, so an interrupted run
  keeps every response it already paid for at the rate limit.
- Retries with exponential backoff up to `HTTP_MAX_RETRIES` (5), and logs
  failures without aborting the run.
- Logs a cache summary (entries, cached `not_found` vs transient, hits/misses/
  refetches) so a low coverage number traces back to genuine no-match versus a
  cached failure.

| Flag | Effect |
|---|---|
| `--force-errors` | Re-fetch cached failures only, ignoring their TTL. The cheap way to clear poisoned entries. |
| `--force` | Bypass the cache entirely. Slow — each phase logs an ETA first. |

Both dispatch off the manifest's `accepts_force` flag, so a phase only receives
them if its callable actually takes `force=`.

Shared HTTP layer: [pipeline/_http.py](pipeline/_http.py). Rate limits and TTLs:
[pipeline/config.py](pipeline/config.py).

Every enrichment phase writes per-field provenance — `source`, `retrieved_at`,
`pipeline_phase`, `confidence`.

## Schema

Canonical version lives in `SCHEMA_VERSION` ([pipeline/config.py](pipeline/config.py)),
currently `5`, mirrored in the manifest. Integer, monotonic.

- Every record emits `_schema_version` as its **first** field. `read_jsonl` also
  loads legacy records without it.
- Writers emit fields in stable order — `FIELD_DEFAULTS` in
  [pipeline/schema.py](pipeline/schema.py) defines it.
- Readers ignore unknown fields; `fill_defaults()` preserves forward-compat.
- Additive fields **do not** bump the version. Breaking renames or removals
  **do**, and require migration tests
  ([tests/test_schema_v5.py](tests/test_schema_v5.py)).

Canonical track identity, used for every cross-phase join:

1. MusicBrainz recording MBID (`musicbrainz_id`)
2. ISRC
3. Normalized artist + track (`artist_normalized` + `track_normalized`)
4. Fallback hash

**All phases must preserve `canonical_track_id`.** No phase may drop or overwrite
it.

## Where files live

| Path | Contents | Tracked |
|---|---|---|
| `tracks.jsonl` | Canonical enriched library — Phase 8 output | yes |
| `scrobbles.jsonl` | Raw play history — Phase 1 output | yes |
| `taste_profile.md` | Hand-edited curation reference, read by Phase 7 | yes |
| `taste_profile_template.md` | Blank starter for the above | yes |
| `tracks_*.jsonl` | Per-phase intermediates | no |
| `inputs/` | Owner-provided exports and audit CSVs | no |
| `.cache/` | Per-API response caches | no |
| `runs/` | Timestamped pipeline logs | no |
| `views/` | Generated XLSX/CSV from `scripts/make_view.py` | no |

`taste_profile.md` is **never written by the pipeline**. Markdown is truth for
human judgement; JSONL is the derived index, regenerated each run.

## Scripts

| Script | Purpose |
|---|---|
| [library_stats.py](scripts/library_stats.py) | Coverage and distribution pulse-check between runs |
| [make_view.py](scripts/make_view.py) | XLSX/CSV view of `tracks.jsonl` for inspection |
| [build_label_queue.py](scripts/build_label_queue.py) | Mood-labeling queue ranked by plays explained |
| [eval_spotify_resolution.py](scripts/eval_spotify_resolution.py) | Precision/recall of the Phase B matcher against known IDs |
| [generate_library_js.py](scripts/generate_library_js.py) | Static offline snapshot for the `file://` workflow |
| [build_frontend.mjs](scripts/build_frontend.mjs) | esbuild JSX → `web/app.bundle.js` |

[scripts/archive/](scripts/archive/README.md) holds one-off utilities kept for
provenance. Nothing imports them.

Coverage percentages move whenever the library grows — a fuller export adds
tracks the enrichment phases haven't run across, so coverage can drop without
anything breaking. Run `library_stats.py` rather than trusting a number written
down here.

## Tests

CI pins Python 3.13. Install once per interpreter, then run:

```bash
py -3.13 -m pip install -r requirements.txt
py -3.13 -m pytest tests/ -q
```

(`python -m pytest` works too, as long as `python` already resolves to 3.13 —
`py -3.13` is explicit so it does the right thing regardless of what a bare
`python` picks up on your PATH.) Running under an older interpreter fails fast
with one clear message rather than a cascade of unrelated failures — see
[tests/conftest.py](tests/conftest.py).

Self-contained — temp fixtures, no network, no secrets. CI runs the full suite on
every push to `main` and every pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Reference

- [CLAUDE.md](CLAUDE.md) — agent orientation: invariants, conventions, gotchas
- [docs/apple-music-xml.md](docs/apple-music-xml.md) — iTunes XML field mapping
- [PERFORMANCE_MAP.md](PERFORMANCE_MAP.md) — load-time and compute profile
- [docs/WOW_FACTOR_IDEAS.md](docs/WOW_FACTOR_IDEAS.md) — visual-polish backlog

The git log is the authoritative history.
