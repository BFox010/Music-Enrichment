HANDOFF — Music Enrichment pipeline

Fast "where are we / what's next" pointer for the next Claude Code session.
The git log is authoritative history; [SAVE_LOG.md](SAVE_LOG.md) is the terse
per-phase checkpoint; [README.md](README.md) is the full phase table. This file
is the human-readable summary of current state + open threads.

_Last updated: 2026-05-31_

---

## Where we are

- **Active branch:** `feat/genre-derivation` (pushed, clean)
- **Base branch:** `integration/regen-tracks-223412`
- **Canonical output:** [tracks.jsonl](tracks.jsonl) — 2,730 unique tracks
- **Tests:** 324 pass (14 new genre tests added this session)
- **Pipeline phases complete:** 1, 2, A, 3a, 3b(manual), 3c, 4, 4b, 5, 6, 7, 8, 9

### Recent commits (newest first)
- `9838a11` feat(scripts): add match variation diagnostic for 308 unmatched tracks
- `41344b7` feat(pipeline): add Phase 4c genre derivation from existing tag data
- `b612dcd` docs: add HANDOFF.md for next Claude Code session
- `3135659` feat(moods): AI-classify the 565-track Claude batch (522 classified)
- `eecbfc5` feat(tags): filter Last.fm tag noise before write
- `4216fd6` feat(pipeline): add Phase 4b Discogs styles + linearize enrichment chain

---

## What was built this session

### Phase 4c — Genre derivation — [pipeline/derive_genres.py](pipeline/derive_genres.py)
New pipeline phase that populates the `genres` field by mapping existing data:
- Priority 1: `itunes_genre` (120 tracks, authoritative)
- Priority 2: `discogs_styles` (1,923 tracks)
- Priority 3: `lastfm_tags` (843 tracks)

Maps to 10 canonical genres: Hip-Hop/Rap · R&B/Soul · Electronic ·
Indie/Alternative · Rock · Pop · Jazz · Country/Folk · Metal · Experimental

**Projected coverage on current tracks.jsonl:**
- 2,048 / 2,730 (75%) get genres from existing data
- 682 tracks get `genres: []` — see breakdown below

Wired into manifest as phase `4c` between `4b` (Discogs) and `5` (Apple check).
Phase 5 now reads `tracks_with_genres.jsonl` instead of `tracks_with_discogs.jsonl`.
Tests: `tests/test_derive_genres.py` (14 tests, all passing).

### Genre gap analysis — 682 tracks with no genre source
Of the 682 tracks `derive_genres` can't tag:
- **326** — Last.fm DID find them (they have a MusicBrainz ID) but they have
  zero community tags. Name variations won't help; these are just untagged on
  Last.fm. Genre can be inferred from artist name via Claude batch later.
- **308** — Genuine pipeline misses: no MBID, no tags, no Discogs match.
  Root cause is **name formatting failures**, not obscurity. Patterns:
  - `A$AP Rocky` tracks: artist name works but long `feat.` strings in track
    title break the Last.fm match (e.g. `1 Train (feat. Kendrick Lamar, Joey
    Bada$$, Yelawolf, Danny Brown, Action Bronson & Big K.R.I.T.)`)
  - Collaboration credits: `JAY-Z & Kanye West`, `070 Shake & Tame Impala`
    (Last.fm indexes under the primary artist only)
  - `ichika Nito` (30 tracks) — Japanese instrumental guitarist, scrobbled
    from YouTube in May 2023. Owner doesn't recognise them; likely ambient
    autoplay. Candidate for removal, not genre-tagging.

### Diagnostic script — [scripts/test_match_variations.py](scripts/test_match_variations.py)
Tests 7 name variations per track against Last.fm for the 308 genuine misses:
1. `original` — baseline (already failed)
2. `strip_feat` — strips `(feat. ...)` and `[feat. ...]` from track title
3. `strip_parens` — strips all `(...)` and `[...]` from track title
4. `first_artist` — splits `A & B` → uses `A` only
5. `first_artist+strip_feat` — combines #2 and #4
6. `clean_artist` — replaces `$` → `S` in artist name (`A$AP` → `ASAP`)
7. `clean_artist+strip_feat` — combines #2 and #6

