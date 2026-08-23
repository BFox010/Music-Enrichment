# 🛑 DO NOT DELETE — holds classifier work and owner labels not on main

**Status:** must NOT be merged, must NOT be deleted.
**Salvage tracked in:** #54. Its `.env` fix is #53.

## Why it cannot be merged

It rewrites the canonical data backwards:

| | this branch | `main` (2026-08-23) |
|---|---|---|
| `tracks.jsonl` | 2,769 records | **3,336** |
| `scrobbles.jsonl` | 13,947 | **16,549** |

Merging drags the library back ~570 tracks and ~2,600 scrobbles and conflicts on
5 paths. Three of its five commits rewrite `tracks.jsonl` as a side effect of
running the classifier.

## Why it cannot be deleted

These exist **only here**:

- `docs/bass_review_2026-06-05.csv` — **317 owner-reviewed Heavy Bass labels.**
  Hand-made judgement; not regenerable.
- `pipeline/classify_moods.py` (+117) — suppress Heavy Bass, tempo-gate Moody/Slow
- `scripts/eval_mood_centroids.py` (358 lines), `scripts/cleanup_centroid_moods.py`
- `scripts/apply_bass_labels.py`, plus 3 new test files
- `docs/mood_centroid_decisions.md` — 149 lines of rationale

## What to do

Cherry-pick the code/docs/CSV commits with `-n`, **dropping every `tracks.jsonl`
and `scrobbles.jsonl` hunk**, then re-run phases 6→8 against the current library.
Full procedure in #54.

`67e3508` also touches `SAVE_LOG.md`, deleted by #51 — drop that hunk.

Triaged 2026-08-23 alongside #49.
