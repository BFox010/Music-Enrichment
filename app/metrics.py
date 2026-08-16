"""Dashboard aggregations over the in-memory JSONL cache.

All public functions read from ``app.data.get_tracks()`` /
``get_scrobbles()`` so they automatically pick up test fixtures injected
via ``data.use_paths()``. Logic is ported from ``scripts/library_stats.py``
but returns dicts instead of printing.
"""

from __future__ import annotations

import math
import re
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
    # saturation_tier deliberately absent: it is a curation choice from the
    # taste profile, not an enrichment. Counting it here reported ~36%
    # "coverage" when the other ~64% simply have no tier because their artist
    # isn't in the owner's rotation list — nothing was missing.
]

_HISTOGRAM_FEATURES: list[str] = [
    "energy", "valence", "danceability", "acousticness",
    "speechiness", "tempo", "loudness",
]


_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_SEASON_RE = re.compile(r"^(\d{4})-(winter|spring|summer|fall)$")
_RANGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$")

_SEASON_BY_MONTH: dict[int, str] = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}


def _name_key(row: dict) -> tuple[str, str]:
    """Normalized artist/track pair — the join both files always share."""
    return (
        (row.get("artist_normalized") or row.get("artist") or "").lower().strip(),
        (row.get("track_normalized") or row.get("track") or "").lower().strip(),
    )


def _track_index() -> dict[tuple[str, str], dict]:
    """Track lookup keyed by every identity a scrobble might arrive with.

    Registers each track under its name pair and, when present, its
    ``canonical_track_id``. Both are indexed rather than picking one: today
    tracks.jsonl carries canonical IDs and scrobbles.jsonl does not, so keying
    solely on the canonical ID would match nothing at all.
    """
    index: dict[tuple[str, str], dict] = {}
    for t in get_tracks():
        index[_name_key(t)] = t
        cid = t.get("canonical_track_id")
        if cid:
            index[("cid", cid)] = t
        # Credit variants folded together by identity resolution. The scrobble
        # log is never rewritten, so a play recorded under "Clipse" must still
        # find the row that now displays the full credit.
        for alias in t.get("identity_aliases") or []:
            if isinstance(alias, (list, tuple)) and len(alias) == 2:
                index.setdefault((alias[0], alias[1]), t)
    return index


def _lookup(index: dict[tuple[str, str], dict], scrobble: dict) -> Optional[dict]:
    cid = scrobble.get("canonical_track_id")
    if cid:
        hit = index.get(("cid", cid))
        if hit is not None:
            return hit
    return index.get(_name_key(scrobble))


def in_window(scrobble: dict, window: Optional[str]) -> bool:
    """Does a scrobble fall inside ``window``?

    Accepted forms — ``None``/``"all"``, ``"2025"``, ``"2025-03"``,
    ``"2025-summer"``, or an explicit ``"2025-03-01:2025-06-30"`` date range.
    Anything unrecognized matches everything, so a bad query degrades to
    "all time" rather than silently returning an empty dashboard.
    """
    if not window or window == "all":
        return True
    stamp = scrobble.get("scrobbled_at") or ""

    if _RANGE_RE.match(window):
        start, _, end = window.partition(":")
        return bool(stamp) and start <= stamp[:10] <= end
    season = _SEASON_RE.match(window)
    if season:
        month = scrobble.get("month")
        return (
            str(scrobble.get("year")) == season.group(1)
            and month is not None
            and _SEASON_BY_MONTH.get(month) == season.group(2)
        )
    if _MONTH_RE.match(window):
        return stamp[:7] == window
    if _YEAR_RE.match(window):
        return str(scrobble.get("year")) == window
    # Unrecognized: match everything. An unparseable window should read as
    # "all time", never as an empty dashboard the reader might mistake for
    # "you listened to nothing".
    return True


def tag_mass(
    field: str,
    window: Optional[str] = None,
    *,
    normalized: bool = True,
) -> tuple[Counter, dict[str, int]]:
    """Play-weighted tag totals for ``field``, plus coverage for the window.

    Every play contributes exactly 1.0, split evenly across the tags on the
    track. Two distortions this removes: a track heard 200 times no longer
    counts the same as one heard once, and a track carrying 4 tags no longer
    outvotes one carrying 2. The totals therefore sum to the number of *tagged*
    plays in the window, which makes the shares directly comparable.

    Returns ``(mass, {"plays", "tagged_plays"})``. Coverage matters because it
    is uneven — moods the audio features cannot predict are left blank, so the
    denominator differs between windows and must be shown alongside the shares.
    """
    index = _track_index()
    mass: Counter = Counter()
    plays = 0
    tagged = 0
    for s in get_scrobbles():
        if not in_window(s, window):
            continue
        plays += 1
        track = _lookup(index, s)
        tags = (track.get(field) or []) if track else []
        tags = [t for t in dict.fromkeys(tags) if t]
        if not tags:
            continue
        tagged += 1
        share = 1.0 / len(tags) if normalized else 1.0
        for tag in tags:
            mass[tag] += share
    return mass, {"plays": plays, "tagged_plays": tagged}


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


