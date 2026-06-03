# Audit Follow-up TODO

Adjudicated from the Codex code-review audit (2026-06-03). Every finding below
was re-verified against the actual branch contents before being accepted; the
notes record where this review **agrees, downgrades, defers, or narrows** the
original recommendation.

## Branch context (read first)

The audit's findings live on two **separate, unmerged** branches — this matters
for *where* each fix lands:

- **PR #15** `claude/handoff-md-visibility-GGjFj` — OPEN, 9 ahead / 0 behind `main`.
  Contains `app/refresh.py`, `web/dashboard.jsx` (the NUL byte), `app/metrics.py`
  (`forgotten_favorites`, `tag_graph`), `web/echarts-charts.jsx`,
  `scripts/generate_library_js.py`, and the handoff docs.
- **Cleanup branch** `claude/project-audit-optimization-gsIfp` — separate, unmerged.
  Contains the forgiving `app/data._load_jsonl()` loader (finding #6 only).

All items except #6 target **PR #15's branch**. Finding #6 targets the **cleanup
branch**.

---

## P1 — Merge blockers for PR #15

### 1. Refresh reports success after failed pipeline phases
- **Verified:** `app/refresh.py:refresh()` stores `_pipeline_run(...)` results in
  its return dict but never inspects them; it always proceeds to `export_pending()`
  and `data.reload()`. The `/api/refresh` endpoint in `app/main.py` only catches
  `RuntimeError`, which `refresh()` never raises. Pipeline phases record `False`
  rather than raising, so a failed phase still yields HTTP 200.
- **Fix:** after `_pipeline_run`, detect failed critical phases and
  `raise RuntimeError(...)` (the endpoint already maps that to HTTP 400) **before**
  `export_pending()` / `reload()`. Do not export or reload on a critical failure.
  Decide which phases are "critical" vs. acceptably skipped (missing-input
  `FileNotFoundError` skips are fine).
- **Related (also on `main`):** `pipeline/run_full_pipeline.py`'s CLI exit only
  checks the `pytest` result — any other failed phase still exits 0. Make the exit
  nonzero on any failed (critical) phase. Worth fixing alongside.
- **Test:** regression in `tests/test_refresh.py` where `_pipeline_run` returns
  e.g. `{"2": True, "3c": False, "8": False}` → refresh raises / 400, and
  `export_pending` / `reload` are **not** called.

### 2. Literal NUL byte makes `web/dashboard.jsx` binary to Git
- **Verified:** exactly one `0x00` byte in `trackKey()`; `git diff --numstat` reports
  the file as binary (`- -`).
- **Fix:** replace the literal NUL with the escape `"\x00"` (or a named `SEP`
  constant). Confirm `numstat` then shows real line counts.

### 3. No backend concurrency guard on `/api/refresh` — **downgraded severity**
- **Verified:** no lock anywhere; overlapping refreshes (two tabs, a retry) can
  interleave scrobble appends and intermediate-file writes.
- **Call:** real, but low real-world risk for a local-first app — **recommended,
  not a hard blocker**. Cheap to do: a module-level `asyncio.Lock` / single-flight
  guard in `app/refresh.py`; return **409** ("already running") while a refresh is
  active.

---

## P2 — Post-merge cleanups

### 4. API-backed dashboard pages cache stale data after refresh
- **Verified:** in `web/echarts-charts.jsx`, Albums uses `if (!active || data) return;`
  and Forgotten Favorites uses `if (!active || items !== null) return;`, caching the
  first API response. No `refreshVersion` / `dataVersion` prop exists, so these views
  stay stale until a full page reload.
- **Fix:** add a `refreshVersion` state in `dashboard.jsx`, bump it on refresh, thread
  it into the API-backed pages, add it to their `useEffect` deps, and clear cached
  child state on change.

### 5. Forgotten Favorites can emit blank artist/track rows
- **Verified:** `app/metrics.py` does `info = track_info.get(key, {})` then
  `info.get("artist") or ""` — an unmatched scrobble key produces a row with empty
  artist/track.
- **Fix:** skip unmatched keys (preferred), or derive fallback labels from the
  scrobble row.

### 6. Loader hides corruption counts — **cleanup branch only**, minor
- **Verified:** the cleanup branch's `app/data._load_jsonl()` logs and skips bad rows
  (a good availability win), but `reload()` returns only `{tracks, scrobbles}`, so
  dropped rows are invisible outside the logs.
- **Fix:** return skipped-row counts from `reload()` and surface them in `/api/reload`.
  Keep strict validation on the pipeline/CLI paths.

### 7. Metrics rebuild aggregates per request — **defer (skip for now)**
- **Verified:** data is cached in-memory at module load; there are no per-request
  JSONL re-reads. Aggregates recompute per request, but the library is tiny.
- **Call:** premature optimization at current scale (the audit agrees it's
  "acceptable"). Note a future `app.cache` index layer as an idea; **do not act now.**

---

## P3 — Low value / scope-limited

### 8. JSONL parsing duplicated — **narrow the scope**
- **Verified:** `pipeline/schema.py:read_jsonl()` exists with line-aware errors, but
  PR #15's new `pipeline/export_tunemymusic.export_pending()` and
  `scripts/generate_library_js.py` parse with raw `json.loads`.
- **Call:** switch **only the two new PR #15 files** to `read_jsonl`. Do **not** do
  the broader ~11-file sweep — that's churn unrelated to this PR.

### 9. Remove session handoff docs from PR #15
- **Verified:** `CLAUDE_DESIGN_BRIEF.md` and `Handoff.md` are present; the PR body
  itself says drop them before merge.
- **Fix:** delete both from the PR branch.

---

## Verification hints for the executor

- **#1/#2:** NUL-byte check —
  `git diff --numstat origin/main...<branch> -- web/dashboard.jsx` should show real
  line counts, not `- -`. Add the failed-phase regression to `tests/test_refresh.py`.
- **#3:** POST `/api/refresh` twice concurrently → the second returns 409.
- **#4:** load the dashboard, trigger a refresh, confirm Albums / Forgotten re-fetch.
- Keep the full suite green before and after on the PR #15 branch.
