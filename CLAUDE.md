# CLAUDE.md

Orientation for agent sessions. Read this before the README.

## What this is

**Listening Atlas** — a personal scrobble-history dashboard. A React SPA over a
FastAPI serving layer, backed by an enrichment pipeline that turns a Last.fm
export into an annotated track library.

The product is **the dashboard**. The pipeline exists to feed it.

**What this is not:** a playlist generator. An earlier iteration aimed at a
natural-language playlist builder that pushed to Spotify/Apple. That direction
was dropped. If you find a doc or comment describing that goal, it is stale —
fix it rather than building toward it.

Two things look like playlist machinery but are not:

- **Phases 3a/3b/3c** (TuneMyMusic → Exportify) acquire *audio features* and
  three other fields. The playlist round-trip was a means, not a feature. See
  *Pipeline chain*.
- **Phase 7** (`apply_taste_profile`) produces `saturation_tier` and
  `curation_state` — dashboard-facing curation metadata. Its `playlists` field
  is a grouping label read out of `taste_profile.md`, not a generated playlist.
  Don't reintroduce `blacklisted`; #63 removed it as a playlist-era leftover.

## Invariants

1. **JSONL is the source of truth**, not a database. `tracks.jsonl` and
   `scrobbles.jsonl` are git-tracked. The serving layer is a derived read-only
   view over them.
2. **Markdown is truth for human judgement.** `taste_profile.md` is hand-edited
   and never written by the pipeline; JSONL is the derived index, regenerated
   each run. One exception: Phase 8 keeps an existing `curation_state`
   (`HUMAN_EDITED_FIELDS`), so deleting an entry from the markdown doesn't clear
   it. That ownership conflict is #107.
3. **Phases 1 and 8 are additive.** The owner's export is a partial pull ("today
   back to the last pull"), so ingest merges and dedupes rather than overwriting.
   A write that would leave fewer rows than are on disk is refused
   (`ScrobbleShrinkError`). Phase 8 has the same guard (`TrackShrinkError`): its
   merge walks the *source* rows only, so a partial intermediate — the output of
   a phase called with `limit=` — would otherwise rewrite `tracks.jsonl` short
   and take every curated field on the dropped rows with it. Both take
   `allow_shrink=True` for a deliberate shrink.
