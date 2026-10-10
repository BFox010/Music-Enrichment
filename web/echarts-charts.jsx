/* ECharts React wrappers. Requires window.echarts (loaded lazily, see below). */
const { useEffect, useRef, useState, useCallback, useMemo } = React;

/* ── CSS-variable theme colours ── */
function themeVars() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  return {
    accent: v("--accent") || "#a78bfa",
    text:   v("--text")   || "#f4f4f7",
    text2:  v("--text-2") || "#b9b9c6",
    muted:  v("--faint")  || "#55555f",
    panel:  v("--panel-2")|| "#1a1a22",
    line:   v("--line")   || "#272732",
  };
}

/* ECharts renders a string tooltip formatter's output as HTML. Track, artist
   and tag names come from Last.fm's crowd-sourced data, so one that contains
   markup ran as script in the page that holds the mutation token (#114).
   Every name interpolated into a formatter goes through this. */
function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/* ── lazy ECharts loader ──
   ECharts (~1 MB) is not in index.html; it is injected on demand — prefetched on
   idle after first paint, loaded immediately when a chart view opens.
   Singleton: the <script> is added at most once and the promise is reused. */
let __echartsPromise = null;
function ensureECharts() {
  if (window.echarts) return Promise.resolve(window.echarts);
  if (__echartsPromise) return __echartsPromise;
  __echartsPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    // Pinned with an integrity hash (#114). "echarts@5" floated to whatever 5.x
    // jsDelivr resolved that day, which no hash can cover. The hash is of the
    // npm tarball's dist/echarts.min.js, which jsDelivr serves verbatim.
    s.src = "https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js";
    s.integrity = "sha384-pPi0zxBAoDu6+JXW/C68UZLvBUUtU+7zonhif43rqj7pxsGyqyqzcian2Rj37Rss";
    s.crossOrigin = "anonymous";
    s.async = true;
    s.onload = () => resolve(window.echarts);
    s.onerror = () => {
      __echartsPromise = null;
      // Once per failed load, for the dashboard's error state; the charts
      // themselves just stay empty rather than retrying on a timer.
      window.dispatchEvent(new CustomEvent("ml:echarts-failed"));
      reject(new Error("ECharts failed to load"));
    };
    document.head.appendChild(s);
  });
  return __echartsPromise;
}

/* ── shared ECharts mount hook ── */
function useEChart(ref) {
  const chartRef = useRef(null);
  // ECharts can finish loading after a view's data has already arrived, in which
  // case the render effect bailed on a null instance and nothing would wake it.
  // Bumping state on init re-renders, so an effect that lists `chart.current`
  // among its dependencies runs again against the live instance. Every view
  // draws in such an effect, separate from its fetch: ListeningMap, Audio
  // Features, Saturation and Tag Constellation drew inside the fetch callback
  // (or keyed on the stable ref) and stayed blank after a late init (#113).
  const [, setReady] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let ro;
    const onResize = () => chartRef.current?.resize();
    // ECharts is loaded lazily (deferred, off the first-paint critical path), so
    // wait on the loader. This used to poll window.echarts every 50 ms; when the
    // CDN failed, every mounted chart polled forever with nothing on screen to
    // say why (#113). A failure is now announced once, by ensureECharts.
    function init(lib) {
      if (cancelled || chartRef.current || !ref.current || !lib) return;
      chartRef.current = lib.init(ref.current, null, { renderer: "canvas" });
      setReady((n) => n + 1);
      window.addEventListener("resize", onResize);
      // The container is often 0×0 at init time (skeleton showing, or the page
      // hidden via display:none). Observe it and resize once it gains real size
      // so the chart fills its card instead of rendering into a 0-height canvas.
      if (typeof ResizeObserver !== "undefined") {
        ro = new ResizeObserver(() => {
          const el = ref.current;
          if (el && el.clientWidth && el.clientHeight) chartRef.current?.resize();
        });
        ro.observe(ref.current);
      }
    }
    ensureECharts().then(init, () => {});
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);
  return chartRef;
}

/* ── Loading skeleton ── */
function ChartLoading({ height = 420 }) {
  return <div className="echart-loading" style={{ height }}>Loading…</div>;
}

/* ── shared bits ── */
// right-aligned cluster for a card-head that also holds a control (seg) + meta
const cardTools = { display: "flex", alignItems: "center", gap: 14, flexShrink: 0, flexWrap: "wrap", justifyContent: "flex-end" };
// one-line explainer that sits directly under a card-head
const cardDesc  = { margin: "0 0 16px", fontSize: 12.5, lineHeight: 1.55, color: "var(--muted-s)", maxWidth: 640 };

/* ── Timeline chart ── */

/* The Trajectory page unmounts whichever view is hidden rather than leaving an
   idle ECharts instance behind the toggle (#31), so this view's state has to
   outlive the component. Module scope, not a ref — the component is gone in
   between. `data` is keyed by the year/month toggle. */
const __timelineState = { by: "year", data: {}, ver: 0 };

