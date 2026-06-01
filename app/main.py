"""FastAPI serving layer for the music dashboard.

API routes are registered before the static-files mount so ``/api/*``
always wins. The ``web/`` directory is mounted at ``/`` and served with
``html=True`` so ``index.html`` is the SPA fallback.
"""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import app.data as data
import app.metrics as metrics
import app.query as query

app = FastAPI(title="Music Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load data eagerly at import time; tests override via data.use_paths().
data.load()


@app.get("/api/overview")
def api_overview():
    return metrics.overview()


@app.get("/api/genres")
def api_genres(top: int = Query(50, ge=1, le=500)):
    return metrics.genres(top=top)


@app.get("/api/moods")
def api_moods():
    return metrics.moods()


@app.get("/api/timeline")
def api_timeline(by: str = Query("year", pattern="^(year|month)$")):
    return metrics.timeline(by=by)


@app.get("/api/time-of-day")
def api_time_of_day():
    return metrics.time_of_day()


@app.get("/api/artist-trajectory")
def api_artist_trajectory(top: int = Query(15, ge=1, le=50)):
    return metrics.artist_trajectory(top=top)


@app.get("/api/top")
def api_top(
    dim: str = Query("artists", pattern="^(artists|tracks)$"),
    n: int = Query(20, ge=1, le=100),
):
    return metrics.top_items(dim=dim, n=n)


@app.get("/api/audio-features")
def api_audio_features():
    return metrics.audio_features()


@app.get("/api/saturation")
def api_saturation():
    return metrics.saturation()


@app.get("/api/tracks")
def api_tracks(
    genre: Optional[str] = Query(None),
    mood: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    artist: Optional[str] = Query(None),
    min_energy: Optional[float] = Query(None, ge=0.0, le=1.0),
    max_energy: Optional[float] = Query(None, ge=0.0, le=1.0),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    return query.query_tracks(
        genre=genre,
        mood=mood,
        year=year,
        artist=artist,
        min_energy=min_energy,
        max_energy=max_energy,
        page=page,
        per_page=per_page,
    )


@app.get("/api/tag-graph")
def api_tag_graph(
    field: str = Query("discogs_styles", pattern="^(discogs_styles|mood_tags|lastfm_tags)$"),
    min_count: int = Query(15, ge=1, le=500),
):
    return metrics.tag_graph(field=field, min_count=min_count)


@app.post("/api/reload")
def api_reload():
    return data.reload()


# Mount static files last so API routes take precedence.
_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.exists():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="static")
