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

- **Phases 3a/3b/3c** (TuneMyMusic → Exportify) acquire *audio features*. The
  playlist round-trip was a means of getting them, not a feature — #37
  replaced it with 5a/5b (MusicBrainz/Deezer → ISRC, then ReccoBeats), and
  3a/3b/3c now sit in the manifest only as an optional legacy fallback.
- **Phase 7** (`apply_taste_profile`) produces `saturation_tier` and
  `curation_state` — dashboard-facing curation metadata. Its `playlists` field
  is a grouping label read out of `taste_profile.md`, not a generated playlist.
  (`blacklisted` was a third such field until #63 removed it — a
  playlist-generator leftover with no consumer; see `docs/blacklist_archive_2026-08.md`.)

## Invariants

1. **JSONL is the source of truth**, not a database. `tracks.jsonl` and
   `scrobbles.jsonl` are git-tracked. The serving layer is a derived read-only
   view over them.
2. **Markdown is truth for human judgement.** `taste_profile.md` is hand-edited
   and never written by the pipeline; JSONL is the derived index, regenerated
   each run.
3. **Phase 1 is additive.** The owner's export is a partial pull ("today back to
   the last pull"), so ingest merges and dedupes rather than overwriting. A write
   that would leave fewer rows than are on disk is refused.
4. **`canonical_track_id` is preserved by every phase.** No phase may drop or
   overwrite it. Identity priority: MusicBrainz recording MBID → ISRC →
   normalized artist+track → fallback hash. **Phase 4e sets it** — on every
   row, singletons included — which is why 5a resolves ISRCs before 4e runs:
   an id derived from a normalized name moves whenever normalization improves,
   and a moved id orphans the row's human-edited fields at the Phase 8 merge.
5. **The manifest drives the orchestrator.** Adding a phase means adding it to
   `pipeline_manifest.yaml` *and* implementing the module. The anti-drift test in
   `tests/test_pipeline_manifest.py` fails otherwise.

## Layout

| Path | What |
|---|---|
| `pipeline/` | Enrichment phases. One module per phase, driven by `pipeline_manifest.yaml` |
| `app/` | FastAPI serving layer — `main.py` routes, `metrics.py` aggregations, `query.py` track table, `data.py` in-memory load |
| `web/` | React SPA. `.jsx` sources compile to `app.bundle.js` |
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
  3c merge_exportify       → tracks_with_audio.jsonl        LEGACY (reads deepest existing intermediate)
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

**3a/3b/3c cannot be retired, despite #37 superseding them for audio features.**
Measured 2026-08-24: ReccoBeats returns features for 98.7% of the tracks
Exportify covers, so it *does* replace them there. But `inputs/exportify.csv`
carries no ISRC column and is the only bulk source of three other fields —
`explicit` and `release_year` (otherwise only the iTunes XML, ~4% of rows) and
`spotify_id` (otherwise only Phase B, which needs credentials). Dropping 3c
trades a ~1.5pt audio-feature gain for collapsing three fields from ~69% to
near zero. Retiring it needs a replacement source for those first.

## Commands

```bash
python -m pytest tests/ -q            # full suite, self-contained
python -m pipeline.run_full_pipeline  # end-to-end; --start-from N, --skip-tests, --skip-pause
uvicorn app.main:app                  # dashboard at http://127.0.0.1:8000
npm install && npm run build          # compile web/*.jsx → web/app.bundle.js
npm run dev                           # same, watch mode
```

**After editing any `web/*.jsx`, run `npm run build` and commit the regenerated
`web/app.bundle.js`.** There is no in-browser transpile — an un-rebuilt bundle
means your change simply does not appear.

`web/data-processing.js`, `web/graph-layout.js` and `web/data-worker.js` are
**not** part of the bundle. They are plain JS holding the pure transforms,
loaded as globals before the bundle so a Web Worker can `importScripts` the
same code. Edit directly; no rebuild needed. Their `node --test` suites run
under `npm test` (which CI runs) — add new ones to the `test` script in
`package.json`, it names each file.

## Enrichment phase contract

Every phase that touches an external API:

- Caches every response — **successes and failures** — to `.cache/<api>.json`.
- Expires negative entries: genuine `not_found` after 30 days, transient
  (`max_retries`, `invalid_json`) after 6 hours. A network blip heals itself
  instead of freezing a track's enrichment permanently.
- Flushes its cache from a `finally`, so an interrupted run keeps every response
  it already paid for at the rate limit.
- Honours `--force-errors` (re-fetch cached failures only) and `--force` (bypass
  cache entirely), dispatched off the manifest's `accepts_force` flag.
