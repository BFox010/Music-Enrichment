# Mood-Centroid Reliability — Decision Ledger

The mood classifier (`pipeline/classify_moods.py`, Phase 6) assigns moods from three
sources, recorded per track in `mood_source`:

| source | confidence | trust |
|---|---|---|
| `claude_batch` | high | human (manual Claude review) |
| `audit` | high | human (owner-labeled CSV) |
| `centroid` | medium | **automated** — audio-feature Euclidean distance |

Only the **centroid** source is unreliable, and only for some moods. This file is the
durable, version-controlled record of which moods get which treatment and **why**. The
machine-readable source of truth is `CENTROID_MOOD_TREATMENTS` in
`pipeline/classify_moods.py`; this doc carries the evidence behind it.

## Treatments

- **suppress** — drop the centroid prediction entirely. Used when the 9 Spotify audio
  features *cannot express* the mood, so no threshold can rescue it. Audit/Claude hits for
  the mood still emit.
- **gate** — emit the centroid prediction only when a per-mood predicate over the raw
  `audio_features` holds (e.g. a tempo ceiling). Used when the mood *is* feature-correlated
  but the global distance threshold (1.6) is too loose.

## How to re-derive (reproducible)

```bash
python scripts/eval_mood_centroids.py          # human-readable report
python scripts/eval_mood_centroids.py --json    # machine-readable
```

The report needs **no** audit CSV — it derives `human` vs `centroid` purely from
`mood_source` + `genres` + `audio_features` already in `tracks.jsonl`, so it runs in any
environment (including the cloud container without the gitignored input CSVs). Two triggers:

- **Trigger A (suppress candidate):** centroid applies a mood `> 3x` more often than humans
  across `≥ 2` major genres. *Caveat:* this is a **screening** signal — sparse human
  labeling can inflate the ratio uniformly. Confirm with the per-genre table: a true
  feature-inadequacy shows **genre inversion** (centroid over-applies in some genres while
  *under*-applying where humans concentrate). Heavy Bass is the textbook case.
- **Trigger B (gate candidate):** the mood is feature-correlated but the centroid tempo
  distribution diverges from the human one (mean gap `> 8 BPM`, or a large `>105 BPM` tail).

Note: a gated mood may still *show* as flagged after the fix — the flag means "this mood is
treated," not "still broken." Verify success via the distribution table (e.g. Slow
`pct>105` → 0%).

## Verdicts

### 2026-05-25 — Group A/B/C (qualitative Excel spot-check, 130 rows / 13 categories)

- **Group A — Sad:** audit sound, all 172 centroid predictions discarded; rebuilt from
  audit + external reference. (`finalize_sad.py`, commit `3c49eb1`)
- **Group B — Dark, Fast, Heartbreak:** centroid 0–1 of 5 correct → **suppress**.
  (commit `5b10e26`)
- **Group C — Dance, Love, Slow:** audit drifted → `taste_profile.md` sections replaced with
  owner playlists. **Note:** this fixed the *playlists* but left the bad per-track centroid
  `mood_tags` in `tracks.jsonl` — addressed for Slow on 2026-06-05 below.

### 2026-06-05 — Quantitative re-audit (Moody / Slow / Heavy Bass)

Owner flagged Moody/Slow/Heavy Bass as over-tagged (~35% each, ~2x the next mood).
Replaced the qualitative spot-check with `scripts/eval_mood_centroids.py`. Evidence:

| mood | finding | verdict |
|---|---|---|
| **Heavy Bass** | Centroid over-applies vs human: **Rock 8.0x, Indie 6.1x, Metal 14.5x, Country 9.7x** — but *under*-applies in Hip-Hop (0.5x). Genre inversion = the feature set has no bass descriptor; centroid degenerates to "loud + energetic + low-acousticness." | **suppress** (feature-inadequate) |
| **Moody** | Centroid tempo mean **138 BPM** vs human 128; **100%** of centroid-Moody is >105 BPM. Keys on low valence, ignores tempo → drags in fast intense tracks (e.g. *Blinding Lights*, 171 BPM). | **gate**: tempo < 125 |
| **Slow** | Centroid mean 98 BPM (genuinely slow) but **~30%** of centroid-Slow is >105 BPM — the global 1.6 threshold is too loose. (Also the Group C "drift" never cleaned from tracks.jsonl.) | **gate**: tempo < 105 |

**Thresholds** (`SLOW_TEMPO_MAX=105`, `MOODY_TEMPO_MAX=125`) are tunable — re-check
`pct>105` and the centroid/human tempo gap in the report after any change.

