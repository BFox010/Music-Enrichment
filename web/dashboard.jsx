/* ============================================================
   dashboard.jsx — main app: state, aggregation, layout
   ============================================================ */
const { useState, useMemo, useEffect, useRef, useCallback } = React;

/* ---------- helpers ---------- */
/* The pure data transforms (SEASON_BY_MONTH, normalizeTrack, aggregateScrobbles,
   parseJSONL, trackKey, buildPlayWindows, attachWindows, buildDrill,
   processLibrary) and the calendar-window constants (CUR_YEAR, CUR_MONTH_KEY, …)
   now live in web/data-processing.js so the off-main-thread parser
   (web/data-worker.js) can share them via importScripts. That file loads as a
   global <script> before this bundle, so the names used below resolve to it. */

function countMap(arr) { const m = {}; for (const k of arr) m[k] = (m[k] || 0) + 1; return m; }
function topEntries(map, n) { return Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, n).map(([key, value]) => ({ key, value })); }

const ACCENT_OPTIONS = ["#f472b6", "#a78bfa", "#5b9dff", "#4ade80"];

// Timeframe windows for play-based metrics
const TIMEFRAMES = [
  ["all", "All time"],
  ["year_this", "This year"],
  ["year_last", "Last year"],
  ["month_this", "This month"],
  ["month_last", "Last month"]
];

function playInWindow(t, tf) {
  if (tf === "all" || !tf) return t.play || 0;
  if (tf === "year_this") return (t.py && t.py[CUR_YEAR]) || 0;
  if (tf === "year_last") return (t.py && t.py[LAST_YEAR]) || 0;
  if (tf === "month_this") return t.tm || 0;
  if (tf === "month_last") return t.lm || 0;
  return t.play || 0;
}

/* Aggregate a set of tracks into the shape the Overview / Genre & Moods /
   Coverage pages render (top artists/tracks, mood/genre/tag counts, coverage,
   KPIs). Pulled out of App so a page with an active date range can recompute it
   over a window-scoped track subset + play function, independent of other
   pages. `playFn(t)` returns the play count to use for `t` in this context. */
function computeAgg(rows, playFn) {
  const artistPlays = {}, moodCount = {}, genreCount = {}, tagCount = {};
  let withMood = 0, cov = { tags: 0, mbid: 0, styles: 0, af: 0, apple: 0, mood: 0, spotify: 0 };
  let totalPlays = 0;
  for (const t of rows) {
    const p = playFn(t);
    artistPlays[t.artist] = (artistPlays[t.artist] || 0) + p;
    totalPlays += p;
    for (const m of t.moods || []) moodCount[m] = (moodCount[m] || 0) + 1;
    if (t.moods && t.moods.length) withMood++;
    for (const g of t.genres) genreCount[g] = (genreCount[g] || 0) + 1;
    for (const tg of t.tags) tagCount[tg] = (tagCount[tg] || 0) + 1;
    for (const st of t.styles) tagCount[st] = (tagCount[st] || 0) + 1;
    if (t.tags.length) cov.tags++;
    if (t.mbid) cov.mbid++;
    if (t.styles.length) cov.styles++;
    if (t.af) cov.af++;
    if (t.apple) cov.apple++;
    if (t.moods) cov.mood++;
    if (t.spotify) cov.spotify++;
  }
  const n = rows.length || 1;
  const topArtists = topEntries(artistPlays, 12).filter((a) => a.value > 0).map((a) => ({ ...a, sub: rows.filter((t) => t.artist === a.key).length + " trk" }));
  const topTracks = [...rows].map((t) => ({ ...t, wp: playFn(t) })).filter((t) => t.wp > 0).sort((a, b) => b.wp - a.wp).slice(0, 12);
  const moods = topEntries(moodCount, 14);
  const genresAll = Object.entries(genreCount).sort((a, b) => b[1] - a[1]);
  const genresTop = genresAll.slice(0, 8).map(([key, value]) => ({ key, value }));
  const otherG = genresAll.slice(8).reduce((s, [, v]) => s + v, 0);
  if (otherG > 0) genresTop.push({ key: "Other", value: otherG });
  const genreTotal = genresTop.reduce((s, g) => s + g.value, 0);
  const tags = topEntries(tagCount, 24);
  const completeness = Math.round(((cov.tags + cov.mbid + cov.styles + cov.af + cov.apple + cov.mood) / (n * 6)) * 100);
  const coverageRows = [
    { label: "Mood tags", value: cov.mood },
    { label: "MusicBrainz ID", value: cov.mbid },
    { label: "Discogs styles", value: cov.styles },
    { label: "Audio features", value: cov.af },
    { label: "Last.fm tags", value: cov.tags },
    { label: "Apple Music", value: cov.apple },
    { label: "Spotify ID", value: cov.spotify }
  ];
  return {
    topArtists, topTracks, moods, genresTop, genreTotal, tags, coverageRows,
    uniqueArtists: Object.keys(artistPlays).filter((k) => artistPlays[k] > 0).length,
    totalPlays, withMood, completeness, trackCount: rows.length,
    maxArtist: topArtists[0]?.value || 1, maxTrack: topTracks[0]?.wp || 1,
    maxMood: moods[0]?.value || 1
  };
}

