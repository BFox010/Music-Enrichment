"""Phase 7 — apply taste_profile.md to tracks.

Parses the human-edited ``taste_profile.md`` and derives per-track:
  - saturation_tier  (1, 2, 3, or None)
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
    """Bullet line → (track, artist); ``track`` is None for a whole-artist entry.

    Formats, highest precedence first: ``"Track" by Artist``, ``Track by Artist``,
    ``Track — Artist``, ``Track - Artist``, bare ``Artist``.
    """
    s = item.strip()
    m = re.match(r'^[\"“](.+?)[\"”]\s+by\s+(.+)$', s, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.+?)\s+by\s+(.+)$", s, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if " — " in s:
        track, _, artist = s.partition(" — ")
        return track.strip(), artist.strip()
    if " - " in s:   # spaces required, so "a-ha" is not split
        track, _, artist = s.partition(" - ")
        return track.strip(), artist.strip()
    return None, s


# ── rich parser (v4 taste profile format) ──


_QUOTED_TRACK_RE = re.compile(r'([^"]+?)"([^"]+)"')
_PLAYLIST_HEADER_BOLD_RE = re.compile(r'\*\*([^*:]+?):\*\*')

_PLAYLIST_SLUG_MAP: dict[str, str] = {
    "summer": "summer",
    "night drive": "night_drive",
    "heavy weather": "heavy_weather",
    "heavy weather (sulk)": "heavy_weather",
    "workout": "workout",
    "workout (lift)": "workout",
    "love": "love",
    "love (💕)": "love",
}

_LABELED_SEGMENT_STATES: list[tuple[str, str]] = [
    ("Locked:", "locked"),
    ("Spine:", "locked"),
    ("Known:", "locked"),
    ("Discovery picks accepted:", "approved"),
    ("Rejected:", "rejected"),
]


def _is_rich_format(markdown: str) -> bool:
    """Detect the v4 format by TIER 1/2/3 markers AND presence of a markdown table."""
    return (
        "TIER 1" in markdown
        and "TIER 2" in markdown
        and "TIER 3" in markdown
        and "|---" in markdown
    )


def _slice_section(text: str, start_marker: str, end_marker: str | None) -> str:
    """Slice text from after start_marker to before end_marker (or end)."""
    i = text.find(start_marker)
    if i == -1:
        return ""
    rest = text[i + len(start_marker):]
    if end_marker:
        j = rest.find(end_marker)
        if j != -1:
            return rest[:j]
    return rest


def _table_first_column(text: str) -> list[str]:
    """Extract first column from a markdown table, skipping header/separator rows."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if not first or first.lower() == "artist" or set(first) <= set("-: "):
            continue
        out.append(first)
    return out


def _inline_dot_list(text: str) -> list[str]:
    """Parse a '·'-separated artist list, stripping (count) parentheticals.

    Multi-line segments — which happen when a section header lands in the slice —
    reduce to their first non-header line. Splits on '·' only, so
    "Tyler, The Creator" survives as one entry.
    """
    out: list[str] = []
    for segment in text.split("·"):
        lines = [
            line.strip()
            for line in segment.splitlines()
            if line.strip()
            and not line.strip().startswith("**")
            and not line.strip().startswith("|")  # table-row leftover
            and not line.strip().startswith("---")
            and not line.strip().lower().startswith("tier")
        ]
        if not lines:
            continue
        # Artist is the first surviving line; later ones are the next section's
        # header bleeding into the slice.
        cleaned = lines[0]
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()   # "(220)", "(37 but deep …)"
        cleaned = cleaned.strip("*").strip()
        if cleaned:
            out.append(cleaned)
    return out


