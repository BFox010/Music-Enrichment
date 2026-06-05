"""FastAPI serving layer for the music dashboard.

API routes are registered before the static-files mount so ``/api/*``
always wins. The ``web/`` directory is mounted at ``/`` and served with
``html=True`` so ``index.html`` is the SPA fallback.
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pipeline.config import REPO_ROOT, SCROBBLES_PATH, TRACKS_PATH

# Load .env so LASTFM_USERNAME / LASTFM_API_KEY (used by the live scrobble sync
# at POST /api/lastfm/sync) reach the server process. The server reads them via
# os.getenv(...) and nothing else loads .env here.
load_dotenv(REPO_ROOT / ".env")

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

# Gzip every response above the threshold. This transparently compresses the
# multi-MB JSONL data endpoints and all static assets (CSS/JSX), which is the
# single biggest win for slow tunnelled mobile loads.
app.add_middleware(GZipMiddleware, minimum_size=1024)

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
def api_time_of_day(year: Optional[int] = Query(None)):
    return metrics.time_of_day(year=year)


@app.get("/api/albums")
def api_albums(
    top: int = Query(50, ge=1, le=500),
    min_tracks: int = Query(2, ge=1, le=50),
):
    return metrics.albums(top=top, min_tracks=min_tracks)


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


@app.get("/api/forgotten-favorites")
def api_forgotten_favorites(
    top: int = Query(30, ge=1, le=200),
    min_peak: int = Query(5, ge=1, le=100),
    recent_years: int = Query(2, ge=1, le=5),
):
    return metrics.forgotten_favorites(top=top, min_peak=min_peak, recent_years=recent_years)


@app.get("/api/tag-graph")
def api_tag_graph(
    field: str = Query("discogs_styles", pattern="^(discogs_styles|mood_tags|lastfm_tags)$"),
    min_count: int = Query(15, ge=1, le=500),
):
    return metrics.tag_graph(field=field, min_count=min_count)


@app.post("/api/reload")
def api_reload():
    return data.reload()


@app.get("/api/lastfm/status")
def lastfm_status():
    scrobbles = data.get_scrobbles()
    configured = bool(os.getenv("LASTFM_USERNAME") and os.getenv("LASTFM_API_KEY"))
    dates = [s.get("scrobbled_at", "") for s in scrobbles if s.get("scrobbled_at")]
    return {
        "scrobble_count": len(scrobbles),
        "last_scrobbled_at": max(dates) if dates else None,
        "first_scrobbled_at": min(dates) if dates else None,
        "configured": configured,
        "username": os.getenv("LASTFM_USERNAME"),
    }


@app.post("/api/lastfm/sync")
async def lastfm_sync():
    from app.lastfm_sync import sync as _sync
    try:
        result = await _sync(SCROBBLES_PATH)
        data.reload()
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/refresh")
async def api_refresh():
    """Full-chain refresh: sync scrobbles → pipeline → export pending → reload."""
    from app.refresh import refresh as _refresh, RefreshInProgress
    try:
        return await _refresh()
    except RefreshInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _conditional_file(path, request: Request, media_type: str):
    """Serve a file with an ETag-based conditional GET.

    A bare ``FileResponse`` does not handle ``304`` on its own, so repeat
    visits would re-download megabytes. The ETag is derived from the file's
    mtime+size, so it changes after a ``reload()``/sync and keeps data fresh
    while making unchanged repeat loads a cheap ``304``.
    """
    if not path.exists():
        raise HTTPException(404, f"{path.name} not found")
    st = path.stat()
    etag = f'W/"{int(st.st_mtime)}-{st.st_size}"'
    cache_headers = {"ETag": etag, "Cache-Control": "public, max-age=0, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    return FileResponse(str(path), media_type=media_type, headers=cache_headers)


@app.get("/tracks.jsonl")
def serve_tracks_jsonl(request: Request):
    return _conditional_file(TRACKS_PATH, request, "application/x-ndjson")


@app.get("/scrobbles.jsonl")
def serve_scrobbles_jsonl(request: Request):
    return _conditional_file(SCROBBLES_PATH, request, "application/x-ndjson")


# Mount static files last so API routes take precedence.
_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.exists():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="static")
