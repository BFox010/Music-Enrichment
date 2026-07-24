"""Dashboard aggregations over the in-memory JSONL cache.

All public functions read from ``app.data.get_tracks()`` /
``get_scrobbles()`` so they automatically pick up test fixtures injected
via ``data.use_paths()``. Logic is ported from ``scripts/library_stats.py``
but returns dicts instead of printing.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Optional

from app.data import get_scrobbles, get_tracks

_COVERAGE_FIELDS: list[tuple[str, str]] = [
    ("genres", "genres"),
    ("mood_tags", "mood_tags"),
    ("audio_features", "audio_features"),
    ("lastfm_tags", "lastfm_tags"),
    ("musicbrainz_id", "musicbrainz_id"),
    ("spotify_id", "spotify_id"),
    ("apple_music_checked", "apple_music_checked_at"),
    ("apple_music_available", "apple_music_available"),
    ("itunes_match", "itunes_persistent_id"),
    ("saturation_tier", "saturation_tier"),
]

_HISTOGRAM_FEATURES: list[str] = [
    "energy", "valence", "danceability", "acousticness",
    "speechiness", "tempo", "loudness",
]


def _track_key(obj: dict) -> str:
    """Normalized artist\\x00track identity, matching the client's trackKey and
    forgotten_favorites' local _key so scrobbles and tracks join consistently."""
    a = (obj.get("artist_normalized") or obj.get("artist") or "").lower().strip()
    t = (obj.get("track_normalized") or obj.get("track") or "").lower().strip()
    return f"{a}\x00{t}"


def _in_range(scrobbled_at: Any, start: Optional[str], end: Optional[str]) -> bool:
    """Whether a scrobble's date (YYYY-MM-DD prefix) falls within [start, end].

    ``start``/``end`` are inclusive ISO date strings (either may be ``None``).
    Lexicographic comparison is valid because the dates are zero-padded ISO.
    A scrobble with no parseable date is excluded from any bounded range.
    """
    date = (scrobbled_at or "")[:10]
    if not date:
        return False
    if start and date < start:
        return False
    if end and date > end:
        return False
    return True


