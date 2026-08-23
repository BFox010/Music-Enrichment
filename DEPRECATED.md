# ⚠️ DEPRECATED BRANCH — do not build on this

**Status:** dead. Safe to delete.
**Superseded by:** nothing — this branch contributes no change.

## Why

`git diff main...this-branch` is **empty**. Its two commits
(`6a88af4` add onboarding guide, `1d1c1b5` remove the test file) cancel out, so
there is nothing here that `main` lacks.

Kept only because branch deletion is blocked from the agent environment
(HTTP 403 on ref deletes). Delete it from the GitHub UI, or:

```
git push origin --delete claude/code-slash-commands-guide-hnymgq
```

Triaged 2026-08-23 alongside #49.
