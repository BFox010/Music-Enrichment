"""Phase 7 — apply taste_profile.md to tracks.

Parses the human-edited ``taste_profile.md`` and derives per-track:
  - saturation_tier  (1, 2, 3, or None)
  - blacklisted      (bool)
  - playlists        (list of slugs)
  - curation_state   ("locked", "approved", "rejected", or None)

Re-runs every pipeline pass — the markdown is the source of truth, the
fields on each track are the derived index.

Expected format: see ``taste_profile_template.md`` at repo root. Parser
tolerates several spellings (Tier 1, Tier I, **Tier 1**); see tests for
the supported variants.

Usage:
    python -m pipeline.apply_taste_profile
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

from pipeline.config import (
    REPO_ROOT,
    TASTE_PROFILE_PATH,
    TRACKS_WITH_AVAILABILITY_PATH,
    TRACKS_WITH_METADATA_PATH,
    TRACKS_WITH_MOODS_PATH,
    TRACKS_PATH,
    configure_logging,
    get_logger,
)
from pipeline.normalize import normalize_artist, normalize_track

log = get_logger(__name__)

# Output is the same as the input — taste profile mutates in-place.
OUTPUT_PATH: Path = REPO_ROOT / "tracks_with_taste.jsonl"

_TIER_HEADER_RE = re.compile(r"tier\s+(\d|i{1,3})\b", re.IGNORECASE)
_PLAYLIST_HEADER_RE = re.compile(
    r"^\s*#{2,4}\s+([\w\-]+)\s*\(\s*(locked|approved|rejected)\s*\)",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_HEADER_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")


_ROMAN_TO_INT = {"i": 1, "ii": 2, "iii": 3}


def _parse_tier(raw: str) -> int | None:
    """'1' → 1, 'i' → 1, 'iii' → 3, etc."""
    raw = raw.strip().lower()
    if raw.isdigit():
        n = int(raw)
        return n if 1 <= n <= 3 else None
    return _ROMAN_TO_INT.get(raw)


def _split_track_artist(item: str) -> tuple[str | None, str]:
    """Pull (track, artist) out of a bullet line. ``track`` is None for whole-artist entries.

    Supported formats (in order of precedence):
      "Track" by Artist              → (Track, Artist)
      Track — Artist                 → (Track, Artist)
      Track - Artist                 → (Track, Artist) when '-' is surrounded by spaces
      Artist                         → (None, Artist)
    """
    s = item.strip()
    # Quote-form: "Track" by Artist
    m = re.match(r'^[\"“](.+?)[\"”]\s+by\s+(.+)$', s, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # by-form (no quotes): Track by Artist
    m = re.match(r"^(.+?)\s+by\s+(.+)$", s, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Em-dash form: Track — Artist
    if " — " in s:
        track, _, artist = s.partition(" — ")
        return track.strip(), artist.strip()
    # Hyphen form: Track - Artist (require spaces; reject "a-ha")
    if " - " in s:
        track, _, artist = s.partition(" - ")
        return track.strip(), artist.strip()
    # Whole-artist fallback
    return None, s


# ── parser ──────────────────────────────────────────────────────────────


def parse_taste_profile(markdown: str) -> dict:
    """Parse markdown → manifest dict.

    Returns:
        {
            "tier_by_artist": {artist_norm: int},
            "blacklist_artists": set[str],
            "blacklist_tracks": set[(str, str)],
            "playlists": {(artist_norm, track_norm): {"playlists": [slug], "curation_state": str}},
        }
    """
    tier_by_artist: dict[str, int] = {}
    blacklist_artists: set[str] = set()
    blacklist_tracks: set[tuple[str, str]] = set()
    playlists: dict[tuple[str, str], dict] = {}

    # State machine: which top-level section are we in?
    section: str = "unknown"          # "tiers", "blacklist", "playlists", "unknown"
    current_tier: int | None = None
    current_playlist: tuple[str, str] | None = None  # (slug, state)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # Match playlist header FIRST — it's most specific (### slug (state))
        plm = _PLAYLIST_HEADER_RE.match(line)
        if plm:
            slug = plm.group(1).lower()
            state = plm.group(2).lower()
            current_playlist = (slug, state)
            current_tier = None
            section = "playlists"
            continue

        # Generic header
        hm = _HEADER_RE.match(line)
        if hm:
            text = hm.group(2).lower().strip(" *_")
            current_playlist = None  # leaving any playlist
            if "saturation" in text or "tier" in text and "tiers" in text:
                section = "tiers"
                current_tier = None
                continue
            if "blacklist" in text:
                section = "blacklist"
                current_tier = None
                continue
            if "playlist" in text and "playlists" in text:
                section = "playlists"
                current_tier = None
                continue
            # Tier sub-headers like "### Tier 1 — heavy rotation"
            tm = _TIER_HEADER_RE.search(text)
            if tm and section in ("tiers", "unknown"):
                current_tier = _parse_tier(tm.group(1))
                section = "tiers"
                continue
            # Unknown sub-header inside a known section: stay in section
            continue

        # Bullet item — what we do depends on the section
        bm = _BULLET_RE.match(line)
        if not bm:
            continue
        item = bm.group(1).strip()

        if section == "tiers" and current_tier:
            artist_norm = normalize_artist(item)
            if artist_norm:
                tier_by_artist[artist_norm] = current_tier
            continue

        if section == "blacklist":
            track, artist = _split_track_artist(item)
            artist_norm = normalize_artist(artist)
            if track:
                blacklist_tracks.add((artist_norm, normalize_track(track)))
            else:
                blacklist_artists.add(artist_norm)
            continue

        if section == "playlists" and current_playlist:
            slug, state = current_playlist
            track, artist = _split_track_artist(item)
            if not track:
                # Bare artist in a playlist section — skip with a debug log
                log.debug("Skipping bare-artist entry in playlist %s: %r", slug, item)
                continue
            key = (normalize_artist(artist), normalize_track(track))
            entry = playlists.setdefault(key, {"playlists": [], "curation_state": None})
            if slug not in entry["playlists"]:
                entry["playlists"].append(slug)
            # If multiple playlists list the same track with different states, prefer
            # the strongest: locked > rejected > approved. Rejected is intentional.
            order = {"locked": 3, "rejected": 2, "approved": 1}
            if (entry["curation_state"] is None
                    or order.get(state, 0) > order.get(entry["curation_state"], 0)):
                entry["curation_state"] = state

    return {
        "tier_by_artist": tier_by_artist,
        "blacklist_artists": blacklist_artists,
        "blacklist_tracks": blacklist_tracks,
        "playlists": playlists,
    }


# ── apply manifest to tracks ─────────────────────────────────────────────


def apply_manifest(tracks: Iterable[dict], manifest: dict) -> dict[str, int]:
    """Mutate ``tracks`` in-place; return counts of how many fields were set."""
    stats = {"tiered": 0, "blacklisted": 0, "in_playlists": 0, "curation_set": 0}
    for track in tracks:
        artist_norm = track["artist_normalized"]
        track_norm = track["track_normalized"]
        key = (artist_norm, track_norm)

        # Tier
        tier = manifest["tier_by_artist"].get(artist_norm)
        track["saturation_tier"] = tier
        if tier is not None:
            stats["tiered"] += 1

        # Blacklist
        is_black = (
            artist_norm in manifest["blacklist_artists"]
            or key in manifest["blacklist_tracks"]
        )
        track["blacklisted"] = is_black
        if is_black:
            stats["blacklisted"] += 1

        # Playlists
        plist = manifest["playlists"].get(key)
        if plist:
            track["playlists"] = list(plist["playlists"])
            track["curation_state"] = plist["curation_state"]
            stats["in_playlists"] += 1
            if plist["curation_state"]:
                stats["curation_set"] += 1
    return stats


def apply(
    profile_path: Path = TASTE_PROFILE_PATH,
    input_path: Path | None = None,
    output_path: Path = OUTPUT_PATH,
    run_log_path: Path | None = None,
) -> dict[str, int]:
    """Apply the taste profile to the latest available track JSONL."""
    configure_logging(run_log_path)
    log.info("=== Phase 7: apply taste_profile.md ===")

    if not profile_path.exists():
        raise FileNotFoundError(profile_path)

    if input_path is None:
        for candidate in (
            TRACKS_WITH_MOODS_PATH,
            TRACKS_WITH_AVAILABILITY_PATH,
            TRACKS_WITH_METADATA_PATH,
            TRACKS_PATH,
        ):
            if candidate.exists():
                input_path = candidate
                break
    if input_path is None:
        raise FileNotFoundError("No tracks JSONL found")

    log.info("Profile : %s", profile_path)
    log.info("Input   : %s", input_path)
    log.info("Output  : %s", output_path)

    markdown = profile_path.read_text(encoding="utf-8")
    manifest = parse_taste_profile(markdown)
    log.info(
        "Parsed: %d tier entries, %d blacklist artists, %d blacklist tracks, "
        "%d playlist entries",
        len(manifest["tier_by_artist"]),
        len(manifest["blacklist_artists"]),
        len(manifest["blacklist_tracks"]),
        len(manifest["playlists"]),
    )

    tracks: list[dict] = []
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tracks.append(json.loads(line))

    stats = apply_manifest(tracks, manifest)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in tracks:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "Phase 7 done: tiered=%d  blacklisted=%d  in_playlists=%d  curation_set=%d  "
        "/  %d total",
        stats["tiered"], stats["blacklisted"], stats["in_playlists"],
        stats["curation_set"], len(tracks),
    )
    return stats


if __name__ == "__main__":
    apply()
    sys.exit(0)
