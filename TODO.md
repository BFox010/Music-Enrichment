# TODO — remaining build-out

What's left to build or fill in on the metadata assembly line. The pipeline
phases (1–8) all run end-to-end; this tracks the *gaps* — sources not yet
wired up, fields still empty, and data the owner still needs to provide.

Snapshot taken 2026-05-29 against `tracks.jsonl` (2,730 tracks).

---

## 1. Discogs enrichment — NOT BUILT

Phase 4 (`enrich_metadata.py`) only calls Last.fm `track.getInfo`. Discogs is
stubbed but never called: `discogs_styles` is seeded to `[]` and stays empty.

- [ ] Implement Discogs API client (token already planned: `DISCOGS_TOKEN`)
- [ ] Match on ISRC first (we now have 82.7% ISRC coverage from Exportify),
      fall back to artist+release search
- [ ] Populate `discogs_styles` — the most granular sub-genre labels available
      (e.g. "Crunk", "Shoegaze", "Boom Bap")
- [ ] Respect "only-if-clear-match" rule from the spec — no fuzzy guessing
- **Current coverage:** `discogs_styles` 0/2730 (0.0%)

## 2. Canonical `genres` field — NOT POPULATED

The `genres` field exists in the schema but nothing writes to it. We have raw
signals (`lastfm_tags`, `itunes_genre`, soon `discogs_styles`) but no phase
that distills them into a clean, deduplicated genre list.

- [ ] Decide precedence: discogs_styles > lastfm_tags > itunes_genre (?)
- [ ] Add a normalization/merge step (probably end of Phase 4, or a new 4b)
- [ ] Filter folksonomy noise from lastfm_tags ("seen live", "favorites", etc.)
- **Current coverage:** `genres` 0/2730 (0.0%)

## 3. Last.fm tag coverage is thin — 30.9%

Only 843/2730 tracks have any `lastfm_tags`. The rest came back empty from
`track.getInfo`. Worth re-running Phase 4 now that Exportify gave us better
identity data (ISRC, spotify_id) to improve match rate.

- [ ] Re-run Phase 4 and measure delta
- [ ] Consider `artist.getTopTags` fallback for tracks with no track-level tags
- **Current coverage:** `lastfm_tags` 843/2730 (30.9%)

## 4. iTunes / Apple Music XML — 3.7% coverage

Phase A only matched ~101 tracks because the iTunes library XML provided so
far covers a small slice of the catalog. Personal play counts / skip counts /
date-added are richer than Last.fm for owned tracks but mostly null right now.

- [ ] Owner: export full Apple Music library as XML → `inputs/apple_music_library.xml`
- [ ] Re-run Phase A, then re-run downstream phases
- **Current coverage:** `itunes_play_count>0` 101/2730 (3.7%)

## 5. Mood classification — 684 tracks unresolved

Phase 6 queued 684 tracks to `inputs/claude_mood_batch.jsonl` (no audio
features, or too far from every centroid). They have no mood tags.

- [ ] Run the batch through Claude.ai
- [ ] Save verdicts as `inputs/claude_mood_results.jsonl`
- [ ] Re-run Phase 6 — Claude verdicts override centroid guesses ("high" conf)
- **Current coverage:** `mood_tags` 2217/2730 (81.2%), 513 still None after merge

## 6. Missing mood centroids — 4 of 14 categories untrained

No owner playlist covered **Fast, Groove, Slow, Uplifting**, so no centroid was
built for them. (Some tracks carry these tags, but only from prior runs /
direct inheritance, not from this session's training.)

- [ ] Owner: provide playlists for Fast / Groove / Slow / Uplifting if wanted,
      OR confirm these should only ever come from the Claude batch pass

---

## Owner inputs still needed (summary)

| Input | Path | Unblocks |
|-------|------|----------|
| Full Apple Music library XML | `inputs/apple_music_library.xml` | #4 |
| Claude mood verdicts | `inputs/claude_mood_results.jsonl` | #5 |
| Fast/Groove/Slow/Uplifting playlists | (Exportify CSVs) | #6 |
| `DISCOGS_TOKEN` in `.env` | — | #1 |

## Done this session (for context)

- Exportify integration: smart-quote fix + auto-prepare from committed raw file
- Ran phases 3c→8 on the new export
- Built mood training set from 7 playlists → `mood_audit.csv` (committed at root)
- 10/14 mood centroids trained; tracks.jsonl at 82.7% audio features, 81.2% moods
