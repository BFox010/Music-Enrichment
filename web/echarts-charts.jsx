/* ============================================================
   echarts-charts.jsx — ECharts React wrappers
   Requires echarts loaded globally via CDN (window.echarts).
   ============================================================ */
const { useEffect, useRef, useState, useCallback, useMemo } = React;

/* ── CSS-variable theme colours ────────────────────────────────────────── */
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

/* ── shared ECharts mount hook ──────────────────────────────────────────── */
function useEChart(ref) {
  const chartRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    let ro;
    let pollId;
    const onResize = () => chartRef.current?.resize();
    // ECharts is loaded lazily (deferred, off the first-paint critical path), so it
    // may not exist yet when this runs. Wait for it instead of giving up once.
    function init() {
      if (cancelled || chartRef.current || !ref.current) return;
      if (!window.echarts) { pollId = setTimeout(init, 50); return; }
      chartRef.current = echarts.init(ref.current, null, { renderer: "canvas" });
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
    init();
    return () => {
      cancelled = true;
      clearTimeout(pollId);
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);
  return chartRef;
}

/* ── Loading skeleton ───────────────────────────────────────────────────── */
function ChartLoading({ height = 420 }) {
  return <div className="echart-loading" style={{ height }}>Loading…</div>;
}

/* ── shared bits ────────────────────────────────────────────────────────── */
// right-aligned cluster for a card-head that also holds a control (seg) + meta
const cardTools = { display: "flex", alignItems: "center", gap: 14, flexShrink: 0, flexWrap: "wrap", justifyContent: "flex-end" };
// one-line explainer that sits directly under a card-head
const cardDesc  = { margin: "0 0 16px", fontSize: 12.5, lineHeight: 1.55, color: "var(--muted-s)", maxWidth: 640 };

/* ── Timeline chart ─────────────────────────────────────────────────────── */
function TimelineChart({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [by, setBy] = useState("year");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch(`/api/timeline?by=${by}`)
      .then((r) => r.json())
      .then((data) => {
        setLoading(false);
        if (!chart.current || !data?.length) return;
        const c = themeVars();
        const periods = data.map((d) => d.period);
        const plays   = data.map((d) => d.plays);
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
      })
      .catch(() => setLoading(false));
  }, [active, by]);

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

/* ── Artist Trajectory (line / stream + artist picker) ──────────────────── */
function ArtistTrajectory({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [raw, setRaw] = useState(null);
  const [mode, setMode] = useState("lines");
  const [selected, setSelected] = useState(null);  // Set of artist names
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  // Fetch once (top 20); seed the selection with the top 8 by total plays.
  useEffect(() => {
    if (!active || raw) return;
    setLoading(true);
    fetch("/api/artist-trajectory?top=20")
      .then((r) => r.json())
      .then((d) => {
        setLoading(false);
        const rows = (d && d.data) || [];
        setRaw({ data: rows });
        const totals = {};
        rows.forEach(([, c, n]) => { totals[n] = (totals[n] || 0) + c; });
        const ordered = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
        setSelected(new Set(ordered.slice(0, 8)));
      })
      .catch(() => setLoading(false));
  }, [active, raw]);

  const artists = useMemo(() => {
    if (!raw) return [];
    const totals = {};
    raw.data.forEach(([, c, n]) => { totals[n] = (totals[n] || 0) + c; });
    return Object.keys(totals).sort((a, b) => totals[b] - totals[a]).map((n) => ({ name: n, total: totals[n] }));
  }, [raw]);

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
  }, [active, raw, mode, selected, chart]);

  const toggleArtist = (name) => setSelected((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const resetTop = () => setSelected(new Set(artists.slice(0, 8).map((a) => a.name)));
  const q = query.trim().toLowerCase();
  const shownArtists = q ? artists.filter((a) => a.name.toLowerCase().includes(q)) : artists;
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
            <span className="card-meta">monthly plays · top 20</span>
          </div>
        </div>
        <p style={cardDesc}>Compare how your listening shifted month to month. <b>Lines</b> plot plays per month per artist; <b>Stream</b> stacks them into a flowing river.</p>
        <div className="artist-picker">
          <div className="ap-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter artists…" />
            <span className="ap-count">{selCount} shown</span>
            <button className="ap-reset" onClick={resetTop} title="Reset to top 8">Top 8</button>
          </div>
          <div className="ap-chips">
            {shownArtists.map((a) => (
              <button key={a.name} className={"ap-chip" + (selected && selected.has(a.name) ? " on" : "")} onClick={() => toggleArtist(a.name)}>
                <span className="ap-dot"></span>{a.name}
              </button>
            ))}
          </div>
        </div>
        <div className="echart-wrap tall" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
      </div>
    </section>
  );
}

/* ── Listening Map: calendar heatmap (per year) + hour×day grid ─────────── */
function ListeningMap({ active }) {
  const calRef  = useRef(null);
  const hwRef   = useRef(null);
  const calChart = useEChart(calRef);
  const hwChart  = useEChart(hwRef);
  const [years, setYears] = useState([]);
  const [year, setYear] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    const q = year != null ? `?year=${year}` : "";
    fetch("/api/time-of-day" + q)
      .then((r) => r.json())
      .then((data) => {
        // First pass (year unknown): learn the available years, default to the
        // most recent, and let the effect re-run with that year filter.
        if (year == null) {
          if (data.years && data.years.length) { setYears(data.years); setYear(data.years[data.years.length - 1]); }
          else setLoading(false);
          return;
        }
        setLoading(false);
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
      })
      .catch(() => setLoading(false));
  }, [active, year]);

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

