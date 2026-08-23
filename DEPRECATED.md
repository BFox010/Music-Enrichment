# ⚠️ DEPRECATED BRANCH — cleared, safe to delete

**Status:** salvage complete. Safe to delete.
**Superseded by:** #58 and #57 (both merged).

The earlier `DO_NOT_DELETE.md` on this branch is withdrawn — everything it was
protecting is now on `main`.

## Resolved

- **`docs/bass_review_2026-06-05.csv`** — 316 owner-reviewed Heavy Bass verdicts,
  with `scripts/apply_bass_labels.py`, its 6 tests, and
  `docs/mood_centroid_decisions.md`, taken by **#58**. Verified
  **byte-identical** to the copy here.
- **The `.env` loading fix** (`a2a023f`) — taken by **#57**, which also
  documented the two Spotify credentials that `.env.example` was missing.

## Still true: do not merge this branch

- Its `tracks.jsonl` (2,769) and `scrobbles.jsonl` (13,947) are far behind `main`.
- Its `CENTROID_MOOD_TREATMENTS` hand-kept suppress/gate dict is the approach
  `main` deliberately left in `cda27fa`, *"Gate mood tags on measured precision,
  not a hand-kept list"*. `pipeline/evaluate_moods.py` derives the same verdicts
  by k-fold cross-validation — it independently withholds Heavy Bass at 0.286
  precision. **Do not reintroduce the treatments dict.**
- `eval_mood_centroids.py` and `cleanup_centroid_moods.py` serve that superseded
  mechanism.

```
git push origin --delete claude/scrobble-data-update-taZcH
```

Triaged 2026-08-23 alongside #49.
