/* Main app: state, aggregation, layout. */
const { useState, useMemo, useEffect, useRef, useCallback } = React;

/* ── helpers ── */
/* The pure transforms and the timeframe anchor live in web/data-processing.js so
   data-worker.js can importScripts the same code. It loads as a global <script>
   before this bundle, so unqualified names below resolve to it. */

function countMap(arr) { const m = {}; for (const k of arr) m[k] = (m[k] || 0) + 1; return m; }
function topEntries(map, n) { return Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, n).map(([key, value]) => ({ key, value })); }

const ACCENT_OPTIONS = ["#f472b6", "#a78bfa", "#5b9dff", "#4ade80"];

// Timeframe windows for play-based metrics
/* Mood sources that represent a human judgement rather than an inference. */
const OWNER_MOOD_SOURCES = new Set(["audit", "claude_batch", "manual"]);

const TIMEFRAMES = [
  ["all", "All time"],
  ["year_this", "This year"],
  ["year_last", "Last year"],
  ["season_this", "This season"],
  ["season_last", "Last season"],
  ["month_this", "This month"],
  ["month_last", "Last month"],
  ["week_this", "This week"],
  ["week_last", "Last week"]
];

function playInWindow(t, tf) {
  if (tf === "all" || !tf) return t.play || 0;
  if (tf === "year_this") return (t.py && t.py[ANCHOR.curYear]) || 0;
  if (tf === "year_last") return (t.py && t.py[ANCHOR.lastYear]) || 0;
  if (tf === "season_this") return t.ts || 0;
  if (tf === "season_last") return t.ls || 0;
  if (tf === "month_this") return t.tm || 0;
  if (tf === "month_last") return t.lm || 0;
  if (tf === "week_this") return t.tw || 0;
  if (tf === "week_last") return t.lw || 0;
  return t.play || 0;
}

/* The active highlight is its own element so it can carry a view-transition-name:
   the browser then tweens that box between items and the marker slides down the
   nav instead of blinking off one row and on to another. */
function NavItem({ id, page, onGo, children }) {
  const active = page === id;
  return (
    <button className={"sidenav-item" + (active ? " active" : "")} onClick={() => onGo(id)}>
      {active && <span className="nav-marker" aria-hidden="true" />}
      {children}
    </button>
  );
}

