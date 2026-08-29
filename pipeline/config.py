"""Pipeline configuration: paths, schema constants, API endpoints, logging."""

from __future__ import annotations

import logging
import logging.config
from datetime import datetime, timezone
from pathlib import Path

# ── Repo root ──
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

# ── Canonical data files ──
TRACKS_PATH: Path = REPO_ROOT / "tracks.jsonl"
SCROBBLES_PATH: Path = REPO_ROOT / "scrobbles.jsonl"

# ── Intermediate per-phase outputs (gitignored) ──
TRACKS_SKELETON_PATH: Path = REPO_ROOT / "tracks_skeleton.jsonl"
TRACKS_WITH_SPOTIFY_PATH: Path = REPO_ROOT / "tracks_with_spotify.jsonl"
TRACKS_WITH_AUDIO_PATH: Path = REPO_ROOT / "tracks_with_audio.jsonl"
TRACKS_WITH_METADATA_PATH: Path = REPO_ROOT / "tracks_with_metadata.jsonl"
TRACKS_WITH_DISCOGS_PATH: Path = REPO_ROOT / "tracks_with_discogs.jsonl"
TRACKS_WITH_GENRES_PATH: Path = REPO_ROOT / "tracks_with_genres.jsonl"
TRACKS_WITH_GENRE_BACKFILL_PATH: Path = REPO_ROOT / "tracks_with_genre_backfill.jsonl"
TRACKS_WITH_ISRCS_PATH: Path = REPO_ROOT / "tracks_with_isrcs.jsonl"
TRACKS_RESOLVED_PATH: Path = REPO_ROOT / "tracks_resolved.jsonl"
TRACKS_WITH_AVAILABILITY_PATH: Path = REPO_ROOT / "tracks_with_availability.jsonl"
TRACKS_WITH_FEATURES_PATH: Path = REPO_ROOT / "tracks_with_features.jsonl"
TRACKS_WITH_MOODS_PATH: Path = REPO_ROOT / "tracks_with_moods.jsonl"

# ── Human-edited reference (DO NOT auto-modify) ──
TASTE_PROFILE_PATH: Path = REPO_ROOT / "taste_profile.md"

# ── Canonical mood training labels (#66) — git-tracked, always the default for
# Phase 6. inputs/existing_audit.csv (below) is a gitignored legacy copy that is
# never authoritative; where the two disagree, this file wins.
MOOD_AUDIT_FILENAME: str = "mood_audit.csv"
MOOD_AUDIT_PATH: Path = REPO_ROOT / MOOD_AUDIT_FILENAME

# ── Directories ──
RUNS_DIR: Path = REPO_ROOT / "runs"
VIEWS_DIR: Path = REPO_ROOT / "views"        # gitignored
CACHE_DIR: Path = REPO_ROOT / ".cache"       # gitignored
INPUTS_DIR: Path = REPO_ROOT / "inputs"      # gitignored

# ── Owner-provided inputs (not committed) ──
INPUT_LASTFM_EXPORT: Path = INPUTS_DIR / "lastfm_export.json"
INPUT_APPLE_MUSIC_LIBRARY: Path = INPUTS_DIR / "apple_music_library.xml"  # iTunes XML export
INPUT_EXISTING_AUDIT: Path = INPUTS_DIR / "existing_audit.csv"  # legacy; see MOOD_AUDIT_PATH
INPUT_EXPORTIFY_CSV: Path = INPUTS_DIR / "exportify.csv"
INPUT_CLAUDE_MOOD_RESULTS: Path = INPUTS_DIR / "claude_mood_results.jsonl"
# Spotify app credentials (Client ID + Secret). Either set the env vars
# SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET, or drop a JSON file here with
# {"client_id": "...", "client_secret": "..."}. Absent → Phase B is skipped.
INPUT_SPOTIFY_CREDENTIALS: Path = INPUTS_DIR / "spotify_credentials.json"

# ── API caches ──
APPLE_MUSIC_CACHE: Path = CACHE_DIR / "apple_music.json"
LASTFM_CACHE: Path = CACHE_DIR / "lastfm.json"
MUSICBRAINZ_CACHE: Path = CACHE_DIR / "musicbrainz.json"
DISCOGS_CACHE: Path = CACHE_DIR / "discogs.json"
SPOTIFY_CACHE: Path = CACHE_DIR / "spotify_search.json"
DEEZER_CACHE: Path = CACHE_DIR / "deezer.json"
RECCOBEATS_CACHE: Path = CACHE_DIR / "reccobeats.json"

# ── Schema ──
# Monotonic int, mirrored in pipeline_manifest.yaml. Bump only on breaking
# renames/removals; additive fields don't (readers ignore unknowns).
SCHEMA_VERSION: int = 6

