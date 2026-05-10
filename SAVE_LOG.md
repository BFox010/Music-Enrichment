# SAVE_LOG

Running checkpoint of pipeline build progress. Update at the end of every phase
or when something is left unfinished. Future sessions read this first.

## How to use this file

Each phase entry is one line: `[DATE] phase N: <state>`. State is one of:
`STARTED`, `BLOCKED`, `DONE`, `SKIPPED`. Notes go in the section below the log.
Keep it terse. The git log is the authoritative history; this is just a fast
"where are we" pointer.

## Log

- 2026-05-10 full-pipeline orchestrator end-to-end VERIFIED — all phases chain cleanly
- 2026-05-10 views/library_overnight.xlsx + library_stats_overnight.txt generated for AM review
- 2026-05-10 phase 7: BUILT — taste_profile.md parser + applier + 19 tests; template at `taste_profile_template.md`
- 2026-05-10 phase 8: RE-RUN with availability — tracks.jsonl finalized with all overnight data
- 2026-05-10 phase 5: DONE — 1,916/2,730 available on Apple Music (70.2%), 813 unavail, 1 error
- 2026-05-09 phase 6: BUILT — mood centroid algorithm + Claude batch I/O (waits on existing_audit.csv)
- 2026-05-09 scripts/make_view.py — XLSX/CSV view generator with filters
- 2026-05-09 scripts/library_stats.py — ASCII analytics summary
- 2026-05-09 phase 9: BUILT — `python -m pipeline.run_full_pipeline` orchestrator
- 2026-05-09 phase 3c: BUILT — Exportify CSV merge (waits on owner's Exportify run)
- 2026-05-09 phase 8: DONE (initial) — 2,730 rows in tracks.jsonl with iTunes + Last.fm + MusicBrainz
- 2026-05-09 phase 4: DONE — 2,165/2,730 matched (79.3%), 565 no-match, 0 errors
- 2026-05-09 iTunes XML enrichment: DONE — 122/2,730 matched (4.5% — expected, iTunes lib only has 278 tracks)
- 2026-05-09 phase 3a: DONE — 2,730 tracks → inputs/tunemymusic_upload.csv
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

## music-meta data is NOT centroid-ready

Investigated `C:/Users/Branden/OneDrive/Documents/Claude Code/Music/music-meta/data/`
to see if Phase 6 mood centroid training data could be reused.

- `library.csv` has 1,838 rows with audio features. **Energy bug confirmed**:
  Kendrick Lamar A.D.H.D shows energy=0.049, Grandaddy A.M. 180 shows 0.094.
  These are 10× too low (real Spotify scale ~0.4–0.5). Per CLAUDE.md spec,
  these values are unusable as centroid training input.
- `mood_classifiers` field uses Essentia format (`mood_aggressive`, `mood_relaxed`,
  `prob_happy`, `prob_sad`, etc.) — NOT the spec's 14-category set
  (Fast/Moody/Slow/Heavy Bass/Dance/Sad/Groove/Heartbreak/Dark/Love/Hype/
  Uplifting/Happy/Sunny). So even with corrected energy, the moods don't
  map directly.
- `taste_profile.json` is JSON not Markdown — different format from the
  Phase 7 spec. Has top_genres with counts but not playlist/saturation.
- **Conclusion**: Phase 6 needs (a) corrected audio features from Exportify
  AND (b) the 14-category audit CSV when available, OR a Claude-classified
  bootstrap subset to compute the first centroids.

## Overnight observations

- **Last.fm `track.getInfo` returns BOTH tags AND MBIDs in one call** — saves
  doubling API rate. So Phase 4 covers what the spec lists as separate
  Last.fm + MusicBrainz lookups. MusicBrainz API not used directly.
- **Discogs deferred** — spec said only-if-clear-match anyway. Will add as
  optional pass later if there's appetite. `discogs_styles: []` for now.
- **iTunes match rate of 4.5%** is intentional: your iTunes library only has
  278 tracks (mostly local files / purchases) while Last.fm has 2,730 unique
  scrobbles (everything streamed). The 122 overlapping tracks now have full
  iTunes metadata (duration_ms, release_year, explicit, itunes_genre,
  itunes_play_count, itunes_skip_count, itunes_date_added, itunes_kind).
- **Phase 5 ETA at 0.33 req/s = ~140 min for 2,730 tracks** — start after
  Phase 4 completes; cache means re-running is cheap.

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
- [x] **A** iTunes XML enrichment → `tracks_with_apple.jsonl` (122/2,730 matched — see notes below)
- [x] **3a** TuneMyMusic CSV export script (output: `inputs/tunemymusic_upload.csv`)
- [ ] **3b** owner runs TuneMyMusic + Exportify (manual — pending owner)
- [x] **3c** Exportify CSV merge — code written, waits on Exportify CSV
- [x] **4** metadata enrichment → `tracks_with_metadata.jsonl` (2,165 matched, 79.3%)
- [x] **5** Apple Music availability → `tracks_with_availability.jsonl` (1,916/2,730 = 70.2%)
- [~] **6** mood classification — code complete, waits on (a) Exportify audio features, (b) existing_audit.csv with 14-category mood labels
- [~] **7** saturation/curation from `taste_profile.md` — code complete, needs owner to fill in `taste_profile.md` (template provided)
- [x] **8** final merge → tracks.jsonl (re-run with availability — current state below)
- [x] **9** orchestrator (`python -m pipeline.run_full_pipeline`)

## Final coverage in tracks.jsonl

| Field                  | Coverage | Note                                      |
|------------------------|----------|-------------------------------------------|
| Apple Music checked    | 100%     | iTunes Search API hit on every track      |
| Apple Music available  | 70.2%    | Probable, not confirmed                   |
| MusicBrainz ID         | 76.8%    | From Last.fm track.getInfo                |
| Last.fm tags           | 30.9%    | Community-tagged subset                   |
| iTunes XML metadata    | 4.5%     | Local library only has 278 tracks         |
| Spotify ID             | 0%       | Phase 3c — waits on Exportify CSV         |
| Audio features         | 0%       | Phase 3c — waits on Exportify CSV         |
| Mood tags              | 0%       | Phase 6 — waits on Exportify + audit      |
| Saturation tier        | 0%       | Phase 7 — waits on taste_profile.md       |
- [ ] **6** mood classification (centroid + Claude batch) → `tracks_with_moods.jsonl`
- [ ] **7** saturation/curation state from `taste_profile.md`
- [ ] **8** final merge → `tracks.jsonl`
- [ ] **9** orchestrator (`python -m pipeline.run_full_pipeline`)