/* ---------- App ---------- */
function App() {
  const [data, setData] = useState(() => window.MUSIC_DATA);
  const tracks = useMemo(() => data.tracks.map((t, i) => (t.i != null ? t : normalizeTrack(t, i))), [data]);
  const scrobbles = useMemo(() => {
    const s = data.scrobbles || {};
    const toArr = (v, len) => Array.isArray(v) ? v : Array.from({ length: len }, (_, i) => (v && v[i]) || 0);
    return { ...s, byHour: toArr(s.byHour, 24), byDow: toArr(s.byDow, 7), bySeason: s.bySeason || {}, byYear: s.byYear || {}, total: s.total || 0 };
  }, [data]);

  const [page, setPage] = useState("overview");
  const [density, setDensity] = useState(() => localStorage.getItem("ml.density") || "comfortable");
  const [accent, setAccent] = useState(() => localStorage.getItem("ml.accent") || "#a78bfa");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("plays");
  const [timeframe, setTimeframe] = useState("all");
  const [shuffleSeed, setShuffleSeed] = useState(1);
  const [infoOpen, setInfoOpen] = useState(false);
  const [filters, setFilters] = useState({ genre: "", mood: "", tag: "", decade: "", artist: "", firstFrom: "", firstTo: "" });
  const [drill, setDrill] = useState(() => window.MUSIC_DATA && window.MUSIC_DATA.drill || null);
  const [drillSel, setDrillSel] = useState(null); // { type: 'season'|'hour'|'dow', value }
  // Raw scrobble rows, retained so the per-page date filters can re-aggregate
  // arbitrary windows on demand (see rangeDataFor). Sample bootstrap data has
  // none, so this stays null until a live library loads.
  const [scrobblesRaw, setScrobblesRaw] = useState(() => (window.MUSIC_DATA && window.MUSIC_DATA.scrobblesRaw) || null);
  // Independent date range per page, keyed by page id: { [page]: { from, to } }.
  // One page's range never affects another. Persisted so a chosen window
  // survives reloads.
  const [pageDates, setPageDates] = useState(() => {
    try { return JSON.parse(localStorage.getItem("ml.pageDates") || "{}") || {}; }
    catch (e) { return {}; }
  });
  const setPageDate = useCallback((pid, range) => {
    setPageDates((d) => {
      const next = { ...d, [pid]: range && (range.from || range.to) ? range : undefined };
      try { localStorage.setItem("ml.pageDates", JSON.stringify(next)); } catch (e) { /* ignore */ }
      return next;
    });
  }, []);

  // ECharts components (assigned to window by echarts-charts.jsx, which loads before this file)
  const TimelineChart    = window.TimelineChart;
  const ArtistTrajectory = window.ArtistTrajectory;
  const ListeningMap     = window.ListeningMap;
  const AudioFeaturesChart = window.AudioFeaturesChart;
  const SaturationChart  = window.SaturationChart;
  const TagConstellation = window.TagConstellation;
  const AlbumsPage               = window.AlbumsPage;
  const ForgottenFavoritesPage   = window.ForgottenFavoritesPage;
  const [dzShow, setDzShow] = useState(false);
  const [toast, setToast] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  // Bumped after a successful refresh so API-backed pages (Albums, Forgotten
  // Favorites) drop their cached response and re-fetch the updated data.
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [isLoadingLive, setIsLoadingLive] = useState(true);
  const fileRef = useRef(null);
  const dragDepth = useRef(0);

  /* Lazy-load ECharts (~1 MB): prefetch during idle after first paint so it is
     usually ready before a chart is opened, and load it immediately if a chart
     view is opened first. The charts (useEChart) pick it up via window.echarts
     once present. ensureECharts() is a singleton, so this loads it at most once. */
  useEffect(() => {
    const idle = window.requestIdleCallback || ((cb) => setTimeout(cb, 1500));
    const id = idle(() => { window.ensureECharts && window.ensureECharts(); });
    return () => (window.cancelIdleCallback || clearTimeout)(id);
  }, []);
  useEffect(() => {
    const CHART_PAGES = ["timeline", "trajectory", "map", "audio", "albums", "constellation", "coverage"];
    if (CHART_PAGES.includes(page)) window.ensureECharts && window.ensureECharts();
  }, [page]);

  /* apply accent + density to <html> */
  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
    document.documentElement.style.setProperty("--accent", accent);
    localStorage.setItem("ml.density", density);
    localStorage.setItem("ml.accent", accent);
  }, [density, accent]);

  /* expose hooks for the Tweaks panel */
  useEffect(() => {
    window.__ml = { density, setDensity, accent, setAccent };
    window.dispatchEvent(new CustomEvent("ml:state"));
  }, [density, accent]);

  const showToast = useCallback((msg) => { setToast(msg); setTimeout(() => setToast(""), 2600); }, []);

  const doRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await fetch("/api/refresh", { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        showToast("Refresh failed: " + (d.detail || r.statusText));
        return;
      }
      const newCount = d.sync?.new ?? 0;
      const pending = d.pending_exportify ?? 0;
      const msg = newCount > 0
        ? `+${newCount} new scrobble${newCount !== 1 ? "s" : ""} · ${pending} track${pending !== 1 ? "s" : ""} awaiting Exportify`
        : `Up to date · ${pending} track${pending !== 1 ? "s" : ""} awaiting Exportify`;
      showToast(msg);
      // Re-fetch live data so the UI reflects the updated tracks/scrobbles
      try {
        const [tr, sc] = await Promise.allSettled([
          fetch("tracks.min.jsonl").then((res) => res.ok ? res.text() : Promise.reject()),
          fetch("scrobbles.jsonl").then((res) => res.ok ? res.text() : Promise.reject()),
        ]);
        const trRows = tr.status === "fulfilled" ? parseJSONL(tr.value) : null;
        const scRows = sc.status === "fulfilled" ? parseJSONL(sc.value) : null;
        const { nt, ns, drill: nd, sc: rawSc } = processLibrary(trRows, scRows);
        if (nt || ns) {
          setData((d) => ({
            meta: { ...d.meta, isSample: false, trackCount: nt ? nt.length : d.meta.trackCount, scrobbleCount: ns ? ns.total : d.meta.scrobbleCount },
            tracks: nt || d.tracks, scrobbles: ns || d.scrobbles,
          }));
          if (nd) setDrill(nd);
          if (rawSc) setScrobblesRaw(rawSc);
        }
      } catch (e) { /* live fetch optional */ }
      // Invalidate cached API-backed pages so they reflect the refreshed data.
      setRefreshVersion((v) => v + 1);
    } catch (e) {
      showToast("Refresh error: " + e.message);
    } finally {
      setRefreshing(false);
    }
  }, [showToast]);

  /* ---------- file loading ---------- */
  const handleFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList);
    let rawTracks = null, rawScrob = null, names = [];
    for (const f of files) {
      const text = await f.text();
      const rows = f.name.endsWith(".json") && text.trim().startsWith("[") ? JSON.parse(text) : parseJSONL(text);
      const lname = f.name.toLowerCase();
      if (lname.includes("scrobble")) { rawScrob = rows; names.push(f.name); }
      else if (lname.includes("track") || rows[0]?.canonical_track_id || rows[0]?.track) { rawTracks = rows; names.push(f.name); }
      else if (rows[0]?.hour != null || rows[0]?.scrobbled_at) { rawScrob = rows; names.push(f.name); }
    }
    // Defer processing until all files are read so windowed plays + drill can
    // be joined from scrobbles regardless of the order the files arrive in.
    const { nt: newTracks, ns: newScrob, drill: nd, sc } = processLibrary(rawTracks, rawScrob);
    if (!newTracks && !newScrob) { showToast("Couldn't recognize that file — expected tracks.jsonl or scrobbles.jsonl"); return; }
    setData((d) => ({
      meta: { ...d.meta, isSample: false, trackCount: newTracks ? newTracks.length : d.meta.trackCount, scrobbleCount: newScrob ? newScrob.total : d.meta.scrobbleCount },
      tracks: newTracks || d.tracks,
      scrobbles: newScrob || d.scrobbles
    }));
    if (nd) setDrill(nd);
    if (sc) setScrobblesRaw(sc);
    showToast(`Loaded your data — ${names.join(", ")}`);
  }, [showToast]);

  /* try fetching real files if served alongside (e.g. in the repo) */
  useEffect(() => {
    let cancelled = false;
    let worker = null;

    // Apply a processed library to state (shared by the worker + sync paths).
    const apply = (nt, ns, nd, sc) => {
      if (cancelled || !(nt || ns)) return;
      setData((d) => ({
        meta: { ...d.meta, isSample: false, trackCount: nt ? nt.length : d.meta.trackCount, scrobbleCount: ns ? ns.total : d.meta.scrobbleCount },
        tracks: nt || d.tracks, scrobbles: ns || d.scrobbles
      }));
      if (nd) setDrill(nd);
      if (sc) setScrobblesRaw(sc);
      showToast("Loaded your live library from the repo");
    };

    (async () => {
      try {
        // Fetch the slim tracks projection (only the fields the UI renders) plus
        // scrobbles. The download is async; the heavy parse + cross-join is handed
        // to a Web Worker so the app shell stays responsive on a fresh mobile load.
        const [tr, sc] = await Promise.allSettled([
          fetch("tracks.min.jsonl").then((r) => r.ok ? r.text() : Promise.reject()),
          fetch("scrobbles.jsonl").then((r) => r.ok ? r.text() : Promise.reject())
        ]);
        if (cancelled) return;
        const tracksText = tr.status === "fulfilled" ? tr.value : null;
        const scrobblesText = sc.status === "fulfilled" ? sc.value : null;
        if (!tracksText && !scrobblesText) { if (!cancelled) setIsLoadingLive(false); return; }

        // Synchronous fallback: parse + process on the main thread.
        const runSync = () => {
          const trRows = tracksText ? parseJSONL(tracksText) : null;
          const scRows = scrobblesText ? parseJSONL(scrobblesText) : null;
          const { nt, ns, drill: nd, sc } = processLibrary(trRows, scRows);
          apply(nt, ns, nd, sc);
          if (!cancelled) setIsLoadingLive(false);
        };

        if (typeof Worker === "undefined") { runSync(); return; }
        try {
          worker = new Worker("data-worker.js");
          worker.onmessage = (e) => {
            const m = e.data || {};
            if (m.ok) { apply(m.nt, m.ns, m.drill, m.sc); }
            else { runSync(); return; }
            if (!cancelled) setIsLoadingLive(false);
            worker.terminate(); worker = null;
          };
          worker.onerror = () => { if (worker) { worker.terminate(); worker = null; } runSync(); };
          worker.postMessage({ tracksText, scrobblesText });
        } catch (e) { runSync(); }
      } catch (e) { if (!cancelled) setIsLoadingLive(false); /* sample stays */ }
    })();
    return () => { cancelled = true; if (worker) { worker.terminate(); worker = null; } };
  }, [showToast]);

  /* drag + drop */
  useEffect(() => {
    const onOver = (e) => { e.preventDefault(); };
    const onEnter = (e) => { e.preventDefault(); if ([...(e.dataTransfer?.types || [])].includes("Files")) { dragDepth.current++; setDzShow(true); } };
    const onLeave = (e) => { e.preventDefault(); dragDepth.current--; if (dragDepth.current <= 0) { dragDepth.current = 0; setDzShow(false); } };
    const onDrop = (e) => { e.preventDefault(); dragDepth.current = 0; setDzShow(false); if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files); };
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => { window.removeEventListener("dragover", onOver); window.removeEventListener("dragenter", onEnter); window.removeEventListener("dragleave", onLeave); window.removeEventListener("drop", onDrop); };
  }, [handleFiles]);

  /* ---------- filtering ---------- */
  const setFilter = (kind, val) => setFilters((f) => ({ ...f, [kind]: f[kind] === val ? "" : val }));
  const setFilterValue = (kind, val) => setFilters((f) => ({ ...f, [kind]: val }));
  const removeFilter = (kind) => setFilters((f) => ({ ...f, [kind]: "" }));
  const clearFilters = () => setFilters({ genre: "", mood: "", tag: "", decade: "", artist: "", firstFrom: "", firstTo: "" });

  /* windowed play count for the active timeframe */
  const playOf = useCallback((t) => playInWindow(t, timeframe), [timeframe]);

  /* ---------- per-page date range ---------- */
  // Earliest/latest scrobble date, used to bound the date pickers.
  const dateBounds = useMemo(() => scrobbleDateBounds(scrobblesRaw || []), [scrobblesRaw]);
  const rangeReady = !!(scrobblesRaw && scrobblesRaw.length);
  const rangeOf = useCallback((pid) => pageDates[pid] || null, [pageDates]);
  // Re-aggregate the raw scrobbles for an inclusive [from, to] window: returns
  // window-scoped hour/dow/season aggregates, a window drill, and a per-track
  // in-range play-count map. Returns null when there is no live scrobble data
  // or no range is set, so callers fall back to their full-history data.
  const rangeDataFor = useCallback((from, to) => {
    if (!rangeReady || (!from && !to)) return null;
    const slice = scrobblesInRange(scrobblesRaw, from, to);
    return {
      scrobbles: aggregateScrobbles(slice),
      drill: buildDrill(tracks, slice),
      counts: buildRangeCounts(slice),
      keys: new Set(slice.map((s) => trackKey(s))),
      total: slice.length,
    };
  }, [rangeReady, scrobblesRaw, tracks]);

  /* stable genre→color map computed once from the WHOLE library (survives filtering/selection) */
  const genreColorMap = useMemo(() => {
    const count = {};
    for (const t of tracks) for (const g of t.genres) count[g] = (count[g] || 0) + 1;
    const ordered = Object.entries(count).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([g]) => g);
    const hues = [330, 262, 192, 152, 28, 50, 210, 96, 280];
    const map = {};
    ordered.forEach((g, i) => { map[g] = `oklch(0.72 0.14 ${hues[i % hues.length]})`; });
    map["Other"] = "oklch(0.55 0.02 280)";
    return map;
  }, [tracks]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tracks.filter((t) => {
      if (filters.genre && !t.genres.includes(filters.genre)) return false;
      if (filters.mood && !(t.moods || []).includes(filters.mood)) return false;
      if (filters.tag && !t.tags.includes(filters.tag) && !t.styles.includes(filters.tag)) return false;
      if (filters.artist && t.artist !== filters.artist) return false;
      if (filters.decade) { const d = t.release_year ? Math.floor(t.release_year / 10) * 10 : null; if (String(d) !== filters.decade) return false; }
      if (filters.firstFrom || filters.firstTo) {
        // First-heard date filter (inclusive). t.first is an ISO date/datetime;
        // compare on the YYYY-MM-DD prefix so bounds work at day granularity.
        const fd = t.first ? String(t.first).slice(0, 10) : null;
        if (!fd) return false;
        if (filters.firstFrom && fd < filters.firstFrom) return false;
        if (filters.firstTo && fd > filters.firstTo) return false;
      }
      if (q && !(t.artist.toLowerCase().includes(q) || t.track.toLowerCase().includes(q))) return false;
      return true;
    });
  }, [tracks, filters, search]);

  const sorted = useMemo(() => {
    const a = [...filtered];
    if (sort === "shuffle") {
      // deterministic shuffle keyed by shuffleSeed
      let s = shuffleSeed * 2654435761 >>> 0;
      const rng = () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
      for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rng() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    }
    else if (sort.startsWith("plays")) a.sort((x, y) => sort === "plays_asc" ? playOf(x) - playOf(y) : playOf(y) - playOf(x));
    else if (sort.startsWith("artist")) { const d = sort.endsWith("_desc") ? -1 : 1; a.sort((x, y) => d * x.artist.localeCompare(y.artist)); }
    else if (sort.startsWith("track")) { const d = sort.endsWith("_desc") ? -1 : 1; a.sort((x, y) => d * x.track.localeCompare(y.track)); }
    else if (sort.startsWith("year")) { const d = sort === "year_asc" ? 1 : -1; a.sort((x, y) => d * ((x.release_year || 0) - (y.release_year || 0))); }
    else if (sort === "recent") a.sort((x, y) => (y.last || "").localeCompare(x.last || ""));
    return a;
  }, [filtered, sort, shuffleSeed, playOf]);

  const toggleSort = (key) => {
    if (key === "plays") setSort((s) => s === "plays" ? "plays_asc" : "plays");
    else if (key === "year") setSort((s) => s === "year_desc" ? "year_asc" : "year_desc");
    else if (key === "artist") setSort((s) => s === "artist" ? "artist_desc" : "artist");
    else if (key === "track") setSort((s) => s === "track" ? "track_desc" : "track");
  };

  /* ---------- aggregations (from filtered set) ---------- */
  const agg = useMemo(() => computeAgg(filtered, playOf), [filtered, playOf]);

  /* Page-scoped aggregations honouring each page's own date range. When a range
     is active the track set is `filtered` restricted to tracks played in the
     window, and play counts come from the window (rangeData.counts); otherwise
     these fall back to the shared, full-history `agg`. Computed per page so one
     page's date filter never affects another's. */
  const usePageAgg = (pid) => {
    const range = pageDates[pid];
    const rd = useMemo(
      () => rangeDataFor(range && range.from, range && range.to),
      [rangeDataFor, range && range.from, range && range.to]
    );
    const scopedAgg = useMemo(() => {
      if (!rd) return agg;
      const rows = filtered.filter((t) => rd.keys.has(trackKey(t)));
      const rp = (t) => rd.counts.get(trackKey(t)) || 0;
      return computeAgg(rows, rp);
    }, [rd, filtered]);
    return { agg: scopedAgg, rangeData: rd };
  };

  // Each of these pages carries its own independent date range.
  const { agg: overviewAgg, rangeData: overviewRange } = usePageAgg("overview");
  const { agg: genresAgg } = usePageAgg("genres");
  const { agg: coverageAgg } = usePageAgg("coverage");
  const seasonalRange = useMemo(
    () => rangeDataFor(pageDates.seasonal && pageDates.seasonal.from, pageDates.seasonal && pageDates.seasonal.to),
    [rangeDataFor, pageDates.seasonal && pageDates.seasonal.from, pageDates.seasonal && pageDates.seasonal.to]
  );
  // Overview + Seasonal data sources: window-scoped when a range is active,
  // else the full-history aggregates/drill.
  const ovScrobbles = overviewRange ? overviewRange.scrobbles : scrobbles;
  const ovDrill = overviewRange ? overviewRange.drill : drill;
  const seasonalDrill = seasonalRange ? seasonalRange.drill : drill;

  // Albums + Audio compute in-browser from `tracks`; scope that track set to the
  // page's date range (and, for Albums, replace lifetime plays with in-window
  // counts) so their client-side view honours the filter too.
  const albumsRange = useMemo(
    () => rangeDataFor(pageDates.albums && pageDates.albums.from, pageDates.albums && pageDates.albums.to),
    [rangeDataFor, pageDates.albums && pageDates.albums.from, pageDates.albums && pageDates.albums.to]
  );
  const albumsTracks = useMemo(() => {
    if (!albumsRange) return tracks;
    return tracks.filter((t) => albumsRange.keys.has(trackKey(t)))
      .map((t) => ({ ...t, play: albumsRange.counts.get(trackKey(t)) || 0 }));
  }, [albumsRange, tracks]);
  const audioRange = useMemo(
    () => rangeDataFor(pageDates.audio && pageDates.audio.from, pageDates.audio && pageDates.audio.to),
    [rangeDataFor, pageDates.audio && pageDates.audio.from, pageDates.audio && pageDates.audio.to]
  );
  const audioTracks = useMemo(() => {
    if (!audioRange) return tracks;
    return tracks.filter((t) => audioRange.keys.has(trackKey(t)));
  }, [audioRange, tracks]);

  const genreColors = genreColorMap;
  const meta = data.meta;
  const nf = (x) => x.toLocaleString();
  const ovRanged = !!overviewRange;
  const tfLabel = ovRanged ? "in range" : (timeframe === "all" ? "by scrobbles" : TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase());

  /* ---------- overview drill-down ---------- */
  const DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const pickDrill = (type, value) => setDrillSel((cur) => (cur && cur.type === type && cur.value === value) ? null : { type, value });
  const drillSlice = (ovDrill && drillSel) ? ovDrill[drillSel.type][drillSel.value] : null;
  const drillLabel = drillSel
    ? (drillSel.type === "season" ? drillSel.value.charAt(0).toUpperCase() + drillSel.value.slice(1) + " listening"
      : drillSel.type === "hour" ? fmt12full(drillSel.value) + " listening"
      : DOW_NAMES[drillSel.value] + " listening")
    : "";
  // available decades / first-heard years for the explorer slicers
  const explorerRanges = useMemo(() => {
    const decs = new Set(), yrs = new Set();
    for (const t of tracks) {
      if (t.release_year) decs.add(Math.floor(t.release_year / 10) * 10);
      if (t.first) { const y = parseInt(String(t.first).slice(0, 4), 10); if (!Number.isNaN(y)) yrs.add(y); }
    }
    return { decades: [...decs].sort((a, b) => a - b), years: [...yrs].sort((a, b) => a - b) };
  }, [tracks]);

  /* mobile-friendly feedback: toast the match count whenever filters change */
  const firstFilterRun = useRef(true);
  useEffect(() => {
    if (firstFilterRun.current) { firstFilterRun.current = false; return; }
    const active = Object.entries(filters).filter(([, v]) => v);
    if (active.length) showToast(`${filtered.length.toLocaleString()} tracks match — ${active.map(([k, v]) => v).join(" · ")}`);
  }, [filters]);

  return (
    <div className="shell">

      {/* ── Top app bar (Music Dashboard) ─────────────────────────── */}
      <header className="appbar">
        <div className="appbar-brand">
          <span className="appbar-logo">🎵</span>
          <h1>Music Dashboard</h1>
          <div className="appbar-meta">
            <span>{nf(tracks.length)} tracks · {nf(scrobbles.total)} scrobbles · {meta.scrobbleRange}</span>
            <span className={"pill-live" + (isLoadingLive ? " loading" : meta.isSample ? "" : " real")}>{isLoadingLive ? "loading library…" : meta.isSample ? "sample data" : "live data"}</span>
          </div>
        </div>
        <div className="appbar-actions">
          <div className="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search artist or track…" />
          </div>
          <button className="btn" onClick={doRefresh} disabled={refreshing} title="Sync scrobbles + re-run pipeline">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ animation: refreshing ? "spin 1s linear infinite" : "none" }}><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15"/></svg>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button className="btn" onClick={() => fileRef.current && fileRef.current.click()} title="Load your tracks.jsonl / scrobbles.jsonl">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3v12m0-12l-4 4m4-4l4 4" /><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" /></svg>
            Load data
          </button>
          <input ref={fileRef} type="file" accept=".jsonl,.json" multiple style={{ display: "none" }} onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
        </div>
      </header>

      <div className="layout">

        {/* ── Sidebar ─────────────────────────────────────────────── */}
        <nav className="sidebar">
          <div className="sidenav">

            <div className="sidenav-section">Overview</div>
            <button className={"sidenav-item" + (page === "overview" ? " active" : "")} onClick={() => setPage("overview")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
              Overview
            </button>

            <div className="sidenav-section">Library</div>
            <button className={"sidenav-item" + (page === "genres" ? " active" : "")} onClick={() => setPage("genres")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 2v10l6.6 3.8"/></svg>
              Genre &amp; Moods
            </button>
            <button className={"sidenav-item" + (page === "albums" ? " active" : "")} onClick={() => setPage("albums")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6"/></svg>
              Albums
            </button>
            <button className={"sidenav-item" + (page === "constellation" ? " active" : "")} onClick={() => setPage("constellation")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="5" cy="5" r="1.5"/><circle cx="19" cy="5" r="1.5"/><circle cx="12" cy="19" r="1.5"/><circle cx="5" cy="19" r="1.5"/><circle cx="19" cy="19" r="1.5"/><line x1="6.5" y1="5" x2="17.5" y2="5"/><line x1="5" y1="6.5" x2="5" y2="17.5"/><line x1="19" y1="6.5" x2="19" y2="17.5"/><line x1="6.5" y1="19" x2="17.5" y2="19"/><line x1="6.5" y1="6.5" x2="17.5" y2="17.5"/></svg>
              Tag Constellation
            </button>
            <button className={"sidenav-item" + (page === "audio" ? " active" : "")} onClick={() => setPage("audio")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
              Audio Features
            </button>
            <button className={"sidenav-item" + (page === "coverage" ? " active" : "")} onClick={() => setPage("coverage")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>
              Coverage
            </button>

            <div className="sidenav-section">Listening</div>
            <button className={"sidenav-item" + (page === "timeline" ? " active" : "")} onClick={() => setPage("timeline")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
              Timeline
            </button>
            <button className={"sidenav-item" + (page === "map" ? " active" : "")} onClick={() => setPage("map")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
              Listening Map
            </button>
            <button className={"sidenav-item" + (page === "trajectory" ? " active" : "")} onClick={() => setPage("trajectory")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 18c3-8 7-10 9-5s5 3 9-5"/><path d="M3 12c2-5 5-7 8-4s5 4 10-2"/><path d="M3 6c2-3 4-4 6-2s4 4 12-2"/></svg>
              Artists
            </button>
            <button className={"sidenav-item" + (page === "seasonal" ? " active" : "")} onClick={() => setPage("seasonal")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/></svg>
              Seasonal
            </button>
            <button className={"sidenav-item" + (page === "forgotten" ? " active" : "")} onClick={() => setPage("forgotten")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z"/><path d="M12 8v4l3 3"/><path d="M8 2.5l-2.5 2.5"/><path d="M16 2.5l2.5 2.5"/></svg>
              Forgotten
            </button>

            <div className="sidenav-section">Browse</div>
            <button className={"sidenav-item" + (page === "explorer" ? " active" : "")} onClick={() => setPage("explorer")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
              Tracks
            </button>

            <div className="sidenav-section">Data</div>
            <button className={"sidenav-item" + (page === "sync" ? " active" : "")} onClick={() => setPage("sync")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15"/></svg>
              Scrobble Sync
            </button>
          </div>
        </nav>

        {/* ── Main content ────────────────────────────────────────── */}
        <div className="main-content">

        {/* ── PAGE: Overview ──────────────────────────────────────── */}
        <div style={{ display: page === "overview" ? "" : "none" }}>
          <div className="slicer">
            <span className="slicer-label">Timeframe</span>
            <div className="seg" role="group" aria-label="Timeframe">
              {TIMEFRAMES.map(([id, label]) => (
                <button key={id} aria-pressed={timeframe === id} disabled={ovRanged} onClick={() => setTimeframe(id)}>{label}</button>
              ))}
            </div>
            <span className="slicer-note">{ovRanged ? <>Overridden by the <b>date range</b> below · metrics show the selected window</> : (timeframe === "all" ? "All recorded scrobbles" : <>Plays counted within <b>{TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase()}</b> · affects play-based metrics</>)}</span>
          </div>
          <DateFilter value={pageDates.overview} onChange={(r) => setPageDate("overview", r)} bounds={dateBounds} disabled={!rangeReady} label="Date range" />

          <div className="kpis">
            <Kpi label="Tracks" val={nf(overviewAgg.trackCount)} sub={ovRanged ? "played in range" : (filtered.length !== tracks.length ? <>of <b>{nf(tracks.length)}</b> total</> : "unique in library")} />
            <Kpi label={ovRanged || timeframe !== "all" ? "Plays" : "Scrobbles"} val={nf(overviewAgg.totalPlays)} sub={<>across <b>{nf(overviewAgg.uniqueArtists)}</b> artists</>} />
            <Kpi label="Artists" val={nf(overviewAgg.uniqueArtists)} sub={ovRanged || timeframe !== "all" ? "played in window" : "distinct performers"} />
            <Kpi label="Avg plays" val={(overviewAgg.totalPlays / (overviewAgg.trackCount || 1)).toFixed(1)} sub="per track" />
            <Kpi label="Enriched" val={overviewAgg.completeness + "%"} sub="field completeness" spark={overviewAgg.completeness} />
            <Kpi label="Mood-tagged" val={Math.round((overviewAgg.withMood / (overviewAgg.trackCount || 1)) * 100) + "%"} sub={<>{nf(overviewAgg.withMood)} classified</>} spark={Math.round((overviewAgg.withMood / (overviewAgg.trackCount || 1)) * 100)} />
          </div>

          <section className="block">
            <div className="grid g-32">
              <div className="card">
                <div className="card-head"><h3 className="card-title">When the music plays</h3><span className="card-meta">{ovDrill ? "hour · click to explore" : "hour of day"}</span></div>
                <HourChart data={ovScrobbles.byHour} onPick={ovDrill ? (h) => pickDrill("hour", h) : undefined} activeKey={drillSel && drillSel.type === "hour" ? drillSel.value : null} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Weekly rhythm</h3><span className="card-meta">{ovDrill ? "day · click to explore" : "day of week"}</span></div>
                <DowChart data={ovScrobbles.byDow} onPick={ovDrill ? (i) => pickDrill("dow", i) : undefined} activeKey={drillSel && drillSel.type === "dow" ? drillSel.value : null} />
              </div>
            </div>
          </section>
          {ovDrill && drillSel && (drillSel.type === "hour" || drillSel.type === "dow") && (
            <section className="block">
              <DrillPanel label={drillLabel} slice={drillSlice} onClose={() => setDrillSel(null)} />
            </section>
          )}
          <section className="block">
            <div className="card">
              <div className="card-head norule" style={{ marginBottom: 12 }}>
                <h3 className="card-title">Seasons of listening</h3>
                <span className="card-meta">{ovDrill ? "season · click to explore" : "scrobbles by season"}</span>
              </div>
              <Seasons data={ovScrobbles.bySeason} total={ovScrobbles.total} onPick={ovDrill ? (s) => pickDrill("season", s) : undefined} activeKey={drillSel && drillSel.type === "season" ? drillSel.value : null} />
            </div>
          </section>
          {ovDrill && drillSel && drillSel.type === "season" && (
            <section className="block">
              <DrillPanel label={drillLabel} slice={drillSlice} onClose={() => setDrillSel(null)} views />
            </section>
          )}
          <section className="block">
            <div className="grid g-2">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Top artists</h3><span className="card-meta">{tfLabel} · click to filter</span></div>
                <HBars items={overviewAgg.topArtists} max={overviewAgg.maxArtist} activeKey={filters.artist} onPick={(k) => setFilter("artist", k)} unit="plays" />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Most played tracks</h3><span className="card-meta">{tfLabel} · top 12</span></div>
                <TrackList items={overviewAgg.topTracks.map((t) => ({ ...t, play: t.wp }))} max={overviewAgg.maxTrack} />
              </div>
            </div>
          </section>
        </div>

        {/* ── PAGE: Timeline ──────────────────────────────────────── */}
        <div style={{ display: page === "timeline" ? "" : "none" }}>
          <DateFilter value={pageDates.timeline} onChange={(r) => setPageDate("timeline", r)} bounds={dateBounds} disabled={!rangeReady} />
          {TimelineChart && <TimelineChart active={page === "timeline"} dateRange={pageDates.timeline || null} />}
        </div>

        {/* ── PAGE: Artist Trajectory ─────────────────────────────── */}
        <div style={{ display: page === "trajectory" ? "" : "none" }}>
          <DateFilter value={pageDates.trajectory} onChange={(r) => setPageDate("trajectory", r)} bounds={dateBounds} disabled={!rangeReady} />
          {ArtistTrajectory && <ArtistTrajectory active={page === "trajectory"} dateRange={pageDates.trajectory || null} />}
        </div>

        {/* ── PAGE: Listening Map ─────────────────────────────────── */}
        <div style={{ display: page === "map" ? "" : "none" }}>
          <DateFilter value={pageDates.map} onChange={(r) => setPageDate("map", r)} bounds={dateBounds} disabled={!rangeReady} />
          {ListeningMap && <ListeningMap active={page === "map"} dateRange={pageDates.map || null} />}
        </div>

        {/* ── PAGE: Audio Features ────────────────────────────────── */}
        <div style={{ display: page === "audio" ? "" : "none" }}>
          <DateFilter value={pageDates.audio} onChange={(r) => setPageDate("audio", r)} bounds={dateBounds} disabled={!rangeReady} />
          {AudioFeaturesChart && <AudioFeaturesChart active={page === "audio"} dateRange={pageDates.audio || null} />}
          <AudioFeatureExtremes tracks={audioTracks} />
        </div>

        {/* ── PAGE: Albums ────────────────────────────────────────── */}
        <div style={{ display: page === "albums" ? "" : "none" }}>
          <DateFilter value={pageDates.albums} onChange={(r) => setPageDate("albums", r)} bounds={dateBounds} disabled={!rangeReady} />
          {AlbumsPage && <AlbumsPage active={page === "albums"} tracks={albumsTracks} />}
        </div>

        {/* ── PAGE: Seasonal Favorites ────────────────────────────── */}
        <div style={{ display: page === "seasonal" ? "" : "none" }}>
          <div className="page-intro">
            <h2 className="page-title">Seasonal favorites</h2>
            <p className="page-lede">What you reach for in each season — top genres, moods, and most-played tracks, from your scrobble history.</p>
          </div>
          <DateFilter value={pageDates.seasonal} onChange={(r) => setPageDate("seasonal", r)} bounds={dateBounds} disabled={!rangeReady} />
          <SeasonalFavorites drill={seasonalDrill} />
        </div>

        {/* ── PAGE: Forgotten Favorites ───────────────────────────── */}
        <div style={{ display: page === "forgotten" ? "" : "none" }}>
          <DateFilter value={pageDates.forgotten} onChange={(r) => setPageDate("forgotten", r)} bounds={dateBounds} disabled={!rangeReady} />
          {ForgottenFavoritesPage && <ForgottenFavoritesPage active={page === "forgotten"} refreshVersion={refreshVersion} dateRange={pageDates.forgotten || null} />}
        </div>

        {/* ── PAGE: Tag Constellation ─────────────────────────────── */}
        <div style={{ display: page === "constellation" ? "" : "none" }}>
          <DateFilter value={pageDates.constellation} onChange={(r) => setPageDate("constellation", r)} bounds={dateBounds} disabled={!rangeReady} />
          {TagConstellation && <TagConstellation active={page === "constellation"} dateRange={pageDates.constellation || null} />}
        </div>

        {/* ── PAGE: Genre & Moods ─────────────────────────────────── */}
        <div style={{ display: page === "genres" ? "" : "none" }}>
          <DateFilter value={pageDates.genres} onChange={(r) => setPageDate("genres", r)} bounds={dateBounds} disabled={!rangeReady} />
          <section className="block">
            <div className="grid g-2">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Mood spectrum</h3><span className="card-meta">tracks per mood · click to filter</span></div>
                <MoodBars items={genresAgg.moods} max={genresAgg.maxMood} activeKey={filters.mood} onPick={(k) => setFilter("mood", k)} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Genre balance</h3><span className="card-meta">share of library · click to filter</span></div>
                <GenreDonut items={genresAgg.genresTop} total={genresAgg.genreTotal} colors={genreColors} activeKey={filters.genre} onPick={(k) => k !== "Other" && setFilter("genre", k)} />
              </div>
            </div>
          </section>
        </div>

        {/* ── PAGE: Coverage ──────────────────────────────────────── */}
        <div style={{ display: page === "coverage" ? "" : "none" }}>
          <DateFilter value={pageDates.coverage} onChange={(r) => setPageDate("coverage", r)} bounds={dateBounds} disabled={!rangeReady} />
          <section className="block">
            <div className="grid g-32">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Tags &amp; styles</h3><span className="card-meta">Last.fm + Discogs · click to filter</span></div>
                <TagCloud items={coverageAgg.tags} activeKey={filters.tag} onPick={(k) => setFilter("tag", k)} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Enrichment coverage</h3><span className="card-meta">{pageDates.coverage || filtered.length !== tracks.length ? "filtered" : "library"}</span></div>
                <CoverageBars rows={coverageAgg.coverageRows} total={coverageAgg.trackCount} />
              </div>
            </div>
          </section>
          <section className="block">
            {SaturationChart && <SaturationChart active={page === "coverage"} dateRange={pageDates.coverage || null} />}
          </section>
        </div>

        {/* ── PAGE: Track Explorer ────────────────────────────────── */}
        <div style={{ display: page === "explorer" ? "" : "none" }}>
          <FilterBar filters={filters} onRemove={removeFilter} onClear={clearFilters} sort={sort} onSort={setSort} onToggle={setFilter} onRange={setFilterValue} decades={explorerRanges.decades} years={explorerRanges.years} curYear={CUR_YEAR} />
          <section className="block">
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Track explorer</h3>
                <div className="explorer-actions">
                  <button className="icon-btn" onClick={() => { setSort("shuffle"); setShuffleSeed((s) => s + 1); }} title="Shuffle tracks">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 3h5v5" /><path d="M4 20L21 3" /><path d="M21 16v5h-5" /><path d="M15 15l6 6" /><path d="M4 4l5 5" /></svg>
                    Shuffle
                  </button>
                  <button className="icon-btn" onClick={() => setInfoOpen(true)} title="What do these mean?" aria-label="Legend">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M12 16v-4" /><path d="M12 8h.01" /></svg>
                    Legend
                  </button>
                  <span className="card-meta">{nf(sorted.length)} tracks</span>
                </div>
              </div>
              <TrackTable rows={sorted} sort={sort} onSort={toggleSort} onPickArtist={(a) => setFilter("artist", a)} playOf={playOf} timeframe={timeframe} />
            </div>
          </section>
        </div>

        {/* ── PAGE: Scrobble Sync ─────────────────────────────────── */}
        <div style={{ display: page === "sync" ? "" : "none" }}>
          <ScrobbleSync />
        </div>

        {/* ── Persistent overlays ─────────────────────────────────── */}
        <div className={"dropzone" + (dzShow ? " show" : "")}>
          <div className="dz-inner">
            <div className="dz-t">Drop your library files</div>
            <div className="dz-s">tracks.jsonl &nbsp;·&nbsp; scrobbles.jsonl</div>
          </div>
        </div>
        <div className={"toast" + (toast ? " show" : "")}>{toast}</div>

        {infoOpen && (
          <div className="modal-scrim" onClick={() => setInfoOpen(false)}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <h3 className="card-title">Reading the track explorer</h3>
                <button className="modal-x" onClick={() => setInfoOpen(false)} aria-label="Close">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M18 6L6 18M6 6l12 12" /></svg>
                </button>
              </div>
              <div className="modal-body">
                <div className="legend-group">
                  <div className="legend-gtitle">Mood source <span className="lg-note">how a track's mood tags were assigned</span></div>
                  <div className="legend-item"><span className="msrc audit">audit</span><span>Human-reviewed and corrected — highest confidence.</span></div>
                  <div className="legend-item"><span className="msrc claude_batch">claude</span><span>Classified by the Claude batch labeller from tags, lyrics &amp; audio features.</span></div>
                  <div className="legend-item"><span className="msrc centroid">centroid</span><span>Inferred from audio-feature similarity to labelled tracks — medium confidence.</span></div>
                </div>
                <div className="legend-group">
                  <div className="legend-gtitle">Data coverage <span className="lg-note">each square = one enrichment source present</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot on"></span></span><span><b>Last.fm tags</b> — community genre/style tags.</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot on"></span></span><span><b>MusicBrainz ID</b> — canonical recording identifier.</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot on"></span></span><span><b>Discogs styles</b> — release styles from Discogs.</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot on"></span></span><span><b>Audio features</b> — danceability, energy, valence, tempo…</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot on"></span></span><span><b>Apple Music</b> — confirmed available on Apple Music.</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot warn"></span></span><span>An <b>amber</b> square marks a mood that's present but lower-confidence (centroid-inferred).</span></div>
                  <div className="legend-item"><span className="covdots"><span className="covdot"></span></span><span>A <b>dim</b> square means that source is missing for the track.</span></div>
                </div>
              </div>
            </div>
          </div>
        )}

        <TweaksPanel title="Tweaks">
          <TweakSection label="Accent" />
          <TweakColor label="Accent" value={accent} options={ACCENT_OPTIONS} onChange={setAccent} />
          <TweakSection label="Layout" />
          <TweakRadio label="Density" value={density} options={["comfortable", "compact"]} onChange={setDensity} />
        </TweaksPanel>
        </div>
      </div>
    </div>
  );
}

function ScrobbleSync() {
  const [status, setStatus] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/lastfm/status");
      if (r.ok) setStatus(await r.json());
    } catch (e) { /* server not running */ }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const doSync = async () => {
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const r = await fetch("/api/lastfm/sync", { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        setError(d.detail || "Sync failed");
      } else {
        setResult(d);
        await fetchStatus();
      }
    } catch (e) {
      setError("Network error: " + e.message);
    } finally {
      setSyncing(false);
    }
  };

  const notConfigured = status && !status.configured;
  const range = status
    ? (status.first_scrobbled_at ? status.first_scrobbled_at.slice(0, 4) : "—") + "–" + (status.last_scrobbled_at ? status.last_scrobbled_at.slice(0, 4) : "—")
    : null;

  return (
    <section className="block">
      <div className="card sync-card">
        <div className="card-head">
          <h3 className="card-title">Scrobble sync</h3>
          <span className="card-meta">Last.fm API</span>
        </div>
        {status ? (
          <div>
            <div className="sync-stat">{(status.scrobble_count || 0).toLocaleString()} scrobbles</div>
            <div className="sync-sub">{range}</div>
            {status.username && <div className="sync-sub" style={{ marginTop: 4 }}>Account: <b>{status.username}</b></div>}
          </div>
        ) : (
          <div className="sync-sub">Loading status…</div>
        )}
        {notConfigured && (
          <div className="sync-hint">
            Set <code>LASTFM_API_KEY</code> and <code>LASTFM_USERNAME</code> in your <code>.env</code> file to enable live sync.
          </div>
        )}
        <button className="btn-sync" onClick={doSync} disabled={syncing || !!notConfigured}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ width: 16, height: 16 }}>
            <path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15"/>
          </svg>
          {syncing ? "Syncing…" : "Sync from Last.fm"}
        </button>
        {result && (
          <div className="sync-result">
            {result.new} new scrobble{result.new !== 1 ? "s" : ""} added · {(result.total || 0).toLocaleString()} total
          </div>
        )}
        {error && <div className="sync-result sync-error">{error}</div>}
      </div>
    </section>
  );
}

function Kpi({ label, val, sub, spark }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-val">{val}</span>
      <span className="kpi-sub">{sub}</span>
      {spark != null && <span className="kpi-spark"><span style={{ width: spark + "%" }}></span></span>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
