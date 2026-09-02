"""FastAPI serving layer for the music dashboard.

API routes are registered before the static-files mount so ``/api/*``
always wins. The ``web/`` directory is mounted at ``/`` and served with
``html=True`` so ``index.html`` is the SPA fallback.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pipeline.config import REPO_ROOT, SCROBBLES_PATH, TRACKS_PATH

# Pipeline phases each load_dotenv() for themselves; nothing under app/ did, so
# the server never saw .env. MUST run before DASHBOARD_TOKEN is read below.
load_dotenv(REPO_ROOT / ".env")

import app.data as data
import app.metrics as metrics
import app.query as query

app = FastAPI(title="Music Dashboard")

# No CORS middleware, deliberately: the SPA is same-origin, and allowing all
# origins would let any site the user visits read their full listening history
# over the public tunnel.

# Guards the mutating endpoints (refresh / sync / reload). The SPA reads it from
# GET /api/config — which, with CORS off, a cross-origin page can neither read nor
# set the custom header for. That is what blocks drive-by CSRF pipeline runs.
# Set DASHBOARD_TOKEN to keep it stable across restarts.
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN") or secrets.token_urlsafe(32)


# Starlette decodes header bytes as latin-1, and secrets.compare_digest raises
# TypeError on a str carrying anything outside ASCII — so a mangled paste or a
# scanner probing the tunnel turned a wrong token into a 500 traceback instead
# of a 403. Comparing the encoded bytes keeps the constant-time property and
# makes every malformed header just another failed comparison.
_TOKEN_BYTES = DASHBOARD_TOKEN.encode("utf-8")


def require_token(x_dashboard_token: str = Header(default="")) -> None:
    supplied = x_dashboard_token.encode("utf-8", "surrogateescape")
    if not secrets.compare_digest(supplied, _TOKEN_BYTES):
        raise HTTPException(status_code=403, detail="missing or invalid dashboard token")


# Compresses the multi-MB JSONL endpoints and static assets — the single biggest
# win for slow tunnelled mobile loads.
#
# Level 6, not Starlette's default 9. Measured on the committed files, 9 costs
# roughly 4x the CPU of 6 (tracks.jsonl 295ms vs 73ms; scrobbles.jsonl 326ms vs
# 96ms) to save ~4% of the bytes. A cold load pays that on every request whose
# ETag misses, and over a tunnel the extra ~30 KB is far cheaper than the extra
# ~450 ms of server CPU.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# Load data eagerly at import time; tests override via data.use_paths().
data.load()


@app.get("/api/config")
def api_config():
    """Token the SPA echoes in ``X-Dashboard-Token`` on mutating requests. With
    CORS disabled, cross-origin pages cannot read this response."""
    # no-store so the token never lands in a shared machine's on-disk HTTP cache.
    return JSONResponse({"token": DASHBOARD_TOKEN}, headers={"Cache-Control": "no-store"})


@app.get("/api/integrity")
def api_integrity():
    """Whether tracks.jsonl play counts still agree with scrobbles.jsonl."""
    return metrics.play_count_integrity()


@app.get("/api/overview")
def api_overview():
    return metrics.overview()


# Forms: "all" | "2025" | "2025-03" | "2025-summer" | "2025-03-01:2025-06-30".
_WINDOW = Query(
    None,
    description='Listening window: "2025", "2025-03", "2025-summer", or "from:to".',
    max_length=32,
)


@app.get("/api/genres")
def api_genres(top: int = Query(50, ge=1, le=500), window: Optional[str] = _WINDOW):
    return metrics.genres(top=top, window=window)


@app.get("/api/moods")
def api_moods(window: Optional[str] = _WINDOW):
    return metrics.moods(window=window)


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
def api_artist_trajectory(top: int = Query(15, ge=1, le=500)):
    """Monthly play counts for the ``top`` most-played artists.

    The ceiling is generous on purpose: the picker on the Trajectory page needs
    the long tail to be reachable, and the response is cheap. Measured 2026-08-29
    against the committed library: top=300 was ~19 KB gzipped and ~35 ms, against
    ~3 KB and ~18 ms at the old top=20 — the payload is mostly repeated artist
    names, so the middleware's gzip does most of the work, and the curve flattens
    well before the cap because a tail artist contributes one or two rows.
    """
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
    window: Optional[str] = _WINDOW,
):
    return metrics.tag_graph(field=field, min_count=min_count, window=window)


@app.post("/api/reload", dependencies=[Depends(require_token)])
async def api_reload():
    """Shares the refresh/sync mutation lock (F-05): a reload racing a
    full refresh could otherwise re-read tracks.jsonl mid-rewrite."""
    from app.refresh import RefreshInProgress, exclusive_mutation
    try:
        async with exclusive_mutation("reload"):
            # data.reload() re-parses both JSONL files synchronously. This
            # endpoint had to become a coroutine to hold the async lock, which
            # took it out of the threadpool FastAPI runs `def` handlers in — so
            # without to_thread the parse would block the event loop and stall
            # every concurrent request for its duration.
            return await asyncio.to_thread(data.reload)
    except RefreshInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# (generation, count, first, last) — see _scrobble_span.
_span_cache: tuple[int, int, Optional[str], Optional[str]] | None = None


def _scrobble_span(snap) -> tuple[int, Optional[str], Optional[str]]:
    """Count and first/last scrobble timestamp for ``snap``, cached by generation.

    The SPA polls /api/lastfm/status, and each poll used to walk all 16.5k
    scrobbles twice (min and max) to produce three numbers that only change on
    reload. Cached the same way as metrics._track_index.
    """
    global _span_cache
    cached = _span_cache
    if cached is not None and cached[0] == snap.generation:
        return cached[1], cached[2], cached[3]
    dates = [s.get("scrobbled_at") for s in snap.scrobbles if s.get("scrobbled_at")]
    span = (len(snap.scrobbles), min(dates) if dates else None, max(dates) if dates else None)
    _span_cache = (snap.generation, *span)
    return span


@app.get("/api/lastfm/status")
def lastfm_status():
    count, first, last = _scrobble_span(data.get_snapshot())
    return {
        "scrobble_count": count,
        "last_scrobbled_at": last,
        "first_scrobbled_at": first,
        "configured": bool(os.getenv("LASTFM_USERNAME") and os.getenv("LASTFM_API_KEY")),
        "username": os.getenv("LASTFM_USERNAME"),
    }


@app.post("/api/lastfm/sync", dependencies=[Depends(require_token)])
async def lastfm_sync():
    """Shares the refresh/reload mutation lock (F-05): a direct sync used to
    run outside it entirely, so it could overlap a full refresh mid-rewrite
    of scrobbles.jsonl and the pipeline intermediates that read it."""
    from app.lastfm_sync import sync as _sync
    from app.refresh import RefreshInProgress, exclusive_mutation
    try:
        async with exclusive_mutation("sync"):
            result = await _sync(SCROBBLES_PATH)
            # Same reasoning as api_reload: this handler is a coroutine (it has
            # to be, to hold the async lock), so a bare data.reload() re-parses
            # both JSONL files *on the event loop* and stalls every concurrent
            # request — including the SPA's own polling of /api/lastfm/status.
            await asyncio.to_thread(data.reload)
            return result
    except RefreshInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/refresh", dependencies=[Depends(require_token)])
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
    # Nanosecond mtime: at second granularity two writes inside the same second
    # produce the same ETag whenever the size is unchanged, and the client keeps
    # the older body. Phase 8 takes minutes so this is a trap rather than a live
    # bug, but the finer field costs nothing.
    etag = f'W/"{st.st_mtime_ns}-{st.st_size}"'
    # "private": this is one person's listening history over a personal tunnel,
    # so no shared cache should ever be entitled to store it.
    cache_headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    return FileResponse(str(path), media_type=media_type, headers=cache_headers)


@app.get("/tracks.jsonl")
def serve_tracks_jsonl(request: Request):
    return _conditional_file(TRACKS_PATH, request, "application/x-ndjson")


@app.get("/scrobbles.jsonl")
def serve_scrobbles_jsonl(request: Request):
    return _conditional_file(SCROBBLES_PATH, request, "application/x-ndjson")


# (generation, body bytes) for /tracks.min.jsonl. Same invalidation model as
# metrics._track_index: a reload() advances data's generation counter, which
# retires this entry without anyone having to clear it.
_min_body_cache: tuple[int, bytes] | None = None


@app.get("/tracks.min.jsonl")
def serve_tracks_min_jsonl(request: Request):
    """Slimmed tracks for the dashboard's first paint — only the fields the UI
    renders (see app.query.project_min_track). ~44% smaller gzipped than the full
    /tracks.jsonl. Full data stays available there and via /api/*.

    Both the ETag and the body come from the in-memory snapshot's generation.
    Deriving the ETag from tracks.jsonl's mtime+size instead — as this used to —
    let the two diverge: the file changes on disk whenever the documented CLI
    workflow runs the pipeline while uvicorn is up, so the client got a fresh
    ETag with the *old* in-memory body and cached it; the subsequent /api/reload
    refreshed the snapshot but left the file untouched, so the revalidation
    304'd and the client kept the stale body until the file changed again.
    """
    global _min_body_cache

    snap = data.get_snapshot()
    etag = f'W/"min-{snap.generation}-{len(snap.tracks)}"'
    cache_headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    cached = _min_body_cache
    if cached is not None and cached[0] == snap.generation:
        body = cached[1]
    else:
        # ~2.6 MB of json.dumps over every row, rebuilt on each non-304 request
        # before this cache. The projection only changes on reload, so key it on
        # the generation and pay it once per generation instead.
        body = "".join(
            json.dumps(query.project_min_track(t), ensure_ascii=False) + "\n"
            for t in snap.tracks
        ).encode("utf-8")
        _min_body_cache = (snap.generation, body)
    return Response(body, media_type="application/x-ndjson", headers=cache_headers)


class CachedStaticFiles(StaticFiles):
    """StaticFiles that adds a short ``Cache-Control`` to file responses.

    Starlette's StaticFiles sends an ETag (→ conditional 304s) but no
    ``Cache-Control``, so over a tunnel every repeat load still revalidates each
    asset. A short max-age lets the browser serve from cache without a round-trip;
    it stays short because app.bundle.js / themes.css aren't content-hashed, so a
    rebuild must become visible quickly."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers.setdefault(
            "Cache-Control", "public, max-age=300, must-revalidate"
        )
        return resp


# Mount static files last so API routes take precedence.
_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.exists():
    app.mount("/", CachedStaticFiles(directory=str(_web_dir), html=True), name="static")