- Writes per-field provenance: `source`, `retrieved_at`, `pipeline_phase`,
  `confidence`.

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

- **Data structures:** plain `list[dict]` and `collections.Counter`. Not pandas,
  despite it being in `requirements.txt`.
- **Comments explain *why*, not *what*.** If the code tells the whole story, no
  comment. Keep the ones carrying a constraint that isn't visible locally.
- **Don't hardcode coverage numbers into docs.** They drift the moment new
  scrobbles land. Compute them when asked.
- **Branches:** `claude/*`, `feat/*`, `fix/*`. One concern per branch per PR.
- CI runs the full pytest suite on push to `main` and every PR
  (`.github/workflows/ci.yml`).

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
  it directly (#83/F-02 replaced the ad hoc rule it used to carry).
- **`mood_audit.csv` at the repo root is the canonical label file** (#66) and
  Phase 6's unconditional default (`classify()`'s `audit_path` resolves to it
  when omitted). The older `inputs/existing_audit.csv` is gitignored and never
  authoritative — if it's present too, Phase 6 logs a warning and ignores it
  rather than silently training on it; reconcile any local edits into
  `mood_audit.csv` instead. To use the legacy file deliberately, pass
  `audit_path=INPUT_EXISTING_AUDIT` explicitly (#83/F-06 — this used to be
  reversed: the gitignored file silently won whenever it existed).
  `tests/test_data_integrity.py` asserts the committed audit still reaches the
  committed library — the drift it caught went unnoticed because every other
  test builds its own fixtures.
- **Dashboard mutating endpoints require a token.** `POST /api/refresh`,
  `/api/lastfm/sync`, `/api/reload` need `X-Dashboard-Token`. The SPA fetches it
  from same-origin `GET /api/config`. Set `DASHBOARD_TOKEN` to keep it stable
  across restarts.
- **No CORS middleware, deliberately.** The dashboard is same-origin; allowing
  all origins would let any site read the full listening history over the tunnel.
- **An ECharts `force.repulsion` array is a `[min, max]` range, not per-node
  values.** `forceLayout.js` does `isArray(repulsion) ? repulsion : [r, r]` and
  then `linearMap(value, extent, arr)`, reading only elements 0 and 1. Passing
  one entry per node silently turns into the range `[first, second]` — the Tag
  Constellation used to pass 105 of them and got an inverted, near-uniform
  repulsion across the whole graph. Same for `edgeLength`.
- **Any `setOption` on a live `layout: "force"` graph restarts the physics.**
  ECharts rebuilds the force instance on every layout pass and re-reads
  `friction` from the option, so a camera patch, a restyle, or a highlight all
  re-heat a settled graph for another ~7s. This is why the constellation
  settles in `graph-layout.js` and renders frozen with `layout: "none"`; don't
  reintroduce a running force layout to get motion (there is a Shake button).
- **A `layout: "none"` graph is already fitted by ECharts, at 80% scale.**
  `chart/graph/createView.js` maps the bounding box of the nodes' own x/y onto
  an aspect-matched rect inside the card, and `getLayoutRect` sizes that rect
  at 80% of the binding dimension when it derives one from an aspect. So
  `zoom: 1` is a fit with a wide margin, and computing a canvas-relative zoom
  as well multiplies the two together — that drew the whole graph at a quarter
  size in the middle of an empty card. `fitCamera()` returns framing only.
  Note also that graph symbols scale by `(zoom-1) * nodeScaleRatio + 1`, not by
  zoom, so changing the framing changes how big the nodes look.

## Open audits

Three tracking issues hold the known-unfixed work. Read the relevant one before
filing a finding — it is probably already there, and several have been
re-measured since filing.

| Issue | Scope | Remaining |
|---|---|---|
| [#77](https://github.com/BFox010/Music-Enrichment/issues/77) | Pipeline data quality (23 Aug) | #68, #70, #75, #76 |
| [#83](https://github.com/BFox010/Music-Enrichment/issues/83) | Orchestration safety, mood precedence, alias gaps (26 Aug) | all of it; tracking only, nothing scheduled |
| [#42](https://github.com/BFox010/Music-Enrichment/issues/42) | Phase A joins the iTunes XML on name, matching ~3% | the whole issue |

**Re-measure before implementing.** Three of #77's items had premises that had
gone stale by the time they were worked (#72's 310-row payoff was 0 rows by
then); the re-measurement is what surfaced four further defects nobody had
filed. Coverage figures in an issue body are a snapshot of its filing date.
