"""Pipeline configuration: paths, schema constants, API endpoints, logging."""

from __future__ import annotations

import logging
import logging.config
from datetime import datetime, timezone
from pathlib import Path

# ── Repo root ────────────────────────────────────────────────────────────
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

# ── Canonical data files ─────────────────────────────────────────────────
TRACKS_PATH: Path = REPO_ROOT / "tracks.jsonl"
SCROBBLES_PATH: Path = REPO_ROOT / "scrobbles.jsonl"

# ── Intermediate per-phase outputs (gitignored) ──────────────────────────
TRACKS_SKELETON_PATH: Path = REPO_ROOT / "tracks_skeleton.jsonl"
TRACKS_WITH_SPOTIFY_PATH: Path = REPO_ROOT / "tracks_with_spotify.jsonl"
TRACKS_WITH_AUDIO_PATH: Path = REPO_ROOT / "tracks_with_audio.jsonl"
TRACKS_WITH_METADATA_PATH: Path = REPO_ROOT / "tracks_with_metadata.jsonl"
TRACKS_WITH_DISCOGS_PATH: Path = REPO_ROOT / "tracks_with_discogs.jsonl"
TRACKS_WITH_GENRES_PATH: Path = REPO_ROOT / "tracks_with_genres.jsonl"
TRACKS_WITH_GENRE_BACKFILL_PATH: Path = REPO_ROOT / "tracks_with_genre_backfill.jsonl"
TRACKS_RESOLVED_PATH: Path = REPO_ROOT / "tracks_resolved.jsonl"
TRACKS_WITH_AVAILABILITY_PATH: Path = REPO_ROOT / "tracks_with_availability.jsonl"
TRACKS_WITH_MOODS_PATH: Path = REPO_ROOT / "tracks_with_moods.jsonl"

# ── Human-edited reference (DO NOT auto-modify) ──────────────────────────
TASTE_PROFILE_PATH: Path = REPO_ROOT / "taste_profile.md"

# ── Directories ──────────────────────────────────────────────────────────
RUNS_DIR: Path = REPO_ROOT / "runs"
VIEWS_DIR: Path = REPO_ROOT / "views"        # gitignored
CACHE_DIR: Path = REPO_ROOT / ".cache"       # gitignored
INPUTS_DIR: Path = REPO_ROOT / "inputs"      # gitignored

# ── Owner-provided inputs (not committed) ────────────────────────────────
INPUT_LASTFM_EXPORT: Path = INPUTS_DIR / "lastfm_export.json"
INPUT_APPLE_MUSIC_LIBRARY: Path = INPUTS_DIR / "apple_music_library.xml"  # iTunes XML export
INPUT_EXISTING_AUDIT: Path = INPUTS_DIR / "existing_audit.csv"
INPUT_EXPORTIFY_CSV: Path = INPUTS_DIR / "exportify.csv"
INPUT_CLAUDE_MOOD_RESULTS: Path = INPUTS_DIR / "claude_mood_results.jsonl"
# Spotify app credentials (Client ID + Secret). Either set the env vars
# SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET, or drop a JSON file here with
# {"client_id": "...", "client_secret": "..."}. Absent → Phase B is skipped.
INPUT_SPOTIFY_CREDENTIALS: Path = INPUTS_DIR / "spotify_credentials.json"

# ── API caches ───────────────────────────────────────────────────────────
APPLE_MUSIC_CACHE: Path = CACHE_DIR / "apple_music.json"
LASTFM_CACHE: Path = CACHE_DIR / "lastfm.json"
MUSICBRAINZ_CACHE: Path = CACHE_DIR / "musicbrainz.json"
DISCOGS_CACHE: Path = CACHE_DIR / "discogs.json"
SPOTIFY_CACHE: Path = CACHE_DIR / "spotify_search.json"
DEEZER_CACHE: Path = CACHE_DIR / "deezer.json"
RECCOBEATS_CACHE: Path = CACHE_DIR / "reccobeats.json"

# ── Schema ───────────────────────────────────────────────────────────────
# Integer, monotonic. Bump only on breaking renames/removals — additive fields
# don't bump (readers ignore unknowns). See pipeline/schema.py for the registry.
# Mirrors manifest schema_version.
SCHEMA_VERSION: int = 6

MOOD_CATEGORIES: tuple[str, ...] = (
    "Fast", "Moody", "Slow", "Heavy Bass", "Dance", "Sad", "Groove",
    "Heartbreak", "Dark", "Love", "Hype", "Uplifting", "Happy", "Sunny",
)

# Controlled vocabularies for schema fields that schema.py types as free-form
# `str | None`. Nothing validates against these — they are the written record of
# what the allowed values are, so keep them in sync with the writers.
# "audit" is the owner's own labelling: the training signal the classifier is
# built on, and the largest single source in the library.
MOOD_SOURCES: tuple[str, ...] = ("audit", "claude_batch", "centroid", "manual", "inherited")
MOOD_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
CURATION_STATES: tuple[object, ...] = (None, "approved", "locked", "rejected")
AUDIO_FEATURE_SOURCES: tuple[str, ...] = ("exportify", "reccobeats")
# isrc_source values actually written by pipeline/resolve_isrcs.py (Phase 5a).
# An Exportify-sourced isrc predates this field and leaves isrc_source None —
# see the field comment in pipeline/schema.py.
ISRC_SOURCES: tuple[str, ...] = ("musicbrainz", "deezer")

