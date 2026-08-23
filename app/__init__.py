"""Serving layer for the Listening Atlas dashboard.

A read-only FastAPI view over the git-tracked JSONL source of truth
(``tracks.jsonl`` + ``scrobbles.jsonl``). JSONL stays canonical; this layer
just caches it in memory and exposes JSON for the dashboard.
"""
