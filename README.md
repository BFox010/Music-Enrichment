# Music-Enrichment

Personal music-library enrichment pipeline. Ingests Last.fm scrobbles, dedupes
into a canonical track list, enriches with Last.fm tags / MusicBrainz IDs /
iTunes XML / Apple Music availability / Spotify audio features, classifies
moods, applies a taste profile, and emits a single canonical `tracks.jsonl`.

The git log is the authoritative history. [SAVE_LOG.md](SAVE_LOG.md) is the
fast "where are we" pointer between sessions.

---

## Pipeline phase table

| Phase | Name                          | Module                                                    | Inputs                                                              | Outputs                                                  | Depends on    | Status                          |
|-------|-------------------------------|-----------------------------------------------------------|---------------------------------------------------------------------|----------------------------------------------------------|---------------|---------------------------------|
| 0     | scaffolding                   | (config, normalize, _http, schema)                        | —                                                                   | tests + module surface                                   | —             | DONE                            |
| 1     | scrobble ingest               | [pipeline/ingest_scrobbles.py](pipeline/ingest_scrobbles.py) | `inputs/lastfm_export.json`                                         | `scrobbles.jsonl`                                        | 0             | DONE (16,549 rows, additive)    |
| 2     | dedupe                        | [pipeline/dedupe.py](pipeline/dedupe.py)                  | `scrobbles.jsonl`                                                   | `tracks_skeleton.jsonl`                                  | 1             | DONE (2,730 unique tracks)      |
| A     | iTunes XML enrichment         | [pipeline/enrich_apple_library.py](pipeline/enrich_apple_library.py) | `tracks_skeleton.jsonl`, `inputs/apple_music_library.xml`           | `tracks_with_apple.jsonl`                                | 2             | DONE (122/2,730 matched)        |
| 3a    | TuneMyMusic export            | [pipeline/export_tunemymusic.py](pipeline/export_tunemymusic.py) | `tracks_with_apple.jsonl`                                           | `inputs/tunemymusic_upload.csv`                          | A             | DONE                            |
| 3b    | TuneMyMusic + Exportify       | _(manual — owner)_                                        | `inputs/tunemymusic_upload.csv`                                     | `inputs/exportify.csv`                                   | 3a            | BLOCKED (owner action)          |
| 3c    | Exportify merge               | [pipeline/merge_exportify.py](pipeline/merge_exportify.py) | `tracks_with_apple.jsonl`, `inputs/exportify.csv`                   | `tracks_with_audio.jsonl`                                | 3b            | BLOCKED on 3b (code complete)   |
| 4     | Last.fm + MusicBrainz         | [pipeline/enrich_metadata.py](pipeline/enrich_metadata.py) | `tracks_with_audio.jsonl` _(or tracks_with_apple.jsonl if 3c skipped)_ | `tracks_with_metadata.jsonl`                          | A (3c if run) | DONE (2,342/2,730 = 85.8%; +177 via name-variation retry) |
| 4b    | Discogs styles                | [pipeline/enrich_discogs.py](pipeline/enrich_discogs.py)  | `tracks_with_metadata.jsonl`                                        | `tracks_with_discogs.jsonl`                              | 4             | DONE (1,923/2,730 = 70.4%)      |
| 4c    | genre derivation              | [pipeline/derive_genres.py](pipeline/derive_genres.py)    | `tracks_with_discogs.jsonl`                                         | `tracks_with_genres.jsonl`                              | 4b            | DONE (no-API; maps itunes/discogs/lastfm tags) |
| 4d    | genre backfill (artist-level) | [pipeline/enrich_genre_backfill.py](pipeline/enrich_genre_backfill.py) | `tracks_with_genres.jsonl`                              | `tracks_with_genre_backfill.jsonl`                      | 4c            | DONE (Last.fm artist tags + MusicBrainz, gap only) |
| 5     | Apple Music availability      | [pipeline/check_apple_music.py](pipeline/check_apple_music.py) | `tracks_with_genre_backfill.jsonl` _(falls back to 4c/4b output if a later phase was skipped)_ | `tracks_with_availability.jsonl`                | 4d            | DONE (1,916/2,730 = 70.2%)      |
| 6     | mood classification           | [pipeline/classify_moods.py](pipeline/classify_moods.py)  | `tracks_with_availability.jsonl`, `inputs/existing_audit.csv`        | `tracks_with_moods.jsonl`                                | 5, 3c         | DONE (98.4%; 1,289 centroid · 876 audit · 522 claude_batch · 43 null) |
| 7     | saturation / curation         | [pipeline/apply_taste_profile.py](pipeline/apply_taste_profile.py) | `tracks_with_moods.jsonl`, [taste_profile.md](taste_profile.md)     | `tracks_with_taste.jsonl`                                | 6             | DONE                            |
| 8     | final merge                   | [pipeline/update_tracks.py](pipeline/update_tracks.py)    | latest per-phase JSONL                                              | `tracks.jsonl`                                           | 7             | DONE (genres 99.2%, moods 98.4%) |
| 9     | orchestrator                  | [pipeline/run_full_pipeline.py](pipeline/run_full_pipeline.py) | all of the above                                                    | `runs/full_run_*.log`                                    | 1–8           | DONE                            |

