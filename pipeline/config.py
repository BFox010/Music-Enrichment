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
TRACKS_WITH_AUDIO_PATH: Path = REPO_ROOT / "tracks_with_audio.jsonl"
TRACKS_WITH_METADATA_PATH: Path = REPO_ROOT / "tracks_with_metadata.jsonl"
TRACKS_WITH_AVAILABILITY_PATH: Path = REPO_ROOT / "tracks_with_availability.jsonl"
TRACKS_WITH_MOODS_PATH: Path = REPO_ROOT / "tracks_with_moods.jsonl"

# ── Human-edited reference (DO NOT auto-modify) ──────────────────────────
TASTE_PROFILE_PATH: Path = REPO_ROOT / "taste_profile.md"
SCROBBLE_REFERENCE_PATH: Path = REPO_ROOT / "scrobble_reference.md"

# ── Directories ──────────────────────────────────────────────────────────
PLAYLISTS_DIR: Path = REPO_ROOT / "playlists"
RUNS_DIR: Path = REPO_ROOT / "runs"
VIEWS_DIR: Path = REPO_ROOT / "views"        # gitignored
CACHE_DIR: Path = REPO_ROOT / ".cache"       # gitignored
INPUTS_DIR: Path = REPO_ROOT / "inputs"      # gitignored

# ── Owner-provided inputs (not committed) ────────────────────────────────
INPUT_LASTFM_EXPORT: Path = INPUTS_DIR / "lastfm_export.json"
INPUT_APPLE_MUSIC_LIBRARY: Path = INPUTS_DIR / "apple_music_library.csv"
INPUT_EXISTING_AUDIT: Path = INPUTS_DIR / "existing_audit.csv"
INPUT_EXPORTIFY_CSV: Path = INPUTS_DIR / "exportify.csv"
INPUT_CLAUDE_MOOD_RESULTS: Path = INPUTS_DIR / "claude_mood_results.jsonl"

# ── API caches ───────────────────────────────────────────────────────────
APPLE_MUSIC_CACHE: Path = CACHE_DIR / "apple_music.json"
LASTFM_CACHE: Path = CACHE_DIR / "lastfm.json"
MUSICBRAINZ_CACHE: Path = CACHE_DIR / "musicbrainz.json"
DISCOGS_CACHE: Path = CACHE_DIR / "discogs.json"

# ── Schema ───────────────────────────────────────────────────────────────
SCHEMA_VERSION: str = "1.0.0"

MOOD_CATEGORIES: tuple[str, ...] = (
    "Fast", "Moody", "Slow", "Heavy Bass", "Dance", "Sad", "Groove",
    "Heartbreak", "Dark", "Love", "Hype", "Uplifting", "Happy", "Sunny",
)
MOOD_SOURCES: tuple[str, ...] = ("claude_batch", "centroid", "manual", "inherited")
MOOD_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
CURATION_STATES: tuple[object, ...] = (None, "approved", "locked", "rejected")
AUDIO_FEATURE_SOURCES: tuple[str, ...] = ("exportify", "reccobeats")

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

# ── Rate limits (req/sec) ────────────────────────────────────────────────
LASTFM_RATE_LIMIT: float = 5.0
MUSICBRAINZ_RATE_LIMIT: float = 1.0   # 1 req/sec hard
DISCOGS_RATE_LIMIT: float = 1.0       # 60/min
ITUNES_RATE_LIMIT: float = 0.33       # ~20/min (conservative)

# Backoff: tries × base × 2^attempt up to max_sleep
HTTP_MAX_RETRIES: int = 5
HTTP_BACKOFF_BASE: float = 0.5
HTTP_BACKOFF_MAX: float = 30.0

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

    Returns the path of the log file actually used.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if run_log_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        run_log_path = RUNS_DIR / f"{ts}.log"

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
            "root": {"level": "DEBUG", "handlers": ["file", "console"]},
        }
    )
    return run_log_path


def get_logger(name: str) -> logging.Logger:
    """Module-scoped logger."""
    return logging.getLogger(name)
