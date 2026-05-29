# Local Machine Handoff — 2026-05-29

Read this before doing anything. This is the full context needed to continue
from a local machine where the network is open.

---

## What this project is

A personal music enrichment pipeline. Pulls 2,730 unique tracks from a
Last.fm scrobble history and enriches them with audio features, mood tags,
genre labels, Apple Music availability, and curation metadata. Output is
`tracks.jsonl` — one record per track, all fields in stable schema order.

**Repo:** `bfox010/music-enrichment`
**Branch:** `claude/exportify-integration-XGam2`

---

## Current state of tracks.jsonl (2,730 tracks)

| Field              | Coverage | Status                                      |
|--------------------|----------|---------------------------------------------|
| audio_features     | 82.7%    | Done — from Exportify (Spotify)             |
| mood_tags          | 81.2%    | Done — centroid; 684 queued for Claude      |
| apple_music_available | 70.2% | Done — iTunes Search API                    |
| lastfm_tags        | 30.9%    | Done — needs re-run to improve              |
| genres             | 33.8%    | Partial — offline only (no Discogs yet)     |
| discogs_styles     | 0%       | Built, never run — blocked by network       |
| saturation_tier    | 34.0%    | Done — from taste_profile.md                |

---

## What to do first — run the pipeline from Phase 4

The cloud session that built all the code was network-blocked. No API calls
have been made in this container since the prior run. **No `.cache/` exists.**
Everything needs to re-run against live APIs.

### Prerequisites

1. Pull the branch:
   ```
   git pull origin claude/exportify-integration-XGam2
   ```

2. Confirm `.env` exists at the repo root with:
   ```
   LASTFM_API_KEY=<your key>
   DISCOGS_TOKEN=<your token>
   MUSICBRAINZ_USER_AGENT=MusicEnrichment/1.0 (your@email.com)
   ```

3. Install dependencies if not already done:
   ```
   pip install -r requirements.txt
   ```

4. Confirm `inputs/` has the Exportify CSV (needed by Phase 3c if you ever
   re-run from scratch, but NOT needed for starting from Phase 4):
   - `inputs/exportify.csv` — Exportify export (OR use committed `exportify` fallback)
   - `inputs/existing_audit.csv` — mood training labels (needed by Phase 6)

### Run command

```
python -m pipeline.run_full_pipeline --start-from 4
```

This runs phases: **4 → 4b → 4c → 5 → 6 → 7 → 8**

| Phase | What it does                        | Rate limit       | ETA        |
|-------|-------------------------------------|------------------|------------|
| 4     | Last.fm tags + MusicBrainz IDs      | 5 req/s          | ~10 min    |
| 4b    | Discogs styles                      | 1 req/s (60/min) | ~45 min    |
| 4c    | Genres distillation (offline)       | no API           | <1 min     |
| 5     | Apple Music availability check      | 0.33 req/s       | ~140 min   |
| 6     | Mood classification (centroid)      | no API           | ~2 min     |
| 7     | Saturation/curation tags            | no API           | <1 min     |
| 8     | Final merge → tracks.jsonl          | no API           | <1 min     |

Total wall time: roughly **3–4 hours** (dominated by Phase 5).

The pipeline is **resumable** — if it stops mid-run, re-run with
`--start-from <phase-id>` and it picks up from the cache. Each phase caches
responses to `.cache/<phase>.json`.

---

## After the run — what to check

```
python scripts/library_stats.py       # ASCII coverage summary
python scripts/make_view.py           # Generate views/library.xlsx
```

Key things to verify in `tracks.jsonl`:
- `discogs_styles` coverage (expect 30–50% — Discogs skews physical/vinyl)
- `genres` coverage (expect 50%+ after Discogs runs)
- `mood_tags` unchanged at 81.2% (Phase 6 should re-confirm, not regress)

---

## Remaining owner actions (after the pipeline run)

### 1. Claude mood batch — 684 tracks still unclassified

File: `inputs/claude_mood_batch.jsonl` (already present, 684 tracks)

Each line is a JSON object with `artist`, `track`, and `audio_features`.
Feed batches to Claude.ai and ask it to classify each track into one or more
of these 14 mood categories:

> Fast, Moody, Slow, Heavy Bass, Dance, Sad, Groove, Heartbreak, Dark,
> Love, Hype, Uplifting, Happy, Sunny

Save responses as `inputs/claude_mood_results.jsonl` — one line per track:
```json
{"artist": "...", "track": "...", "mood_tags": ["Hype", "Heavy Bass"]}
```
Then re-run Phase 6:
```
python -m pipeline.run_full_pipeline --start-from 6
```

### 2. Full Apple Music library XML — 4.4% iTunes coverage

Currently only 120/2730 tracks have iTunes metadata (play counts, skip counts,
date added, kind). This is because the XML export provided was a partial
library.

Export your full Apple Music library:
- Music app → File → Library → Export Library → save as XML
- Drop into `inputs/apple_music_library.xml`
- Re-run from Phase A: `python -m pipeline.run_full_pipeline --start-from A`

### 3. Missing mood centroids — Fast, Groove, Slow, Uplifting

No training playlists were provided for these 4 mood categories, so no
centroids were built. Tracks fitting these moods can only get tagged via the
Claude batch pass (item 1 above).

If you want centroid coverage too:
- Export Exportify CSVs for any playlists matching those moods
- Run mood audit builder (see `pipeline/classify_moods.py` for how to add
  training data)

---

## Pipeline architecture (quick reference)

```
1 (scrobble ingest)
→ 2 (dedupe)
→ A (iTunes XML)
→ 3a (TuneMyMusic export)
→ 3b (manual: run Exportify)
→ 3c (Exportify merge)
→ 4 (Last.fm + MusicBrainz)
→ 4b (Discogs styles)          ← NEW this session
→ 4c (genres distillation)     ← NEW this session
→ 5 (Apple Music availability)
→ 6 (mood classification)
→ 7 (saturation/curation)
→ 8 (final merge → tracks.jsonl)
```

All phases are resumable. Intermediate files are gitignored; only
`tracks.jsonl`, `scrobbles.jsonl`, `mood_audit.csv`, and `exportify` are
committed.

**Add `--skip-tests` to skip pytest before the run** (saves ~1s but loses
the safety net — not recommended for a full re-run).

---

## Key files

| Path | What it is |
|------|-----------|
| `tracks.jsonl` | Canonical output — one record per track |
| `scrobbles.jsonl` | Raw Last.fm scrobble history (13,669 rows) |
| `exportify` | Committed raw Exportify export (fallback if inputs/ missing) |
| `mood_audit.csv` | Mood training labels from 7 owner playlists |
| `taste_profile.md` | Owner's taste profile — drives saturation tiers |
| `pipeline_manifest.yaml` | Phase execution order + metadata |
| `.env` | API keys — gitignored, must exist locally |
| `inputs/` | Gitignored owner-provided inputs |
| `.cache/` | Gitignored API response caches |
| `SAVE_LOG.md` | Running session checkpoint log |
| `TODO.md` | Remaining build-out items |

---

## Tests

```
python -m pytest tests/ -q
```

300 tests, all passing as of last commit. Run before and after any code changes.