Status legend: **DONE** code committed and verified end-to-end ·
**PENDING** code complete, waits on input or upstream phase ·
**BLOCKED** cannot proceed until external action lands.

---

## Plan → actual file mapping

The action plan refers to several modules by names that differ from the files
actually in the repo. Current code names are authoritative; the plan terminology
is documented here so future work doesn't drift.

| Plan name                                | Actual file                                                                |
|------------------------------------------|----------------------------------------------------------------------------|
| `pipeline/mood_classifier.py`            | [pipeline/classify_moods.py](pipeline/classify_moods.py)                   |
| `pipeline/enrich_lastfm.py`              | [pipeline/enrich_metadata.py](pipeline/enrich_metadata.py)                 |
| `pipeline/check_apple_availability.py`   | [pipeline/check_apple_music.py](pipeline/check_apple_music.py)             |
| `pipeline/merge_final.py` (Phase 8)      | [pipeline/update_tracks.py](pipeline/update_tracks.py)                     |
| `pipeline/schemas.py`                    | [pipeline/schema.py](pipeline/schema.py) _(singular — to evolve in Phase α Step 2)_ |
| `foxXg_taste_profile_v4.md`              | [taste_profile.md](taste_profile.md) + [taste_profile_template.md](taste_profile_template.md) |

The plan's genre work (`pipeline/genre_harmonize.py`) shipped as two phases:
[pipeline/derive_genres.py](pipeline/derive_genres.py) (4c, maps existing tags)
and [pipeline/enrich_genre_backfill.py](pipeline/enrich_genre_backfill.py) (4d,
artist-level Last.fm + MusicBrainz backfill for the remaining gap). The other
planned modules (`pipeline/emotion_fusion.py`, `pipeline/recency.py`,
`pipeline/enrich_acousticbrainz.py`) are not yet built.

---

## Where files live

| Path                                | Contents                                                  | Tracked? |
|-------------------------------------|-----------------------------------------------------------|----------|
| `tracks.jsonl`                      | Canonical enriched library — Phase 8 output               | yes      |
| `scrobbles.jsonl`                   | Raw scrobble history — Phase 1 output                     | yes      |
| `taste_profile.md`                  | Human-edited curation reference (read by Phase 7)         | yes      |
| `taste_profile_template.md`         | Blank starter for taste_profile.md                        | yes      |
| `tracks_*.jsonl` (intermediates)    | Per-phase intermediate outputs                            | **no**   |
| `inputs/`                           | Owner-provided inputs (Last.fm export, Apple XML, audit, Exportify CSV) | **no** |
| `.cache/`                           | API response caches (`apple_music.json`, `lastfm.json`, `musicbrainz.json`, `discogs.json`) | **no** |
| `runs/`                             | Timestamped pipeline run logs                             | (logs gitignored) |
| `views/`                            | Generated XLSX/CSV views from `scripts/make_view.py`      | **no**   |
| `models/`                           | Trained classifier artifacts, splits, calibration plots (Phase β) | mixed (artifacts yes; large blobs may be ignored) |
| `reports/`                          | Evaluation reports (Phase β Step 9 onward)                | yes      |

Required `.env` key: `LASTFM_API_KEY`. `DISCOGS_TOKEN` and
`MUSICBRAINZ_USER_AGENT` are reserved placeholders (not wired up; leave
blank). See [.env.example](.env.example) for current status.

---

## Pipeline execution flow

