# Capture coverage — 2026-08-24 run

Point-in-time record of the first full run of the Spotify-free audio-feature
chain (#37: Phase 5a `resolve_isrcs` → Phase 5b `enrich_audio_features`), and
the first run since #63 dropped `blacklisted`/`rejected_reason`.

**These numbers are a dated snapshot, not a live claim.** They move as soon as
new scrobbles land. Recompute rather than cite:

```bash
python scripts/coverage_snapshot.py                      # today's numbers
python scripts/coverage_snapshot.py --compare before.json  # against a baseline
```

## Headline

`audio_features` rose 23.7 points while `spotify_id` stayed flat. Those two
were previously pinned to an identical 68.84% because Exportify was the sole
source of both; that coupling is now broken.

| Field | Before | After | Δ |
|---|---|---|---|
| `isrc` | 2217 · 68.53% | 3072 · **95.20%** | +26.67 |
| `audio_features` | 2227 · 68.84% | 2986 · **92.53%** | +23.69 |
| `spotify_id` | 2227 · 68.84% | 2238 · 69.35% | +0.51 |
| `duration_ms` | 2253 · 69.64% | 3111 · 96.41% | +26.76 |
| `genres` | 2596 · 80.25% | 3170 · 98.23% | +17.99 |
| `apple_music_available` | 2618 · 80.93% | 3227 · 100.00% | +19.07 |
| `apple_music_id` | 1907 · 58.95% | 2390 · 74.06% | +15.11 |
| `discogs_styles` | 1873 · 57.90% | 2219 · 68.76% | +10.87 |
| `mood_tags` | 1909 · 59.01% | 2179 · 67.52% | +8.51 |
| `musicbrainz_id` | 2586 · 79.94% | 2666 · 82.62% | +2.68 |

Library: 3235 → 3227 rows (the drop is identity clustering, not lost tracks).

`audio_features` by source: `exportify` 2227 → 2235, `reccobeats` **0 → 751**.

**Phase B (Spotify) was skipped for the entire run** — no credentials present.
That is what makes the audio-feature gain attributable to 5a/5b rather than to
Spotify quietly backfilling.

## What the new chain delivered

Phase 5a resolved **3000 of 3227 ISRCs (93%)** with no credentials of any kind:

| Resolver | Resolved |
|---|---|
| Deezer | 2521 |
| MusicBrainz | 479 |
| unresolved | 227 |

Phase 5b then turned those into 751 new feature vectors, with 88 ISRCs that
ReccoBeats had no data for.

Two things worth acting on:

- **Deezer resolves ~5x more tracks than MusicBrainz, but that is volume, not
  quality — the documented order is right.** MusicBrainz only runs for tracks
  carrying an MBID, so Deezer naturally handles the rest. Measured on 150
  tracks MusicBrainz had resolved, Deezer agreed on 84%, disagreed on 8.7%, and
  found nothing for 7.3%. The disagreements are genuinely different releases
  (boygenius "Not Strong Enough" `USUG12209242` vs `USUG12300710`), and since an
  ISRC identifies a specific recording, taking Deezer's answer first would feed
  ReccoBeats a different master. MusicBrainz's MBID→ISRC join stays first.
- **The manual Exportify step (3a/3b/3c) is close to retirable.** Exportify
  matched 2236/3375 (66%) this run; 5a/5b covered 93% needing no account, no
  playlist round-trip, and no manual step. The 2235 Exportify rows persist only
  because 5b will not overwrite an existing block.

Both API response shapes were unverified before this run (the modules were
written without outbound access). They were confirmed correct against live
endpoints: ReccoBeats returns 12 of the 13 fields Exportify does, missing only
`time_signature`, which nothing downstream reads.

## Data integrity

The first attempt produced a corrupt library. Fixed in the same branch; the
numbers above are from the clean re-run.

| | first attempt | after fixes |
|---|---|---|
| rows | 3446 | 3227 |
| duplicate `canonical_track_id` | 128 | 2 |
| recordings split across two rows | 28 | 0 |
| owner `audit` mood labels lost | 3 | 0 |
| `curation_state` lost | 54 | 0 |
| `play_count` total | inflated | 16549 (= scrobble count) |

Three root causes, each fixed with regression tests:

1. **Phase 5 read past Phase 4e.** Its `_INPUT_PRIORITY` still led with 4d's
   output after 4e was inserted between them, so identity clustering was
   computed, written, and ignored.
2. **Phase 8 keyed the merge on the normalized name pair.** #27 rewriting
   feat-credits moved that key on 183 rows; the merge saw new tracks and
   dropped the fields that live only in `tracks.jsonl`. It now tries the
   canonical id and the name pair, claiming each existing row at most once.
3. **Phase 1 deduped on a derived key it never re-derived.** `normalize_artist`
   gaining `&` → "and" meant every scrobble on disk stopped matching its own
   re-export: 1102 historical plays across 104 artists were appended a second
   time. Both sides are now recomputed, and stored keys heal in place.

The 2 remaining duplicate ids are one recording Last.fm delivers under two
spellings ("feat." vs "with"). Deezer gives both the same ISRC — the evidence
that they are the same track — but Phase 4e cannot use it, because 4e runs
before 5a resolves ISRCs.

## Operational notes

- **Clear the intermediates before a fresh run.** `merge_exportify` reads the
  deepest existing intermediate by design, so a `tracks_with_availability.jsonl`
  left over from a previous run silently feeds the next one. This cost a full
  chain re-run to notice.
- **The suite needs Python 3.13**, and bare `python` on the owner's machine is
  3.9. Use `py -3.13`.
- A cold full run was ~7 hours, dominated by Phase 5 (iTunes at 0.33 req/sec,
  ~3.3h) and Phase 5a (~2.5h, split between MusicBrainz's hard 1 req/sec and
  Deezer's then-2 req/sec). Raising Deezer to 5 req/sec should take roughly 30
  minutes off 5a; the rest is rate limits we do not control. Warm, minutes.
