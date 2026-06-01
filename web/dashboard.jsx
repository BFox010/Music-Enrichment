/* ============================================================
   dashboard.jsx — main app: state, aggregation, layout
   ============================================================ */
const { useState, useMemo, useEffect, useRef, useCallback } = React;

/* ---------- helpers ---------- */
const SEASON_BY_MONTH = { 12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall" };

function normalizeTrack(raw, i) {
  return {
    i,
    artist: raw.artist || "Unknown",
    track: raw.track || "Untitled",
    album: raw.album || "",
    release_year: raw.release_year || null,
    genres: Array.isArray(raw.genres) ? raw.genres : [],
    tags: Array.isArray(raw.lastfm_tags) ? raw.lastfm_tags : (Array.isArray(raw.tags) ? raw.tags : []),
    styles: Array.isArray(raw.discogs_styles) ? raw.discogs_styles : (Array.isArray(raw.styles) ? raw.styles : []),
    moods: raw.mood_tags || raw.moods || null,
    mood_source: raw.mood_source || null,
    mood_confidence: raw.mood_confidence || null,
    play: Number(raw.play_count != null ? raw.play_count : (raw.play || 0)) || 0,
    py: raw.py || null,
    tm: raw.tm != null ? raw.tm : null,
    lm: raw.lm != null ? raw.lm : null,
    peak_year: raw.peak_year || null,
    first: raw.first_scrobbled || raw.first || null,
    last: raw.last_scrobbled || raw.last || null,
    mbid: !!(raw.musicbrainz_id || raw.mbid),
    spotify: !!(raw.spotify_id || raw.spotify),
    apple: raw.apple_music_available != null ? raw.apple_music_available : (raw.apple || null),
    af: raw.audio_features || raw.af || null,
    sources: raw.enrichment_sources || raw.sources || [],
    sat: raw.saturation_tier != null ? raw.saturation_tier : (raw.sat || null),
    playlists: raw.playlists || []
  };
}

function aggregateScrobbles(rows) {
  const byHour = Array(24).fill(0), byDow = Array(7).fill(0), bySeason = { winter: 0, spring: 0, summer: 0, fall: 0 }, byYear = {};
  let total = 0;
  for (const s of rows) {
    total++;
    if (s.hour != null) byHour[s.hour]++;
    if (s.day_of_week != null) byDow[s.day_of_week]++;
    const season = s.season || (s.month ? SEASON_BY_MONTH[s.month] : null);
    if (season) bySeason[season] = (bySeason[season] || 0) + 1;
    if (s.year != null) byYear[s.year] = (byYear[s.year] || 0) + 1;
  }
  return { byHour, byDow, bySeason, byYear, total };
}

function parseJSONL(text) {
  const out = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try { out.push(JSON.parse(t)); } catch (e) { /* skip */ }
  }
  return out;
}

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
const CUR_YEAR = 2026, LAST_YEAR = 2025;
function playInWindow(t, tf) {
  if (tf === "all" || !tf) return t.play || 0;
  if (tf === "year_this") return (t.py && t.py[CUR_YEAR]) || 0;
  if (tf === "year_last") return (t.py && t.py[LAST_YEAR]) || 0;
  if (tf === "month_this") return t.tm || 0;
  if (tf === "month_last") return t.lm || 0;
  return t.play || 0;
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

  const [density, setDensity] = useState(() => localStorage.getItem("ml.density") || "comfortable");
  const [accent, setAccent] = useState(() => localStorage.getItem("ml.accent") || "#a78bfa");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("plays");
  const [timeframe, setTimeframe] = useState("all");
  const [shuffleSeed, setShuffleSeed] = useState(1);
  const [infoOpen, setInfoOpen] = useState(false);
  const [filters, setFilters] = useState({ genre: "", mood: "", tag: "", decade: "", artist: "" });
  const [dzShow, setDzShow] = useState(false);
  const [toast, setToast] = useState("");
  const fileRef = useRef(null);
  const dragDepth = useRef(0);

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

  /* ---------- file loading ---------- */
  const handleFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList);
    let newTracks = null, newScrob = null, names = [];
    for (const f of files) {
      const text = await f.text();
      const rows = f.name.endsWith(".json") && text.trim().startsWith("[") ? JSON.parse(text) : parseJSONL(text);
      const lname = f.name.toLowerCase();
      if (lname.includes("scrobble")) { newScrob = aggregateScrobbles(rows); names.push(f.name); }
      else if (lname.includes("track") || rows[0]?.canonical_track_id || rows[0]?.track) { newTracks = rows.map(normalizeTrack); names.push(f.name); }
      else if (rows[0]?.hour != null || rows[0]?.scrobbled_at) { newScrob = aggregateScrobbles(rows); names.push(f.name); }
    }
    if (!newTracks && !newScrob) { showToast("Couldn't recognize that file — expected tracks.jsonl or scrobbles.jsonl"); return; }
    setData((d) => ({
      meta: { ...d.meta, isSample: false, trackCount: newTracks ? newTracks.length : d.meta.trackCount, scrobbleCount: newScrob ? newScrob.total : d.meta.scrobbleCount },
      tracks: newTracks || d.tracks,
      scrobbles: newScrob || d.scrobbles
    }));
    showToast(`Loaded your data — ${names.join(", ")}`);
  }, [showToast]);

  /* try fetching real files if served alongside (e.g. in the repo) */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [tr, sc] = await Promise.allSettled([
          fetch("tracks.jsonl").then((r) => r.ok ? r.text() : Promise.reject()),
          fetch("scrobbles.jsonl").then((r) => r.ok ? r.text() : Promise.reject())
        ]);
        if (cancelled) return;
        let nt = null, ns = null;
        if (tr.status === "fulfilled") { const rows = parseJSONL(tr.value); if (rows.length) nt = rows.map(normalizeTrack); }
        if (sc.status === "fulfilled") { const rows = parseJSONL(sc.value); if (rows.length) ns = aggregateScrobbles(rows); }
        if (nt || ns) {
          setData((d) => ({
            meta: { ...d.meta, isSample: false, trackCount: nt ? nt.length : d.meta.trackCount, scrobbleCount: ns ? ns.total : d.meta.scrobbleCount },
            tracks: nt || d.tracks, scrobbles: ns || d.scrobbles
          }));
          showToast("Loaded your live library from the repo");
        }
      } catch (e) { /* sample stays */ }
    })();
    return () => { cancelled = true; };
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
  const removeFilter = (kind) => setFilters((f) => ({ ...f, [kind]: "" }));
  const clearFilters = () => setFilters({ genre: "", mood: "", tag: "", decade: "", artist: "" });

  /* windowed play count for the active timeframe */
  const playOf = useCallback((t) => playInWindow(t, timeframe), [timeframe]);

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
  const agg = useMemo(() => {
    const artistPlays = {}, moodCount = {}, genreCount = {}, tagCount = {};
    let withMood = 0, cov = { tags: 0, mbid: 0, styles: 0, af: 0, apple: 0, mood: 0, spotify: 0 };
    let totalPlays = 0;
    for (const t of filtered) {
      const p = playOf(t);
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
    const n = filtered.length || 1;
    const topArtists = topEntries(artistPlays, 12).filter((a) => a.value > 0).map((a) => ({ ...a, sub: filtered.filter((t) => t.artist === a.key).length + " trk" }));
    const topTracks = [...filtered].map((t) => ({ ...t, wp: playOf(t) })).filter((t) => t.wp > 0).sort((a, b) => b.wp - a.wp).slice(0, 12);
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
      totalPlays, withMood, completeness,
      maxArtist: topArtists[0]?.value || 1, maxTrack: topTracks[0]?.wp || 1,
      maxMood: moods[0]?.value || 1
    };
  }, [filtered, playOf]);

  const genreColors = genreColorMap;
  const meta = data.meta;
  const nf = (x) => x.toLocaleString();
  const tfLabel = timeframe === "all" ? "by scrobbles" : TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase();

  /* mobile-friendly feedback: toast the match count whenever filters change */
  const firstFilterRun = useRef(true);
  useEffect(() => {
    if (firstFilterRun.current) { firstFilterRun.current = false; return; }
    const active = Object.entries(filters).filter(([, v]) => v);
    if (active.length) showToast(`${filtered.length.toLocaleString()} tracks match — ${active.map(([k, v]) => v).join(" · ")}`);
  }, [filters]);

  return (
    <div className="app">
      {/* ---------- topbar ---------- */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-eyebrow"><span className="dot"></span> Music-Enrichment · Library Analytics</div>
          <h1>Listening Atlas</h1>
          <div className="sub">
            <span>{nf(tracks.length)} tracks · {nf(scrobbles.total)} scrobbles · {meta.scrobbleRange}</span>
            <span className={"pill-live" + (meta.isSample ? "" : " real")}>{meta.isSample ? "sample data" : "live data"}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search artist or track…" />
          </div>
          <button className="btn" onClick={() => fileRef.current && fileRef.current.click()} title="Load your tracks.jsonl / scrobbles.jsonl">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3v12m0-12l-4 4m4-4l4 4" /><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" /></svg>
            Load data
          </button>
          <input ref={fileRef} type="file" accept=".jsonl,.json" multiple style={{ display: "none" }} onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
        </div>
      </header>

      {/* ---------- timeframe slicer ---------- */}
      <div className="slicer">
        <span className="slicer-label">Timeframe</span>
        <div className="seg" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map(([id, label]) => (
            <button key={id} aria-pressed={timeframe === id} onClick={() => setTimeframe(id)}>{label}</button>
          ))}
        </div>
        <span className="slicer-note">{timeframe === "all" ? "All recorded scrobbles" : <>Plays counted within <b>{TIMEFRAMES.find((t) => t[0] === timeframe)[1].toLowerCase()}</b> · affects play-based metrics</>}</span>
      </div>

      {/* ---------- KPIs ---------- */}
      <div className="kpis">
        <Kpi label="Tracks" val={nf(filtered.length)} sub={filtered.length !== tracks.length ? <>of <b>{nf(tracks.length)}</b> total</> : "unique in library"} />
        <Kpi label={timeframe === "all" ? "Scrobbles" : "Plays"} val={nf(agg.totalPlays)} sub={<>across <b>{nf(agg.uniqueArtists)}</b> artists</>} />
        <Kpi label="Artists" val={nf(agg.uniqueArtists)} sub={timeframe === "all" ? "distinct performers" : "played in window"} />
        <Kpi label="Avg plays" val={(agg.totalPlays / (filtered.length || 1)).toFixed(1)} sub="per track" />
        <Kpi label="Enriched" val={agg.completeness + "%"} sub="field completeness" spark={agg.completeness} />
        <Kpi label="Mood-tagged" val={Math.round((agg.withMood / (filtered.length || 1)) * 100) + "%"} sub={<>{nf(agg.withMood)} classified</>} spark={Math.round((agg.withMood / (filtered.length || 1)) * 100)} />
      </div>

      {/* ---------- Listening patterns (global) ---------- */}
      <section className="block">
        <div className="grid g-32">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">When the music plays</h3>
              <span className="card-meta">hour of day</span>
            </div>
            <HourChart data={scrobbles.byHour} />
          </div>
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Weekly rhythm</h3>
              <span className="card-meta">day of week</span>
            </div>
            <DowChart data={scrobbles.byDow} />
          </div>
        </div>
      </section>
      <section className="block">
        <div className="card">
          <div className="card-head norule" style={{ marginBottom: 12 }}>
            <h3 className="card-title">Seasons of listening</h3>
            <span className="card-meta">scrobbles by season</span>
          </div>
          <Seasons data={scrobbles.bySeason} total={scrobbles.total} />
        </div>
      </section>

      {/* ---------- filter bar ---------- */}
      <FilterBar filters={filters} onRemove={removeFilter} onClear={clearFilters} sort={sort} onSort={setSort} />

      {/* ---------- top artists / tracks ---------- */}
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

      {/* ---------- moods + genres ---------- */}
      <section className="block">
        <div className="grid g-2">
          <div className="card">
            <div className="card-head"><h3 className="card-title">Mood spectrum</h3><span className="card-meta">tracks per mood · click to filter</span></div>
            <MoodBars items={agg.moods} max={agg.maxMood} activeKey={filters.mood} onPick={(k) => setFilter("mood", k)} />
          </div>
          <div className="card">
            <div className="card-head"><h3 className="card-title">Genre balance</h3><span className="card-meta">share of library · click to filter</span></div>
            <GenreDonut items={agg.genresTop} total={agg.genreTotal} colors={genreColors} activeKey={filters.genre} onPick={(k) => k !== "Other" && setFilter("genre", k)} />
          </div>
        </div>
      </section>

      {/* ---------- tags + coverage ---------- */}
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

      {/* ---------- explorer ---------- */}
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

      {/* ---------- dropzone + toast ---------- */}
      <div className={"dropzone" + (dzShow ? " show" : "")}>
        <div className="dz-inner">
          <div className="dz-t">Drop your library files</div>
          <div className="dz-s">tracks.jsonl &nbsp;·&nbsp; scrobbles.jsonl</div>
        </div>
      </div>
      <div className={"toast" + (toast ? " show" : "")}>{toast}</div>

      {/* ---------- legend modal ---------- */}
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

      {/* ---------- Tweaks ---------- */}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Accent" />
        <TweakColor label="Accent" value={accent} options={ACCENT_OPTIONS} onChange={setAccent} />
        <TweakSection label="Layout" />
        <TweakRadio label="Density" value={density} options={["comfortable", "compact"]} onChange={setDensity} />
      </TweaksPanel>
    </div>
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