def _playlist_prose_entries(text: str) -> dict[tuple[str, str], dict]:
    """Parse rich-prose playlist sections: ``**Name:**`` headers, then the
    ``_LABELED_SEGMENT_STATES`` segments inside each. Tracks read as
    ``Artist "Track Name"``.
    """
    out: dict[tuple[str, str], dict] = {}
    matches = list(_PLAYLIST_HEADER_BOLD_RE.finditer(text))

    for i, m in enumerate(matches):
        name_raw = m.group(1).strip()
        name_lower = name_raw.lower()
        slug = _PLAYLIST_SLUG_MAP.get(name_lower)
        if slug is None:
            cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", name_raw).strip().lower()
            slug = _PLAYLIST_SLUG_MAP.get(cleaned)
        if slug is None:
            continue

        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end]

        label_positions: list[tuple[int, str, str]] = []
        for label, state in _LABELED_SEGMENT_STATES:
            idx = content.find(label)
            if idx != -1:
                label_positions.append((idx, label, state))
        label_positions.sort()

        for j, (idx, label, state) in enumerate(label_positions):
            seg_start = idx + len(label)
            seg_end = label_positions[j + 1][0] if j + 1 < len(label_positions) else len(content)
            segment = content[seg_start:seg_end]

            for tm in _QUOTED_TRACK_RE.finditer(segment):
                raw_artist = tm.group(1).strip()
                cleaned_artist = re.sub(r"^[,.\s·]+", "", raw_artist).strip()
                # a previous item's trailing parenthetical can lead this one
                cleaned_artist = re.sub(r"^\([^)]*\)\s*", "", cleaned_artist).strip()
                track = tm.group(2).strip()
                if not cleaned_artist or not track:
                    continue
                key = (normalize_artist(cleaned_artist), normalize_track(track))
                entry = out.setdefault(key, {"playlists": [], "curation_state": None})
                if slug not in entry["playlists"]:
                    entry["playlists"].append(slug)
                order = {"locked": 3, "rejected": 2, "approved": 1}
                if (
                    entry["curation_state"] is None
                    or order.get(state, 0) > order.get(entry["curation_state"], 0)
                ):
                    entry["curation_state"] = state
    return out


def parse_rich_taste_profile(markdown: str) -> dict:
    """Parse the v4 rich taste-profile format (tables, inline dot-lists, prose)."""
    tier_by_artist: dict[str, int] = {}

    # End at the next H2, NOT the next `---` — that matches `|---|` inside a
    # markdown table separator row.
    sat_section = _slice_section(markdown, "## SATURATION TIERS", "\n## ")

    tier1_section = _slice_section(sat_section, "TIER 1", "TIER 2")
    for artist in _table_first_column(tier1_section):
        tier_by_artist[normalize_artist(artist)] = 1

    tier2_section = _slice_section(sat_section, "TIER 2", "TIER 3")
    for artist in _inline_dot_list(tier2_section):
        norm = normalize_artist(artist)
        if norm not in tier_by_artist:  # Tier 1 wins over Tier 2
            tier_by_artist[norm] = 2

    tier3_section = _slice_section(sat_section, "TIER 3", None)
    for artist in _inline_dot_list(tier3_section):
        norm = normalize_artist(artist)
        if norm not in tier_by_artist:
            tier_by_artist[norm] = 3

    pl_section = _slice_section(markdown, "## EXISTING PLAYLIST DNA", "\n## ")
    playlists = _playlist_prose_entries(pl_section)

    return {
        "tier_by_artist": tier_by_artist,
        "playlists": playlists,
    }


# ── parser ──


def parse_taste_profile(markdown: str) -> dict:
    """Public entry: auto-detect rich vs simple format and dispatch."""
    if _is_rich_format(markdown):
        return parse_rich_taste_profile(markdown)
    return parse_simple_taste_profile(markdown)


