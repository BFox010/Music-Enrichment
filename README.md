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
| 1     | scrobble ingest               | [pipeline/ingest_scrobbles.py](pipeline/ingest_scrobbles.py) | `inputs/lastfm_export.json`                                         | `scrobbles.jsonl`                                        | 0             | DONE (13,669 rows)              |
| 2     | dedupe                        | [pipeline/dedupe.py](pipeline/dedupe.py)                  | `scrobbles.jsonl`                                                   | `tracks_skeleton.jsonl`                                  | 1             | DONE (2,730 unique tracks)      |
| A     | iTunes XML enrichment         | [pipeline/enrich_apple_library.py](pipeline/enrich_apple_library.py) | `tracks_skeleton.jsonl`, `inputs/apple_music_library.xml`           | `tracks_with_apple.jsonl`                                | 2             | DONE (122/2,730 matched)        |
| 3a    | TuneMyMusic export            | [pipeline/export_tunemymusic.py](pipeline/export_tunemymusic.py) | `tracks_with_apple.jsonl`                                           | `inputs/tunemymusic_upload.csv`                          | A             | DONE                            |
| 3b    | TuneMyMusic + Exportify       | _(manual — owner)_                                        | `inputs/tunemymusic_upload.csv`                                     | `inputs/exportify.csv`                                   | 3a            | BLOCKED (owner action)          |
| 3c    | Exportify merge               | [pipeline/merge_exportify.py](pipeline/merge_exportify.py) | `tracks_with_apple.jsonl`, `inputs/exportify.csv`                   | `tracks_with_audio.jsonl`                                | 3b            | BLOCKED on 3b (code complete)   |
| 4     | Last.fm + MusicBrainz         | [pipeline/enrich_metadata.py](pipeline/enrich_metadata.py) | `tracks_with_audio.jsonl` _(or tracks_with_apple.jsonl if 3c skipped)_ | `tracks_with_metadata.jsonl`                          | A (3c if run) | DONE (2,165/2,730 = 79.3%)      |
| 5     | Apple Music availability      | [pipeline/check_apple_music.py](pipeline/check_apple_music.py) | `tracks_with_metadata.jsonl`                                        | `tracks_with_availability.jsonl`                         | 4             | DONE (1,916/2,730 = 70.2%)      |
| 6     | mood classification           | [pipeline/classify_moods.py](pipeline/classify_moods.py)  | `tracks_with_availability.jsonl`, `inputs/existing_audit.csv`        | `tracks_with_moods.jsonl`                                | 5, 3c         | PENDING (centroid → swap in β)  |
| 7     | saturation / curation         | [pipeline/apply_taste_profile.py](pipeline/apply_taste_profile.py) | `tracks_with_moods.jsonl`, [taste_profile.md](taste_profile.md)     | `tracks_with_taste.jsonl`                                | 6             | PENDING (waits on filled profile) |
| 8     | final merge                   | [pipeline/update_tracks.py](pipeline/update_tracks.py)    | latest per-phase JSONL                                              | `tracks.jsonl`                                           | 7             | DONE (initial — pre-mood)       |
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

New modules introduced by the plan (`pipeline/genre_harmonize.py`,
`pipeline/emotion_fusion.py`, `pipeline/recency.py`,
`pipeline/enrich_acousticbrainz.py`) will be added under those names.

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

Required `.env` keys: `LASTFM_API_KEY`, `DISCOGS_TOKEN`, `MUSICBRAINZ_USER_AGENT`.
See [.env.example](.env.example).

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

Run end-to-end:

```powershell
py -3.13 -m pipeline.run_full_pipeline
py -3.13 -m pipeline.run_full_pipeline --skip-tests
py -3.13 -m pipeline.run_full_pipeline --skip-pause
py -3.13 -m pipeline.run_full_pipeline --start-from 4
```

Or the standalone overnight runner: `py -3.13 run_pipeline.py`.

---

## Schema version policy

Canonical version lives in `SCHEMA_VERSION` ([pipeline/config.py:49](pipeline/config.py)).
Current value: `"1.0.0"` _(target: `5`, to be migrated in Phase α Step 2)._

Rules:

- Every JSONL record must emit `_schema_version` as its **first** field (target state — being introduced in Phase α Step 2).
- Writers emit fields in stable, documented order. [pipeline/schema.py](pipeline/schema.py) defines `FIELD_DEFAULTS` in canonical write order.
- Readers silently ignore unknown fields. Forward-compat is preserved by `fill_defaults()` in [pipeline/schema.py:95](pipeline/schema.py).
- Minor additive fields **do not** bump the schema version.
- Breaking renames or removals **do** bump the schema version. Migration tests required.
- Required migration tests (target): v4 reader on v5 file · v5 roundtrip · unknown future fields ignored.

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

Target state (rolling out in Phase α Step 3 and Phase δ Step 17):

- Phases skip already-completed outputs by default.
- `--force` flag overwrites completed outputs.
- `--phase <name>` runs a single phase.
- API enrichment phases cache to SQLite (`cache.db`) and never re-fetch unless `--force`.
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

- SQLite caching — no re-fetch unless `--force`.
- Timeout handling.
- Exponential backoff, retry max = 3.
- Non-fatal failures with structured error logging.
- Resumable execution — already-cached records are skipped.

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
`test_classify_moods`, `test_dedupe`, `test_enrich_apple_library`,
`test_enrich_metadata`, `test_http`, `test_ingest_scrobbles`,
`test_merge_exportify`, `test_normalize`, `test_schema`, `test_update_tracks`.
