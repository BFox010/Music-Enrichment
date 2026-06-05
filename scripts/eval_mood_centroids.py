"""Mood-centroid reliability report.

Scores the medium-confidence ``centroid`` mood source against the trusted human
sources (``audit`` / ``claude_batch`` / ``manual``) already baked into
tracks.jsonl, and flags moods whose centroid output looks unreliable. This is
the reproducible replacement for the old manual Excel spot-check — re-run it any
time the data changes (e.g. after a scrobble sync):

    python scripts/eval_mood_centroids.py
    python scripts/eval_mood_centroids.py --json        # machine-readable
    python scripts/eval_mood_centroids.py --min-genre-n 80

It needs NO audit CSV — every signal comes from ``mood_source`` + ``genres`` +
``audio_features`` in tracks.jsonl, so it runs anywhere the data file does.

Two independent flag triggers:
  A (suppress candidate): centroid applies a mood >RATIO_THRESHOLD x more often
     than humans across >=2 major genres → feature-inadequate, suppress.
  B (gate candidate): the mood is feature-correlated, but the centroid's tempo
     distribution diverges from the human one in the wrong direction (mean off
     by >TEMPO_SEPARATION_BPM, or a large >105 BPM tail) → threshold-loose, gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import MOOD_CATEGORIES, TRACKS_PATH  # noqa: E402

# mood_source → coarse class. Anything not listed is ignored (e.g. null).
SOURCE_CLASS: dict[str, str] = {
    "audit": "human",
    "claude_batch": "human",
    "manual": "human",
    "centroid": "centroid",
}

# Flag-trigger defaults.
RATIO_THRESHOLD: float = 3.0          # centroid/human application-rate ratio (trigger A)
MIN_GENRES_FLAGGED: int = 2           # how many genres must trip the ratio (trigger A)
TEMPO_SEPARATION_BPM: float = 8.0     # centroid-vs-human tempo mean gap (trigger B)
TEMPO_GT_TAIL: float = 105.0          # tempo "too fast for slow/moody" cutoff
TAIL_FRACTION_FLAG: float = 0.20      # centroid >105 BPM fraction that trips trigger B

ALL_BUCKET = "__ALL__"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def classify_source(mood_source: str | None) -> str | None:
    """Map a track's mood_source to "human" | "centroid" | None.

    Shared definition of "trusted human label" vs "centroid guess" used by the
    whole report (and conceptually by the cleanup migration).
    """
    if not mood_source:
        return None
    return SOURCE_CLASS.get(mood_source)


def _print_header(title: str) -> None:
    print()
    print("=" * 74)
    print(f" {title}")
    print("=" * 74)


# ── rate analysis ────────────────────────────────────────────────────────


def application_rates(
    tracks: list[dict],
    moods: tuple[str, ...] = MOOD_CATEGORIES,
    genres: list[str] | None = None,
) -> dict[tuple[str, str], dict]:
    """For each (genre, mood), how often human vs centroid tracks carry the mood.

    Genre ``__ALL__`` aggregates every track regardless of genre. A track counts
    toward every genre in its (multi-valued) ``genres`` list.
    """
    # Pre-bucket tracks by source class once.
    by_class: dict[str, list[dict]] = {"human": [], "centroid": []}
    for t in tracks:
        cls = classify_source(t.get("mood_source"))
        if cls in by_class:
            by_class[cls].append(t)

    genre_set = set(genres) if genres else None
    out: dict[tuple[str, str], dict] = {}

    def _rate(pool: list[dict], mood: str, genre: str | None) -> tuple[int, int]:
        n = hits = 0
        for t in pool:
            if genre is not None and genre not in (t.get("genres") or []):
                continue
            n += 1
            if mood in (t.get("mood_tags") or []):
                hits += 1
        return n, hits

    buckets: list[str | None] = [None]  # None == __ALL__
    if genre_set:
        buckets += sorted(genre_set)

    for genre in buckets:
        gkey = ALL_BUCKET if genre is None else genre
        for mood in moods:
            hn, hh = _rate(by_class["human"], mood, genre)
            cn, ch = _rate(by_class["centroid"], mood, genre)
            hrate = hh / hn if hn else None
            crate = ch / cn if cn else None
            ratio = (crate / hrate) if (hrate not in (None, 0.0) and crate is not None) else None
            out[(gkey, mood)] = {
                "human_n": hn, "human_hits": hh, "human_rate": hrate,
                "centroid_n": cn, "centroid_hits": ch, "centroid_rate": crate,
                "ratio": ratio,
            }
    return out


def feature_distributions(
    tracks: list[dict],
    moods: tuple[str, ...] = MOOD_CATEGORIES,
    feature_keys: tuple[str, ...] = ("tempo", "energy", "valence", "loudness", "acousticness"),
) -> dict[tuple[str, str], dict[str, dict]]:
    """For each (mood, source_class), summary stats of each audio feature."""
    pools: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in tracks:
        cls = classify_source(t.get("mood_source"))
        if cls is None:
            continue
        af = t.get("audio_features") or {}
        if not af:
            continue
        for mood in (t.get("mood_tags") or []):
            if mood not in moods:
                continue
            for k in feature_keys:
                v = af.get(k)
                if v is not None:
                    pools[(mood, cls)][k].append(float(v))

    def _summ(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0}
        s = sorted(vals)
        n = len(s)
        mean = sum(s) / n
        d = {
            "n": n,
            "mean": mean,
            "median": s[n // 2],
            "p25": s[int(n * 0.25)],
            "p75": s[min(int(n * 0.75), n - 1)],
        }
        return d

    out: dict[tuple[str, str], dict[str, dict]] = {}
    for key, feats in pools.items():
        summ = {k: _summ(v) for k, v in feats.items()}
        tempo_vals = feats.get("tempo", [])
        if tempo_vals:
            summ["_pct_gt_105"] = sum(1 for v in tempo_vals if v > TEMPO_GT_TAIL) / len(tempo_vals)
        out[key] = summ
    return out


# ── flagging ─────────────────────────────────────────────────────────────


def flag_moods(
    rates: dict[tuple[str, str], dict],
    dists: dict[tuple[str, str], dict[str, dict]],
    genres: list[str],
    *,
    ratio_threshold: float = RATIO_THRESHOLD,
    min_genres_flagged: int = MIN_GENRES_FLAGGED,
    min_genre_n: int = 50,
    tempo_separation_bpm: float = TEMPO_SEPARATION_BPM,
    tail_fraction_flag: float = TAIL_FRACTION_FLAG,
) -> dict[str, dict]:
    """Apply triggers A (suppress) and B (gate) to each mood."""
    out: dict[str, dict] = {}
    for mood in MOOD_CATEGORIES:
        reasons: list[str] = []
        treatment = "ok"

        # Trigger A — volume divergence across major genres.
        over_genres = []
        for g in genres:
            r = rates.get((g, mood))
            if not r:
                continue
            if (r["human_n"] >= min_genre_n and r["ratio"] is not None
                    and r["ratio"] > ratio_threshold):
                over_genres.append((g, r["ratio"]))
        if len(over_genres) >= min_genres_flagged:
            treatment = "suppress"
            detail = ", ".join(f"{g} {ratio:.1f}x" for g, ratio in over_genres)
            reasons.append(f"trigger A: centroid over-applies vs human in [{detail}]")

        # Trigger B — tempo drift (only meaningful if not already suppressed).
        cdist = dists.get((mood, "centroid"), {})
        hdist = dists.get((mood, "human"), {})
        ctempo = cdist.get("tempo", {})
        htempo = hdist.get("tempo", {})
        if treatment != "suppress" and ctempo.get("n") and htempo.get("n"):
            gap = ctempo["mean"] - htempo["mean"]
            tail = cdist.get("_pct_gt_105", 0.0)
            if abs(gap) > tempo_separation_bpm:
                treatment = "gate:tempo"
                reasons.append(
                    f"trigger B: centroid tempo mean {ctempo['mean']:.0f} vs human "
                    f"{htempo['mean']:.0f} BPM (gap {gap:+.0f})"
                )
            elif tail > tail_fraction_flag:
                treatment = "gate:tempo"
                reasons.append(
                    f"trigger B: {tail:.0%} of centroid tracks >{TEMPO_GT_TAIL:.0f} BPM"
                )

        out[mood] = {
            "flag": treatment != "ok",
            "suggested_treatment": treatment,
            "reasons": reasons,
        }
    return out


# ── output ───────────────────────────────────────────────────────────────


def _fmt_rate(r: float | None) -> str:
    return f"{r*100:5.1f}%" if r is not None else "   -  "


def _fmt_ratio(r: float | None) -> str:
    return f"{r:5.1f}x" if r is not None else "   -  "


def _pick_genres(tracks: list[dict], min_genre_n: int) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for t in tracks:
        if classify_source(t.get("mood_source")) is None:
            continue
        for g in set(t.get("genres") or []):
            counts[g] += 1
    return [g for g, n in sorted(counts.items(), key=lambda x: -x[1]) if n >= min_genre_n]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    ap.add_argument("--min-genre-n", type=int, default=50,
                    help="min human-tagged tracks for a genre to enter the per-genre table")
    ap.add_argument("--genres", nargs="*", default=None,
                    help="explicit genre buckets (default: auto by volume)")
    ap.add_argument("--ratio-threshold", type=float, default=RATIO_THRESHOLD)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    tracks = _load_jsonl(args.tracks)
    if not tracks:
        print(f"No tracks found at {args.tracks}", file=sys.stderr)
        return 1

    genres = args.genres or _pick_genres(tracks, args.min_genre_n)
    rates = application_rates(tracks, genres=genres)
    dists = feature_distributions(tracks)
    flags = flag_moods(rates, dists, genres,
                       ratio_threshold=args.ratio_threshold,
                       min_genre_n=args.min_genre_n)

    if args.json:
        payload = {
            "rates": {f"{g}|{m}": v for (g, m), v in rates.items()},
            "distributions": {f"{m}|{c}": v for (m, c), v in dists.items()},
            "flags": flags,
            "genres": genres,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Source-class census.
    census: dict[str, int] = defaultdict(int)
    for t in tracks:
        census[t.get("mood_source") or "(none)"] += 1
    _print_header(f"MOOD CENTROID RELIABILITY  /  {len(tracks)} tracks")
    print("  mood_source census: " + ", ".join(
        f"{k}={census[k]}" for k in sorted(census, key=lambda x: -census[x])))

    # 1. Global rates.
    _print_header("GLOBAL APPLICATION RATE  (human vs centroid)")
    print(f"  {'mood':12s}  {'human':>8s} {'(n)':>7s}   {'centroid':>8s} {'(n)':>7s}   {'ratio':>6s}")
    for mood in MOOD_CATEGORIES:
        r = rates[(ALL_BUCKET, mood)]
        print(f"  {mood:12s}  {_fmt_rate(r['human_rate'])} {r['human_n']:>7d}   "
              f"{_fmt_rate(r['centroid_rate'])} {r['centroid_n']:>7d}   {_fmt_ratio(r['ratio'])}")

    # 2. Per-genre table, for flagged-or-interesting moods only (keep it readable).
    focus = [m for m in MOOD_CATEGORIES if flags[m]["flag"]] or list(MOOD_CATEGORIES)
    _print_header("PER-GENRE RATE  (centroid vs human)  for flagged moods")
    print(f"  genres: {', '.join(genres)}")
    for mood in focus:
        print(f"\n  {mood}:")
        print(f"    {'genre':22s}  {'centroid':>8s}  {'human':>8s}  {'ratio':>6s}")
        for g in genres:
            r = rates.get((g, mood))
            if not r or (r["centroid_n"] == 0 and r["human_n"] == 0):
                continue
            print(f"    {g:22s}  {_fmt_rate(r['centroid_rate'])}  "
                  f"{_fmt_rate(r['human_rate'])}  {_fmt_ratio(r['ratio'])}")

    # 3. Tempo distributions for flagged moods.
    _print_header("TEMPO DISTRIBUTION  (centroid vs human)  for flagged moods")
    print(f"  {'mood':12s} {'src':8s}  {'n':>5s}  {'mean':>6s} {'med':>5s} {'p25':>5s} {'p75':>5s}  {'>105':>6s}")
    for mood in focus:
        for cls in ("centroid", "human"):
            d = dists.get((mood, cls), {})
            tempo = d.get("tempo", {})
            if not tempo.get("n"):
                continue
            tail = d.get("_pct_gt_105")
            tail_s = f"{tail*100:5.1f}%" if tail is not None and cls == "centroid" else "   -  "
            print(f"  {mood:12s} {cls:8s}  {tempo['n']:>5d}  {tempo['mean']:>6.1f} "
                  f"{tempo['median']:>5.0f} {tempo['p25']:>5.0f} {tempo['p75']:>5.0f}  {tail_s}")

    # 4. Flagged summary.
    _print_header("FLAGGED MOODS  →  suggested treatment")
    any_flag = False
    for mood in MOOD_CATEGORIES:
        f = flags[mood]
        if not f["flag"]:
            continue
        any_flag = True
        print(f"  [{f['suggested_treatment']:11s}] {mood}")
        for reason in f["reasons"]:
            print(f"                {reason}")
    if not any_flag:
        print("  (none — all moods within thresholds)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