# Month-number → season name. Winter = Dec/Jan/Feb, etc.
SEASON_BY_MONTH: dict[int, str] = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}

# ── API endpoints ────────────────────────────────────────────────────────
LASTFM_API_ROOT: str = "https://ws.audioscrobbler.com/2.0/"
MUSICBRAINZ_API_ROOT: str = "https://musicbrainz.org/ws/2/"
DISCOGS_API_ROOT: str = "https://api.discogs.com/"
ITUNES_SEARCH_API: str = "https://itunes.apple.com/search"
# Spotify Search (NOT the deprecated audio-features endpoint — search is still
# open to new apps). Token via Client-Credentials flow (no user login).
SPOTIFY_API_ROOT: str = "https://api.spotify.com/v1/"
SPOTIFY_TOKEN_URL: str = "https://accounts.spotify.com/api/token"
# Deezer — no auth, no key. Used by Phase 5a as the name-search ISRC fallback
# for tracks a MusicBrainz recording ID doesn't cover.
DEEZER_API_ROOT: str = "https://api.deezer.com/"
# ReccoBeats — no auth, no key, mirrors the pre-deprecation Spotify
# audio-features corpus (validated bit-identical on 33 Exportify rows, see
# issue #37). Endpoint shape below is unverified against a live response —
# the sandbox this was built in has no outbound access to reccobeats.com —
# so it is isolated in pipeline/enrich_audio_features.py's
# _resolve_track_ids/_fetch_audio_features helpers for a one-line fix if the
# real API disagrees. Confirm on the first real run.
RECCOBEATS_API_ROOT: str = "https://api.reccobeats.com/v1/"

# ── Rate limits (req/sec) ────────────────────────────────────────────────
LASTFM_RATE_LIMIT: float = 5.0
MUSICBRAINZ_RATE_LIMIT: float = 1.0   # 1 req/sec hard
DISCOGS_RATE_LIMIT: float = 1.0       # 60/min
ITUNES_RATE_LIMIT: float = 0.33       # ~20/min (conservative)
SPOTIFY_RATE_LIMIT: float = 3.0       # conservative; Spotify uses a 30s rolling window
DEEZER_RATE_LIMIT: float = 2.0        # conservative; documented limit is ~50 req/5s
RECCOBEATS_RATE_LIMIT: float = 2.0    # conservative; no published limit

# Backoff: tries × base × 2^attempt up to max_sleep
HTTP_MAX_RETRIES: int = 5
HTTP_BACKOFF_BASE: float = 0.5
HTTP_BACKOFF_MAX: float = 30.0

# ── Negative-cache expiry ────────────────────────────────────────────────
# Failures are cached like successes so re-runs stay cheap, but they must not
# be permanent: one transient blip would otherwise freeze a track's enrichment
# forever. A genuine 404 is stable and gets a long TTL; anything transient
# (max_retries, invalid_json, network error) retries on the next day's run.
HTTP_NEGATIVE_TTL_SECONDS: float = 30 * 24 * 3600   # not_found — genuine no-match
HTTP_TRANSIENT_TTL_SECONDS: float = 6 * 3600        # max_retries / invalid_json

# ── Cache freshness ──────────────────────────────────────────────────────
APPLE_MUSIC_CACHE_DAYS: int = 90

# ── Logging ──────────────────────────────────────────────────────────────
LOG_FORMAT: str = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
LOG_DATEFMT: str = "%Y-%m-%dT%H:%M:%S"


def configure_logging(
    run_log_path: Path | None = None,
    *,
    console_level: str = "INFO",
) -> Path:
    """Configure root logger to write a timestamped run log + console.

    Closes any existing FileHandlers first so repeated calls (e.g. in tests)
    don't leak open handles on Windows.  Returns the path of the log file used.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if run_log_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        run_log_path = RUNS_DIR / f"{ts}.log"

    # Close existing file handlers before reconfiguring to avoid handle leaks
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": LOG_FORMAT,
                    "datefmt": LOG_DATEFMT,
                },
            },
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": str(run_log_path),
                    "formatter": "default",
                    "level": "DEBUG",
                    "encoding": "utf-8",
                },
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": console_level,
                },
            },
            "loggers": {
                # urllib3 logs every request line at DEBUG, and Last.fm takes
                # its API key as a query parameter — so the root DEBUG level
                # wrote LASTFM_API_KEY in plaintext into runs/*.log, thousands
                # of times per run. Pinning it to INFO drops those lines and
                # leaves the pipeline's own DEBUG output untouched.
                "urllib3": {"level": "INFO"},
            },
            "root": {"level": "DEBUG", "handlers": ["file", "console"]},
        }
    )
    return run_log_path


def get_logger(name: str) -> logging.Logger:
    """Module-scoped logger."""
    return logging.getLogger(name)
