# SAVE_LOG

Running checkpoint of pipeline build progress. Update at the end of every phase
or when something is left unfinished. Future sessions read this first.

## How to use this file

Each phase entry is one line: `[DATE] phase N: <state>`. State is one of:
`STARTED`, `BLOCKED`, `DONE`, `SKIPPED`. Notes go in the section below the log.
Keep it terse. The git log is the authoritative history; this is just a fast
"where are we" pointer.

## Log

- 2026-05-08 phase 0: STARTED — scaffolding (config, normalize, tests, .gitignore, requirements)
- 2026-05-08 python 3.13.13 installed via winget; `py -3.13` is now default
- 2026-05-08 venv created at `.venv/`, deps installed (pandas 3.0.2, requests 2.33.1, pytest 9.0.3, etc.)
- 2026-05-08 phase 0: DONE — 21/21 tests pass; `from pipeline import normalize, config` works
- 2026-05-08 phase 1: DONE — 13,669 scrobbles ingested (0 skipped), scrobbles.jsonl written
- 2026-05-08 phase 2: DONE — 2,730 unique tracks, tracks_skeleton.jsonl written
- 2026-05-08 GitHub push blocked — auth token not configured; needs `gh auth login` or PAT in credential manager
- 2026-05-08 Apple Music library XML inspected and copied to inputs/apple_music_library.xml
- 2026-05-08 .env created (blank values, user to fill); .env.example committed as template

## Apple Music XML — useful fields confirmed

The iTunes XML export (`inputs/apple_music_library.xml`) contains per-track:
- `Total Time` (ms) → `duration_ms`
- `Year` / `Release Date` → `release_year`
- `Explicit` (boolean, present when true) → `explicit`
- `Genre` → iTunes genre tag (supplement to Last.fm tags)
- `Persistent ID` → local iTunes library UUID (NOT Apple Music streaming ID)
- `Play Count`, `Skip Count` → secondary cross-reference (Last.fm is source of truth)
- `Date Added` → when added to iTunes library
- `Kind` → "Apple Music AAC audio file" = streaming; "Purchased AAC audio file" = bought;
  "MPEG audio file" = local rip; etc.
- Playlists section at end of XML (to be parsed for `playlists/` CSVs in Phase 7)

Plan: add `pipeline/ingest_apple_library.py` to enrich tracks_skeleton with
duration_ms, release_year, explicit, itunes_genre from the XML during Phase 4
or as a sub-step. The `Persistent ID` is a local ID only — do not confuse with
`apple_music_id` from iTunes Search API (Phase 5).

Note: spec said `apple_music_library.csv` but actual export is XML. Config path
updated to `apple_music_library.xml`.

## Open notes / deviations

- **Python:** 3.13.13 (installed 2026-05-08). Older 3.8/3.9/3.10 kept side-by-side
  via the `py` launcher. Always invoke as `py -3.13` to be explicit. Project venv
  lives at `.venv/` (gitignored) and runs `py -3.13 -m venv .venv`.

- **Prior version (`music-meta/`)** lives at
  `C:/Users/Branden/OneDrive/Documents/Claude Code/Music/music-meta/`.
  Contains: `library.csv|jsonl|parquet`, `cache.db` (SQLite of API responses),
  per-phase scripts in `scripts/clients/` and `scripts/pipeline/`,
  `taste_profile.json`. The 1,332-track audit referenced in the spec is most
  likely in `library.csv` or `library.parquet`. Do NOT migrate code wholesale
  — re-read for ideas only. Confirmed bug: prior energy values were ~0.04
  (off by ~10× from real Spotify scale 0.4–0.5). Validate before reusing as
  centroid training data.

- **Owner-provided inputs not yet present.** Pipeline expects these in
  `inputs/` (gitignored) when their phase runs:
    - `lastfm_export.json` (Phase 1)
    - `apple_music_library.csv` (Phase 5+)
    - `existing_audit.csv` (Phase 6 centroids — likely sourced from `music-meta/library.csv`)
    - Exportify CSV (Phase 3c, named at runtime)
  `.env` keys also TBD: `LASTFM_API_KEY`, `DISCOGS_TOKEN`, `MUSICBRAINZ_USER_AGENT`.

## Phase checklist (mirror of CLAUDE.md, for quick scanning)

- [x] **0** scaffolding (committed)
- [x] **1** scrobble ingest → `scrobbles.jsonl` (13,669 rows)
- [x] **2** dedupe → `tracks_skeleton.jsonl` (2,730 unique tracks)
- [ ] **3a** TuneMyMusic CSV export script
- [ ] **3b** owner runs TuneMyMusic + Exportify (manual)
- [ ] **3c** Exportify CSV merge → `tracks_with_audio.jsonl`
- [ ] **4** metadata enrichment → `tracks_with_metadata.jsonl`
- [ ] **5** Apple Music availability → `tracks_with_availability.jsonl`
- [ ] **6** mood classification (centroid + Claude batch) → `tracks_with_moods.jsonl`
- [ ] **7** saturation/curation state from `taste_profile.md`
- [ ] **8** final merge → `tracks.jsonl`
- [ ] **9** orchestrator (`python -m pipeline.run_full_pipeline`)
