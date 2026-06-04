# scripts/archive

One-off utilities retained for provenance. None are imported by the pipeline,
the app, or the test suite, and none run as part of `run_full_pipeline`. They
were used once to produce or repair data that now lives in `tracks.jsonl` /
`taste_profile.md`. Kept (not deleted) so the regeneration path is recoverable.

| Script | What it did |
|---|---|
| `finalize_sad.py` | Rebuilt the `### sad (locked)` section of `taste_profile.md` (PR #5). |
| `rebuild_dance_love.py` | Rebuilt Dance/Love/Slow sections from owner playlists (PR #10). |
| `dump_itunes_playlists.py` | Diagnostic — confirmed the old Sad playlist no longer exists in the iTunes XML. |
| `build_mood_spotcheck.py` | Generated the 2026-05-25 mood-quality spot-check sample. |
| `add_mood_dump.py` | One-time mood-batch listing dump for Claude classification. |
| `flatten_audit.py` | Flattened the audit CSV during Phase 6 bootstrap. |

If you need to re-run one, move it back up to `scripts/` first — paths inside
assume the repo root.
