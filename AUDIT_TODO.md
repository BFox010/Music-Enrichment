# Audit Follow-up TODO

Adjudicated from the Codex code-review audit (2026-06-03), then executed in part.
Findings were re-verified against the actual code before being accepted; notes
record where this review **agreed, downgraded, deferred, or narrowed** the
original recommendation.

**Status (2026-06-04):** the P1 blockers, the cleanup pass, and the P2/P3
follow-ups are **merged to `main`** (PR #15, #16, and the cleanup batch). The only
open item is #7, which is intentionally deferred.

---

## ✅ Done (merged to main)

- **#1 Refresh false-success** — pipeline phases now report `ok`/`skipped`/`failed`;
  `app.refresh.refresh()` raises on a genuinely failed phase (→ HTTP 400) **before**
  exporting or reloading. Benign skips (Phase 3c with no Exportify CSV) still pass.
  Also fixed `run_full_pipeline`'s CLI to exit nonzero on any failed phase, not just
  a failed `pytest`. (PR #15)
- **#2 NUL byte in `web/dashboard.jsx`** — replaced the literal `0x00` in `trackKey()`
  with a `\x00` escape; the file is text again (diff vs main: `226/24`, not binary).
  (PR #15)
- **#3 Refresh concurrency guard** — process-level `asyncio.Lock`; a concurrent
  refresh raises `RefreshInProgress`, mapped to **HTTP 409**. (PR #15)
- **#6 Loader hardening + observability** — `_load_jsonl()` logs and skips malformed /
  non-object rows (PR #16); `reload()` and `/api/reload` now also report
  `skipped: {tracks, scrobbles}` so dropped rows aren't invisible. (cleanup batch)
- **#9 Session handoff docs** — `CLAUDE_DESIGN_BRIEF.md` and `Handoff.md` dropped
  before merge; not versioned in `main`. (PR #15)
- **Cleanup pass** — untracked generated cruft (raw `exportify`, `runs/unmatched_*`,
  `.bak` snapshots), archived one-off scripts under `scripts/archive/`. (PR #16)
- **#4 Stale dashboard cache after refresh** — `dashboard.jsx` bumps a `refreshVersion`
  after a successful refresh; `AlbumsPage` / `ForgottenFavoritesPage` cache per-version
  (still cached on navigation) and re-fetch when it changes. (cleanup batch)
- **#5 Forgotten Favorites blank rows** — `forgotten_favorites()` now falls back to the
  scrobble's own artist/track when a key isn't in `tracks.jsonl`, and skips rows with no
  usable label at all. (cleanup batch)
- **#8 JSONL parsing duplicated** — `export_tunemymusic` (both readers) and
  `generate_library_js.py` now use `pipeline.schema.read_jsonl` (line-aware errors)
  instead of raw `json.loads`. (cleanup batch)

Regression coverage: failed-phase abort / concurrent 409 (`tests/test_refresh.py`);
label fallback + skipped-row counts (`tests/test_audit_cleanups.py`). Full suite green
(459 passed).

---

## Open / deferred

**#7 — Metrics rebuild aggregates per request — DEFERRED (no action)**
- Data is cached in-memory at module load; no per-request JSONL re-reads. Aggregates
  recompute per request, but the library is tiny — premature optimization at current
  scale. Revisit with a future `app.cache` index layer only if the library grows.

---

## Housekeeping note

The two superseded precursor branches (`claude/exportify-integration-XGam2`,
`claude/testing-oCKP6`) were verified as fully superseded by main's merged genre work
(`enrich_discogs.py`, `derive_genres.py`, `enrich_genre_backfill.py`, `normalize.py`).
Safe to delete from the GitHub UI.
