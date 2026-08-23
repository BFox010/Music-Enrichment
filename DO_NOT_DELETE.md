# 🛑 DO NOT DELETE — holds owner data that exists nowhere else

**Status:** superseded *as code*, but **NOT** safe to delete.
**Salvage tracked in:** #55.

## Read this before touching the branch

An older note (in the since-deleted `AUDIT_TODO.md`) called this branch "fully
superseded ... safe to delete". **That assessment covered the genre code only and
is wrong about the data.**

### The code IS superseded — do not merge it

| On this branch | Superseded by, on `main` |
|---|---|
| `pipeline/distill_genres.py` (Phase 4c) | `pipeline/derive_genres.py` |
| `pipeline/enrich_discogs.py` (Phase 4b) | same module, re-landed independently |
| `JUNK_TAGS` blocklist | `pipeline/tag_filter.py` (broader: radio-station, artist-as-tag, "my …", year rules) |
| `HANDOFF.md`, `TODO.md`, `runs/unmatched_*` | session cruft; `runs/` is gitignored on `main` |

The branch also carries a May-2026 `tracks.jsonl`. **Never take it** — `main` is
far ahead.

### The data is NOT superseded — this is why the branch lives

`mood_audit.csv` (root, 377 rows of `artist,track,mood_tags`) is **owner-made mood
labelling**. Measured against `main` on 2026-08-23:

- 377 labelled tracks
- only **150** appear on `main` as `mood_source: "audit"`
- **92** are on `main` carrying *no* mood at all
- **95** are not in `main`s `tracks.jsonl` at all

So **187 owner judgements are recorded only here.** With mood coverage at 54.9%
(1,505 nulls), and `CLAUDE.md` stating that `mood_source: "audit"` is the training
signal the whole classifier rests on, these cannot be regenerated — only re-done
by hand.

## What to do instead of deleting

Cherry-pick `d62e6c3` ("preserve mood audit CSV at root + self-healing phase 6
fallback"). It is 2 files / 386 lines — the CSV plus 8 lines in
`classify_moods.py` — and touches no JSONL. See #55.

**Delete this branch only once that CSV is committed to `main`.**

Triaged 2026-08-23 alongside #49.
