# ⚠️ DEPRECATED BRANCH — do not build on this

**Status:** dead, content verified absorbed or preserved elsewhere. Safe to delete.
**Knowledge preserved in:** #37 (comment, 2026-08-23).

## What was here

A TuneMyMusic/Exportify batch-matching effort from 2026-05-29.

| Content | Disposition |
|---|---|
| `inputs/exportify_retry.csv` — audio features for 140 tracks | **Fully absorbed.** All 117 that still resolve into `main` by `spotify_id` already carry those features; the other 23 no longer match any track. |
| `TUNEMYMUSIC_PLAYBOOK.md` — 61 lines of matching findings | **Preserved verbatim in #37**, which is about replacing this chain. |
| `inputs/tunemymusic_*.csv`, `tunemymusic_review.xlsx` | Working artifacts of a manual step; `inputs/` is gitignored on `main` by design. |

The playbook cited `clean_artist_for_search()` / `clean_track_for_search()` in
`pipeline/normalize.py`. **Neither ever landed on `main`** — so its cleaning rules
describe behaviour that does not exist in the codebase.

Its genuinely useful part — the four residual failure patterns that define the
ceiling of artist+title matching (symbolic titles, acronym formatting,
ultra-generic titles, artist-name collisions) — is recorded in #37 as fixtures for
whatever resolver replaces this path.

Kept only because branch deletion is blocked from the agent environment
(HTTP 403 on ref deletes). Delete it from the GitHub UI, or:

```
git push origin --delete claude/testing-oCKP6
```

Triaged 2026-08-23 alongside #49.