MOOD_CATEGORIES: tuple[str, ...] = (
    "Fast", "Moody", "Slow", "Heavy Bass", "Dance", "Sad", "Groove",
    "Heartbreak", "Dark", "Love", "Hype", "Uplifting", "Happy", "Sunny",
)

# Controlled vocabularies for fields schema.py types as free-form `str | None`.
# Nothing validates against these — they are the written record. Keep in sync
# with the writers. "audit" is the owner's own labelling: the classifier's
# training signal, and the largest single source in the library.
MOOD_SOURCES: tuple[str, ...] = ("audit", "claude_batch", "centroid", "manual", "inherited")
# Trust order when two rows or two fresh inputs disagree about mood. The single
# definition classify_moods and resolve_identity both consult, so a hand label
# always wins over a model's and neither module can encode the opposite order.
# "manual" tops it: the bass overlay writes it onto rows the owner reviewed by
# hand, and is applied last precisely so it survives a Phase 6 re-run. Every
# source is listed — an absent key scores 0 and would lose to the centroid.
MOOD_SOURCE_RANK: dict[str | None, int] = {
    "manual": 5, "audit": 4, "claude_batch": 3, "centroid": 2,
    "inherited": 1, None: 0,
}
# Sources at or above this rank are curated judgements — a person's, or an LLM
# pass a person commissioned and reviewed. Below it are machine guesses derived
# from audio features. The split matters because Phase 6 declining to label a
# row (mood_source None) is itself a machine verdict: it must clear a stale
# machine guess but never erase a curated label. See
# update_tracks._merge_with_existing.
MOOD_CURATED_MIN_RANK: int = MOOD_SOURCE_RANK["claude_batch"]
MOOD_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
CURATION_STATES: tuple[object, ...] = (None, "approved", "locked", "rejected")
AUDIO_FEATURE_SOURCES: tuple[str, ...] = ("exportify", "reccobeats")
# Written by Phase 5a only. An Exportify-sourced isrc predates the field and
# leaves isrc_source None.
ISRC_SOURCES: tuple[str, ...] = ("musicbrainz", "deezer")

# Month-number → season name. Winter = Dec/Jan/Feb, etc.
SEASON_BY_MONTH: dict[int, str] = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}

# ── API endpoints ──
LASTFM_API_ROOT: str = "https://ws.audioscrobbler.com/2.0/"
MUSICBRAINZ_API_ROOT: str = "https://musicbrainz.org/ws/2/"
DISCOGS_API_ROOT: str = "https://api.discogs.com/"
ITUNES_SEARCH_API: str = "https://itunes.apple.com/search"
# Search only — the audio-features endpoint is closed to new apps. Token via
# Client-Credentials (no user login).
SPOTIFY_API_ROOT: str = "https://api.spotify.com/v1/"
SPOTIFY_TOKEN_URL: str = "https://accounts.spotify.com/api/token"
# No auth. Phase 5a's name-search ISRC fallback where MusicBrainz has no recording ID.
DEEZER_API_ROOT: str = "https://api.deezer.com/"
# No auth. Mirrors the pre-deprecation Spotify audio-features corpus
# (bit-identical on 33 Exportify rows, #37).
RECCOBEATS_API_ROOT: str = "https://api.reccobeats.com/v1/"

# ── Rate limits (req/sec) ──
LASTFM_RATE_LIMIT: float = 5.0
MUSICBRAINZ_RATE_LIMIT: float = 1.0   # 1 req/sec hard
DISCOGS_RATE_LIMIT: float = 1.0       # 60/min
ITUNES_RATE_LIMIT: float = 0.33       # ~20/min (conservative)
SPOTIFY_RATE_LIMIT: float = 3.0       # conservative; Spotify uses a 30s rolling window
# Half of Deezer's documented ~10/sec. Phase 5a spends most of its wall clock
# here — it issues several name variations per track, so this dominates the run.
# MusicBrainz's 1/sec above is a hard published limit and must not be raised.
DEEZER_RATE_LIMIT: float = 5.0
RECCOBEATS_RATE_LIMIT: float = 2.0    # conservative; no published limit

# Backoff: tries × base × 2^attempt up to max_sleep
HTTP_MAX_RETRIES: int = 5
HTTP_BACKOFF_BASE: float = 0.5
HTTP_BACKOFF_MAX: float = 30.0

# ── Negative-cache expiry ──
# Failures are cached like successes, but must expire or one transient blip
# freezes a track's enrichment forever. A 404 is stable → long TTL; transient
# kinds retry on the next day's run.
HTTP_NEGATIVE_TTL_SECONDS: float = 30 * 24 * 3600   # not_found — genuine no-match
HTTP_TRANSIENT_TTL_SECONDS: float = 6 * 3600        # max_retries / invalid_json

# ── Cache freshness ──
APPLE_MUSIC_CACHE_DAYS: int = 90

# ── Logging ──
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