**This script needs to be run locally** (requires `LASTFM_API_KEY` in `.env`):
```powershell
.venv\Scripts\python.exe scripts/test_match_variations.py --limit 20  # quick test
.venv\Scripts\python.exe scripts/test_match_variations.py              # full 308
```

Output: `inputs/match_variation_results.csv` — one row per variation attempt,
`hit: Y/N`. The summary printed to stdout shows which variation rule recovered
the most tracks. That tells us exactly what normalization to add permanently
to `pipeline/enrich_metadata.py`.

---

## Current field coverage in tracks.jsonl

| Field | Coverage | Notes |
|---|---|---|
| Mood tags | **98.4%** (2,687/2,730) | 876 audit · 1,289 centroid · 522 claude_batch · 43 null |
| genres | **0%** | Phase 4c written but not yet run against current tracks |
| Apple Music available | 70.2% | iTunes Search API |
| Discogs styles | 70.4% (1,923) | Phase 4b |
| MusicBrainz ID | 76.8% | from Last.fm track.getInfo |
| Spotify ID | 81.6% | from Exportify |
| Audio features | 0% | Spotify AF API dead late-2024 |

---

## What's next (priority order)

### Immediate
1. **Run `test_match_variations.py` locally** — tells us which normalization
   rules to add to `enrich_metadata.py`. Expected result: ~150-200 of the 308
   will be recoverable with `strip_feat` and `first_artist` rules.
2. **Add normalization to `enrich_metadata.py`** — based on the CSV results,
   add a retry loop: try original → strip_feat → first_artist → etc. until a
   hit is found. This closes the 308-track gap without touching the 326 untagged.
3. **Run Phase 4c** — execute `derive_genres` against current
   `tracks_with_discogs.jsonl` to produce `tracks_with_genres.jsonl`, then
   re-run phases 5→8 to get genres into `tracks.jsonl`.
4. **Decide on ichika Nito** — 30 tracks, owner doesn't recognise them.
   Remove from `tracks.jsonl`? Set `blacklisted: true`? Or leave as-is?

### Medium
5. **Genre gap for 326 untagged tracks** — these have MBIDs so Last.fm knows
   them, they just have no community tags. Claude batch can classify by artist
   name alone (A$AP Rocky → Hip-Hop/Rap is trivial). Separate PR when ready.
6. **Merge open PRs** — #5 → #6 → #10 (stacked), then #7, #8, #9 — all into
   `main`. Confirm with owner before merging.
7. **Merge `integration/regen-tracks-223412` → `main`** — confirm with owner.

### Low priority / accepted
- 43 mood-null tracks — owner signed off; skits/interludes
- Audio features — Spotify AF API dead; no alternative sourced yet
- Scratch files (`mood_work.tmp.txt` etc.) — gitignored, safe to delete

---

## Branch strategy
```
main
└── integration/regen-tracks-223412   ← accumulation branch, all real work
    └── feat/genre-derivation          ← current branch (Phase 4c + diagnostic)
```
Open PRs (#5–#10) are separate feature branches, not yet merged to `main`.
No PR from `integration` to `main` opened yet — owner confirms first.

---

## Norms (owner preferences observed)
- Commit only when asked; commit messages end with the session URL.
- Owner reviews/approves before commits; surfaces loose ends explicitly.
- Keep `inputs/` and intermediate `tracks_*.jsonl` gitignored; only `tracks.jsonl`
  is canonical and tracked.
- Prefer non-destructive, reproducible changes (filters on read/write, scripts as
  source of truth) over editing generated data by hand.
- `gh` CLI not installed. Remote is HTTPS via Git Credential Manager.
- Windows dev machine: use `.venv\Scripts\python.exe`, not `python`.
