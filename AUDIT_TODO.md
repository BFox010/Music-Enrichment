# Audit Follow-up TODO

Adjudicated from the Codex code-review audit (2026-06-03), then executed in part.
Findings were re-verified against the actual code before being accepted; notes
record where this review **agreed, downgraded, deferred, or narrowed** the
original recommendation.

**Status (2026-06-04):** the P1 blockers and the cleanup pass are **merged to
`main`** via PR #15 and PR #16. What remains below is the post-merge follow-up
list — none are blockers.

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
- **#6 (loader hardening half)** — `app/data._load_jsonl()` logs and skips malformed /
  non-object rows instead of crashing the API at import. (PR #16)
- **#9 Session handoff docs** — `CLAUDE_DESIGN_BRIEF.md` and `Handoff.md` dropped
  before merge; not versioned in `main`. (PR #15)
- **Cleanup pass** — untracked generated cruft (raw `exportify`, `runs/unmatched_*`,
  `.bak` snapshots), archived one-off scripts under `scripts/archive/`. (PR #16)

Regression coverage added: failed-phase abort, skipped-only success, concurrent 409
(`tests/test_refresh.py`). Full suite green on merged `main` (452 passed).

---

## Remaining follow-ups (post-merge, non-blocking)

### P2

**#4 — API-backed dashboard pages cache stale data after refresh**
- In `web/echarts-charts.jsx`, Albums (`if (!active || data) return;`) and Forgotten
  Favorites (`if (!active || items !== null) return;`) cache the first API response;
  no `refreshVersion` prop exists, so they stay stale until a full page reload.
- **Fix:** add a `refreshVersion` state in `dashboard.jsx`, bump it on refresh, thread
  it into the API-backed pages, add it to their `useEffect` deps, clear cached child
  state on change.

**#5 — Forgotten Favorites can emit blank artist/track rows**
- `app/metrics.py` does `info = track_info.get(key, {})` then `info.get("artist") or ""`,
  so an unmatched scrobble key yields a row with empty artist/track.
- **Fix:** skip unmatched keys (preferred), or derive fallback labels from the scrobble row.

**#6 (observability half) — surface skipped-row counts**
- The hardened loader skips bad rows silently except in logs; `reload()` returns only
  `{tracks, scrobbles}`.
- **Fix:** return skipped-row counts from `reload()` and surface them in `/api/reload`.
  Keep strict validation on the pipeline/CLI paths.

**#7 — Metrics rebuild aggregates per request — DEFERRED (no action)**
- Data is cached in-memory at module load; no per-request JSONL re-reads. Aggregates
  recompute per request, but the library is tiny — premature optimization at current
  scale. Revisit with a future `app.cache` index layer only if the library grows.

### P3

**#8 — JSONL parsing duplicated (narrow scope)**
- `pipeline/schema.py:read_jsonl()` exists with line-aware errors, but the merged
  `pipeline/export_tunemymusic.export_pending()` and `scripts/generate_library_js.py`
  parse with raw `json.loads`.
- **Fix:** switch **only those two files** to `read_jsonl`. Do **not** do the broader
  ~11-file sweep — that's unrelated churn.

---

## Housekeeping note

The two superseded precursor branches (`claude/exportify-integration-XGam2`,
`claude/testing-oCKP6`) were verified as fully superseded by main's merged genre work
(`enrich_discogs.py`, `derive_genres.py`, `enrich_genre_backfill.py`, `normalize.py`).
Safe to delete from the GitHub UI.
