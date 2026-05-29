# TODO — remaining build-out

What's left to build or fill in on the metadata assembly line. The pipeline
phases all run end-to-end; this tracks *gaps* — API runs that need network,
data the owner needs to provide, and fields still under-populated.

Snapshot taken 2026-05-29 against `tracks.jsonl` (2,730 tracks).

---

## 1. Run full pipeline — BLOCKED on network

The cloud execution environment blocks outbound API calls (Last.fm, Discogs,
iTunes). All code is written and tested. To unblock:

- [ ] Allowlist `ws.audioscrobbler.com`, `api.discogs.com`, `itunes.apple.com`
      in the environment's network policy (see code.claude.com/docs), OR
- [ ] Pull branch locally and run with open network

Once network is available, run from Phase 4 (Last.fm already ran once, but
.cache/ is gone from this container — full re-run needed):

```
python -m pipeline.run_full_pipeline --start-from 4
```

Expected runtime at rate limits: ~45 min Phase 4, ~45 min Phase 4b, ~140 min Phase 5.

## 2. Discogs styles — BUILT, not yet run

Phase 4b (`enrich_discogs.py`) is complete with 21 tests. Will populate
`discogs_styles` via artist+title search (0.85 similarity threshold).

- **Current coverage:** `discogs_styles` 0/2730 (0.0%)
- **Unblocked by:** #1 (network)

## 3. Genres coverage — 33.8% (offline only)

Phase 4c ran offline against existing lastfm+itunes signals. Will jump to ~50%+
once Discogs runs. Genres from Discogs have the best sub-genre granularity
("Boom Bap", "Shoegaze", etc.).

- **Current coverage:** `genres` 922/2730 (33.8%)
- **Unblocked by:** #1 + #2 (network + Discogs run)

## 4. Last.fm tag coverage — 30.9%

843/2730 tracks have tags. Re-running Phase 4 with fresh cache will re-hit
the same tracks but won't improve coverage unless we add an
`artist.getTopTags` fallback for tracks with no track-level match.

- [ ] Re-run Phase 4 (covered by #1)
- [ ] Consider `artist.getTopTags` fallback — adds ~artist-level genre signal
      for the 1,887 tracks with no track tags
- **Current coverage:** `lastfm_tags` 843/2730 (30.9%)

## 5. Claude mood batch — 684 tracks unresolved

Phase 6 queued 684 tracks to `inputs/claude_mood_batch.jsonl` (no audio
features, or too far from every centroid). They have no mood tags.

- [ ] Run the batch through Claude.ai
- [ ] Save verdicts as `inputs/claude_mood_results.jsonl`
- [ ] Re-run Phase 6 — Claude verdicts override centroid guesses
- **Current coverage:** `mood_tags` 2217/2730 (81.2%), 513 still None

## 6. iTunes / Apple Music XML — 4.4% coverage

Phase A matched only 120 tracks because the library XML covers a small slice.
Full export would raise this significantly.

- [ ] Owner: export full Apple Music library as XML → `inputs/apple_music_library.xml`
- [ ] Re-run Phase A, then re-run downstream phases
- **Current coverage:** `itunes_play_count > 0` 101/2730 (3.7%)

## 7. Missing mood centroids — 4 of 14 categories untrained

No owner playlist covered **Fast, Groove, Slow, Uplifting**, so no centroid
was built for them. Tracks with these moods can only come from the Claude batch.

- [ ] Owner: provide Exportify CSVs for Fast / Groove / Slow / Uplifting playlists,
      OR confirm these should only come from the Claude batch pass

---

## Owner inputs still needed (summary)

| Input | Path | Unblocks |
|-------|------|----------|
| Network access (allowlist or local run) | — | #1, #2, #3, #4 |
| Claude mood verdicts | `inputs/claude_mood_results.jsonl` | #5 |
| Full Apple Music library XML | `inputs/apple_music_library.xml` | #6 |
| Fast/Groove/Slow/Uplifting playlists | (Exportify CSVs) | #7 |

## Done this session (2026-05-29)

- Researched Discogs API — 60 req/min auth limit, no ISRC search endpoint
- Built Phase 4b: `enrich_discogs.py` — artist+title search, 0.85 similarity
  threshold, Authorization header auth, `.cache/discogs.json`, 21 tests
- Built Phase 4c: `distill_genres.py` — merges discogs+lastfm+itunes into
  `genres`, minimal junk filter per user preference (keep mood-adjacent tags)
- Expanded JUNK_TAGS blocklist from real Last.fm data analysis — added artist
  name tags (kanye west, drake, etc.), specific year tags (2011–2025),
  nationality tags, radio station tag, descriptor noise
- Applied genres distillation offline → 922/2730 tracks now have genres
- Both API tokens saved to `.env` (gitignored)
- 300 tests passing