/* ── Audio Features: scatter + histograms ───────────────────────────────── */
function AudioFeaturesChart({ active }) {
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

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/audio-features")
      .then((r) => r.json())
      .then(({ scatter, histograms }) => {
        setLoading(false);
        const c = themeVars();

        if (scChart.current && scatter?.length) {
          const maxP = Math.max(...scatter.map((d) => d.play_count || 1));
          scChart.current.setOption({
            backgroundColor: "transparent",
            tooltip: {
              formatter: (p) => `<b>${p.data.name}</b><br>Energy: ${p.data.value[0].toFixed(2)}<br>Valence: ${p.data.value[1].toFixed(2)}<br>Plays: ${p.data.value[2]}`,
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
      })
      .catch(() => setLoading(false));
  }, [active]);

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

/* ── Saturation donut (folded into the Coverage page) ───────────────────── */
function SaturationChart({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const TIER_LABELS = { "1": "Tier 1 — Heavy rotation", "2": "Tier 2 — Regular", "3": "Tier 3 — Deep cuts", "unranked": "Unranked" };

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/saturation")
      .then((r) => r.json())
      .then((d) => { setLoading(false); setData(d); })
      .catch(() => setLoading(false));
  }, [active]);

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
        tooltip: { trigger: "item", formatter: (p) => `${p.name}<br>${p.value} tracks (${p.percent}%)`,
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
  }, [active, data, chart]);

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="card-title">Data saturation</h3>
        <span className="card-meta">tracks by enrichment tier</span>
      </div>
      <p style={cardDesc}>How thoroughly each track is enriched, weighted by how much you play it. <b>Tier 1</b> = heavy rotation, fully enriched · <b>Tier 2</b> = regular plays · <b>Tier 3</b> = deep cuts · <b>Unranked</b> = not yet scored.</p>
      <div className="echart-wrap" ref={elRef} style={{ display: loading ? "none" : "block", height: 320 }} />
      {loading && <ChartLoading height={320} />}
    </div>
  );
}

/* ── Albums (most-played, with listening spread) ────────────────────────── */
function _albumHue(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}
// Most-played albums, scored by total plays + how evenly listening spreads
// across the album's tracks. Computed in-browser from the already-loaded
// `tracks` (no /api/albums round-trip) — mirrors app/metrics.py::albums.
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

