# ⚠️ DEPRECATED BRANCH — do not build on this

**Status:** dead. Safe to delete.
**Superseded by:** #51 (docs audit), merged to `main` 2026-08-23.

## Why

Its single commit (`bac4d5c`) adds two things and nothing else:

1. `docs/frontend-phase-handoff.md` — **deliberately deleted by #51.** It declared
   the project goal to be a natural-language playlist builder pushing to
   Spotify/Apple (a direction that was dropped), specified a vanilla-JS stack
   with no framework (the app ships React + esbuild), and stated that `app/` and
   `web/` did not exist yet (both are the bulk of the product).
2. `requirements.txt` deps (`fastapi`, `uvicorn`, `httpx`) — already on `main`.

Merging this branch would resurrect exactly the document #51 removed for being
actively misleading to anyone orienting in the repo.

Kept only because branch deletion is blocked from the agent environment
(HTTP 403 on ref deletes). Delete it from the GitHub UI, or:

```
git push origin --delete feat/dashboard-foundation
```

Triaged 2026-08-23 alongside #49.