**Applied:** `scripts/cleanup_centroid_moods.py --apply` scrubbed existing centroid tags in
place (Heavy Bass −464, Moody −239, Slow −146; 4 rows fully cleared). Audit/Claude tags
untouched. This shares `apply_centroid_policy()` with the live classifier, so a later
authoritative Phase 6 re-run on the owner's machine converges to the same result and the
cleanup becomes a no-op (idempotent).

## Open follow-ups (not yet acted on)

- **Fast → gate?** Currently *suppressed* (Group B), but it's tempo-correlated (human-Fast
  ~147 BPM). Could migrate suppress → gate (`tempo > 140`) to recover medium-confidence Fast
  tags. Run the report on Fast's distribution first.
- **Broader centroid inflation.** The 2026-06-05 report also flags Happy / Sunny / Uplifting
  / Love / Dance / Groove / Hype / Sad as centroid-inflated. Some of this is the sparse-human
  -labeling confound (they over-apply *uniformly* across genres rather than inverting like
  Heavy Bass), so it needs the genre-inversion check before acting. Left for a future pass.

## Roadmap: reproducible bass detection & human-label coverage

Recorded 2026-06-05 in response to: *"Would more human tags help? How are we calculating /
creating reproducibility in the bass category?"* No code changes yet — this is the documented
plan for when we revisit.

### Data-limited vs feature-limited — the key distinction

A mood's centroid can fail for two fundamentally different reasons, and the fix differs:

- **Data-limited** (Slow, Moody, Fast): the feature space *can* express the mood (tempo,
  valence), the centroid is just under-trained or loosely thresholded. **More human/Claude
  tags directly improve these** — tighter centroids, and enough coverage could justify
  loosening a gate or lifting Fast's suppression.
- **Feature-limited** (Heavy Bass): the 9 Spotify/ReccoBeats features contain **no bass
  descriptor**, so the centroid degenerates to loud+energetic+low-acousticness. **More
  training data cannot fix this** — even perfect labels can't make a bass-blind vector
  separate bass-heavy trap from loud rock. The limitation is the *inputs*, not the volume.

### So: would more human tags help Heavy Bass? Yes — for *coverage*, not for the model

- As of 2026-06-05, **50% of the library (1,371 tracks) has no human mood label** (1,285
  centroid-only + 86 none). Since the Heavy Bass centroid is suppressed, **Heavy Bass is now
  impossible to assign to any of them** — every genuinely bass-heavy track in that half is a
  silent false-negative.
- More human/Claude labels recover exactly that population. The cheapest scalable route is the
  existing **Claude-batch pipeline** (`claude_mood_batch.jsonl` → `claude_mood_results.jsonl`,
  `mood_source="claude_batch"`), the same mechanism that produced the current 522 high-conf
  tags. It helps Heavy Bass *coverage* AND the data-limited moods' *accuracy*.

### What "reproducibility in the bass category" currently means

Two different things share the name; for bass we only have the first:

| | Status |
|---|---|
| Reproducible **measurement of the centroid's failure + suppression decision** | ✅ `eval_mood_centroids.py` re-derives the 8–14x genre over-tag deterministically (no randomness/network); `apply_centroid_policy()` enforces it identically across classifier, cleanup, and future re-runs. |
| Reproducible **detection of bass itself** | ❌ None. We suppressed the only automated path because it was wrong. Heavy Bass is now 100% human/Claude labels — version-controlled, but not *calculated*. |

### Options for a reproducible bass *signal* (measured 2026-06-05)

1. **Genre/tag heuristic** (`808`, `trap`, `dubstep`, `dnb`, `phonk`, `drill`, …). High-precision
   tags hit **~65% precision but only ~6% recall**, because only **32% of tracks have Last.fm
   tags** at all. Viable as a high-confidence *booster* on top of human labels, not a standalone
   detector. Its value scales with Last.fm tag coverage.
2. **A real bass-aware audio feature** — low-frequency / sub-bass energy from spectral analysis.
   The *correct* fix, but a heavy lift (needs audio files or an analysis API). **ReccoBeats does
   NOT help** despite being listed in `AUDIO_FEATURE_SOURCES` — it returns the same Spotify-style
   features with no bass descriptor.
3. **Human/Claude labels** — current approach; reproducible as version-controlled data.

### Recommendation

Highest-leverage, lowest-effort: **grow human coverage via the Claude batch** (recovers Heavy
Bass on the unlabeled 50% and sharpens the data-limited moods). A tag-based bass booster
(option 1) is a reasonable reproducible secondary signal but is capped by the 32% tag-coverage
ceiling, so it's best done after/alongside more Last.fm enrichment. A true bass feature
(option 2) is the only path to genuine reproducible bass *detection* and should be scoped
separately if it becomes a priority.