def genres(top: int = 50, window: Optional[str] = None) -> dict[str, Any]:
    """Genre share of listening in ``window`` — play-weighted, not per-track."""
    mass, cov = tag_mass("genres", window)
    return {
        "window": window or "all",
        "coverage": cov,
        "items": [
            {
                "genre": g,
                "plays": round(m, 2),
                "share": round(m / cov["tagged_plays"], 4) if cov["tagged_plays"] else 0.0,
            }
            for g, m in mass.most_common(top)
        ],
    }


def moods(window: Optional[str] = None) -> dict[str, Any]:
    """Mood share of listening in ``window`` — play-weighted, not per-track.

    Previously this counted one vote per track in the library, which described
    the catalog rather than the listening: a song played once weighed the same
    as one played 200 times.
    """
    mass, cov = tag_mass("mood_tags", window)
    return {
        "window": window or "all",
        "coverage": cov,
        "items": [
            {
                "mood": m,
                "plays": round(v, 2),
                "share": round(v / cov["tagged_plays"], 4) if cov["tagged_plays"] else 0.0,
            }
            for m, v in mass.most_common()
        ],
    }


def timeline(by: str = "year") -> list[dict]:
    counts: Counter[str] = Counter()
    for s in get_scrobbles():
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


def time_of_day(year: Optional[int] = None) -> dict[str, Any]:
    """Calendar + hour×weekday heatmaps.

    The hour×weekday density always spans the full history (more data = a
    cleaner pattern). The calendar is filtered to ``year`` when given so the
    grid can render one large, legible year at a time. ``years`` lists every
    year present so the UI can build a picker.
    """
    hw: Counter[tuple[int, int]] = Counter()
    cal: Counter[str] = Counter()
    years: set[int] = set()
    for s in get_scrobbles():
        if s.get("year") is not None:
            years.add(s["year"])
        hour, dow = s.get("hour"), s.get("day_of_week")
        if hour is not None and dow is not None:
            hw[(hour, dow)] += 1
        if year is None or s.get("year") == year:
            date = (s.get("scrobbled_at") or "")[:10]
            if date:
                cal[date] += 1
    return {
        "hour_weekday": [[h, dow, n] for (h, dow), n in hw.items()],
        "calendar": [[date, n] for date, n in sorted(cal.items())],
        "years": sorted(years),
    }


def albums(top: int = 50, min_tracks: int = 2) -> list[dict]:
    """Most-played albums, scored by total plays + how evenly listening
    spreads across the album's tracks (Spotify-Wrapped style).

    ``spread`` is normalized play-count entropy: 1.0 = plays perfectly even
    across tracks, →0 = one track dominates. Single-track albums are skipped.
    """
    by_album: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"plays": [], "total": 0, "artist": "", "album": "", "years": set()}
    )
    for t in get_tracks():
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


def artist_trajectory(top: int = 15) -> dict[str, Any]:
    tracks = get_tracks()
    scrobbles = get_scrobbles()

    artist_plays: Counter[str] = Counter()
    artist_display: dict[str, str] = {}
    for t in tracks:
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


def audio_features() -> dict[str, Any]:
    tracks = get_tracks()
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


def saturation() -> list[dict]:
    counts: Counter[str] = Counter()
    for t in get_tracks():
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
    top: int = 30, min_peak: int = 5, recent_years: int = 2
) -> list[dict]:
    """Tracks with a strong historical peak that have faded from recent listening.

    Builds per-track yearly play counts from scrobbles, scores each track by
    ``peak_plays / max(recent_plays, 1)``, and returns the most-forgotten tracks
    sorted descending by that ratio.  Only tracks whose peak pre-dates the
    recent window are included.
    """
    scrobbles = get_scrobbles()
    tracks = get_tracks()
    if not scrobbles:
        return []

    def _key(obj: dict) -> str:
        a = (obj.get("artist_normalized") or obj.get("artist") or "").lower().strip()
        t = (obj.get("track_normalized") or obj.get("track") or "").lower().strip()
        return f"{a}\x00{t}"

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
    window: Optional[str] = None,
) -> dict[str, Any]:
    """Co-occurrence graph for a tag field, weighted by plays.

    Nodes are tags whose play count >= min_count. Edges connect any two tags
    heard together on the same track; edge weight = shared plays.
    """
    if field not in _TAG_GRAPH_FIELDS:
        field = "discogs_styles"

    # Weighted by plays: an edge between two tags should be thick because the
    # pairing was listened to often, not because it appears on many tracks that
    # were each heard once.
    index = _track_index()
    tag_counts: Counter[str] = Counter()
    co_occur: Counter[tuple[str, str]] = Counter()

    for s in get_scrobbles():
        if not in_window(s, window):
            continue
        track = _lookup(index, s)
        if not track:
            continue
        tags = list(dict.fromkeys(v for v in (track.get(field) or []) if v))
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
