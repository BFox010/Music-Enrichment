"""Serving layer for the music dashboard (Frontend phase, Slice 1).

A read-only FastAPI view over the git-tracked JSONL source of truth
(``tracks.jsonl`` + ``scrobbles.jsonl``). JSONL stays canonical; this layer
just caches it in memory and exposes JSON for the dashboard.
"""