function TimelineChart({ active, refreshVersion = 0 }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [by, setBy] = useState(__timelineState.by);
  const [rows, setRows] = useState(() => __timelineState.data[__timelineState.by] || null);
  const loading = rows === null;
  useEffect(() => { __timelineState.by = by; });

  useEffect(() => {
    if (!active) return;
    // A refresh rewrites scrobbles.jsonl underneath the cache. This view used to
    // re-fetch every time the page was opened, so the caching that survives the
    // #31 toggle has to drop on a refresh or the page would show the old counts
    // for the rest of the session.
    if (__timelineState.ver !== refreshVersion) {
      __timelineState.ver = refreshVersion;
      __timelineState.data = {};
    }
    const cached = __timelineState.data[by];
    if (cached) { setRows(cached); return; }
    setRows(null);
    let stale = false;
    fetch(`/api/timeline?by=${by}`)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => { __timelineState.data[by] = d || []; if (!stale) setRows(d || []); })
      .catch(() => { if (!stale) setRows([]); });
    return () => { stale = true; };
  }, [active, by, refreshVersion]);

  useEffect(() => {
    if (!active || !chart.current || !rows?.length) return;
    chart.current.resize();
    const c = themeVars();
    const periods = rows.map((d) => d.period);
    const plays   = rows.map((d) => d.plays);
    chart.current.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
      grid: { top: 20, bottom: 36, left: 52, right: 16 },
      xAxis: {
        type: "category", data: periods,
        axisLabel: { color: c.muted, rotate: periods.length > 24 ? 45 : 0, fontSize: 11 },
        axisLine: { lineStyle: { color: c.line } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: c.muted },
        splitLine: { lineStyle: { color: c.line, type: "dashed" } },
      },
      series: [{
        type: "line", data: plays, smooth: true,
        symbol: "circle", symbolSize: 4,
        lineStyle: { color: c.accent, width: 2 },
        itemStyle: { color: c.accent },
        areaStyle: {
          color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: c.accent + "55" }, { offset: 1, color: c.accent + "05" }] },
        },
      }],
    });
  }, [active, rows, chart.current]);

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Scrobble timeline</h3>
          <div style={cardTools}>
            <div className="seg" role="group">
              {[["year","By Year"],["month","By Month"]].map(([v,l]) => (
                <button key={v} aria-pressed={by === v} onClick={() => setBy(v)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">scrobbles over time</span>
          </div>
        </div>
        <div className="echart-wrap" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading />}
      </div>
    </section>
  );
}

/* ── Artist Trajectory (line / stream + artist picker) ── */

/* How deep into the play-count tail the picker can reach. The page used to
   fetch 20, which made it a view of the same heavy-rotation handful and
   nothing else. Measured 2026-08-29 against the committed library: 300 costs
   ~19 KB gzipped and ~35 ms server-side, against ~3 KB at 20 — and the fetch is
   gated on the page being open, so the dashboard's first paint is untouched
   either way. /api/artist-trajectory caps at 500 if this needs to go further. */
const TRAJECTORY_TOP = 300;

/* How many artists the picker seeds with, and how many Shuffle draws. */
const TRAJECTORY_SEED = 8;

/* Chips are cheap individually but 300 of them is a real layout cost on every
   keystroke. Render a window of the current ordering and tell the reader the
   filter box reaches the rest. */
const TRAJECTORY_CHIP_LIMIT = 80;

/* Artists by total plays, descending — the picker's ordering and the source of
   both the "Top N" seed and the shuffle pool. */
function rankArtists(rows) {
  const totals = {};
  rows.forEach(([, c, n]) => { totals[n] = (totals[n] || 0) + c; });
  return Object.keys(totals).sort((a, b) => totals[b] - totals[a]).map((n) => ({ name: n, total: totals[n] }));
}

/* Held across the unmount the view toggle causes (see __timelineState): the
   payload is the page's largest fetch, and losing the reader's artist
   selection on every flick of the toggle would make the picker unusable. */
const __trajectoryState = { raw: null, mode: "lines", selected: null, query: "", ver: 0 };

function ArtistTrajectory({ active, refreshVersion = 0 }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [raw, setRaw] = useState(__trajectoryState.raw);
  const [mode, setMode] = useState(__trajectoryState.mode);
  const [selected, setSelected] = useState(__trajectoryState.selected);  // Set of artist names
  const [query, setQuery] = useState(__trajectoryState.query);
  const [loading, setLoading] = useState(!__trajectoryState.raw);
  useEffect(() => {
    __trajectoryState.raw = raw;
    __trajectoryState.mode = mode;
    __trajectoryState.selected = selected;
    __trajectoryState.query = query;
  });

  // Fetch once; seed the selection with the most-played TRAJECTORY_SEED.
  useEffect(() => {
    if (!active) return;
    // See __timelineState: the cache outlives the component, so a refresh has to
    // invalidate it explicitly. Dropping `raw` re-enters this effect and fetches.
    if (__trajectoryState.ver !== refreshVersion) {
      __trajectoryState.ver = refreshVersion;
      __trajectoryState.raw = null;
      if (raw) { setLoading(true); setRaw(null); return; }
    }
    if (raw) return;
    setLoading(true);
    fetch(`/api/artist-trajectory?top=${TRAJECTORY_TOP}`)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => {
        const rows = (d && d.data) || [];
        const seed = new Set(rankArtists(rows).slice(0, TRAJECTORY_SEED).map((a) => a.name));
        // Straight into module state as well as component state: the view can be
        // toggled away mid-flight, and this is the page's one big fetch — a
        // response already paid for should not be thrown away with the unmount.
        __trajectoryState.raw = { data: rows };
        __trajectoryState.selected = seed;
        setLoading(false);
        setRaw(__trajectoryState.raw);
        setSelected(seed);
      })
      .catch(() => setLoading(false));
  }, [active, raw, refreshVersion]);

  const artists = useMemo(() => (raw ? rankArtists(raw.data) : []), [raw]);

  useEffect(() => {
    if (!active || !chart.current || !raw || !selected) return;
    chart.current.resize();
    const c = themeVars();
    const rows = raw.data.filter((d) => selected.has(d[2]));
    if (!rows.length) { chart.current.clear(); return; }

    if (mode === "stream") {
      chart.current.setOption({
        backgroundColor: "transparent",
        tooltip: { trigger: "axis", axisPointer: { type: "line" }, backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
        legend: { type: "scroll", bottom: 0, textStyle: { color: c.text2, fontSize: 11 } },
        singleAxis: { top: 24, bottom: 60, type: "time", axisLabel: { color: c.muted }, axisLine: { lineStyle: { color: c.line } }, splitLine: { lineStyle: { color: c.line, type: "dashed" } } },
        series: [{ type: "themeRiver", emphasis: { focus: "adjacency" }, label: { show: true, fontSize: 10, color: c.text }, data: rows }],
      }, true);
    } else {
      const periods = [...new Set(raw.data.map((d) => d[0]))].sort();
      const names = [...selected];
      const series = names.map((n) => {
        const m = {};
        rows.forEach((d) => { if (d[2] === n) m[d[0]] = d[1]; });
        return {
          name: n, type: "line", smooth: true, smoothMonotone: "x",
          showSymbol: false, connectNulls: true, emphasis: { focus: "series" },
          lineStyle: { width: 2 }, data: periods.map((p) => m[p] || 0),
        };
      });
      chart.current.setOption({
        backgroundColor: "transparent",
        tooltip: { trigger: "axis", axisPointer: { type: "line" }, order: "valueDesc", backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
        legend: { type: "scroll", bottom: 0, textStyle: { color: c.text2, fontSize: 11 } },
        grid: { top: 20, left: 54, right: 20, bottom: 56 },
        xAxis: { type: "category", data: periods.map((p) => p.slice(0, 7)), boundaryGap: false, axisLabel: { color: c.muted, rotate: periods.length > 18 ? 45 : 0, fontSize: 10 }, axisLine: { lineStyle: { color: c.line } } },
        yAxis: { type: "value", name: "plays / mo", nameTextStyle: { color: c.muted, fontSize: 10 }, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.line, type: "dashed" } } },
        series,
      }, true);
    }
  }, [active, raw, mode, selected, chart.current]);

  const toggleArtist = (name) => setSelected((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const resetTop = () => setSelected(new Set(artists.slice(0, TRAJECTORY_SEED).map((a) => a.name)));

  /* Partial Fisher-Yates over a copy: the whole point is to surface artists the
     top-N ordering buries, so every artist in the payload is equally likely. */
  const shuffleArtists = () => {
    const pool = artists.map((a) => a.name);
    const n = Math.min(TRAJECTORY_SEED, pool.length);
    for (let i = 0; i < n; i++) {
      const j = i + Math.floor(Math.random() * (pool.length - i));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    setSelected(new Set(pool.slice(0, n)));
  };

  const q = query.trim().toLowerCase();
  const matching = q ? artists.filter((a) => a.name.toLowerCase().includes(q)) : artists;
  /* Selected artists always stay visible, even outside the chip window — a
     shuffled pick from the deep tail must remain un-clickable-off. */
  const shownArtists = useMemo(() => {
    const head = matching.slice(0, TRAJECTORY_CHIP_LIMIT);
    const inHead = new Set(head.map((a) => a.name));
    const strays = selected ? matching.filter((a) => selected.has(a.name) && !inHead.has(a.name)) : [];
    return head.concat(strays);
  }, [matching, selected]);
  const hiddenCount = matching.length - shownArtists.length;
  const selCount = selected ? selected.size : 0;

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Artist trajectory</h3>
          <div style={cardTools}>
            <div className="seg" role="group">
              {[["lines", "Lines"], ["stream", "Stream"]].map(([v, l]) => (
                <button key={v} aria-pressed={mode === v} onClick={() => setMode(v)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">monthly plays · {artists.length} artists</span>
          </div>
        </div>
        <p style={cardDesc}>Compare how your listening shifted month to month. <b>Lines</b> plot plays per month per artist; <b>Stream</b> stacks them into a flowing river. Filter by name, or <b>Shuffle</b> for a random handful from the tail.</p>
        <div className="artist-picker">
          <div className="ap-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter artists…" />
            <span className="ap-count">{selCount} shown</span>
            <button className="ap-reset" onClick={shuffleArtists} title={`Pick ${TRAJECTORY_SEED} at random from all ${artists.length} artists`}>Shuffle</button>
            <button className="ap-reset" onClick={resetTop} title={`Reset to the ${TRAJECTORY_SEED} most-played`}>Top {TRAJECTORY_SEED}</button>
          </div>
          <div className="ap-chips">
            {shownArtists.map((a) => (
              <button key={a.name} className={"ap-chip" + (selected && selected.has(a.name) ? " on" : "")} onClick={() => toggleArtist(a.name)}>
                <span className="ap-dot"></span>{a.name}
              </button>
            ))}
          </div>
          {hiddenCount > 0 && (
            <p className="ap-more">{hiddenCount} more — type to narrow the list.</p>
          )}
        </div>
        <div className="echart-wrap tall" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
      </div>
    </section>
  );
}

/* ── Trajectory page: the two time-oriented views behind one toggle (#31) ──
   Timeline used to be its own nav entry; both views answer "how did this move
   over time", so they share a page.

   The hidden view is unmounted, not merely handed active={false}: useEChart
   disposes on unmount, so the toggle never leaves a second idle ECharts
   instance holding a canvas — which is the cost the `active` prop exists to
   avoid in the first place. Each view keeps its fetched payload and its own
   controls in module state (__timelineState / __trajectoryState), so coming
   back to a view is a re-render rather than a re-fetch. */
function TrajectoryPage({ active, refreshVersion = 0 }) {
  const [view, setView] = useState("overall");
  return (
    <div>
      <div className="page-intro">
        <h2 className="page-title">Trajectory</h2>
        <p className="page-lede">How your listening moved over time — the whole history at a glance, or month by month for the artists you choose.</p>
      </div>
      <div className="slicer">
        <span className="slicer-label">View</span>
        <div className="seg" role="group" aria-label="Trajectory view">
          {[["overall", "Overall"], ["artist", "By artist"]].map(([v, l]) => (
            <button key={v} aria-pressed={view === v} onClick={() => setView(v)}>{l}</button>
          ))}
        </div>
        <span className="slicer-note">
          {view === "overall"
            ? <>Every scrobble counted, by <b>year</b> or <b>month</b></>
            : <>Monthly plays for the artists you pick</>}
        </span>
      </div>
      {view === "overall"
        ? <TimelineChart active={active} refreshVersion={refreshVersion} />
        : <ArtistTrajectory active={active} refreshVersion={refreshVersion} />}
    </div>
  );
}

/* ── Listening Map: calendar heatmap (per year) + hour×day grid ── */
function ListeningMap({ active, refreshVersion = 0 }) {
  const calRef  = useRef(null);
  const hwRef   = useRef(null);
  const calChart = useEChart(calRef);
  const hwChart  = useEChart(hwRef);
  const [years, setYears] = useState([]);
  const [year, setYear] = useState(null);
  const [loading, setLoading] = useState(true);
  const [mapData, setMapData] = useState(null);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    // Clicking 2023 then 2024 quickly could draw 2024 under a "2023" selection
    // when the responses crossed (#113); only the newest request may land.
    let stale = false;
    const q = year != null ? `?year=${year}` : "";
    fetch("/api/time-of-day" + q)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((data) => {
        if (stale) return;
        // First pass: learn the available years, default to the most recent, and
        // let the effect re-run with that filter.
        if (year == null) {
          if (data.years && data.years.length) { setYears(data.years); setYear(data.years[data.years.length - 1]); }
          else setLoading(false);
          return;
        }
        // Every response carries the year list, so a year a sync just added
        // appears without a separate request.
        if (data.years && data.years.length) setYears(data.years);
        setLoading(false);
        setMapData({ year, data });
      })
      .catch(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [active, year, refreshVersion]);

  useEffect(() => {
    if (!active || !mapData) return;
    const { year, data } = mapData;
    const c = themeVars();
    const colorScale = ["#191527", "#4c2f95", "#7c4ddb", c.accent];

    // Calendar heatmap — one large, legible year
    if (calChart.current) {
      calChart.current.resize();
      const max = data.calendar.length ? Math.max(...data.calendar.map((d) => d[1])) : 1;
      calChart.current.setOption({
        backgroundColor: "transparent",
        tooltip: { formatter: (p) => `${p.data[0]} — ${p.data[1]} plays`, backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
        visualMap: { min: 0, max, type: "continuous", orient: "horizontal", left: "center", bottom: 6,
          itemWidth: 14, itemHeight: 120, inRange: { color: colorScale }, textStyle: { color: c.muted } },
        calendar: [{
          top: 30, left: 42, right: 18, range: String(year),
          cellSize: ["auto", 18],
          itemStyle: { color: "#14141b", borderWidth: 3, borderColor: c.panel, borderRadius: 3 },
          splitLine: { show: false },
          yearLabel: { show: false },
          dayLabel: { color: c.muted, fontSize: 10, firstDay: 1, nameMap: ["Su","Mo","Tu","We","Th","Fr","Sa"] },
          monthLabel: { color: c.text2, fontSize: 11, fontWeight: 600 },
        }],
        series: [{ type: "heatmap", coordinateSystem: "calendar", data: data.calendar,
          itemStyle: { borderRadius: 3, borderWidth: 3, borderColor: c.panel } }],
      }, true);
    }

    // Hour × weekday heatmap (full history — denser = cleaner pattern)
    if (hwChart.current && data?.hour_weekday?.length) {
      hwChart.current.resize();
      const days  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
      const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
      const max   = Math.max(...data.hour_weekday.map((d) => d[2]));
      hwChart.current.setOption({
        backgroundColor: "transparent",
        tooltip: {
          formatter: (p) => { const [dow,h,n] = p.data; return `${days[dow]} ${hours[h]}: ${n} plays`; },
          backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
        },
        grid: { top: 12, bottom: 40, left: 48, right: 12 },
        xAxis: { type: "category", data: days, axisLabel: { color: c.muted },
          axisLine: { lineStyle: { color: c.line } }, splitArea: { show: true } },
        yAxis: { type: "category", data: hours, axisLabel: { color: c.muted, fontSize: 9 },
          axisLine: { lineStyle: { color: c.line } }, splitArea: { show: true } },
        visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0,
          inRange: { color: colorScale }, textStyle: { color: c.muted } },
        series: [{ type: "heatmap", data: data.hour_weekday.map(([h, dow, n]) => [dow, h, n]),
          itemStyle: { borderRadius: 2 },
          label: { show: false }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,.5)" } } }],
      }, true);
    }
  }, [active, mapData, calChart.current, hwChart.current]);

  return (
    <section className="block">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Listening calendar</h3>
            <div style={cardTools}>
              {years.length > 1 && (
                <div className="seg seg-sm" role="group" aria-label="Calendar year">
                  {years.map((y) => (
                    <button key={y} aria-pressed={year === y} onClick={() => setYear(y)}>{y}</button>
                  ))}
                </div>
              )}
              <span className="card-meta">plays per day{year ? ` · ${year}` : ""}</span>
            </div>
          </div>
          {loading && <ChartLoading height={220} />}
          <div ref={calRef} className="echart-wrap cal" style={{ display: loading ? "none" : "block" }} />
        </div>
        <div className="card">
          <div className="card-head norule"><h3 className="card-title">Hour × weekday</h3><span className="card-meta">play density · all time</span></div>
          {loading && <ChartLoading height={320} />}
          <div ref={hwRef} style={{ width: "100%", height: 360, display: loading ? "none" : "block" }} />
        </div>
      </div>
    </section>
  );
}

/* ── Audio Features: scatter + histograms ── */
function AudioFeaturesChart({ active, refreshVersion = 0 }) {
  const scRef   = useRef(null);
  const histRef = useRef(null);
  const scChart   = useEChart(scRef);
  const histChart = useEChart(histRef);
  const [loading, setLoading] = useState(true);

  const HISTS = [
    { key: "energy",       label: "Energy",      color: "#e040fb" },
    { key: "valence",      label: "Valence",     color: "#40c4ff" },
    { key: "danceability", label: "Danceability",color: "#69f0ae" },
    { key: "acousticness", label: "Acousticness",color: "#ffab40" },
  ];

  const [afData, setAfData] = useState(null);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    let stale = false;
    fetch("/api/audio-features")
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => {
        if (stale) return;
        setLoading(false);
        setAfData(d);
      })
      .catch(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [active, refreshVersion]);

  useEffect(() => {
    if (!active || !afData) return;
    const { scatter, histograms } = afData;
    const c = themeVars();

    if (scChart.current && scatter?.length) {
      const maxP = Math.max(...scatter.map((d) => d.play_count || 1));
      scChart.current.setOption({
        backgroundColor: "transparent",
        tooltip: {
          formatter: (p) => `<b>${escapeHtml(p.data.name)}</b><br>Energy: ${p.data.value[0].toFixed(2)}<br>Valence: ${p.data.value[1].toFixed(2)}<br>Plays: ${p.data.value[2]}`,
          backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
        },
        grid: { top: 24, bottom: 52, left: 64, right: 20 },
        xAxis: { name: "Energy", nameLocation: "middle", nameGap: 30,
          type: "value", min: 0, max: 1,
          nameTextStyle: { color: c.text2, fontSize: 12, fontWeight: 500 }, axisLabel: { color: c.muted },
          splitLine: { lineStyle: { color: c.line, type: "dashed" } } },
        yAxis: { name: "Valence", nameLocation: "middle", nameGap: 42, nameRotate: 90,
          type: "value", min: 0, max: 1,
          nameTextStyle: { color: c.text2, fontSize: 12, fontWeight: 500 }, axisLabel: { color: c.muted },
          splitLine: { lineStyle: { color: c.line, type: "dashed" } } },
        series: [{
          type: "scatter",
          data: scatter.map((d) => ({
            value: [d.energy, d.valence, d.play_count],
            name: `${d.artist} — ${d.track}`,
            symbolSize: Math.max(4, Math.sqrt((d.play_count || 1) / maxP) * 18),
          })),
          itemStyle: { color: c.accent, opacity: 0.6 },
          emphasis: { itemStyle: { opacity: 1 } },
        }],
      });
    }

    if (histChart.current && histograms) {
      const feats = HISTS.filter((f) => histograms[f.key]?.length);
      if (!feats.length) return;
      const cols  = feats.length;
      const gridW = Math.floor(100 / cols);
      histChart.current.setOption({
        backgroundColor: "transparent",
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
        title: feats.map((f, i) => ({
          text: f.label, textStyle: { color: c.text2, fontSize: 12, fontWeight: "normal" },
          left: `${i * gridW + gridW / 2}%`, top: 8, textAlign: "center",
        })),
        grid: feats.map((_, i) => ({ left: `${i * gridW + 1}%`, width: `${gridW - 2}%`, top: 36, bottom: 30 })),
        xAxis: feats.map((f, i) => ({
          gridIndex: i, type: "category",
          data: histograms[f.key].map((b) => b.bin_start.toFixed(1)),
          axisLabel: { color: c.muted, fontSize: 9, rotate: 45 },
          axisLine: { lineStyle: { color: c.line } },
        })),
        yAxis: feats.map((_, i) => ({
          gridIndex: i, type: "value",
          axisLabel: { show: i === 0, color: c.muted, fontSize: 10 },
          splitLine: { lineStyle: { color: c.line, type: "dashed" } },
        })),
        series: feats.map((f, i) => ({
          type: "bar", xAxisIndex: i, yAxisIndex: i,
          data: histograms[f.key].map((b) => b.count),
          itemStyle: { color: f.color, opacity: 0.85, borderRadius: [2, 2, 0, 0] },
        })),
      });
    }
  }, [active, afData, scChart.current, histChart.current]);

  return (
    <section className="block">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div className="card">
          <div className="card-head norule"><h3 className="card-title">Energy × Valence</h3><span className="card-meta">bubble = play count</span></div>
          <p style={cardDesc}>Each bubble is a track, placed by its energy and emotional valence — bigger bubbles are the ones you play most.</p>
          {loading && <ChartLoading />}
          <div ref={scRef} className="echart-wrap" style={{ display: loading ? "none" : "block" }} />
        </div>
        <div className="card">
          <div className="card-head norule"><h3 className="card-title">Feature distributions</h3><span className="card-meta">tracks per bin</span></div>
          {loading && <ChartLoading height={240} />}
          <div ref={histRef} style={{ width: "100%", height: 240, display: loading ? "none" : "block" }} />
        </div>
      </div>
    </section>
  );
}

/* ── Saturation donut (folded into the Coverage page) ── */
function SaturationChart({ active, refreshVersion = 0 }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const TIER_LABELS = { "1": "Tier 1 — Heavy rotation", "2": "Tier 2 — Regular", "3": "Tier 3 — Deep cuts", "unranked": "Unranked" };

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/saturation")
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => { setLoading(false); setData(d); })
      .catch(() => setLoading(false));
  }, [active, refreshVersion]);

  useEffect(() => {
    if (!active || !chart.current || !data || !data.length) return;
    const render = () => {
      if (!chart.current) return;
      chart.current.resize();
      const c = themeVars();
      const narrow = elRef.current && elRef.current.clientWidth < 520;
      const COLORS = { "1": c.accent, "2": c.accent + "aa", "3": c.accent + "55", "unranked": c.line };
      chart.current.setOption({
        backgroundColor: "transparent",
        tooltip: { trigger: "item", formatter: (p) => `${escapeHtml(p.name)}<br>${p.value} tracks (${p.percent}%)`,
          backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
        legend: narrow
          ? { orient: "horizontal", bottom: 0, left: "center", textStyle: { color: c.text2, fontSize: 11 } }
          : { orient: "vertical", right: 12, top: "center", textStyle: { color: c.text2, fontSize: 12 } },
        series: [{
          type: "pie", radius: ["46%", "72%"], center: narrow ? ["50%", "42%"] : ["36%", "50%"],
          avoidLabelOverlap: false, label: { show: false },
          emphasis: { label: { show: true, fontSize: 14, fontWeight: "bold", color: c.text },
            itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,.5)" } },
          data: data.map((d) => ({
            name: TIER_LABELS[d.tier] || d.tier,
            value: d.count,
            itemStyle: { color: COLORS[d.tier] || c.muted },
          })),
        }],
      }, true);
    };
    render();
    window.addEventListener("resize", render);
    return () => window.removeEventListener("resize", render);
    // `chart` is a stable ref, so listing it never re-ran this after a late
    // init; the instance itself does (#113).
  }, [active, data, chart.current]);

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="card-title">Rotation tiers</h3>
        <span className="card-meta">tracks by the tier you assigned their artist</span>
      </div>
      <p style={cardDesc}>Your own rotation judgement, from <code>taste_profile.md</code> — not a data-quality measure. <b>Tier 1</b> = heavy rotation · <b>Tier 2</b> = moderate · <b>Tier 3</b> = special-context only · <b>Unranked</b> = artist not tiered. Tiers were written to stop generated playlists over-using favourite artists; here they simply show how much of your library you have opinions about.</p>
      <div className="echart-wrap" ref={elRef} style={{ display: loading ? "none" : "block", height: 320 }} />
      {loading && <ChartLoading height={320} />}
    </div>
  );
}

/* ── Albums (most-played, with listening spread) ── */
function _albumHue(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}
// Scored by total plays + how evenly listening spreads across the album's tracks.
// In-browser from the loaded `tracks` (no /api/albums round-trip); mirrors
// app/metrics.py::albums.
function computeAlbums(tracks, { top = 60, minTracks = 3 } = {}) {
  if (!tracks || !tracks.length) return [];
  const byAlbum = new Map();
  for (const t of tracks) {
    const album = (t.album || "").trim();
    if (!album) continue;
    const key = (t.artist || "").toLowerCase() + "\x00" + album.toLowerCase();
    let rec = byAlbum.get(key);
    if (!rec) { rec = { plays: [], total: 0, artist: t.artist, album, years: new Set() }; byAlbum.set(key, rec); }
    const p = t.play || 0;
    rec.plays.push(p);
    rec.total += p;
    if (t.release_year) rec.years.add(t.release_year);
  }
  const out = [];
  for (const rec of byAlbum.values()) {
    const n = rec.plays.length;
    if (n < minTracks) continue;
    let spread = 0;
    if (rec.total > 0 && n > 1) {
      // normalized play-count entropy: 1 = perfectly even, →0 = one track dominates
      let entropy = 0;
      for (const p of rec.plays) { if (p > 0) { const s = p / rec.total; entropy -= s * Math.log(s); } }
      spread = Math.round((entropy / Math.log(n)) * 1000) / 1000;
    }
    out.push({
      album: rec.album, artist: rec.artist, track_count: n, plays: rec.total,
      spread, year: rec.years.size ? Math.min(...rec.years) : null,
    });
  }
  out.sort((a, b) => b.plays - a.plays);
  return out.slice(0, top);
}
function AlbumsPage({ active, tracks }) {
  const [sort, setSort] = useState("plays");
  const data = useMemo(() => computeAlbums(tracks), [tracks]);

  const rows = useMemo(() => {
    const a = [...data];
    if (sort === "spread") a.sort((x, y) => y.spread - x.spread || y.plays - x.plays);
    else if (sort === "tracks") a.sort((x, y) => y.track_count - x.track_count || y.plays - x.plays);
    else a.sort((x, y) => y.plays - x.plays);
    return a;
  }, [data, sort]);
  const maxPlays = rows.length ? Math.max(...rows.map((r) => r.plays)) : 1;

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Albums</h3>
          <div style={cardTools}>
            <div className="seg seg-sm" role="group">
              {[["plays", "Plays"], ["spread", "Spread"], ["tracks", "Tracks"]].map(([v, l]) => (
                <button key={v} aria-pressed={sort === v} onClick={() => setSort(v)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">≥3 tracks · top 60</span>
          </div>
        </div>
        <p style={cardDesc}>Albums you actually sat with. <b>Plays</b> totals every track; <b>Spread</b> shows how evenly your listening covered the album — a full bar means you played the whole thing, a short bar means one or two tracks carried it.</p>
        {
          rows.length ? (
            <div className="album-list">
              {rows.map((a, i) => (
                <div className="album-row" key={a.artist + "|" + a.album}>
                  <span className="album-rank num">{i + 1}</span>
                  <span className="album-art" style={{ background: `linear-gradient(135deg, hsl(${_albumHue(a.album)} 52% 44%), hsl(${(_albumHue(a.album) + 42) % 360} 50% 28%))` }}>{(a.album || "?").charAt(0).toUpperCase()}</span>
                  <div className="album-meta">
                    <div className="album-name" title={a.album}>{a.album}</div>
                    <div className="album-artist">{a.artist}{a.year ? ` · ${a.year}` : ""} · {a.track_count} tracks</div>
                  </div>
                  <div className="album-spread" title={`Spread ${Math.round(a.spread * 100)}% — how evenly plays cover the album`}>
                    <div className="album-spread-track"><div className="album-spread-fill" style={{ width: (a.spread * 100) + "%" }}></div></div>
                    <span className="album-spread-val num">{Math.round(a.spread * 100)}%</span>
                  </div>
                  <div className="album-plays">
                    <div className="mini"><span style={{ width: (a.plays / maxPlays * 100) + "%" }}></span></div>
                    <span className="pc num">{a.plays}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty"><div className="big">No albums yet</div><div>Load your library to see album rankings.</div></div>
          )
        }
      </div>
    </section>
  );
}

/* ── Tag Constellation: scenes + focus (#92) ──
   Answers "which tags cluster into scenes in my listening, and what sits next
   to any one tag?". The scenes come from the server (modularity clustering over
   pairing strength). The layout is computed here, once per graph, and then held
   still: the old force sim never stopped, so the framing drifted out of view
   and the page animated forever regardless of prefers-reduced-motion. */

// Hex rather than the oklch() the rest of the app authors in: ECharts derives
// emphasis and blur colours from these, and its colour parser can't read
// oklch(). Eight hues 45° apart at the genre palette's lightness; scenes past
// the eighth share one grey, since a ninth hue would no longer read as distinct.
const SCENE_COLORS = ["#9e8fe9", "#51b67a", "#e47b7c", "#4fa6e9", "#d58c3b", "#00b6be", "#d07ebe", "#a4a537"];
const SCENE_OTHER = "#838592";
const sceneColor = (id) => SCENE_COLORS[id] || SCENE_OTHER;

/* A released tag returns home like it's pulled by a magnet: slow to leave,
   faster as it nears its scene, and it stops dead on contact. Accelerating
   in, rather than easing out, is what reads as attraction. (No overshoot:
   the tween clamps an easing's output at 1, so one would never draw.) */
const magnetEase = (t) => Math.pow(t, 2.2);

const CONST_FIELDS = [["discogs_styles", "Styles"], ["lastfm_tags", "Genres"], ["mood_tags", "Moods"]];
const CONST_MIN_PLAYS = [5, 15, 30, 60];
const CONST_STRENGTHS = [[0, "All"], [0.05, "Weak+"], [0.15, "Medium+"], [0.3, "Strong"]];
const constSymbolSize = (count, maxCount) => 10 + 46 * Math.sqrt(count / Math.max(maxCount, 1));

/* Deterministic two-level layout, in pixels at scale 1. No randomness, so the
   same graph always lands the same way.

   A single force layout over every tag couldn't do this job: hub tags ("rock",
   "pop") pulled nodes across scene lines until the colours bled together, and
   repulsion flung small disconnected scenes to the edge, squeezing everything
   else into a corner. So each scene is laid out as its own compact disc, and
   the discs are then packed, with scenes that share the most pulled together.
   Every colour is one contiguous region, and nothing can drift off alone.

   `aspect` (width / height) shapes the packing to the canvas, so a wide card
   gets a wide constellation instead of a disc with empty margins.

   Returns { pos: [[x, y]...] in node order, rad: [r...], box: [x0, y0, x1, y1] }
   with the box including each node's radius. */
function layoutConstellation(nodes, edges, aspect = 1) {
  const n = nodes.length;
  if (!n) return { pos: [], rad: [], box: [0, 0, 1, 1] };
  const at = new Map(nodes.map((d, i) => [d.tag, i]));
  const maxCount = Math.max(...nodes.map((d) => d.count), 1);
  const rad = nodes.map((d) => constSymbolSize(d.count, maxCount) / 2);
  const sceneOf = nodes.map((d) => d.scene || 0);
  const sceneIds = [...new Set(sceneOf)].sort((a, b) => a - b);
  const slot = new Map(sceneIds.map((s, k) => [s, k]));
  const members = sceneIds.map(() => []);
  nodes.forEach((d, i) => members[slot.get(sceneOf[i])].push(i));  // most-played first
  const links = edges
    .map((e) => [at.get(e.source), at.get(e.target), e.strength == null ? 1 : e.strength])
    .filter(([a, b]) => a !== undefined && b !== undefined);

  // 1. Inside each scene: biggest tag at the centre on a golden-angle spiral,
  //    relaxed by the scene's own links, then strictly de-overlapped.
  const local = new Array(n);
  const discR = [];
  members.forEach((m, k) => {
    const meanR = m.reduce((s, i) => s + rad[i], 0) / m.length;
    const K = meanR * 2 + 8;
    m.forEach((i, j) => {
      const r = K * 0.55 * Math.sqrt(j), t = j * 2.399963;
      local[i] = [r * Math.cos(t), r * Math.sin(t)];
    });
    const inner = links.filter(([a, b]) => slot.get(sceneOf[a]) === k && slot.get(sceneOf[b]) === k);
    for (let it = 0; it < 160; it++) {
      const temp = K * 0.6 * (1 - it / 160) + 0.2;
      const dx = new Float64Array(n), dy = new Float64Array(n);
      for (let x = 0; x < m.length; x++) {
        for (let y = x + 1; y < m.length; y++) {
          const i = m[x], j = m[y];
          let ex = local[i][0] - local[j][0], ey = local[i][1] - local[j][1];
          let d = Math.sqrt(ex * ex + ey * ey);
          if (d < 1e-3) { ex = 0.01 * (y - x); ey = 0.01; d = Math.sqrt(ex * ex + ey * ey); }
          const gap = rad[i] + rad[j] + 3;
          const f = (K * K) / d * 0.5 + (d < gap ? (gap - d) * 2 : 0);
          dx[i] += (ex / d) * f; dy[i] += (ey / d) * f;
          dx[j] -= (ex / d) * f; dy[j] -= (ey / d) * f;
        }
      }
      for (const [a, b, s] of inner) {
        const ex = local[a][0] - local[b][0], ey = local[a][1] - local[b][1];
        const d = Math.sqrt(ex * ex + ey * ey) || 0.01;
        const f = (d * d / K) * s;
        dx[a] -= (ex / d) * f; dy[a] -= (ey / d) * f;
        dx[b] += (ex / d) * f; dy[b] += (ey / d) * f;
      }
      for (const i of m) {
        dx[i] -= local[i][0] * 0.35; dy[i] -= local[i][1] * 0.35;  // keeps the disc round and tight
        const mag = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]);
        if (mag > 0) { const st = Math.min(mag, temp); local[i][0] += (dx[i] / mag) * st; local[i][1] += (dy[i] / mag) * st; }
      }
    }
    separate(m, local, rad, 2);
    // Re-centre on the disc's own middle so packing works with true radii.
    let cx = 0, cy = 0;
    for (const i of m) { cx += local[i][0]; cy += local[i][1]; }
    cx /= m.length; cy /= m.length;
    let R = 0;
    for (const i of m) {
      local[i][0] -= cx; local[i][1] -= cy;
      R = Math.max(R, Math.hypot(local[i][0], local[i][1]) + rad[i]);
    }
    discR.push(R + 6);
  });

  // 2. Pack the discs: shared strength pulls scenes together, a hard
  //    constraint keeps them apart, and a weak pull to the middle keeps the
  //    whole thing round.
  const S = sceneIds.length;
  const aff = Array.from({ length: S }, () => new Float64Array(S));
  for (const [a, b, s] of links) {
    const ka = slot.get(sceneOf[a]), kb = slot.get(sceneOf[b]);
    if (ka !== kb) { aff[ka][kb] += s; aff[kb][ka] += s; }
  }
  const maxAff = Math.max(1e-9, ...aff.map((row) => Math.max(...row)));
  const C = sceneIds.map((_, k) => {
    if (k === 0) return [0, 0];
    const r = (discR[0] + discR[k]) * 0.9 * Math.sqrt(k), t = k * 2.399963;
    return [r * Math.cos(t), r * Math.sin(t)];
  });
  for (let it = 0; it < 400; it++) {
    const step = 0.08 * (1 - it / 400) + 0.01;
    for (let a = 0; a < S; a++) {
      for (let b = a + 1; b < S; b++) {
        const ex = C[b][0] - C[a][0], ey = C[b][1] - C[a][1];
        const d = Math.sqrt(ex * ex + ey * ey) || 0.01;
        const gap = discR[a] + discR[b] + 10;
        if (aff[a][b] > 0 && d > gap) {
          const pull = (d - gap) * step * (0.2 + 0.8 * aff[a][b] / maxAff);
          const wa = discR[b] / (discR[a] + discR[b]);  // the smaller disc moves more
          C[a][0] += (ex / d) * pull * wa; C[a][1] += (ey / d) * pull * wa;
          C[b][0] -= (ex / d) * pull * (1 - wa); C[b][1] -= (ey / d) * pull * (1 - wa);
        }
      }
    }
    const sa = Math.sqrt(Math.max(aspect, 0.25));
    for (let a = 0; a < S; a++) { C[a][0] *= 1 - (step * 0.15) / sa; C[a][1] *= 1 - step * 0.15 * sa; }
    separateDiscs(C, discR, 10);
  }
  for (let pass = 0; pass < 60; pass++) if (!separateDiscs(C, discR, 10)) break;

  const pos = nodes.map((_, i) => {
    const k = slot.get(sceneOf[i]);
    return [C[k][0] + local[i][0], C[k][1] + local[i][1]];
  });
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  pos.forEach(([x, y], i) => {
    x0 = Math.min(x0, x - rad[i]); x1 = Math.max(x1, x + rad[i]);
    y0 = Math.min(y0, y - rad[i]); y1 = Math.max(y1, y + rad[i]);
  });
  return { pos, rad, box: [x0, y0, x1, y1] };
}

/* Push overlapping circles (indices `m`) apart until none overlap by more
   than `pad`. */
function separate(m, p, rad, pad) {
  for (let pass = 0; pass < 80; pass++) {
    let moved = false;
    for (let x = 0; x < m.length; x++) {
      for (let y = x + 1; y < m.length; y++) {
        const i = m[x], j = m[y];
        const ex = p[i][0] - p[j][0], ey = p[i][1] - p[j][1];
        const d = Math.sqrt(ex * ex + ey * ey) || 0.01;
        const gap = rad[i] + rad[j] + pad;
        if (d < gap - 0.01) {
          const push = (gap - d) / 2;
          p[i][0] += (ex / d) * push; p[i][1] += (ey / d) * push;
          p[j][0] -= (ex / d) * push; p[j][1] -= (ey / d) * push;
          moved = true;
        }
      }
    }
    if (!moved) return;
  }
}

function separateDiscs(C, R, pad) {
  let moved = false;
  for (let a = 0; a < C.length; a++) {
    for (let b = a + 1; b < C.length; b++) {
      let ex = C[b][0] - C[a][0], ey = C[b][1] - C[a][1];
      let d = Math.sqrt(ex * ex + ey * ey);
      if (d < 1e-3) { ex = 0.01 * (b - a); ey = 0.01; d = Math.sqrt(ex * ex + ey * ey); }
      const gap = R[a] + R[b] + pad;
      if (d < gap - 0.01) {
        const push = gap - d, wa = R[b] / (R[a] + R[b]);
        C[a][0] -= (ex / d) * push * wa; C[a][1] -= (ey / d) * push * wa;
        C[b][0] += (ex / d) * push * (1 - wa); C[b][1] += (ey / d) * push * (1 - wa);
        moved = true;
      }
    }
  }
  return moved;
}

function TagConstellation({ active, refreshVersion = 0 }) {
  const elRef = useRef(null);
  const panelRef = useRef(null);
  const chart = useEChart(elRef);
  const [field, setField] = useState("discogs_styles");
  const [minPlays, setMinPlays] = useState(15);
  // Strong pairings only by default: every weaker tether made the picture a
  // hairball again, and the reader can always ask for more.
  const [minStrength, setMinStrength] = useState(0.3);
  const [loading, setLoading] = useState(true);
  const [graph, setGraph] = useState(null);
  const [focus, setFocus] = useState(null);           // a tag
  const [focusScene, setFocusScene] = useState(null); // a scene id, or "other"
  const [detail, setDetail] = useState(null);
  const [query, setQuery] = useState("");
  const [viewKey, setViewKey] = useState(0);          // bumps to reset pan/zoom
  const layoutRef = useRef({ graph: null, lay: null });
  const zoomedRef = useRef(false);

  // Moods has 14 tags, so a play threshold only hides real ones.
  const effMinPlays = field === "mood_tags" ? 1 : minPlays;

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    let stale = false;
    fetch(`/api/tag-graph?field=${field}&min_count=${effMinPlays}&min_strength=${minStrength}`)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((g) => {
        if (stale) return;
        setLoading(false);
        setGraph(g);
      })
      .catch(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [active, field, effMinPlays, minStrength, refreshVersion]);

  // A new field or threshold can drop the focused tag; never focus a ghost.
  useEffect(() => {
    if (focus && graph && !graph.nodes.some((d) => d.tag === focus)) setFocus(null);
  }, [graph]);
  useEffect(() => { setFocus(null); setFocusScene(null); }, [field]);

  useEffect(() => {
    if (!focus) { setDetail(null); return; }
    let stale = false;
    fetch(`/api/tag-detail?field=${field}&tag=${encodeURIComponent(focus)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => { if (!stale) setDetail(d); })
      .catch(() => { if (!stale) setDetail(null); });
    return () => { stale = true; };
  }, [focus, field]);

  useEffect(() => {
    if (!focus) return;
    const onKey = (e) => { if (e.key === "Escape") setFocus(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focus]);

  const scenes = (graph && graph.scenes) || [];
  const sceneName = (id) => (scenes[id] && scenes[id].name) || "";
  const inFocusScene = (sid) => focusScene == null
    || (focusScene === "other" ? sid >= SCENE_COLORS.length : sid === focusScene);

  // Drawn in an effect keyed on the instance as well as the data, so a graph
  // that arrived before ECharts finished loading is drawn once it does (#113).
  useEffect(() => {
    if (!active || !graph || !chart.current) return;
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    let rafA = 0, rafB = 0;

    // `spring`: the redraw that pulls a released tag home. Every tag is handed
    // its home position on every draw; only the dragged one is away from it,
    // so only it moves.
    const draw = (spring = false) => {
      const inst = chart.current;
      if (!inst) return;
      // Resize only when the canvas really changed size. An ECharts resize
      // re-renders with animation off and re-reads every tag's position, which
      // would snap a released tag home before its pull-back could play.
      const box = elRef.current;
      if (box && (box.clientWidth !== inst.getWidth() || box.clientHeight !== inst.getHeight())) inst.resize();
      const w = inst.getWidth(), h = inst.getHeight();
      if (w < 10 || h < 10) return;
      // The layout belongs to the graph, not to the focus: focusing a tag must
      // never move anything, or the reader loses their place.
      const fresh = layoutRef.current.graph !== graph;
      if (fresh) layoutRef.current = { graph, lay: layoutConstellation(nodes, edges, w / h), fit: null };
      const L = layoutRef.current;
      const { pos, rad } = L.lay;
      // Fit once per layout and canvas size, never per update. ECharts re-fits
      // a "none" layout to its nodes' bounding box on every setOption, so a
      // dragged tag that widened the box rescaled everything on the next focus
      // change. Two invisible anchors at the layout's corners pin that box,
      // and the series box is sized to it times k, which makes ECharts' own
      // fit exactly k. Positions and circles scale together (ECharts draws
      // symbols at their pixel size), so the fit can't make circles overlap.
      if (!L.fit || L.fit.w !== w || L.fit.h !== h) {
        const box = [Infinity, Infinity, -Infinity, -Infinity];
        pos.forEach(([x, y], i) => {
          box[0] = Math.min(box[0], x - rad[i]); box[2] = Math.max(box[2], x + rad[i]);
          box[1] = Math.min(box[1], y - rad[i]); box[3] = Math.max(box[3], y + rad[i]);
        });
        const bw = Math.max(box[2] - box[0], 1), bh = Math.max(box[3] - box[1], 1);
        const pad = 28;
        const k = Math.min(1.5, (w - 2 * pad) / bw, (h - 2 * pad) / bh);
        L.fit = {
          w, h, k,
          anchors: [[box[0], box[1]], [box[2], box[3]]],
          seriesBox: { left: (w - k * bw) / 2, top: (h - k * bh) / 2, width: k * bw, height: k * bh },
        };
      }
      const { k, anchors, seriesBox } = L.fit;

      const c = themeVars();
      const reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      const near = new Set();
      if (focus) {
        near.add(focus);
        for (const e of edges) {
          if (e.source === focus) near.add(e.target);
          if (e.target === focus) near.add(e.source);
        }
      }
      const lit = (d) => (focus ? near.has(d.tag) : inFocusScene(d.scene || 0));

      const data = nodes.map((d, i) => {
        const on = lit(d);
        return {
          name: d.tag, value: d.count, scene: d.scene || 0,
          x: pos[i][0], y: pos[i][1],
          symbolSize: rad[i] * 2 * k,
          itemStyle: {
            color: sceneColor(d.scene || 0), opacity: on ? 0.95 : 0.2,
            borderColor: d.tag === focus ? c.text : "transparent", borderWidth: d.tag === focus ? 2 : 0,
          },
          // With nothing focused every tag is labelled and hideOverlap keeps
          // the biggest legible; zooming in reveals the rest. A focus labels
          // its whole neighbourhood and nothing else.
          label: { show: on, fontWeight: d.tag === focus ? 700 : 400 },
        };
      });
      const sceneOf = new Map(nodes.map((d) => [d.tag, d.scene || 0]));
      const maxW = edges.reduce((m, e) => Math.max(m, e.strength == null ? 1 : e.strength), 0.01);
      const links = edges.map((e) => {
        const s = e.strength == null ? 1 : e.strength;
        const touches = focus ? (e.source === focus || e.target === focus) : true;
        const sceneOk = focus ? touches : inFocusScene(sceneOf.get(e.source) || 0);
        // Links between scenes are the bridges between them, so they get a
        // visibility floor and a little extra weight rather than fading out.
        // A focus brings its cross-scene links up to full strength.
        const across = sceneOf.get(e.source) !== sceneOf.get(e.target);
        const base = 0.12 + 0.4 * (s / maxW);
        return {
          source: e.source, target: e.target, value: e.weight, strength: s,
          lineStyle: {
            width: 0.5 + 2.5 * (s / maxW) + (across ? 0.6 : 0),
            opacity: (touches && sceneOk) ? (focus ? 0.75 : (across ? Math.max(0.32, base) : base)) : 0.03,
            color: "source", curveness: 0.12,
          },
        };
      });

      const option = {
        backgroundColor: "transparent",
        animation: !reduced,
        animationDurationUpdate: reduced ? 0 : (spring ? 480 : 300),
        animationEasingUpdate: spring ? magnetEase : "cubicOut",
        tooltip: {
          formatter(p) {
            if (p.dataType === "edge") {
              const s = p.data.strength != null ? ` · pairing ${Math.round(p.data.strength * 100)}%` : "";
              return `<b>${escapeHtml(p.data.source)}</b> ↔ <b>${escapeHtml(p.data.target)}</b><br>${p.data.value} plays together${s}`;
            }
            const sn = sceneName(p.data.scene);
            return `<b>${escapeHtml(p.data.name)}</b><br>${p.data.value} plays${sn ? ` · ${escapeHtml(sn)} scene` : ""}`;
          },
          backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
        },
        series: [{
          id: "constellation", type: "graph", layout: "none", ...seriesBox,
          // Dragging moves only the grabbed tag: there is no physics, so its
          // links stretch instead of hauling the whole graph along, and it
          // follows the pointer with no pull until it's let go. Panning is off
          // until the reader zooms in: at the fitted zoom there is nothing off
          // screen, and a near-miss on a node used to grab the background.
          roam: zoomedRef.current ? true : "scale", draggable: true,
          scaleLimit: { min: 0.5, max: 6 },
          labelLayout: { hideOverlap: true },
          label: { position: "right", distance: 4, fontSize: 11, color: c.text, formatter: "{b}" },
          emphasis: { focus: focus ? "none" : "adjacency", label: { show: true }, lineStyle: { opacity: 0.85 } },
          data: data.concat(anchors.map(([x, y], j) => ({
            name: `\u0000anchor${j}`, x, y, symbolSize: 0, draggable: false,
            itemStyle: { opacity: 0 }, label: { show: false },
            tooltip: { show: false }, emphasis: { disabled: true },
          }))),
          edges: links,
        }],
      };
      // The first frame is the final shape: the layout is complete and fitted
      // before anything is drawn, so there is no zoomed-in start followed by a
      // re-fit (#92). A new graph resets the camera; a focus change or resize
      // keeps the reader's pan and zoom, which a not-merge setOption drops.
      if (fresh) { zoomedRef.current = false; option.series[0].roam = "scale"; inst.setOption(option, true); }
      else inst.setOption(option);
      // A hover highlight survives a merge, so a node hovered before a focus
      // change could stay blurred under the new one; clear it each time.
      if (inst.dispatchAction) inst.dispatchAction({ type: "downplay", seriesIndex: 0 });

      if (inst.off) { inst.off("click"); inst.off("graphroam"); inst.off("mousedown"); }
      inst.on("mousedown", (p) => {
        if (p.dataType === "node" && p.dataIndex < nodes.length) dragIdx = p.dataIndex;
      });
      inst.on("click", (p) => {
        if (p.dataType === "node" && !String(p.data.name).startsWith("\u0000")) {
          setFocus((f) => (f === p.data.name ? null : p.data.name));
        }
      });
      inst.on("graphroam", () => {
        const z = (inst.getOption().series[0] || {}).zoom || 1;
        const zoomed = z > 1.02;
        if (zoomed !== zoomedRef.current) {
          zoomedRef.current = zoomed;
          inst.setOption({ series: [{ id: "constellation", roam: zoomed ? true : "scale" }] });
        }
      });
    };

    // The wrap flips from display:none when loading ends, and React hasn't
    // painted yet; two frames give the canvas real dimensions to lay out in.
    rafA = requestAnimationFrame(() => { rafB = requestAnimationFrame(() => draw()); });

    // On release, a tag that was dragged off its spot is pulled back home.
    // Capture phase, on window, so a release outside the canvas still counts;
    // the redraw waits a frame so ECharts has finished its own drag-end.
    let dragIdx = null, rafS = 0;
    const onRelease = () => {
      if (dragIdx == null) return;
      const i = dragIdx;
      dragIdx = null;
      const inst = chart.current;
      const lay = layoutRef.current.lay;
      const sd = inst && inst.getModel().getSeriesByIndex(0)?.getData();
      const at = sd && sd.getItemLayout(i);
      if (!at || !lay || !lay.pos[i]) return;
      if (Math.hypot(at[0] - lay.pos[i][0], at[1] - lay.pos[i][1]) < 0.5) return;  // a click, not a drag
      rafS = requestAnimationFrame(() => draw(true));
    };
    window.addEventListener("mouseup", onRelease, true);
    window.addEventListener("touchend", onRelease, true);
    // Re-fit whenever the canvas itself changes size (a scrollbar appearing,
    // the wrap leaving display:none, the sidebar collapsing), not only on a
    // window resize. A canvas that was still 0×0 at the first frame draws as
    // soon as it has a size, instead of staying blank until something else
    // happens to redraw it.
    let ro;
    if (window.ResizeObserver && elRef.current) {
      ro = new ResizeObserver(() => draw());
      ro.observe(elRef.current);
    }
    return () => {
      cancelAnimationFrame(rafA); cancelAnimationFrame(rafB); cancelAnimationFrame(rafS);
      if (ro) ro.disconnect();
      window.removeEventListener("mouseup", onRelease, true);
      window.removeEventListener("touchend", onRelease, true);
    };
  }, [active, graph, chart.current, focus, focusScene]);

  // Reset view: a new graph object counts as a fresh layout, so the next draw
  // replaces the option outright and restores the default pan and zoom. The
  // layout is deterministic, so nothing moves.
  useEffect(() => {
    if (viewKey) setGraph((g) => g && { ...g });
  }, [viewKey]);

  useEffect(() => {
    if (focus && detail && panelRef.current && panelRef.current.scrollIntoView) {
      panelRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [detail]);

  const nodes = (graph && graph.nodes) || [];
  const focusNode = focus ? nodes.find((d) => d.tag === focus) : null;
  const pickTag = (value) => {
    const hit = nodes.find((d) => d.tag.toLowerCase() === value.trim().toLowerCase());
    if (hit) { setFocus(hit.tag); setFocusScene(null); setQuery(""); }
  };
  const named = scenes.slice(0, SCENE_COLORS.length);
  const rest = scenes.slice(SCENE_COLORS.length);
  const DrillPanel = window.DrillPanel;

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Tag constellation</h3>
          <div style={cardTools}>
            <div className="seg" role="group" aria-label="Tag field">
              {CONST_FIELDS.map(([v, l]) => (
                <button key={v} aria-pressed={field === v} onClick={() => setField(v)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">{nodes.length} tags · {(graph && graph.edges ? graph.edges.length : 0)} links · {scenes.length} scenes</span>
          </div>
        </div>
        <p style={cardDesc}><b>Which tags cluster into scenes in your listening, and what sits next to any one tag?</b> Tags heard on the same tracks pull together, and colour marks the scenes that clustering finds. Click a tag to see what it pairs with and the tracks behind it. Drag a tag to move it; scroll to zoom, and once zoomed in, drag the background to pan.</p>

        <div className="artist-picker">
          <div className="ap-search">
            {/* Own wrapper so the icon stays inside the input when the row wraps. */}
            <span className="const-find">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
              <input value={query} list="const-tags" placeholder="Find a tag…" aria-label="Find a tag"
                onChange={(e) => { setQuery(e.target.value); pickTag(e.target.value); }}
                onKeyDown={(e) => { if (e.key === "Enter") pickTag(query); }} />
            </span>
            <datalist id="const-tags">{nodes.map((d) => <option key={d.tag} value={d.tag} />)}</datalist>
            {field !== "mood_tags" && (
              <div className="seg seg-sm" role="group" aria-label="Minimum plays per tag" title="Hide tags with fewer plays than this">
                {CONST_MIN_PLAYS.map((v) => (
                  <button key={v} aria-pressed={minPlays === v} onClick={() => setMinPlays(v)}>{v}+ plays</button>
                ))}
              </div>
            )}
            <div className="seg seg-sm" role="group" aria-label="Minimum link strength" title="Hide pairings weaker than this">
              {CONST_STRENGTHS.map(([v, l]) => (
                <button key={v} aria-pressed={minStrength === v} onClick={() => setMinStrength(v)}>{l}</button>
              ))}
            </div>
            <button className="ap-reset" onClick={() => { setFocus(null); setFocusScene(null); setViewKey((k) => k + 1); }}>Reset view</button>
          </div>
          <div className="ap-chips const-scenes" role="group" aria-label="Scenes">
            {named.map((s) => (
              <button key={s.id} className={"ap-chip" + (focusScene === s.id ? " on" : "")}
                aria-pressed={focusScene === s.id}
                onClick={() => { setFocus(null); setFocusScene((f) => (f === s.id ? null : s.id)); }}>
                <span className="ap-dot" style={{ background: sceneColor(s.id) }}></span>{s.name}
                <span className="ap-count">{s.tags}</span>
              </button>
            ))}
            {rest.length > 0 && (
              <button className={"ap-chip" + (focusScene === "other" ? " on" : "")} aria-pressed={focusScene === "other"}
                onClick={() => { setFocus(null); setFocusScene((f) => (f === "other" ? null : "other")); }}>
                <span className="ap-dot" style={{ background: SCENE_OTHER }}></span>{rest.length} smaller scenes
                <span className="ap-count">{rest.reduce((n, s) => n + s.tags, 0)}</span>
              </button>
            )}
          </div>
          <p className="const-key">
            <span><i className="ck-dot"></i><b>Colour</b> is the scene: tags you tend to hear on the same tracks.</span>
            <span><i className="ck-size"></i><b>Size</b> is plays.</span>
            <span><i className="ck-line"></i><b>Lines</b> join tags heard together. Thicker means a stronger pairing: shared plays relative to each tag's total.</span>
          </p>
        </div>

        <div className="echart-wrap tall" ref={elRef} style={{ display: loading || !nodes.length ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
        {!loading && graph && !nodes.length && (
          <div className="empty"><div className="big">No tags reach {effMinPlays} plays</div><div>Lower the play threshold to bring them back.</div></div>
        )}

        {focus && (
          <div className="const-panel" ref={panelRef}>
            <div className="const-nbrs">
              <span className="const-nbrs-title">Sits next to <b>{focus}</b>{focusNode ? ` (${sceneName(focusNode.scene)} scene)` : ""}</span>
              <div className="ap-chips">
                {(detail ? detail.neighbours : []).map((nb) => {
                  const nn = nodes.find((d) => d.tag === nb.tag);
                  return (
                    <button key={nb.tag} className="ap-chip" disabled={!nn} title={nn ? `Focus ${nb.tag}` : `${nb.tag} is below the current play threshold`}
                      onClick={() => nn && setFocus(nb.tag)}>
                      <span className="ap-dot" style={{ background: nn ? sceneColor(nn.scene) : SCENE_OTHER }}></span>{nb.tag}
                      <span className="ap-count">{Math.round(nb.strength * 100)}%</span>
                    </button>
                  );
                })}
              </div>
            </div>
            {DrillPanel && detail && (
              <DrillPanel label={focus} slice={detail} onClose={() => setFocus(null)} views={true} />
            )}
          </div>
        )}
      </div>
    </section>
  );
}

/* ── Forgotten Favorites sparkline (pure SVG, no ECharts) ── */
function FfSparkline({ sparkline, peakYear, recentStart }) {
  if (!sparkline || !sparkline.length) return null;
  const W = 120, H = 38, GAP = 2;
  const n = sparkline.length;
  const bw = Math.max(3, Math.floor((W - GAP * (n - 1)) / n));
  const totalW = n * bw + (n - 1) * GAP;
  const max = Math.max(...sparkline.map(([, v]) => v), 1);

  return (
    <svg width={totalW} height={H} style={{ display: "block", overflow: "visible" }}>
      {sparkline.map(([yr, plays], i) => {
        const bh = Math.max(3, (plays / max) * (H - 4));
        const x = i * (bw + GAP);
        const isPeak  = yr === peakYear;
        const isRecent = yr >= recentStart;
        const fill    = isPeak ? "var(--accent)" : isRecent ? "var(--faint)" : "var(--muted-s)";
        const opacity = isRecent && !isPeak ? 0.5 : 1;
        return (
          <g key={yr}>
            <rect x={x} y={H - bh} width={bw} height={bh} fill={fill} rx={1.5} opacity={opacity} />
            {isPeak && (
              <rect x={x} y={H - bh - 4} width={bw} height={3}
                fill="var(--accent)" rx={1}
                style={{ filter: "blur(2px)", opacity: 0.65 }} />
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* ── Forgotten Favorites page ── */
function ForgottenFavoritesPage({ active, refreshVersion = 0 }) {
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [shown, setShown] = useState(25);
  const loadedVer = useRef(-1);

  useEffect(() => {
    if (!active) return;
    if (loadedVer.current === refreshVersion) return;  // already loaded for this version
    loadedVer.current = refreshVersion;                // claim up-front to avoid double-fetch
    setLoading(true);
    setError(null);
    fetch("/api/forgotten-favorites?top=100")
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d) => { setItems(d); setLoading(false); })
      .catch((e) => { loadedVer.current = -1; setError(String(e)); setLoading(false); });
  }, [active, refreshVersion]);

  if (!active) return null;

  if (loading) return (
    <section className="block"><ChartLoading height={400} /></section>
  );
  if (error) return (
    <section className="block">
      <div className="card">
        <div className="empty"><div className="big">Could not load</div><div>{error}</div></div>
      </div>
    </section>
  );
  if (!items) return null;
  if (!items.length) return (
    <section className="block">
      <div className="card">
        <div className="empty">
          <div className="big">No forgotten favorites found</div>
          <div>Your recent listening covers your full history, or scrobble data isn't loaded yet.</div>
        </div>
      </div>
    </section>
  );

  const maxYear     = Math.max(...items.flatMap((d) => d.sparkline.map(([y]) => y)));
  const recentStart = maxYear - 1; // 2 years: maxYear-1 .. maxYear
  const visible     = items.slice(0, shown);

  return (
    <div>
      <div className="page-intro">
        <h2 className="page-title">Forgotten favorites</h2>
        <p className="page-lede">
          Tracks you once played constantly — then quietly set aside. Each sparkline shows
          yearly play counts; the <span style={{ color: "var(--accent)", fontWeight: 600 }}>accent bar</span> marks
          your peak year. Ranked by how sharply listening dropped off.
        </p>
      </div>
      <section className="block">
        <div className="card">
          <div className="ff-list">
            {visible.map((item, i) => {
              const hue     = ((item.artist.charCodeAt(0) || 50) * 37 + (item.track.charCodeAt(0) || 50) * 13) % 360;
              const initial = (item.artist || "?").slice(0, 2).toUpperCase();
              const scoreStr = item.score >= 10 ? Math.round(item.score) + "×" : item.score.toFixed(1) + "×";
              return (
                <div className="ff-row" key={i}>
                  <span className="ff-rank num">{i + 1}</span>
                  <div
                    className="ff-art"
                    style={{ background: `linear-gradient(135deg, oklch(0.38 0.14 ${hue}), oklch(0.24 0.08 ${(hue + 55) % 360}))` }}
                  >
                    <span>{initial}</span>
                  </div>
                  <div className="ff-info">
                    <div className="ff-track">{item.track}</div>
                    <div className="ff-artist">{item.artist}</div>
                    <div className="ff-tags">
                      <span className="ff-badge">Peak {item.peak_year}</span>
                      <span className="ff-badge ff-badge-muted">Last {item.last_heard}</span>
                      {item.genres.slice(0, 1).map((g) => <span className="ff-tag" key={g}>{g}</span>)}
                      {item.moods.slice(0, 1).map((m) => <span className="ff-tag ff-tag-mood" key={m} style={{ "--mood": moodColor(m) }}>{m}</span>)}
                    </div>
                  </div>
                  <div className="ff-spark-wrap">
                    <FfSparkline sparkline={item.sparkline} peakYear={item.peak_year} recentStart={recentStart} />
                    <div className="ff-spark-legend">
                      <span className="num">{item.peak_plays} at peak</span>
                      <span className="num">{item.recent_plays} recent</span>
                    </div>
                  </div>
                  <div className="ff-score">
                    <span className="ff-score-val num">{scoreStr}</span>
                    <span className="ff-score-lab">fade</span>
                  </div>
                </div>
              );
            })}
          </div>
          {items.length > shown && (
            <div className="tablefoot">
              <span>Showing {shown} of {items.length}</span>
              <button className="linkbtn" onClick={() => setShown((s) => s + 25)}>Show more ↓</button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

Object.assign(window, {
  TrajectoryPage, ListeningMap,
  AudioFeaturesChart, SaturationChart, TagConstellation, AlbumsPage,
  ForgottenFavoritesPage,
});