```
inputs/lastfm_export.json
       │
       ▼
   Phase 1  scrobbles.jsonl
       │
       ▼
   Phase 2  tracks_skeleton.jsonl
       │
       ▼
   Phase A  tracks_with_apple.jsonl     ← inputs/apple_music_library.xml
       │
       ▼
   Phase 3a inputs/tunemymusic_upload.csv
       │
       ▼  [manual — Phase 3b]
       │
       ▼
   Phase 3c tracks_with_audio.jsonl     ← inputs/exportify.csv
       │
       ▼
   Phase 4  tracks_with_metadata.jsonl  ← Last.fm + MusicBrainz API
       │
       ▼
   Phase 4b tracks_with_discogs.jsonl   ← Discogs API (styles)
       │
       ▼
   Phase 4c tracks_with_genres.jsonl    ← map existing tags → genres (no API)
       │
       ▼
   Phase 4d tracks_with_genre_backfill.jsonl ← Last.fm artist tags + MusicBrainz (gap only)
       │
       ▼
   Phase 5  tracks_with_availability.jsonl ← iTunes Search API
       │
       ▼
   Phase 6  tracks_with_moods.jsonl     ← inputs/existing_audit.csv (training)
       │
       ▼
   Phase 7  tracks_with_taste.jsonl     ← taste_profile.md
       │
       ▼
   Phase 8  tracks.jsonl                ← canonical output
```

**Phase 1 is additive.** The owner's export workflow is a partial pull covering
"today back to the last pull", so ingest merges into `scrobbles.jsonl` and dedupes
on `(scrobbled_at, artist_normalized, track_normalized)` rather than overwriting.
A write that would leave *fewer* rows than are already on disk is refused —
`scrobbles.jsonl` is the base record every play-weighted number derives from, and
Phase 2 recomputes `play_count`, `first_scrobbled`, `last_scrobbled` and
`peak_year` from whatever survives.

```
python -m pipeline.ingest_scrobbles                        # merge (default)
python -m pipeline.ingest_scrobbles --replace              # rebuild; still refuses to shrink
python -m pipeline.ingest_scrobbles --replace --allow-shrink   # genuinely drop history
```

For incremental updates without a manual export at all, `POST /api/lastfm/sync`
([app/lastfm_sync.py](app/lastfm_sync.py)) fetches from the last stored timestamp
and appends through the same guarded path.

Run end-to-end:

```powershell
py -3.13 -m pipeline.run_full_pipeline
py -3.13 -m pipeline.run_full_pipeline --skip-tests
py -3.13 -m pipeline.run_full_pipeline --skip-pause
py -3.13 -m pipeline.run_full_pipeline --start-from 4
```

---

## Schema version policy

Canonical version lives in `SCHEMA_VERSION` ([pipeline/config.py](pipeline/config.py)).
Current value: `5` (integer, monotonic). The manifest pins the same value in
[pipeline_manifest.yaml](pipeline_manifest.yaml).

Rules:

- Every JSONL record emits `_schema_version` as its **first** field (shipped in schema v5; `read_jsonl` also loads legacy records without it).
- Writers emit fields in stable, documented order. [pipeline/schema.py](pipeline/schema.py) defines `FIELD_DEFAULTS` in canonical write order.
- Readers silently ignore unknown fields. Forward-compat is preserved by `fill_defaults()` in [pipeline/schema.py](pipeline/schema.py).
- Minor additive fields **do not** bump the schema version.
- Breaking renames or removals **do** bump the schema version. Migration tests required.
- Migration tests live in [tests/test_schema_v5.py](tests/test_schema_v5.py): v5 roundtrip · legacy-record compat · unknown future fields preserved.

Canonical track identity priority (used for all cross-phase joins):

1. MusicBrainz recording MBID (`musicbrainz_id`)
2. ISRC
3. Normalized artist + track title (`artist_normalized` + `track_normalized`)
4. Fallback hash

All phases must preserve `canonical_track_id`. No phase may drop or overwrite it.

---

## Resumability

Today, the orchestrator supports `--start-from N` to skip earlier phases. Phase
modules overwrite their output JSONL on each run.

API enrichment phases (4, 4b, 4d, 5) cache every response — successes *and*
failures — to a per-API JSON file under `.cache/`, and skip anything already
cached. Two flags override that:

| Flag | Effect |
|---|---|
| `--force-errors` | Re-fetch only cached failures, ignoring their TTL. The cheap way to clear poisoned entries. |
| `--force`        | Bypass the cache entirely and re-fetch everything. Slow — each phase logs an ETA first. |

Both are dispatched off the manifest's `accepts_force` flag, so a phase only
receives them if its callable actually takes `force=`; the anti-drift test in
[tests/test_pipeline_manifest.py](tests/test_pipeline_manifest.py) checks both
directions.

Still target state (Phase α Step 3 / Phase δ Step 17):

- Phases skip already-completed outputs by default.
- `--phase <name>` runs a single phase.
- All phases are idempotent.