/* ── App ── */
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
  // Both motion features are opt-out and remember the choice; the modules own
  // the localStorage key, this is just the mirrored state the Tweaks UI binds to.
  const [ambient, setAmbient] = useState(() => localStorage.getItem("ml.ambient") !== "off");
  const [pointerFx, setPointerFx] = useState(() => localStorage.getItem("ml.pointerfx") !== "off");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("plays");
  const [timeframe, setTimeframe] = useState("all");
  const [shuffleSeed, setShuffleSeed] = useState(1);
  const [infoOpen, setInfoOpen] = useState(false);
  const [filters, setFilters] = useState({ genre: "", mood: "", tag: "", decade: "", artist: "", firstFrom: "", firstTo: "" });
  const [drill, setDrill] = useState(() => window.MUSIC_DATA && window.MUSIC_DATA.drill || null);
  const [cube, setCube] = useState(() => window.MUSIC_DATA && window.MUSIC_DATA.cube || null);
  // Overview cross-filter. Each dimension is independent, so "Tuesdays at 9am
  // in summer" is expressible; null means that dimension is unconstrained.
  const [sel, setSel] = useState({ hour: null, dow: null, season: null });

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

  /* ECharts (~1 MB) is off the first-paint path: prefetch on idle, and load
     immediately if a chart page opens first. ensureECharts() is a singleton. */
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

  useEffect(() => { if (window.MLAmbient) window.MLAmbient.setEnabled(ambient); }, [ambient]);
  useEffect(() => { if (window.MOTION) window.MOTION.setPointerFx(pointerFx); }, [pointerFx]);

  /* expose hooks for the Tweaks panel */
  useEffect(() => {
    window.__ml = { density, setDensity, accent, setAccent };
    window.dispatchEvent(new CustomEvent("ml:state"));
  }, [density, accent]);

  const showToast = useCallback((msg) => { setToast(msg); setTimeout(() => setToast(""), 2600); }, []);

  /* Page switches go through a View Transition: the outgoing page lifts and
     fades while the incoming one settles up into place, and the sidebar's
     active marker slides between entries. flushSync is required — the browser
     snapshots the DOM when the callback returns, so the state update has to
     have committed by then rather than being batched for later. MOTION falls
     back to a plain call where the API is missing or motion is unwanted. */
  const pageRef = useRef(page);
  pageRef.current = page;
  const goPage = useCallback((next) => {
    if (pageRef.current === next) return;
    const apply = () => ReactDOM.flushSync(() => setPage(next));
    if (window.MOTION) window.MOTION.viewTransition(apply); else apply();
  }, []);

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
      try {
        const [tr, sc] = await Promise.allSettled([
          fetch("tracks.min.jsonl").then((res) => res.ok ? res.text() : Promise.reject()),
          fetch("scrobbles.jsonl").then((res) => res.ok ? res.text() : Promise.reject()),
        ]);
        const trRows = tr.status === "fulfilled" ? parseJSONL(tr.value) : null;
        const scRows = sc.status === "fulfilled" ? parseJSONL(sc.value) : null;
        const { nt, ns, drill: nd, cube: nc } = processLibrary(trRows, scRows);
        if (nt || ns) {
          setData((d) => ({
            meta: { ...d.meta, isSample: false, trackCount: nt ? nt.length : d.meta.trackCount, scrobbleCount: ns ? ns.total : d.meta.scrobbleCount },
            tracks: nt || d.tracks, scrobbles: ns || d.scrobbles,
          }));
          if (nd) setDrill(nd);
          if (nc) setCube(nc);
        }
      } catch (e) { /* live fetch optional */ }
      setRefreshVersion((v) => v + 1);
    } catch (e) {
      showToast("Refresh error: " + e.message);
    } finally {
      setRefreshing(false);
    }
  }, [showToast]);

  /* ── file loading ── */
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
    // Process only after every file is read, so the scrobble cross-join works
    // regardless of arrival order.
    const { nt: newTracks, ns: newScrob, drill: nd, cube: nc } = processLibrary(rawTracks, rawScrob);
    if (!newTracks && !newScrob) { showToast("Couldn't recognize that file — expected tracks.jsonl or scrobbles.jsonl"); return; }
    setData((d) => ({
      meta: { ...d.meta, isSample: false, trackCount: newTracks ? newTracks.length : d.meta.trackCount, scrobbleCount: newScrob ? newScrob.total : d.meta.scrobbleCount },
      tracks: newTracks || d.tracks,
      scrobbles: newScrob || d.scrobbles
    }));
    if (nd) setDrill(nd);
    if (nc) setCube(nc);
    showToast(`Loaded your data — ${names.join(", ")}`);
  }, [showToast]);

  /* Live library, when served alongside (i.e. from the repo). */
  useEffect(() => {
    let cancelled = false;
    let worker = null;

    const apply = (nt, ns, nd, nc) => {
      if (cancelled || !(nt || ns)) return;
      setData((d) => ({
        meta: { ...d.meta, isSample: false, trackCount: nt ? nt.length : d.meta.trackCount, scrobbleCount: ns ? ns.total : d.meta.scrobbleCount },
        tracks: nt || d.tracks, scrobbles: ns || d.scrobbles
      }));
      if (nd) setDrill(nd);
      if (nc) setCube(nc);
      showToast("Loaded your live library from the repo");
    };

    (async () => {
      try {
        // Slim projection + scrobbles. Parse and cross-join go to a Web Worker so
        // the shell stays responsive on a fresh mobile load.
        const [tr, sc] = await Promise.allSettled([
          fetch("tracks.min.jsonl").then((r) => r.ok ? r.text() : Promise.reject()),
          fetch("scrobbles.jsonl").then((r) => r.ok ? r.text() : Promise.reject())
        ]);
        if (cancelled) return;
        const tracksText = tr.status === "fulfilled" ? tr.value : null;
        const scrobblesText = sc.status === "fulfilled" ? sc.value : null;
        if (!tracksText && !scrobblesText) { if (!cancelled) setIsLoadingLive(false); return; }

        // Fallback when Worker is unavailable or fails.
        const runSync = () => {
          const trRows = tracksText ? parseJSONL(tracksText) : null;
          const scRows = scrobblesText ? parseJSONL(scrobblesText) : null;
          const { nt, ns, drill: nd, cube: nc } = processLibrary(trRows, scRows);
          apply(nt, ns, nd, nc);
          if (!cancelled) setIsLoadingLive(false);
        };

        if (typeof Worker === "undefined") { runSync(); return; }
        try {
          worker = new Worker("data-worker.js");
          worker.onmessage = (e) => {
            const m = e.data || {};
            if (m.ok) { apply(m.nt, m.ns, m.drill, m.cube); }
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

  /* ── filtering ── */
  const setFilter = (kind, val) => setFilters((f) => ({ ...f, [kind]: f[kind] === val ? "" : val }));
  const setFilterValue = (kind, val) => setFilters((f) => ({ ...f, [kind]: val }));
  const removeFilter = (kind) => setFilters((f) => ({ ...f, [kind]: "" }));
  const clearFilters = () => setFilters({ genre: "", mood: "", tag: "", decade: "", artist: "", firstFrom: "", firstTo: "" });

  /* windowed play count for the active timeframe */
  const playOf = useCallback((t) => playInWindow(t, timeframe), [timeframe]);

  /* Computed from the WHOLE library, so colors survive filtering and selection. */
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
        const fy = t.first ? parseInt(String(t.first).slice(0, 4), 10) : null;
        if (fy == null || Number.isNaN(fy)) return false;
        if (filters.firstFrom && fy < parseInt(filters.firstFrom, 10)) return false;
        if (filters.firstTo && fy > parseInt(filters.firstTo, 10)) return false;
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

  /* ── aggregations (from filtered set) ── */
  const agg = useMemo(() => {
    const artistPlays = {}, moodCount = {}, moodOwned = {}, genreCount = {}, tagCount = {};
    let withMood = 0, cov = { tags: 0, mbid: 0, styles: 0, af: 0, apple: 0, mood: 0, spotify: 0 };
    let totalPlays = 0;
    /* Tag charts are play-weighted with conserved mass: one play contributes
       exactly 1.0, split across that track's tags. Counting one vote per track
       described the library rather than the listening, and let a 4-tag track
       outvote a 2-tag one purely because of which pipeline labeled it. */
    const addMass = (bucket, keys, plays) => {
      if (!keys || !keys.length || plays <= 0) return;
      const share = plays / keys.length;
      for (const k of keys) bucket[k] = (bucket[k] || 0) + share;
    };
    let taggedPlays = 0;
    for (const t of filtered) {
      const p = playOf(t);
      artistPlays[t.artist] = (artistPlays[t.artist] || 0) + p;
      totalPlays += p;
      addMass(moodCount, t.moods, p);
      // Track the hand-labelled portion separately so the bars can show how
      // much of each mood is a judgement versus an inference.
      if (OWNER_MOOD_SOURCES.has(t.mood_source)) addMass(moodOwned, t.moods, p);
      if (t.moods && t.moods.length && p > 0) taggedPlays += p;
      if (t.moods && t.moods.length) withMood++;
      addMass(genreCount, t.genres, p);
      addMass(tagCount, [...(t.tags || []), ...(t.styles || [])], p);
      if (t.tags.length) cov.tags++;
      if (t.mbid) cov.mbid++;
      if (t.styles.length) cov.styles++;
      if (t.af) cov.af++;
      if (t.apple) cov.apple++;
      // Length-checked: mood_tags arrives as [] for tracks the classifier
      // declined to guess on, and an empty array is truthy.
      if (t.moods && t.moods.length) cov.mood++;
      if (t.spotify) cov.spotify++;
    }
    const n = filtered.length || 1;
    const topArtists = topEntries(artistPlays, 12).filter((a) => a.value > 0).map((a) => ({ ...a, sub: filtered.filter((t) => t.artist === a.key).length + " trk" }));
    const topTracks = [...filtered].map((t) => ({ ...t, wp: playOf(t) })).filter((t) => t.wp > 0).sort((a, b) => b.wp - a.wp).slice(0, 12);
    const moods = topEntries(moodCount, 14).map((m) => ({ ...m, owned: moodOwned[m.key] || 0 }));
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
      totalPlays, withMood, completeness, taggedPlays,
      // Share of plays in this window that carry any mood. Worth showing
      // because it is uneven across windows — the classifier leaves a mood
      // blank when the audio features cannot predict it, so the denominator
      // behind the mood chart moves as you change timeframe.
      moodCoverage: totalPlays > 0 ? taggedPlays / totalPlays : 0,
      maxArtist: topArtists[0]?.value || 1, maxTrack: topTracks[0]?.wp || 1,
      maxMood: moods[0]?.value || 1
    };
  }, [filtered, playOf]);

  const genreColors = genreColorMap;
  const meta = data.meta;
  const nf = (x) => x.toLocaleString();
  const tfLabel = timeframe === "all" ? "by scrobbles" : TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase();

  /* ── overview cross-filter ──
     Every overview chart reads one re-aggregation of the scrobble cube, so the
     timeframe and chart selections compose: the hour chart shows the selected
     days, the day chart the selected hours, the drill-down their intersection.
     One linear pass over typed arrays, memoized on its inputs.

     No cube (bundled sample, or a failed load) falls back to the pre-baked
     aggregates, which ignore the timeframe — stale, but not an empty dashboard. */
  const DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const view = useMemo(() => {
    if (!cube) return { byHour: scrobbles.byHour, byDow: scrobbles.byDow, bySeason: scrobbles.bySeason, total: scrobbles.total, slice: null };
    return aggregateCube(cube, timeframe, sel, tracks);
  }, [cube, timeframe, sel, tracks, scrobbles]);

  const pickDim = (dim, value) => setSel((cur) => ({ ...cur, [dim]: cur[dim] === value ? null : value }));
  const clearSel = () => setSel({ hour: null, dow: null, season: null });
  const selActive = sel.hour != null || sel.dow != null || sel.season != null;
  // Read as one phrase: "Tuesday · 9 AM · Summer listening".
  const drillLabel = [
    sel.dow != null ? DOW_NAMES[sel.dow] : null,
    sel.hour != null ? fmt12full(sel.hour) : null,
    sel.season ? sel.season.charAt(0).toUpperCase() + sel.season.slice(1) : null,
  ].filter(Boolean).join(" · ") + " listening";
  // available decades / first-heard years for the explorer slicers
  const explorerRanges = useMemo(() => {
    const decs = new Set(), yrs = new Set();
    for (const t of tracks) {
      if (t.release_year) decs.add(Math.floor(t.release_year / 10) * 10);
      if (t.first) { const y = parseInt(String(t.first).slice(0, 4), 10); if (!Number.isNaN(y)) yrs.add(y); }
    }
    return { decades: [...decs].sort((a, b) => a - b), years: [...yrs].sort((a, b) => a - b) };
  }, [tracks]);

  /* The backdrop is tinted by whatever is actually on screen: the selected
     genre if there is one, otherwise the dominant genres of the current
     filter. Same oklch() strings the donut and legend are drawn with, so the
     room and the chart always agree. */
  useEffect(() => {
    if (!window.MLAmbient) return;
    const top = agg.genresTop.map((g) => g.key).filter((k) => k !== "Other" && k !== filters.genre);
    // A selected genre takes two of the four blobs rather than all four: the
    // room leans toward that colour instead of flooding with it, which keeps
    // the backdrop a hint and not a filter over the whole page.
    const keys = filters.genre ? [filters.genre, filters.genre, ...top] : top;
    window.MLAmbient.setColors(keys.slice(0, 4).map((k) => genreColorMap[k]).filter(Boolean));
  }, [filters.genre, agg.genresTop, genreColorMap]);

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
            <NavItem id="overview" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
              Overview
            </NavItem>

            <div className="sidenav-section">Library</div>
            <NavItem id="genres" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 2v10l6.6 3.8"/></svg>
              Genre &amp; Moods
            </NavItem>
            <NavItem id="albums" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6"/></svg>
              Albums
            </NavItem>
            <NavItem id="constellation" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="5" cy="5" r="1.5"/><circle cx="19" cy="5" r="1.5"/><circle cx="12" cy="19" r="1.5"/><circle cx="5" cy="19" r="1.5"/><circle cx="19" cy="19" r="1.5"/><line x1="6.5" y1="5" x2="17.5" y2="5"/><line x1="5" y1="6.5" x2="5" y2="17.5"/><line x1="19" y1="6.5" x2="19" y2="17.5"/><line x1="6.5" y1="19" x2="17.5" y2="19"/><line x1="6.5" y1="6.5" x2="17.5" y2="17.5"/></svg>
              Tag Constellation
            </NavItem>
            <NavItem id="audio" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
              Audio Features
            </NavItem>
            <NavItem id="coverage" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>
              Coverage
            </NavItem>

            <div className="sidenav-section">Listening</div>
            <NavItem id="timeline" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
              Timeline
            </NavItem>
            <NavItem id="map" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
              Listening Map
            </NavItem>
            <NavItem id="trajectory" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 18c3-8 7-10 9-5s5 3 9-5"/><path d="M3 12c2-5 5-7 8-4s5 4 10-2"/><path d="M3 6c2-3 4-4 6-2s4 4 12-2"/></svg>
              Artists
            </NavItem>
            <NavItem id="seasonal" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/></svg>
              Seasonal
            </NavItem>
            <NavItem id="forgotten" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z"/><path d="M12 8v4l3 3"/><path d="M8 2.5l-2.5 2.5"/><path d="M16 2.5l2.5 2.5"/></svg>
              Forgotten
            </NavItem>

            <div className="sidenav-section">Browse</div>
            <NavItem id="explorer" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
              Tracks
            </NavItem>

            <div className="sidenav-section">Data</div>
            <NavItem id="sync" page={page} onGo={goPage}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15"/></svg>
              Scrobble Sync
            </NavItem>
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
                <button key={id} aria-pressed={timeframe === id} onClick={() => setTimeframe(id)}>{label}</button>
              ))}
            </div>
            <span className="slicer-note">
              {timeframe === "all"
                ? "All recorded scrobbles"
                : <>Plays counted within <b>{TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase()}</b> · every chart on this page</>}
              {ANCHOR.stale && ANCHOR.dataEnd
                ? <> · data through <b>{ANCHOR.dataEnd}</b></>
                : null}
            </span>
          </div>

          <div className="kpis">
            <Kpi label="Tracks" val={nf(filtered.length)} sub={filtered.length !== tracks.length ? <>of <b>{nf(tracks.length)}</b> total</> : "unique in library"} />
            <Kpi label={timeframe === "all" ? "Scrobbles" : "Plays"} val={nf(agg.totalPlays)} sub={<>across <b>{nf(agg.uniqueArtists)}</b> artists</>} />
            <Kpi label="Artists" val={nf(agg.uniqueArtists)} sub={timeframe === "all" ? "distinct performers" : "played in window"} />
            <Kpi label="Avg plays" val={(agg.totalPlays / (filtered.length || 1)).toFixed(1)} sub="per track" />
            <Kpi label="Enriched" val={agg.completeness + "%"} sub="field completeness" spark={agg.completeness} />
            <Kpi label="Mood-tagged" val={Math.round((agg.withMood / (filtered.length || 1)) * 100) + "%"} sub={<>{nf(agg.withMood)} classified</>} spark={Math.round((agg.withMood / (filtered.length || 1)) * 100)} />
          </div>

          <section className="block">
            <div className="grid g-32">
              <div className="card">
                <div className="card-head"><h3 className="card-title">When the music plays</h3><span className="card-meta">{sel.dow != null ? DOW_NAMES[sel.dow].toLowerCase() + "s only · click to explore" : "hour · click to explore"}</span></div>
                <HourChart data={view.byHour} onPick={(h) => pickDim("hour", h)} activeKey={sel.hour} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Weekly rhythm</h3><span className="card-meta">{sel.hour != null ? fmt12full(sel.hour) + " only · click to explore" : "day · click to explore"}</span></div>
                <DowChart data={view.byDow} onPick={(i) => pickDim("dow", i)} activeKey={sel.dow} />
              </div>
            </div>
          </section>
          {selActive && (
            <section className="block">
              {/* "Tracks & time" shows a by-hour radial, which only says
                  something while the hour dimension is still open. */}
              <DrillPanel label={drillLabel} slice={view.slice} onClose={clearSel} views={sel.hour == null} />
            </section>
          )}
          <section className="block">
            <div className="card">
              <div className="card-head norule" style={{ marginBottom: 12 }}>
                <h3 className="card-title">Seasons of listening</h3>
                <span className="card-meta">{selActive ? "within your selection · click to explore" : "season · click to explore"}</span>
              </div>
              <Seasons data={view.bySeason} total={view.total} onPick={(s) => pickDim("season", s)} activeKey={sel.season} />
            </div>
          </section>
          <section className="block">
            <div className="grid g-2">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Top artists</h3><span className="card-meta">{tfLabel} · click to filter</span></div>
                <HBars items={agg.topArtists} max={agg.maxArtist} activeKey={filters.artist} onPick={(k) => setFilter("artist", k)} unit="plays" />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Most played tracks</h3><span className="card-meta">{tfLabel} · top 12</span></div>
                <TrackList items={agg.topTracks.map((t) => ({ ...t, play: t.wp }))} max={agg.maxTrack} />
              </div>
            </div>
          </section>
        </div>

        {/* ── PAGE: Timeline ──────────────────────────────────────── */}
        <div style={{ display: page === "timeline" ? "" : "none" }}>
          {TimelineChart && <TimelineChart active={page === "timeline"} />}
        </div>

        {/* ── PAGE: Artist Trajectory ─────────────────────────────── */}
        <div style={{ display: page === "trajectory" ? "" : "none" }}>
          {ArtistTrajectory && <ArtistTrajectory active={page === "trajectory"} />}
        </div>

        {/* ── PAGE: Listening Map ─────────────────────────────────── */}
        <div style={{ display: page === "map" ? "" : "none" }}>
          {ListeningMap && <ListeningMap active={page === "map"} />}
        </div>

        {/* ── PAGE: Audio Features ────────────────────────────────── */}
        <div style={{ display: page === "audio" ? "" : "none" }}>
          {AudioFeaturesChart && <AudioFeaturesChart active={page === "audio"} />}
          <AudioFeatureExtremes tracks={tracks} />
        </div>

        {/* ── PAGE: Albums ────────────────────────────────────────── */}
        <div style={{ display: page === "albums" ? "" : "none" }}>
          {AlbumsPage && <AlbumsPage active={page === "albums"} tracks={tracks} />}
        </div>

        {/* ── PAGE: Seasonal Favorites ────────────────────────────── */}
        <div style={{ display: page === "seasonal" ? "" : "none" }}>
          <div className="page-intro">
            <h2 className="page-title">Seasonal favorites</h2>
            <p className="page-lede">What you reach for in each season — top genres, moods, and most-played tracks, from your scrobble history.</p>
          </div>
          <SeasonalFavorites drill={drill} />
        </div>

        {/* ── PAGE: Forgotten Favorites ───────────────────────────── */}
        <div style={{ display: page === "forgotten" ? "" : "none" }}>
          {ForgottenFavoritesPage && <ForgottenFavoritesPage active={page === "forgotten"} refreshVersion={refreshVersion} />}
        </div>

        {/* ── PAGE: Tag Constellation ─────────────────────────────── */}
        <div style={{ display: page === "constellation" ? "" : "none" }}>
          {TagConstellation && <TagConstellation active={page === "constellation"} />}
        </div>

        {/* ── PAGE: Genre & Moods ─────────────────────────────────── */}
        <div style={{ display: page === "genres" ? "" : "none" }}>
          <section className="block">
            <div className="grid g-2">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Mood spectrum</h3><span className="card-meta">share of plays · {Math.round(agg.moodCoverage * 100)}% of plays tagged · click to filter</span></div>
                <MoodBars items={agg.moods} max={agg.maxMood} activeKey={filters.mood} onPick={(k) => setFilter("mood", k)} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Genre balance</h3><span className="card-meta">share of library · click to filter</span></div>
                <GenreDonut items={agg.genresTop} total={agg.genreTotal} colors={genreColors} activeKey={filters.genre} onPick={(k) => k !== "Other" && setFilter("genre", k)} />
              </div>
            </div>
          </section>
        </div>

        {/* ── PAGE: Coverage ──────────────────────────────────────── */}
        <div style={{ display: page === "coverage" ? "" : "none" }}>
          <section className="block">
            <div className="grid g-32">
              <div className="card">
                <div className="card-head"><h3 className="card-title">Tags &amp; styles</h3><span className="card-meta">Last.fm + Discogs · click to filter</span></div>
                <TagCloud items={agg.tags} activeKey={filters.tag} onPick={(k) => setFilter("tag", k)} />
              </div>
              <div className="card">
                <div className="card-head"><h3 className="card-title">Enrichment coverage</h3><span className="card-meta">{filtered.length !== tracks.length ? "filtered" : "library"}</span></div>
                <CoverageBars rows={agg.coverageRows} total={filtered.length} />
              </div>
            </div>
          </section>
          <section className="block">
            {SaturationChart && <SaturationChart active={page === "coverage"} />}
          </section>
        </div>

        {/* ── PAGE: Track Explorer ────────────────────────────────── */}
        <div style={{ display: page === "explorer" ? "" : "none" }}>
          <FilterBar filters={filters} onRemove={removeFilter} onClear={clearFilters} sort={sort} onSort={setSort} onToggle={setFilter} onRange={setFilterValue} decades={explorerRanges.decades} years={explorerRanges.years} curYear={ANCHOR.curYear} />
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
                  <div className="legend-gtitle">Data coverage <span className="lg-note">the squares keep their order on every row — colour tells you which is which</span></div>
                  {COV_SOURCES.map((c, i) => (
                    <div className="legend-item" key={c.key}>
                      <span className="covdots">
                        {COV_SOURCES.map((o, j) => (
                          <span key={o.key} className={"covdot" + (i === j ? " on" : "")} style={{ "--cov": o.color }}></span>
                        ))}
                      </span>
                      <span><b>{c.label}</b> — {c.note}</span>
                    </div>
                  ))}
                  <div className="legend-item">
                    <span className="covdots"><span className="covdot warn" style={{ "--cov": COV_SOURCES[COV_SOURCES.length - 1].color }}></span></span>
                    <span>A <b>hollow</b> square is a source that is present but lower-confidence — today only a centroid-inferred mood.</span>
                  </div>
                  <div className="legend-item">
                    <span className="covdots"><span className="covdot"></span></span>
                    <span>A <b>dim</b> square means that source is missing for the track.</span>
                  </div>
                </div>
                <div className="legend-group">
                  <div className="legend-gtitle">Moods <span className="lg-note">grouped into families — warm is energy, blue is low, green is bright</span></div>
                  <div className="mood-key">
                    {Object.keys(MOOD_COLORS).map((m) => (
                      <span className="minimood" key={m} style={{ "--mood": MOOD_COLORS[m] }}>{m}</span>
                    ))}
                  </div>
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
          <TweakSection label="Motion" />
          <TweakToggle label="Ambient backdrop" value={ambient} onChange={setAmbient} />
          <TweakToggle label="Pointer effects" value={pointerFx} onChange={setPointerFx} />
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
