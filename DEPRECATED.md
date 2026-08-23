# ⚠️ DEPRECATED BRANCH — cleared, safe to delete

**Status:** salvage complete. Safe to delete.
**Superseded by:** #56 (merged), plus main's genre work.

The earlier `DO_NOT_DELETE.md` on this branch is withdrawn — the data it was
protecting is now on `main`.

## Resolved

`mood_audit.csv` (377 owner mood labels, of which 187 existed only here) was
committed to `main` by **#56**, and `classify_moods.classify()` now falls back to
it when the gitignored `inputs/existing_audit.csv` is absent. Verified
**byte-identical** to the copy on this branch.

## Still true: do not merge this branch

Its code remains superseded.

| Here | On `main` |
|---|---|
| `pipeline/distill_genres.py` | `pipeline/derive_genres.py` |
| `pipeline/enrich_discogs.py` | same module, re-landed independently |
| `JUNK_TAGS` blocklist | `pipeline/tag_filter.py`, broader |
| `HANDOFF.md`, `TODO.md`, `runs/unmatched_*` | session cruft |
| its May-2026 `tracks.jsonl` | far behind `main` |

```
git push origin --delete claude/exportify-integration-XGam2
```

Triaged 2026-08-23 alongside #49.