---

## Branch workflow discipline

One phase = one branch = one PR. Branch names mirror plan section names:

| Phase           | Branch                          |
|-----------------|---------------------------------|
| α Foundations   | `feature/foundations`           |
| β Modeling swap | `feature/modeling-swap`         |
| γ Genre         | `feature/genre-harmonize`       |
| γ Emotion       | `feature/emotion-fusion`        |
| γ Recency       | `feature/recency`               |
| γ AcousticBrainz | `feature/acousticbrainz`       |
| δ Final merge   | `feature/final-merge`           |
| ε Taste profile | `feature/taste-profile-refresh` |

**A PR cannot merge until:** tests pass · schema validation passes · regression diff reviewed (`scripts/diff_tracks_jsonl.py`).

---

## API reliability rules

Every external enrichment phase implements:

- Disk-backed JSON cache per API (`.cache/lastfm.json`, `musicbrainz.json`,
  `discogs.json`, `apple_music.json`) — no re-fetch unless the entry has expired
  or `--force` / `--force-errors` is passed.
- Timeout handling.
- Exponential backoff, retry max = `HTTP_MAX_RETRIES` (5).
- Non-fatal failures with structured error logging.
- **Expiring negative cache.** Failures are cached too, so a re-run is cheap, but
  they carry a `_cached_at` timestamp and expire: a genuine `not_found` after
  `HTTP_NEGATIVE_TTL_SECONDS` (30 days), anything transient (`max_retries`,
  `invalid_json`) after `HTTP_TRANSIENT_TTL_SECONDS` (6 hours). A transient
  network blip therefore heals itself on the next day's run instead of freezing
  that track's enrichment permanently. Cache entries written before this format
  have no timestamp and count as expired — the first run after upgrading retries
  them once.
- **Crash-safe.** Every phase flushes its cache from a `finally`, so an
  interrupted run keeps every response it already paid for at the rate limit.
- Resumable execution — already-cached records are skipped. Each phase logs a
  cache summary (entries, cached `not_found` vs transient, hits/misses/refetches)
  so a low coverage number can be traced to genuine no-match versus cached failure.

See [pipeline/_http.py](pipeline/_http.py) for the shared HTTP layer.

---

## Provenance

Every enrichment phase writes per-field provenance:

```json
{
  "source": "...",
  "retrieved_at": "...",
  "pipeline_phase": "...",
  "confidence": 0.0
}
```

---

## Tests

```powershell
py -3.13 -m pytest tests/ -q
```

Existing suites: `test_apply_taste_profile*`, `test_check_apple_music`,
`test_classify_moods`, `test_dedupe`, `test_derive_genres`,
`test_enrich_apple_library`, `test_enrich_discogs`, `test_enrich_genre_backfill`,
`test_enrich_metadata`, `test_enrich_metadata_variations`, `test_http`,
`test_ingest_scrobbles`, `test_merge_exportify`, `test_name_variations`,
`test_normalize`, `test_pipeline_manifest`, `test_schema`, `test_tag_filter`,
`test_update_tracks`.

CI runs this suite on every push to `main` and every pull request — see
[.github/workflows/ci.yml](.github/workflows/ci.yml).

## Frontend dashboard (`web/`)

The dashboard is a React SPA. The `.jsx` sources are **pre-compiled** into a single
minified bundle (`web/app.bundle.js`) by [scripts/build_frontend.mjs](scripts/build_frontend.mjs)
using esbuild — there is no in-browser Babel transpile.

```bash
npm install          # one-time (installs esbuild)
npm run build        # compile web/*.jsx -> web/app.bundle.js (+ .map)
npm run dev          # same, but rebuilds on save (watch mode)
```

**After editing any `web/*.jsx`, run `npm run build`** (or keep `npm run dev` running)
and commit the regenerated `web/app.bundle.js`. The dashboard is served by the FastAPI
app (`uvicorn app.main:app`), which also gzip-compresses the dataset and `/api/*`
responses. See [PERFORMANCE_MAP.md](PERFORMANCE_MAP.md) for the load-time profile.

`web/data-processing.js` and `web/data-worker.js` are **plain JS, not part of the
bundle** — they hold the pure parse/aggregate transforms so a Web Worker can run the
initial-load parse off the main thread (the bundle references them as globals). Edit
them directly; no rebuild needed. On first paint the dashboard fetches
`/tracks.min.jsonl` (a slimmed projection of the tracks the UI renders) rather than the
full `tracks.jsonl`, which stays available for direct download and via `/api/*`.
