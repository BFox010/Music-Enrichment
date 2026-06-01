"""Dashboard aggregations over the in-memory JSONL cache.

All public functions read from ``app.data.get_tracks()`` /
``get_scrobbles()`` so they automatically pick up test fixtures injected
via ``data.use_paths()``. Logic is ported from ``scripts/library_stats.py``
but returns dicts instead of printing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

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
    if scrobbles:
        years = [s["year"] for s in scrobbles]
        scrobble_range = {"first": min(years), "last": max(years)}

    return {
        "track_count": n,
        "scrobble_count": len(scrobbles),
        "scrobble_range": scrobble_range,
        "coverage": cov,
    }


def genres(top: int = 50) -> list[dict]:
    counts: Counter[str] = Counter()
    for t in get_tracks():
        for g in t.get("genres") or []:
            counts[g] += 1
    return [{"genre": g, "count": c} for g, c in counts.most_common(top)]


def moods() -> list[dict]:
    counts: Counter[str] = Counter()
    for t in get_tracks():
        for m in t.get("mood_tags") or []:
            counts[m] += 1
    return [{"mood": m, "count": c} for m, c in counts.most_common()]


def timeline(by: str = "year") -> list[dict]:
    counts: Counter[str] = Counter()
    for s in get_scrobbles():
        if by == "month":
            key = f"{s['year']}-{s['month']:02d}"
        else:
            key = str(s["year"])
        counts[key] += 1
    return [{"period": p, "plays": c} for p, c in sorted(counts.items())]


def time_of_day() -> dict[str, Any]:
    hw: Counter[tuple[int, int]] = Counter()
    cal: Counter[str] = Counter()
    for s in get_scrobbles():
        hw[(s["hour"], s["day_of_week"])] += 1
        date = (s.get("scrobbled_at") or "")[:10]
        if date:
            cal[date] += 1
    return {
        "hour_weekday": [[h, dow, n] for (h, dow), n in hw.items()],
        "calendar": [[date, n] for date, n in sorted(cal.items())],
    }


def artist_trajectory(top: int = 15) -> dict[str, Any]:
    tracks = get_tracks()
    scrobbles = get_scrobbles()

    artist_plays: Counter[str] = Counter()
    artist_display: dict[str, str] = {}
    for t in tracks:
        key = t["artist"].lower()
        artist_plays[key] += int(t.get("play_count") or 0)
        artist_display[key] = t["artist"]

    top_set = {a for a, _ in artist_plays.most_common(top)}

    traj: Counter[tuple[str, str]] = Counter()
    for s in scrobbles:
        key = s["artist"].lower()
        if key in top_set:
            period = f"{s['year']}-{s['month']:02d}-01"
            name = artist_display.get(key, s["artist"])
            traj[(period, name)] += 1

    data = [[period, count, name] for (period, name), count in traj.items()]
    data.sort(key=lambda x: x[0])
    return {"data": data}


def top_items(dim: str = "artists", n: int = 20) -> list[dict]:
    tracks = get_tracks()
    if dim == "artists":
        plays: Counter[str] = Counter()
        for t in tracks:
            plays[t["artist"]] += int(t.get("play_count") or 0)
        return [{"name": a, "plays": c} for a, c in plays.most_common(n)]
    # dim == "tracks"
    by_plays = sorted(tracks, key=lambda t: -int(t.get("play_count") or 0))[:n]
    return [
        {
            "name": f"{t['artist']} — {t['track']}",
            "artist": t["artist"],
            "track": t["track"],
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
                    "artist": t["artist"],
                    "track": t["track"],
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


def tag_graph(field: str = "discogs_styles", min_count: int = 15) -> dict[str, Any]:
    """Co-occurrence graph for a tag field.

    Nodes are tags whose track count >= min_count. Edges connect any two tags
    that appear together on the same track; edge weight = number of shared tracks.
    """
    if field not in _TAG_GRAPH_FIELDS:
        field = "discogs_styles"

    tag_counts: Counter[str] = Counter()
    co_occur: Counter[tuple[str, str]] = Counter()

    for t in get_tracks():
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
