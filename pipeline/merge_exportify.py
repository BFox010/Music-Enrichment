"""Phase 3c — merge Exportify audio-features CSV into the track set.

Reads an Exportify CSV (default: ``inputs/exportify.csv``) and merges audio
features into the latest available intermediate JSONL. Handles the known
Exportify column layout; tolerates missing optional columns.

Includes a sanity check for the documented energy-scale bug from the prior
pipeline iteration (median energy < 0.1 means values are off by ~10×). If
the new run hits that, abort before writing anything.

Usage:
    python -m pipeline.merge_exportify
    python -m pipeline.merge_exportify --csv inputs/my_export.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.config import (
    INPUT_EXPORTIFY_CSV,
    REPO_ROOT,
    TRACKS_WITH_AUDIO_PATH,
    TRACKS_WITH_METADATA_PATH,
    configure_logging,
    get_logger,
)
from pipeline.enrich_apple_library import TRACKS_WITH_APPLE_PATH
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)

# Exportify column names — current as of March 2026 verification.
# Some columns may be absent depending on Exportify version; use defaults.
_AUDIO_FEATURE_COLUMNS: dict[str, str] = {
    "danceability": "Danceability",
    "energy": "Energy",
    "valence": "Valence",
    "tempo": "Tempo",
    "loudness": "Loudness",
    "speechiness": "Speechiness",
    "acousticness": "Acousticness",
    "instrumentalness": "Instrumentalness",
    "liveness": "Liveness",
    "key": "Key",
    "mode": "Mode",
    "time_signature": "Time Signature",
}

# Read from the DEEPEST existing intermediate so we don't lose downstream data
# (e.g. apple_music availability set by Phase 5).
_INPUT_PRIORITY = [
    REPO_ROOT / "tracks_with_availability.jsonl",
    TRACKS_WITH_METADATA_PATH,
    TRACKS_WITH_APPLE_PATH,
    REPO_ROOT / "tracks_skeleton.jsonl",
]


def _f(value: Any) -> float | None:
    """Parse to float; return None on empty/malformed."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _b(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


def _year_from_date(value: Any) -> int | None:
    """Extract a 4-digit year from common date formats (YYYY, YYYY-MM, YYYY-MM-DD)."""
    if not value:
        return None
    s = str(value).strip()
    if len(s) >= 4:
        try:
            return int(s[:4])
        except ValueError:
            return None
    return None


def _ci_get(row: dict, *candidates: str) -> str:
    """Case-insensitive lookup across candidate column names. Returns '' if absent."""
    if not row:
        return ""
    lookup = {k.lower().strip(): v for k, v in row.items() if k}
    for cand in candidates:
        v = lookup.get(cand.lower().strip())
        if v is not None and v != "":
            return str(v)
    return ""


def _spotify_id_from_uri(uri: str) -> str | None:
    """``spotify:track:xxxxx`` → ``xxxxx``; return None if not a valid track URI."""
    if not uri:
        return None
    s = uri.strip()
    if s.startswith("spotify:track:"):
        return s[len("spotify:track:"):] or None
    # Some Exportify versions output bare IDs
    if len(s) == 22 and s.isalnum():
        return s
    return None