4. **Only Phase 4e writes `canonical_track_id`.** Every other phase must carry
   it through untouched. Identity priority: MusicBrainz recording MBID → ISRC →
   normalized artist+track → fallback hash. 4e recomputes it on every row, every
   run, so it is not stable: it moves `norm:` → `isrc:` → `mbid:` as identifiers
   arrive, and Phase 8 overwrites the stored id with the new one. That is why 5a
   resolves ISRCs before 4e, and why Phase 8 also matches on the name pair
   (`update_tracks._candidate_keys`). Don't key human edits on the id alone
   (#108).
5. **The manifest drives the orchestrator.** Adding a phase means adding it to
   `pipeline_manifest.yaml` *and* implementing the module. The anti-drift test in
   `tests/test_pipeline_manifest.py` fails otherwise.

## Layout

| Path | What |
|---|---|
| `pipeline/` | Enrichment phases. One module per phase, driven by `pipeline_manifest.yaml` |
| `app/` | FastAPI serving layer — `main.py` routes, `metrics.py` aggregations, `query.py` track table, `data.py` in-memory load |
| `web/` | React SPA. `.jsx` sources compile to `app.bundle.js`. Visual brief: `web/DESIGN.md` |
| `scripts/` | Operational helpers (view generation, label queues, eval harnesses) |
| `scripts/archive/` | One-off utilities kept for provenance. Not imported by anything |
| `tests/` | pytest suite, self-contained (no network, no secrets) |
| `docs/` | Provenance — audit findings, adjudicated decisions, dated measurements |
| `pipeline_manifest.yaml` | Phase order, dependencies, `optional`/`accepts_force` flags |
| `tracks.jsonl` | Canonical enriched library — Phase 8 output |
| `scrobbles.jsonl` | Raw play history — Phase 1 output |
| `taste_profile.md` | Hand-edited curation reference, read by Phase 7 |
| `mood_audit.csv` | Hand-labelled mood training set, read by Phase 6 |

Gitignored and absent from a fresh clone: `inputs/` (owner-provided exports),
`.cache/` (API responses), `runs/` (logs), `views/`, `tracks_*.jsonl`
intermediates, and the two review logs a run regenerates —
`identity_review.jsonl` (4e near-misses) and `taste_profile_unmatched.jsonl`
(Phase 7 entries that matched nothing, #65).

## Pipeline chain

```
inputs/lastfm_export.json
  1  ingest_scrobbles      → scrobbles.jsonl                (additive; refuses to shrink)
  2  dedupe                → tracks_skeleton.jsonl
  A  enrich_apple_library  → tracks_with_apple.jsonl        ← inputs/apple_music_library.xml
  B  enrich_spotify_ids    → tracks_with_spotify.jsonl      ← Spotify Search (optional, legacy last resort)
  3a export_tunemymusic    → inputs/tunemymusic_upload.csv  LEGACY (optional)
  3b (manual — owner)      → inputs/exportify.csv           LEGACY (optional; no longer blocks a run)
  3c merge_exportify       → tracks_with_audio.jsonl        LEGACY (reads this run's B or A output)
  4  enrich_metadata       → tracks_with_metadata.jsonl     ← Last.fm track.getInfo (tags + MBIDs)
  4b enrich_discogs        → tracks_with_discogs.jsonl      ← Discogs (styles, optional)
  4c derive_genres         → tracks_with_genres.jsonl       (no API — maps existing tags)
  4d enrich_genre_backfill → tracks_with_genre_backfill.jsonl ← artist-level, gap only
  5a resolve_isrcs         → tracks_with_isrcs.jsonl        ← MusicBrainz → ISRC, then Deezer
  4e resolve_identity      → tracks_resolved.jsonl          (clusters on the ISRC 5a just resolved)
  5  check_apple_music     → tracks_with_availability.jsonl ← iTunes Search
  5b enrich_audio_features → tracks_with_features.jsonl     ← ReccoBeats, keyed by ISRC
  6  classify_moods        → tracks_with_moods.jsonl        ← mood_audit.csv (see gotcha)
  7  apply_taste_profile   → tracks_with_taste.jsonl        ← taste_profile.md
  8  update_tracks         → tracks.jsonl                   canonical
```

Phase B's real output is the **ISRC**; `spotify_id` is incidental and stored only
because ReccoBeats accepts it. B is the last-resort resolver: 5a/5b (#37) are
the primary route now — MusicBrainz `musicbrainz_id` → ISRC, needing no auth,
then Deezer name search, then ReccoBeats for the feature vector. B stays in the
manifest, `optional: true`, for whatever those miss.

**Don't retire 3a/3b/3c**, even though #37 superseded them for audio features.
`inputs/exportify.csv` is the only bulk source of `explicit`, `release_year`
(otherwise only the iTunes XML) and `spotify_id` (otherwise only Phase B, which
needs credentials). Retiring 3c needs a replacement source for those first. The
measurement is in `docs/capture_coverage_2026-08-24.md`.

## Commands

```bash
python -m pytest tests/ -q            # full suite, self-contained
ruff check .                          # lint, as CI runs it
python -m pipeline.run_full_pipeline  # end-to-end; --start-from N, --skip-tests, --skip-pause
uvicorn app.main:app                  # dashboard at http://127.0.0.1:8000
npm install && npm run build          # compile web/*.jsx → web/app.bundle.js
npm run dev                           # same, watch mode
```

**Before any visual change under `web/`, read `web/DESIGN.md`** — the shared
brief (principles, banned defaults, quality floor) that keeps the dashboard one
look whichever agent is editing it. New visual ideas start in `web/prototypes/`.

**After editing any `web/*.jsx`, run `npm run build` and commit the regenerated
`web/app.bundle.js`.** There is no in-browser transpile — an un-rebuilt bundle
means your change simply does not appear.

`web/data-processing.js` and `web/data-worker.js` are **not** part of the bundle.
They are plain JS holding the pure transforms, loaded as globals before the
bundle so a Web Worker can `importScripts` the same code. Edit directly; no
rebuild needed.

## Enrichment phase contract

Every phase that touches an external API:

- Caches every response — **successes and failures** — to `.cache/<api>.json`.
- Expires negative entries: stable negatives (`not_found`, `bad_request`)
  after 30 days, transient ones (`auth_error`, `max_retries`, `invalid_json`)
  after 6 hours. A network blip heals itself instead of freezing a track's
  enrichment permanently. `auth_error` is transient on purpose: it is never
  retried *within* a run, but the operator fixing the credential must take
  effect on the next one, not in a month.
- Fails fast on a deterministic 4xx. Only 429/408/425 and 5xx go round the
  backoff ladder, honouring a 429/503 `Retry-After` up to `HTTP_BACKOFF_MAX`; a
  400/401/403 is a property of the request or the credential, so retrying it
  spends ~10 s per cache key to reach the same refusal. A 401/403 is logged at
  ERROR and not retried within the run. After `HTTP_AUTH_BREAKER_THRESHOLD`
  consecutive auth errors the client's circuit opens: no more requests that run,
  and uncached keys get an `auth_error` that is not cached.
- Classifies **in-band** errors via `RateLimitedClient.get(..., classify=)`.
  Deezer answers a rate limit with HTTP 200 and `{"error": {"code": 4}}`,
  Last.fm with `{"error": 29}`. **An API that signals failure in the body needs a
  classifier, or its failures are cached as successes, which never expire.**
  `classify_lastfm` lives in `_http.py`; Deezer's is `resolve_isrcs._classify_deezer`.
- Classifies a search API's **empty result** with `classify_empty(...)`, so the
  miss is a `not_found` on the 30-day TTL rather than a success that never
  expires (#109). The caller must treat that `not_found` as it treated the empty
  body. A non-empty success is still permanent until `--force`; a phase with its
  own freshness gate passes `refresh=True` for a stale row (Phase 5).
- Treats an error as *unknown*, not as a negative: it leaves the field as it was
  (Phase 5 keeps the prior availability, 4b leaves `discogs_styles` unset). Only
  a checked miss (`not_found`) writes the negative value.
- Flushes its cache from a `finally`, so an interrupted run keeps every response
  it already paid for at the rate limit.
- Honours `--force-errors` (re-fetch cached failures only) and `--force` (bypass
  cache entirely), dispatched off the manifest's `accepts_force` flag. Phase B
  predates this and takes neither.
- Records where what it adds came from, at least a source and a retrieval date
  (as `isrc_source`/`isrc_retrieved_at` do). Existing phases are uneven: only 4d
  writes a full block, and nothing writes `pipeline_phase`. Don't assume a field
  has provenance; check.

Shared HTTP layer: `pipeline/_http.py`. Rate limits and TTLs: `pipeline/config.py`.

## Schema

`SCHEMA_VERSION` lives in `pipeline/config.py` (currently `6`) and is mirrored in
`pipeline_manifest.yaml`. Integer, monotonic.

- Every record emits `_schema_version` first. `read_jsonl` also loads legacy
  records without it.
- Readers ignore unknown fields; `fill_defaults()` in `pipeline/schema.py`
  preserves forward-compat.
- Additive fields **do not** bump the version. Breaking renames/removals **do**,
  and require migration tests — `tests/test_schema_v6.py` covers the current
  schema; `tests/test_schema_v5.py` stays as a legacy-read compat test.
- `FIELD_DEFAULTS` in `pipeline/schema.py` defines canonical write order.

## Conventions

- **Data structures:** plain `list[dict]` and `collections.Counter`, not pandas.
- **Comments: see the section below.**
- **Don't hardcode coverage numbers or row counts into docs or comments.** They
  drift the moment new scrobbles land. Compute them when asked. A dated file in
  `docs/` is a snapshot and may carry its figures.
- **Branches:** `claude/*`, `feat/*`, `fix/*`. One concern per branch per PR.
- CI runs `ruff check .` (pyflakes rules), the full pytest suite, `npm test` and
  a stale-bundle check on push to `main` and every PR (`.github/workflows/ci.yml`).
  Python deps install from `requirements.lock`; regenerate it when you change a
  requirements file (command in `requirements-dev.txt`).

## Comments

Agents treat comments as instructions, so a wrong comment does more harm than a
missing one.

- **A comment your change makes false is yours to fix in the same PR.** So is one
  you find false while working nearby: fix it or delete it.
- **Warnings are the comments that matter.** Write them in one or two lines that
  name the failure and the forbidden change: "Don't X: Y breaks". Never delete a
  warning to shorten a block; compress it.
- **Comments explain *why*, not *what*.** If the code tells the whole story, no
  comment.
- **Ceilings:** 4 lines for an inline block, 8 for a function or class docstring,
  12 for a module docstring. Never longer than the code being explained. Longer
  text adds cost without adding protection.
- **No history, chronology, or measurement logs** ("used to", "before #NN", row
  counts). Those go stale first. Git and `docs/` hold the story. A short pointer
  like `(#83/F-02)` may mark a ruled decision.
- **Keep per-site traps in the file, next to the code.** Moved into CLAUDE.md
  they protect less, and an agent tends to refuse the task instead of finding the
  safe way round.
- **A trap where a wrong change corrupts data needs a test, not just a comment.**

## Gotchas

- **Coverage percentages move when the library grows.** Ingesting a fuller
  export adds tracks the enrichment phases have not run across, so coverage can
  drop without anything breaking. Check whether phases 4–8 have been re-run
  before treating a low number as a regression.
- **iTunes `Persistent ID` is not `apple_music_id`.** The former is a local
  library UUID from the XML export; the latter comes from the iTunes Search API
  in Phase 5. Do not conflate them.
- **A null `mood_tags` from Phase 6 is a verdict, not a gap.** The classifier
  declines to guess where audio features can't support one, and `update_tracks`
  must let that blank survive the merge. A decline is a *machine* verdict, so it
  clears a machine guess (`centroid`, `inherited`) but never a curated label
  (`manual`, `audit`, `claude_batch`) — the split is `MOOD_CURATED_MIN_RANK`,
  next to `MOOD_SOURCE_RANK`. Ranking the two sides alone is not enough: every
  ranked source outranks an unset one, which silently reinstates the guess the
  classifier just withdrew.
- **`mood_source: "audit"` is the owner's own labelling** — the training signal
  the whole classifier is built on. A fresher centroid pass must never overwrite
  one. Trust order lives once, in `MOOD_SOURCE_RANK` (`pipeline/config.py`):
  `manual` > `audit` > `claude_batch` > `centroid` > `inherited` > unset.
  Don't re-encode it locally — `update_tracks._merge_with_existing()` consults
  it directly (#83/F-02).
- **`mood_audit.csv` at the repo root is the canonical label file** (#66) and
  Phase 6's unconditional default (`classify()`'s `audit_path` resolves to it
  when omitted). The older `inputs/existing_audit.csv` is gitignored and never
  authoritative — if it's present too, Phase 6 logs a warning and ignores it
  rather than silently training on it; reconcile any local edits into
  `mood_audit.csv` instead. To use the legacy file deliberately, pass
  `audit_path=INPUT_EXISTING_AUDIT` explicitly (#83/F-06).
  `tests/test_data_integrity.py` asserts the committed audit still reaches the
  committed library; every other test builds its own fixtures.
- **Dashboard mutating endpoints require a token.** `POST /api/refresh`,
  `/api/lastfm/sync`, `/api/reload` need `X-Dashboard-Token`. The SPA fetches it
  from same-origin `GET /api/config`. Set `DASHBOARD_TOKEN` to keep it stable
  across restarts.
- **`/tracks.min.jsonl` is served from memory, so its ETag must be a hash of the
  served body.** Don't derive it from `tracks.jsonl`'s mtime+size: a CLI pipeline
  run with the server up moves the file but not the snapshot, and clients cache
  a stale body under a fresh ETag. Don't use the snapshot generation either: it
  restarts at 1 in every process (#103). `/tracks.jsonl` is a `FileResponse`,
  where ETag and body both come from disk, and is fine as is.
- **The mutating routes are coroutines, so every blocking call inside them needs
  `asyncio.to_thread`.** They are `async def` to hold the mutation lock, which
  takes them out of FastAPI's threadpool. A bare `data.reload()`,
  `ingest_from_records()` or `export_pending()` stalls the whole event loop,
  including the SPA's own `/api/lastfm/status` polling.
- **Pin any new HTTP client's logger in `configure_logging`.** Root is set to
  DEBUG with a FileHandler, Last.fm takes its API key as a *query parameter*,
  and both `urllib3` and `httpx` log full request URLs. The first `/api/refresh`
  installs this config in the uvicorn process, so an unpinned client writes the
  key into `runs/*.log` from then on.
- **No CORS middleware, deliberately.** The dashboard is same-origin; allowing
  all origins would let any site read the full listening history over the tunnel.

## Open work

Known-unfixed work lives in GitHub issues. Search them before filing a finding;
it is probably already there. The umbrella issues are
[#97](https://github.com/BFox010/Music-Enrichment/issues/97) (the 2026-09-25 code
audit, one sub-issue per finding) and
[#77](https://github.com/BFox010/Music-Enrichment/issues/77) (pipeline data
quality). Read the issues themselves for what remains; a list copied here goes
stale within days.

**Re-measure before implementing.** Figures in an issue body are a snapshot of
its filing date, and premises go stale. One #77 item's 310-row payoff was 0 rows
by the time it was worked.
