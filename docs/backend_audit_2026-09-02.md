# Backend audit — 2026-09-02

Two independent read-only reviews of `app/`, `pipeline/_http.py`, `schema.py`
and the orchestrator, against `main` @ `03ee185` (3,201 tracks / 16,549
scrobbles). They overlapped heavily; this records what was verified, what was
fixed, and what was deliberately left.

**Both reports cited line numbers that do not match this tree** (e.g.
`app/query.py:303` in a 167-line file, `pipeline/config.py:517` in a 222-line
file). Every finding below was re-verified against the source before being
acted on; the two that did not survive that check are in the last section.

## Fixed

| # | Finding | Where |
|---|---|---|
| 1 | `LASTFM_API_KEY` written to `runs/*.log` in plaintext | `pipeline/config.py` |
| 2 | `/tracks.min.jsonl` could serve a stale body under a fresh ETag, then 304 forever | `app/main.py` |
| 3 | Non-ASCII `X-Dashboard-Token` → 500 instead of 403 | `app/main.py` |
| 4 | Sync and refresh blocked the event loop (~0.1–0.6 s per call) | `app/main.py`, `app/refresh.py`, `app/lastfm_sync.py` |
| 5 | In-band API errors cached as permanent successes | `pipeline/_http.py`, `resolve_isrcs.py`, `enrich_metadata.py`, `enrich_genre_backfill.py` |
| 6 | Deterministic 4xx retried five times, then cached as transient | `pipeline/_http.py` |
| 7 | Last.fm sync had no retry and raised non-`RuntimeError` past the routes | `app/lastfm_sync.py` |
| 8 | Phase 8 had no shrink guard | `pipeline/update_tracks.py` |
| 9 | Energy filter dropped tracks with energy exactly `0.0` | `app/query.py` |
| 10 | Full-run log fragmented into one file per phase | `pipeline/config.py` |
| 11 | GZip level 9 for 4% smaller payloads at 4× the CPU | `app/main.py` |
| 12 | `in_window` re-parsed the window string per scrobble | `app/metrics.py` |
| 13 | `/tracks.min.jsonl` rebuilt 2.6 MB per request | `app/main.py` |
| 14 | `/api/lastfm/status` scanned all scrobbles on every SPA poll | `app/main.py` |
| 15 | Sync pagination unbounded; `pages_fetched` and `new` were estimates | `app/lastfm_sync.py` |
| 16 | `--replace` did not dedupe within the export | `pipeline/ingest_scrobbles.py` |
| 17 | Personal email hardcoded in two User-Agent strings | `resolve_isrcs.py`, `check_apple_music.py` |
| 18 | `Cache-Control: public` on personal listening history; no `no-store` on `/api/config` | `app/main.py` |
| 19 | `_identity_title` recomputed per pair in an O(k²) loop | `pipeline/resolve_identity.py` |
| 20 | `SEASON_BY_MONTH` defined twice; `albums()` normalized album but not artist | `app/metrics.py` |
| 21 | `from_cache` reported cache *size*, not hits | `enrich_metadata.py`, `enrich_discogs.py` |
| 22 | `args.start_from in ("1", "1")`; dead `default is False` branch in `fill_defaults` | `run_full_pipeline.py`, `schema.py` |

87 regression tests were added (918 → 1005). Each was checked to fail against
the unfixed code, not merely to pass against the fix.

## Measured on the committed data

| | before | after |
|---|---|---|
| `window_predicate("2025-summer")` over 16,549 scrobbles | 14.9 ms | 3.2 ms |
| `window_predicate("2025")` | 13.2 ms | 1.8 ms |
| gzip of the 2.65 MB `tracks.min` body | 133.5 ms → 266 KB (level 9) | 34.3 ms → 278 KB (level 6) |
| `tracks.min` body build | 49 ms per request | 49 ms per *generation* |

## Left, deliberately

- **Run the pipeline in a subprocess rather than in the server process.** A
  refresh holds one HTTP request open for minutes — past a tunnel's ~100 s idle
  cut — contends for the GIL with every dashboard request, and would take
  uvicorn down with it on a `SystemExit`. The proposed shape (202 + a job id,
  progress from the run log) is right, but it is an API change with a UI half,
  not an audit fix. Worth its own issue.
- **Standardize the 13 inline `json.loads(line)` loops onto `schema.read_jsonl`.**
  Real (the inline loops give no line number on a bad row), but it touches every
  phase module and belongs in its own pass.
- **`data.load()` at import time.** A `lifespan` startup hook is the idiomatic
  fix; it changes how every test that imports `app.main` gets its data, so it
  needs its own change.
- **Multi-worker uvicorn.** `DASHBOARD_TOKEN`, the mutation lock and the
  snapshot are all per process, so `--workers 2` breaks auth and cache coherence.
  Nothing here starts it that way; a README note or an import-time guard is the
  fix, not a code change to this layer.
- **Static mount serves `.jsx` and `app.bundle.js.map`.** Noise, not exposure.

## Did not survive re-verification

- **"`/api/lastfm/sync` maps transport errors to a 500."** The 500 was real, but
  the suggested 502 was not adopted: with every failure now leaving `sync()` as
  `RuntimeError`, the existing `except RuntimeError` → 400 covers it without a
  traceback, and changing the status would churn the SPA's error handling for no
  gain. The traceback was the bug.
- **"`forgotten_favorites._key` re-implements `_name_key`."** True, but it
  returns a joined string used as a dict key where `_name_key` returns a tuple.
  Converting it is a behaviour-neutral edit inside logic nothing else was
  changing; left alone.