/* ── Tag Constellation (force graph) ────────────────────────────────────── */
function TagConstellation({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [field, setField] = useState("discogs_styles");
  const [loading, setLoading] = useState(true);

  const FIELDS = [
    ["discogs_styles", "Styles"],
    ["mood_tags",      "Moods"],
    ["lastfm_tags",    "Genres"],
  ];

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    let fitTimer = null;
    let onWinResize = null;
    let rafA = 0, rafB = 0;
    let fitted = false;

    // Read settled node positions and patch the view (zoom + center) so the
    // bounding box of the cluster fills the canvas with margin. Layout stays
    // "force" — physics keeps running, only the camera moves.
    const fitView = () => {
      const inst = chart.current;
      if (!inst) return;
      const cw = inst.getWidth(), ch = inst.getHeight();
      if (cw < 10 || ch < 10) return;  // canvas not laid out yet — skip
      const gdata = inst.getModel().getSeriesByIndex(0)?.getData();
      if (!gdata || gdata.count() === 0) return;
      let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
      let ok = true;
      gdata.each((idx) => {
        const p = gdata.getItemLayout(idx);
        if (!p || !isFinite(p[0]) || !isFinite(p[1])) { ok = false; return; }
        if (p[0] < xMin) xMin = p[0]; if (p[0] > xMax) xMax = p[0];
        if (p[1] < yMin) yMin = p[1]; if (p[1] > yMax) yMax = p[1];
      });
      if (!ok || !isFinite(xMin)) return;
      const bw = xMax - xMin, bh = yMax - yMin;
      if (bw <= 0 || bh <= 0) return;
      const cxData = (xMin + xMax) / 2, cyData = (yMin + yMax) / 2;
      const margin = 1.2;
      const zoom = Math.min(cw / (bw * margin), ch / (bh * margin));
      if (!isFinite(zoom) || zoom <= 0) return;
      inst.setOption({ series: [{ zoom, center: [cxData, cyData] }] });
      fitted = true;
    };

    const setupChart = (nodes, edges) => {
      if (!chart.current) return;
      chart.current.resize();
      const c = themeVars();
      const maxCount  = nodes[0]?.count || 1;
      const maxWeight = edges.reduce((m, e) => Math.max(m, e.weight), 1);
      const nodeColor = (i, n) =>
        `hsl(${Math.round((i / Math.max(n - 1, 1)) * 260 + 200)}, 58%, 50%)`;
      // Seed each node on a circle so the force sim starts from a 2-D spread
      // instead of all-zero coords (which whip them across the screen). Radius
      // is generous so the seeded ring already approximates the equilibrium
      // spread the sim will produce. Centered on (0,0) because ECharts force
      // gravity pulls nodes toward the origin regardless of where we seed them —
      // matching that center keeps the initial view aligned with where the
      // nodes will actually settle.
      const cw = chart.current.getWidth(), ch = chart.current.getHeight();
      const R = Math.min(cw, ch) * 0.46;

      const symSize = (d) => Math.max(14, Math.sqrt(d.count / maxCount) * 72);
      const nodeData = nodes.map((d, i) => {
        const x = R * Math.cos((2 * Math.PI * i) / nodes.length);
        const y = R * Math.sin((2 * Math.PI * i) / nodes.length);
        return {
          name: d.tag, value: d.count, x, y,
          symbolSize: symSize(d),
          itemStyle: { color: nodeColor(i, nodes.length) },
          label: { show: d.count >= maxCount * 0.08, fontSize: 11, color: c.text },
        };
      });
      const edgeData = edges.map((e) => ({
        source: e.source, target: e.target, value: e.weight,
        lineStyle: {
          width: Math.max(0.5, Math.log2(e.weight + 1) * 0.9),
          opacity: 0.18 + (e.weight / maxWeight) * 0.32,
          color: "source", curveness: 0,
        },
      }));
      chart.current.setOption({
        backgroundColor: "transparent",
        tooltip: {
          formatter(p) {
            if (p.dataType === "edge") return `<b>${p.data.source}</b> ↔ <b>${p.data.target}</b><br>${p.data.value} shared tracks`;
            return `<b>${p.data.name}</b><br>${p.data.value} tracks`;
          },
          backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
        },
        series: [{
          type: "graph", layout: "force",
          center: [0, 0],  // align initial view with the (0,0) gravity well
          // Per-node repulsion scaled with symbol area so big circles push
          // harder than small ones — keeps the large hub nodes from sliding
          // under each other while still letting small nodes pack in close.
          force: {
            // Per-node repulsion scales with symbol area so big circles push
            // much harder than small ones — keeps the large hub nodes from
            // sliding under each other while small nodes can still pack in.
            repulsion: nodeData.map((n) => Math.min(4000, Math.max(420, n.symbolSize * n.symbolSize / 1.4))),
            // edgeLength min larger than the biggest-pair diameter (~72) so
            // even strongly connected hub pairs sit edge-to-edge, not overlapping.
            gravity: 0.13, edgeLength: [150, 240],
            layoutAnimation: true, friction: 0.4,
          },
          roam: true, draggable: true,
          label: { show: false, formatter: "{b}" },
          emphasis: { scale: true, focus: "adjacency",
            label: { show: true, fontSize: 12, color: c.text },
            lineStyle: { opacity: 0.85, width: 2 } },
          data: nodeData,
          edges: edgeData,
        }],
      }, true);

      // One fit ~1.5s in, after the force sim has roughly settled. Layout stays
      // "force", so nodes keep bouncing — only the camera moves.
      fitTimer = setTimeout(fitView, 1500);
      // Re-fit on window resize so framing follows the new canvas size.
      onWinResize = () => { if (fitted) fitView(); };
      window.addEventListener("resize", onWinResize);
    };

    const minCount = field === "mood_tags" ? 1 : 15;
    fetch(`/api/tag-graph?field=${field}&min_count=${minCount}`)
      .then((r) => r.json())
      .then(({ nodes, edges }) => {
        setLoading(false);
        if (!chart.current || !nodes?.length) return;
        // setLoading(false) flips the wrap from display:none to block, but the
        // DOM update is async (React hasn't painted yet). Wait two animation
        // frames so the wrap has real dimensions before we seed the layout —
        // otherwise every node spawns at (0,0) and the view computes against
        // a 0×0 canvas, producing zoom=0 and a blank chart.
        rafA = requestAnimationFrame(() => {
          rafB = requestAnimationFrame(() => setupChart(nodes, edges));
        });
      })
      .catch(() => setLoading(false));

    return () => {
      if (fitTimer) clearTimeout(fitTimer);
      if (rafA) cancelAnimationFrame(rafA);
      if (rafB) cancelAnimationFrame(rafB);
      if (onWinResize) window.removeEventListener("resize", onWinResize);
    };
  }, [active, field]);

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Tag constellation</h3>
          <div style={cardTools}>
            <div className="seg" role="group">
              {FIELDS.map(([v, l]) => (
                <button key={v} aria-pressed={field === v} onClick={() => setField(v)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">force graph</span>
          </div>
        </div>
        <p style={cardDesc}>Each node is a tag, sized by how many tracks carry it; links connect tags that share tracks. Drag nodes to untangle, scroll to zoom.</p>
        <div className="echart-wrap tall" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
      </div>
    </section>
  );
}

/* ── Forgotten Favorites sparkline (pure SVG, no ECharts) ──────────────── */
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

/* ── Forgotten Favorites page ───────────────────────────────────────────── */
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
                      {item.moods.slice(0, 1).map((m) => <span className="ff-tag ff-tag-mood" key={m}>{m}</span>)}
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
  TimelineChart, ArtistTrajectory, ListeningMap,
  AudioFeaturesChart, SaturationChart, TagConstellation, AlbumsPage,
  ForgottenFavoritesPage,
});