def _scrobbles_in_range(
    start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    """Scrobbles whose ``scrobbled_at`` falls in [start, end]. With neither
    bound set this returns the full list unchanged (no date filter)."""
    scrobbles = get_scrobbles()
    if not start and not end:
        return scrobbles
    return [s for s in scrobbles if _in_range(s.get("scrobbled_at"), start, end)]


def _track_keys_in_range(
    start: Optional[str] = None, end: Optional[str] = None
) -> Optional[set[str]]:
    """Set of track keys with at least one scrobble in [start, end], or ``None``
    when no bound is set (meaning "no filter — keep every track")."""
    if not start and not end:
        return None
    keys: set[str] = set()
    for s in get_scrobbles():
        if _in_range(s.get("scrobbled_at"), start, end):
            keys.add(_track_key(s))
    return keys


def _tracks_in_range(
    start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    """Tracks with at least one scrobble in [start, end]; the full library when
    no bound is set. Used by metadata metrics (genres/moods/albums/…) so a date
    range scopes them to the music actually listened to in that window."""
    keys = _track_keys_in_range(start, end)
    if keys is None:
        return get_tracks()
    return [t for t in get_tracks() if _track_key(t) in keys]


def _histogram(values: list[float], n_bins: int = 10) -> list[dict]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [{"bin_start": lo, "bin_end": hi, "count": len(values)}]
    w = (hi - lo) / n_bins
    counts: list[int] = [0] * n_bins
    for v in values:
        i = min(int((v - lo) / w), n_bins - 1)
        counts[i] += 1
    return [
        {
            "bin_start": round(lo + i * w, 4),
            "bin_end": round(lo + (i + 1) * w, 4),
            "count": c,
        }
        for i, c in enumerate(counts)
    ]


def overview() -> dict[str, Any]:
    tracks = get_tracks()
    scrobbles = get_scrobbles()
    n = len(tracks)

    cov: dict[str, dict] = {}
    for label, field in _COVERAGE_FIELDS:
        count = sum(1 for t in tracks if t.get(field))
        cov[label] = {"n": count, "pct": round(count / n * 100, 1) if n else 0.0}

    scrobble_range: dict[str, Any] = {}
    years = [s["year"] for s in scrobbles if s.get("year") is not None]
    if years:
        scrobble_range = {"first": min(years), "last": max(years)}

    return {
        "track_count": n,
        "scrobble_count": len(scrobbles),
        "scrobble_range": scrobble_range,
        "coverage": cov,
    }


def genres(
    top: int = 50, start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    counts: Counter[str] = Counter()
    for t in _tracks_in_range(start, end):
        for g in t.get("genres") or []:
            counts[g] += 1
    return [{"genre": g, "count": c} for g, c in counts.most_common(top)]


def moods(start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    counts: Counter[str] = Counter()
    for t in _tracks_in_range(start, end):
        for m in t.get("mood_tags") or []:
            counts[m] += 1
    return [{"mood": m, "count": c} for m, c in counts.most_common()]


def timeline(
    by: str = "year", start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    counts: Counter[str] = Counter()
    for s in _scrobbles_in_range(start, end):
        year = s.get("year")
        if year is None:
            continue
        if by == "month":
            month = s.get("month")
            if month is None:
                continue
            key = f"{year}-{month:02d}"
        else:
            key = str(year)
        counts[key] += 1
    return [{"period": p, "plays": c} for p, c in sorted(counts.items())]


def time_of_day(
    year: Optional[int] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict[str, Any]:
    """Calendar + hour×weekday heatmaps.

    With no date filter the hour×weekday density spans the full history (more
    data = a cleaner pattern) and the calendar is filtered to ``year`` when
    given so the grid can render one large, legible year at a time. When a
    ``start``/``end`` range is supplied both grids are scoped to that window.
    ``years`` lists every year present (in the active scope) so the UI can
    build a picker.
    """
    ranged = bool(start or end)
    source = _scrobbles_in_range(start, end) if ranged else get_scrobbles()
    hw: Counter[tuple[int, int]] = Counter()
    cal: Counter[str] = Counter()
    years: set[int] = set()
    for s in source:
        if s.get("year") is not None:
            years.add(s["year"])
        hour, dow = s.get("hour"), s.get("day_of_week")
        if hour is not None and dow is not None:
            hw[(hour, dow)] += 1
        if ranged or year is None or s.get("year") == year:
            date = (s.get("scrobbled_at") or "")[:10]
            if date:
                cal[date] += 1
    return {
        "hour_weekday": [[h, dow, n] for (h, dow), n in hw.items()],
        "calendar": [[date, n] for date, n in sorted(cal.items())],
        "years": sorted(years),
    }


def albums(
    top: int = 50,
    min_tracks: int = 2,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[dict]:
    """Most-played albums, scored by total plays + how evenly listening
    spreads across the album's tracks (Spotify-Wrapped style).

    ``spread`` is normalized play-count entropy: 1.0 = plays perfectly even
    across tracks, →0 = one track dominates. Single-track albums are skipped.
    A ``start``/``end`` range scopes the ranking to albums with a track played
    in that window (play totals remain the track's lifetime ``play_count``).
    """
    by_album: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"plays": [], "total": 0, "artist": "", "album": "", "years": set()}
    )
    for t in _tracks_in_range(start, end):
        album = (t.get("album") or "").strip()
        artist = t.get("artist")
        if not album or not artist:
            continue
        key = (artist.lower(), album.lower())
        plays = int(t.get("play_count") or 0)
        rec = by_album[key]
        rec["plays"].append(plays)
        rec["total"] += plays
        rec["artist"] = artist
        rec["album"] = album
        if t.get("release_year"):
            rec["years"].add(t["release_year"])

    result: list[dict] = []
    for rec in by_album.values():
        n = len(rec["plays"])
        if n < min_tracks:
            continue
        total = rec["total"]
        if total > 0 and n > 1:
            shares = [p / total for p in rec["plays"] if p > 0]
            entropy = -sum(s * math.log(s) for s in shares) if shares else 0.0
            spread = round(entropy / math.log(n), 3)
        else:
            spread = 0.0
        result.append(
            {
                "album": rec["album"],
                "artist": rec["artist"],
                "track_count": n,
                "plays": total,
                "spread": spread,
                "year": min(rec["years"]) if rec["years"] else None,
            }
        )
    result.sort(key=lambda x: -x["plays"])
    return result[:top]


def artist_trajectory(
    top: int = 15, start: Optional[str] = None, end: Optional[str] = None
) -> dict[str, Any]:
    ranged = bool(start or end)
    scrobbles = _scrobbles_in_range(start, end)

    artist_plays: Counter[str] = Counter()
    artist_display: dict[str, str] = {}
    if ranged:
        # Rank artists by in-range scrobble counts so the top set reflects the
        # selected window rather than lifetime play counts.
        for s in scrobbles:
            artist = s.get("artist")
            if not artist:
                continue
            key = artist.lower()
            artist_plays[key] += 1
            artist_display[key] = artist
    else:
        for t in get_tracks():
            artist = t.get("artist")
            if not artist:
                continue
            key = artist.lower()
            artist_plays[key] += int(t.get("play_count") or 0)
            artist_display[key] = artist

    top_set = {a for a, _ in artist_plays.most_common(top)}

    traj: Counter[tuple[str, str]] = Counter()
    for s in scrobbles:
        artist = s.get("artist")
        year, month = s.get("year"), s.get("month")
        if not artist or year is None or month is None:
            continue
        key = artist.lower()
        if key in top_set:
            period = f"{year}-{month:02d}-01"
            name = artist_display.get(key, artist)
            traj[(period, name)] += 1

    data = [[period, count, name] for (period, name), count in traj.items()]
    data.sort(key=lambda x: x[0])
    return {"data": data}


def top_items(dim: str = "artists", n: int = 20) -> list[dict]:
    tracks = get_tracks()
    if dim == "artists":
        plays: Counter[str] = Counter()
        for t in tracks:
            artist = t.get("artist")
            if not artist:
                continue
            plays[artist] += int(t.get("play_count") or 0)
        return [{"name": a, "plays": c} for a, c in plays.most_common(n)]
    # dim == "tracks"
    by_plays = sorted(tracks, key=lambda t: -int(t.get("play_count") or 0))[:n]
    return [
        {
            "name": f"{t.get('artist') or ''} — {t.get('track') or ''}",
            "artist": t.get("artist") or "",
            "track": t.get("track") or "",
            "plays": int(t.get("play_count") or 0),
        }
        for t in by_plays
    ]


def audio_features(
    start: Optional[str] = None, end: Optional[str] = None
) -> dict[str, Any]:
    tracks = _tracks_in_range(start, end)
    af_tracks = [t for t in tracks if t.get("audio_features")]

    feat_values: dict[str, list[float]] = defaultdict(list)
    scatter: list[dict] = []

    for t in af_tracks:
        af = t["audio_features"]
        for feat in _HISTOGRAM_FEATURES:
            v = af.get(feat)
            if v is not None:
                feat_values[feat].append(float(v))
        if af.get("energy") is not None and af.get("valence") is not None:
            scatter.append(
                {
                    "energy": round(float(af["energy"]), 4),
                    "valence": round(float(af["valence"]), 4),
                    "artist": t.get("artist") or "",
                    "track": t.get("track") or "",
                    "play_count": int(t.get("play_count") or 0),
                }
            )

    return {
        "histograms": {feat: _histogram(vals) for feat, vals in feat_values.items()},
        "scatter": scatter,
    }


def saturation(
    start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    counts: Counter[str] = Counter()
    for t in _tracks_in_range(start, end):
        tier = t.get("saturation_tier")
        key = str(tier) if tier is not None else "unranked"
        counts[key] += 1
    order = ["1", "2", "3", "unranked"]
    result = [{"tier": k, "count": counts[k]} for k in order if counts[k]]
    # add any unexpected tiers not in the standard order
    for k, c in counts.items():
        if k not in order:
            result.append({"tier": k, "count": c})
    return result


_TAG_GRAPH_FIELDS: frozenset[str] = frozenset(
    ["discogs_styles", "mood_tags", "lastfm_tags"]
)


def forgotten_favorites(
    top: int = 30,
    min_peak: int = 5,
    recent_years: int = 2,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[dict]:
    """Tracks with a strong historical peak that have faded from recent listening.

    Builds per-track yearly play counts from scrobbles, scores each track by
    ``peak_plays / max(recent_plays, 1)``, and returns the most-forgotten tracks
    sorted descending by that ratio.  Only tracks whose peak pre-dates the
    recent window are included. A ``start``/``end`` range restricts the scrobble
    history considered.
    """
    scrobbles = _scrobbles_in_range(start, end)
    tracks = get_tracks()
    if not scrobbles:
        return []

    _key = _track_key

    yearly: dict[str, Counter] = defaultdict(Counter)
    scrobble_labels: dict[str, dict] = {}
    for s in scrobbles:
        yr = s.get("year")
        if yr is None:
            continue
        k = _key(s)
        yearly[k][int(yr)] += 1
        # Keep a display label from the scrobble itself as a fallback for keys
        # that have no matching row in tracks.jsonl (avoids blank artist/track).
        scrobble_labels.setdefault(
            k, {"artist": s.get("artist") or "", "track": s.get("track") or ""}
        )

    track_info: dict[str, dict] = {_key(t): t for t in tracks}

    all_years = sorted({y for c in yearly.values() for y in c})
    if not all_years:
        return []
    max_year = all_years[-1]
    recent_start = max_year - recent_years + 1

    result: list[dict] = []
    for key, by_year in yearly.items():
        peak_year = max(by_year, key=by_year.__getitem__)
        peak_plays = by_year[peak_year]
        if peak_plays < min_peak:
            continue
        if peak_year >= recent_start:
            continue  # peaked in the recent window — not forgotten

        recent_plays = sum(by_year.get(y, 0) for y in range(recent_start, max_year + 1))
        score = round(peak_plays / max(recent_plays, 1), 2)
        if score < 2.0:
            continue

        last_heard = max(y for y in by_year if by_year[y] > 0)
        info = track_info.get(key, {})
        label = scrobble_labels.get(key, {})
        artist = (info.get("artist") or label.get("artist") or "").strip()
        track = (info.get("track") or label.get("track") or "").strip()
        if not artist and not track:
            continue  # no usable label in tracks or scrobbles — skip blank row
        result.append(
            {
                "artist": artist,
                "track": track,
                "release_year": info.get("release_year"),
                "genres": (info.get("genres") or [])[:2],
                "moods": (info.get("mood_tags") or [])[:2],
                "peak_year": peak_year,
                "peak_plays": peak_plays,
                "recent_plays": recent_plays,
                "total_plays": sum(by_year.values()),
                "score": score,
                "last_heard": last_heard,
                "sparkline": [[y, c] for y, c in sorted(by_year.items())],
            }
        )

    result.sort(key=lambda x: (-x["score"], -x["peak_plays"]))
    return result[:top]


def tag_graph(
    field: str = "discogs_styles",
    min_count: int = 15,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict[str, Any]:
    """Co-occurrence graph for a tag field.

    Nodes are tags whose track count >= min_count. Edges connect any two tags
    that appear together on the same track; edge weight = number of shared tracks.
    A ``start``/``end`` range scopes the graph to tracks played in that window.
    """
    if field not in _TAG_GRAPH_FIELDS:
        field = "discogs_styles"

    tag_counts: Counter[str] = Counter()
    co_occur: Counter[tuple[str, str]] = Counter()

    for t in _tracks_in_range(start, end):
        tags = list(dict.fromkeys(v for v in (t.get(field) or []) if v))
        for tag in tags:
            tag_counts[tag] += 1
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                key = (tags[i], tags[j]) if tags[i] <= tags[j] else (tags[j], tags[i])
                co_occur[key] += 1

    kept = {tag for tag, n in tag_counts.items() if n >= min_count}
    nodes = sorted(
        [{"tag": t, "count": tag_counts[t]} for t in kept],
        key=lambda x: -x["count"],
    )
    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in co_occur.items()
        if a in kept and b in kept
    ]
    return {"nodes": nodes, "edges": edges, "field": field, "min_count": min_count}