def parse_simple_taste_profile(markdown: str) -> dict:
    """Parse markdown → manifest dict.

    Returns:
        {
            "tier_by_artist": {artist_norm: int},
            "playlists": {(artist_norm, track_norm): {"playlists": [slug], "curation_state": str}},
        }
    """
    tier_by_artist: dict[str, int] = {}
    playlists: dict[tuple[str, str], dict] = {}

    section: str = "unknown"          # "tiers" | "playlists" | "unknown"
    current_tier: int | None = None
    current_playlist: tuple[str, str] | None = None  # (slug, state)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # Playlist header first — `### slug (state)` is the most specific pattern.
        plm = _PLAYLIST_HEADER_RE.match(line)
        if plm:
            slug = plm.group(1).lower()
            state = plm.group(2).lower()
            current_playlist = (slug, state)
            current_tier = None
            section = "playlists"
            continue

        hm = _HEADER_RE.match(line)
        if hm:
            text = hm.group(2).lower().strip(" *_")
            current_playlist = None
            if "saturation" in text or "tier" in text and "tiers" in text:
                section = "tiers"
                current_tier = None
                continue
            if "playlist" in text and "playlists" in text:
                section = "playlists"
                current_tier = None
                continue
            tm = _TIER_HEADER_RE.search(text)
            if tm and section in ("tiers", "unknown"):
                current_tier = _parse_tier(tm.group(1))
                section = "tiers"
                continue
            continue   # unknown sub-header: stay in the current section

        bm = _BULLET_RE.match(line)
        if not bm:
            continue
        item = bm.group(1).strip()

        if section == "tiers" and current_tier:
            artist_norm = normalize_artist(item)
            if artist_norm:
                tier_by_artist[artist_norm] = current_tier
            continue

        if section == "playlists" and current_playlist:
            slug, state = current_playlist
            track, artist = _split_track_artist(item)
            if not track:
                log.debug("Skipping bare-artist entry in playlist %s: %r", slug, item)
                continue
            key = (normalize_artist(artist), normalize_track(track))
            entry = playlists.setdefault(key, {"playlists": [], "curation_state": None})
            if slug not in entry["playlists"]:
                entry["playlists"].append(slug)
            # Strongest state wins across playlists: locked > rejected > approved.
            # Rejected outranking approved is intentional.
            order = {"locked": 3, "rejected": 2, "approved": 1}
            if (entry["curation_state"] is None
                    or order.get(state, 0) > order.get(entry["curation_state"], 0)):
                entry["curation_state"] = state

    return {
        "tier_by_artist": tier_by_artist,
        "playlists": playlists,
    }


# ── apply manifest to tracks ──


def _track_identity_keys(track: dict) -> list[tuple[str, str]]:
    """Every (artist_normalized, track_normalized) this row answers to — its own
    key plus whatever Phase 4e folded in.

    Without this, a taste_profile.md entry written against a credit variant that
    4e later merges away silently stops matching.
    """
    own = (track.get("artist_normalized") or "", track.get("track_normalized") or "")
    keys = [own]
    for alias in track.get("identity_aliases") or []:
        if isinstance(alias, (list, tuple)) and len(alias) == 2:
            pair = (alias[0], alias[1])
            if pair not in keys:
                keys.append(pair)
    return keys


def apply_manifest(tracks: Iterable[dict], manifest: dict) -> dict[str, int]:
    """Mutate ``tracks`` in place; return how many fields were set.

    Matched against every identity the row carries (``_track_identity_keys``),
    not just its current display key.
    """
    stats = {"tiered": 0, "in_playlists": 0, "curation_set": 0}
    for track in tracks:
        keys = _track_identity_keys(track)
        artists = [k[0] for k in keys]

        # Keyed on artist alone, so any alias's artist credit counts.
        tier = None
        for artist_norm in artists:
            tier = manifest["tier_by_artist"].get(artist_norm)
            if tier is not None:
                break
        track["saturation_tier"] = tier
        if tier is not None:
            stats["tiered"] += 1

        plist = None
        for key in keys:
            candidate = manifest["playlists"].get(key)
            if candidate:
                plist = candidate
                break
        if plist:
            track["playlists"] = list(plist["playlists"])
            track["curation_state"] = plist["curation_state"]
            stats["in_playlists"] += 1
            if plist["curation_state"]:
                stats["curation_set"] += 1
        else:
            track["playlists"] = []
            track["curation_state"] = None
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
            REPO_ROOT / "tracks_with_features.jsonl",
            REPO_ROOT / "tracks_with_isrcs.jsonl",
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
        "Parsed: %d tier entries, %d playlist entries",
        len(manifest["tier_by_artist"]),
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
        "Phase 7 done: tiered=%d  in_playlists=%d  curation_set=%d  /  %d total",
        stats["tiered"], stats["in_playlists"], stats["curation_set"], len(tracks),
    )
    return stats


if __name__ == "__main__":
    apply()
    sys.exit(0)