def parse_exportify_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one Exportify CSV row to a merge-block.

    Tolerant of column-name variations (case + alternate forms used by
    TuneMyMusic exports vs. real Exportify). Returns None on bad rows.
    """
    track_name = _ci_get(row, "Track Name", "Track name", "Name").strip()
    artists_raw = _ci_get(
        row, "Artist Name(s)", "Artist Name", "Artist name", "Artist"
    ).strip()
    if not track_name or not artists_raw:
        return None

    # Real Exportify uses ';' for multi-artist; TuneMyMusic exports use ',' or single artist.
    # Try ';' first (Exportify convention), fall back to ',' if no semicolon present.
    if ";" in artists_raw:
        first_artist = artists_raw.split(";")[0].strip()
    else:
        first_artist = artists_raw.split(",")[0].strip()

    # Audio features block — populate only if at least one feature has a value
    audio_features: dict[str, Any] = {"source": "exportify"}
    has_any_feature = False
    for our_key, csv_col in _AUDIO_FEATURE_COLUMNS.items():
        raw = _ci_get(row, csv_col)
        if our_key in ("key", "mode", "time_signature"):
            value = _i(raw)
        else:
            value = _f(raw)
        audio_features[our_key] = value
        if value is not None:
            has_any_feature = True

    return {
        "artist_normalized": normalize_artist(first_artist),
        "track_normalized": normalize_track(track_name),
        "exportify_artist": first_artist,
        "exportify_track": track_name,
        "spotify_id": _spotify_id_from_uri(
            _ci_get(row, "Track URI", "Spotify ID", "Spotify - id", "Spotify Id")
        ),
        "duration_ms": _i(_ci_get(
            row, "Track Duration (ms)", "Duration (ms)", "Duration",
        )),
        "explicit": _b(_ci_get(row, "Explicit")),
        "isrc": _ci_get(row, "ISRC").strip() or None,
        "release_year": _year_from_date(
            _ci_get(row, "Album Release Date", "Release Date")
        ),
        "audio_features": audio_features if has_any_feature else None,
    }


def _check_energy_bug(rows: list[dict]) -> float | None:
    """Return median energy if values look correct; raise if they look ~10× off.

    The prior pipeline iteration recorded median energies around 0.04, two
    orders of magnitude below the real Spotify scale of ~0.4–0.5. This check
    catches the same bug if it ever recurs. Returns None when no energies
    are present (which is normal for a Spotify-IDs-only CSV).
    """
    energies = [
        r["audio_features"]["energy"]
        for r in rows
        if r.get("audio_features") and r["audio_features"].get("energy") is not None
    ]
    if not energies:
        return None
    median = statistics.median(energies)
    log.info("Median energy: %.4f (n=%d)", median, len(energies))
    if median < 0.1:
        raise ValueError(
            f"Median energy {median:.4f} is < 0.1 — likely the off-by-10 bug. "
            f"Inspect Exportify CSV before re-running."
        )
    return median


def _pick_input(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    for p in _INPUT_PRIORITY:
        if p.exists():
            return p
    raise FileNotFoundError(
        "No upstream JSONL found. Run earlier phases first."
    )


def merge(
    csv_path: Path = INPUT_EXPORTIFY_CSV,
    input_path: Path | None = None,
    output_path: Path = TRACKS_WITH_AUDIO_PATH,
    run_log_path: Path | None = None,
) -> dict[str, Any]:
    """Merge Exportify CSV into the track set, write tracks_with_audio.jsonl."""
    configure_logging(run_log_path)
    log.info("=== Phase 3c: Exportify audio-features merge ===")

    if not csv_path.exists():
        log.error(
            "Exportify CSV not found at %s. Run Phase 3a, do TuneMyMusic + Exportify, "
            "then save the result here.", csv_path,
        )
        raise FileNotFoundError(csv_path)

    chosen_input = _pick_input(input_path)
    log.info("CSV    : %s", csv_path)
    log.info("Input  : %s", chosen_input)
    log.info("Output : %s", output_path)

    # Parse CSV (utf-8-sig strips a BOM if Excel/TuneMyMusic emits one)
    csv_blocks: list[dict] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            block = parse_exportify_row(row)
            if block is not None:
                csv_blocks.append(block)

    log.info("CSV rows parsed: %d", len(csv_blocks))

    # Energy sanity check — abort if scale looks broken
    median = _check_energy_bug(csv_blocks)
    if median is not None and median < 0.2:
        log.warning("Median energy %.3f is unusually low — verify Exportify output", median)

    # Build join-key index. Multiple CSV rows can share a key (compilation
    # appearances, etc.); take the FIRST encountered.
    csv_index: dict[tuple[str, str], dict] = {}
    for block in csv_blocks:
        key = (block["artist_normalized"], block["track_normalized"])
        csv_index.setdefault(key, block)

    # Load existing tracks
    tracks: list[dict] = []
    with open(chosen_input, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))

    matched = 0
    unmatched: list[tuple[str, str]] = []
    for track in tracks:
        key = (track["artist_normalized"], track["track_normalized"])
        block = csv_index.get(key)
        if block is None:
            unmatched.append((track["artist"], track["track"]))
            continue
        matched += 1
        # Only set audio_features if Exportify actually provided them
        if block.get("audio_features") is not None:
            track["audio_features"] = block["audio_features"]
        if block.get("spotify_id"):
            track["spotify_id"] = block["spotify_id"]
        if block.get("isrc"):
            track["isrc"] = block["isrc"]
        # Only fill these if missing (Exportify is more authoritative than iTunes XML)
        for field in ("duration_ms", "explicit", "release_year"):
            new_value = block.get(field)
            if new_value is not None:
                track[field] = new_value

    pct = (matched / len(tracks) * 100) if tracks else 0.0
    log.info("Matched: %d / %d (%.1f%%)", matched, len(tracks), pct)

    if pct < 80.0:
        log.warning("Match rate %.1f%% is below the 80%% target.", pct)

    # Persist unmatched track list for inspection
    if unmatched:
        unmatched_path = REPO_ROOT / "runs" / (
            f"unmatched_exportify_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}.txt"
        )
        unmatched_path.parent.mkdir(parents=True, exist_ok=True)
        with open(unmatched_path, "w", encoding="utf-8") as fh:
            for a, t in unmatched:
                fh.write(f"{a}\t{t}\n")
        log.info("Unmatched tracks logged to %s", unmatched_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for track in tracks:
            fh.write(json.dumps(track, ensure_ascii=False) + "\n")

    log.info("Wrote → %s", output_path)
    return {
        "total": len(tracks),
        "matched": matched,
        "match_rate": pct,
        "csv_rows": len(csv_blocks),
        "median_energy": median,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Merge Exportify CSV into tracks.")
    p.add_argument("--csv", type=Path, default=INPUT_EXPORTIFY_CSV)
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--output", type=Path, default=TRACKS_WITH_AUDIO_PATH)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    stats = merge(csv_path=args.csv, input_path=args.input, output_path=args.output)
    sys.exit(0 if stats["match_rate"] >= 50 else 1)
