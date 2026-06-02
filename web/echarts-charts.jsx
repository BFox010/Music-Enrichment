/* ============================================================
   echarts-charts.jsx — ECharts React wrappers
   Requires echarts loaded globally via CDN (window.echarts).
   ============================================================ */
const { useEffect, useRef, useState, useCallback } = React;

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
    if (!ref.current || !window.echarts) return;
    chartRef.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    // The container is often 0×0 at init time (skeleton showing, or the page
    // hidden via display:none). Observe it and resize once it gains real size
    // so the chart fills its card instead of rendering into a 0-height canvas.
    let ro;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        const el = ref.current;
        if (el && el.clientWidth && el.clientHeight) chartRef.current?.resize();
      });
      ro.observe(ref.current);
    }
    return () => {
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
const cardTools = { display: "flex", alignItems: "center", gap: 14, flexShrink: 0 };
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

/* ── Artist Trajectory (ThemeRiver) ─────────────────────────────────────── */
function ArtistTrajectory({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/artist-trajectory?top=12")
      .then((r) => r.json())
      .then((data) => {
        setLoading(false);
        if (!chart.current || !data?.data?.length) return;
        const c = themeVars();
        chart.current.setOption({
          backgroundColor: "transparent",
          tooltip: {
            trigger: "axis", axisPointer: { type: "line" },
            backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
          },
          legend: { type: "scroll", bottom: 0, textStyle: { color: c.text2, fontSize: 11 } },
          singleAxis: {
            top: 50, bottom: 60, type: "time",
            axisLabel: { color: c.muted },
            axisLine: { lineStyle: { color: c.line } },
            splitLine: { lineStyle: { color: c.line, type: "dashed" } },
          },
          series: [{
            type: "themeRiver",
            emphasis: { focus: "adjacency" },
            label: { show: true, fontSize: 10, color: c.text },
            data: data.data,
          }],
        });
      })
      .catch(() => setLoading(false));
  }, [active]);

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Artist trajectory</h3>
          <span className="card-meta">listening share over time · top 12</span>
        </div>
        <div className="echart-wrap tall" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
      </div>
    </section>
  );
}

/* ── Listening Map: calendar heatmap + hour×day grid ────────────────────── */
function ListeningMap({ active }) {
  const calRef  = useRef(null);
  const hwRef   = useRef(null);
  const calChart = useEChart(calRef);
  const hwChart  = useEChart(hwRef);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/time-of-day")
      .then((r) => r.json())
      .then((data) => {
        setLoading(false);
        const c = themeVars();
        const colorScale = ["#1a1a2e", c.accent + "88", c.accent];

        // Calendar heatmap
        if (calChart.current && data?.calendar?.length) {
          const dates = data.calendar.map((d) => d[0]);
          const max   = Math.max(...data.calendar.map((d) => d[1]));
          calChart.current.setOption({
            backgroundColor: "transparent",
            tooltip: { formatter: (p) => `${p.data[0]}: ${p.data[1]} plays`, backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
            visualMap: { min: 0, max, type: "continuous", orient: "horizontal", left: "center", bottom: 4,
              inRange: { color: colorScale }, textStyle: { color: c.muted } },
            calendar: [{ top: 30, left: 30, right: 10, range: [dates[0], dates[dates.length - 1]],
              cellSize: ["auto", 13], itemStyle: { borderWidth: 0.5, borderColor: c.line },
              yearLabel: { color: c.muted }, dayLabel: { color: c.muted, nameMap: ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"] },
              monthLabel: { color: c.muted } }],
            series: [{ type: "heatmap", coordinateSystem: "calendar", data: data.calendar }],
          });
        }

        // Hour × weekday heatmap
        if (hwChart.current && data?.hour_weekday?.length) {
          const days  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
          const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
          const max   = Math.max(...data.hour_weekday.map((d) => d[2]));
          hwChart.current.setOption({
            backgroundColor: "transparent",
            tooltip: {
              formatter: (p) => { const [dow,h,n] = p.data; return `${days[dow]} ${hours[h]}: ${n} plays`; },
              backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text },
            },
            grid: { top: 12, bottom: 36, left: 48, right: 12 },
            xAxis: { type: "category", data: days, axisLabel: { color: c.muted },
              axisLine: { lineStyle: { color: c.line } }, splitArea: { show: true } },
            yAxis: { type: "category", data: hours, axisLabel: { color: c.muted, fontSize: 9 },
              axisLine: { lineStyle: { color: c.line } }, splitArea: { show: true } },
            visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0,
              inRange: { color: colorScale }, textStyle: { color: c.muted } },
            series: [{ type: "heatmap", data: data.hour_weekday.map(([h, dow, n]) => [dow, h, n]),
              label: { show: false }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,.5)" } } }],
          });
        }
      })
      .catch(() => setLoading(false));
  }, [active]);

  return (
    <section className="block">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div className="card">
          <div className="card-head norule"><h3 className="card-title">Listening calendar</h3><span className="card-meta">plays per day</span></div>
          {loading && <ChartLoading height={260} />}
          <div ref={calRef} className="echart-wrap short" style={{ display: loading ? "none" : "block" }} />
        </div>
        <div className="card">
          <div className="card-head norule"><h3 className="card-title">Hour × weekday</h3><span className="card-meta">play density</span></div>
          {loading && <ChartLoading height={320} />}
          <div ref={hwRef} style={{ width: "100%", height: 320, display: loading ? "none" : "block" }} />
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
            grid: { top: 20, bottom: 36, left: 44, right: 16 },
            xAxis: { name: "Energy →", type: "value", min: 0, max: 1,
              nameTextStyle: { color: c.muted, fontSize: 11 }, axisLabel: { color: c.muted },
              splitLine: { lineStyle: { color: c.line, type: "dashed" } } },
            yAxis: { name: "Valence →", type: "value", min: 0, max: 1,
              nameTextStyle: { color: c.muted, fontSize: 11 }, axisLabel: { color: c.muted },
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

/* ── Saturation donut ────────────────────────────────────────────────────── */
function SaturationChart({ active }) {
  const elRef = useRef(null);
  const chart = useEChart(elRef);
  const [loading, setLoading] = useState(true);

  const TIER_LABELS = { "1": "Tier 1 — Heavy rotation", "2": "Tier 2 — Regular", "3": "Tier 3 — Deep cuts", "unranked": "Unranked" };

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    fetch("/api/saturation")
      .then((r) => r.json())
      .then((data) => {
        setLoading(false);
        if (!chart.current || !data?.length) return;
        const c = themeVars();
        const COLORS = { "1": c.accent, "2": c.accent + "aa", "3": c.accent + "55", "unranked": c.line };
        chart.current.setOption({
          backgroundColor: "transparent",
          tooltip: { trigger: "item", formatter: (p) => `${p.name}<br>${p.value} tracks (${p.percent}%)`,
            backgroundColor: c.panel, borderColor: c.line, textStyle: { color: c.text } },
          legend: { orient: "vertical", right: 10, top: "center", textStyle: { color: c.text2, fontSize: 12 } },
          series: [{
            type: "pie", radius: ["44%", "70%"], center: ["42%", "50%"],
            avoidLabelOverlap: false, label: { show: false },
            emphasis: { label: { show: true, fontSize: 14, fontWeight: "bold", color: c.text },
              itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,.5)" } },
            data: data.map((d) => ({
              name: TIER_LABELS[d.tier] || d.tier,
              value: d.count,
              itemStyle: { color: COLORS[d.tier] || c.muted },
            })),
          }],
        });
      })
      .catch(() => setLoading(false));
  }, [active]);

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Data saturation</h3>
          <span className="card-meta">tracks by enrichment tier</span>
        </div>
        <div className="echart-wrap" ref={elRef} style={{ display: loading ? "none" : "block", height: 360 }} />
        {loading && <ChartLoading />}
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
    ["lastfm_tags",    "Last.fm"],
  ];

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    const minCount = field === "mood_tags" ? 1 : 15;
    fetch(`/api/tag-graph?field=${field}&min_count=${minCount}`)
      .then((r) => r.json())
      .then(({ nodes, edges }) => {
        setLoading(false);
        if (!chart.current || !nodes?.length) return;
        const c = themeVars();
        const maxCount  = nodes[0]?.count || 1;
        const maxWeight = edges.reduce((m, e) => Math.max(m, e.weight), 1);
        const nodeColor = (i, n) =>
          `hsl(${Math.round((i / Math.max(n - 1, 1)) * 260 + 200)}, 58%, 50%)`;

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
            force: { repulsion: 420, gravity: 0.04, edgeLength: [30, 140], layoutAnimation: true, friction: 0.65 },
            roam: true, draggable: true,
            label: { show: false, formatter: "{b}" },
            emphasis: { scale: true, focus: "adjacency",
              label: { show: true, fontSize: 12, color: c.text },
              lineStyle: { opacity: 0.85, width: 2 } },
            data: nodes.map((d, i) => ({
              name: d.tag, value: d.count,
              symbolSize: Math.max(14, Math.sqrt(d.count / maxCount) * 72),
              itemStyle: { color: nodeColor(i, nodes.length) },
              label: { show: d.count >= maxCount * 0.08, fontSize: 11, color: c.text },
            })),
            edges: edges.map((e) => ({
              source: e.source, target: e.target, value: e.weight,
              lineStyle: {
                width: Math.max(0.5, Math.log2(e.weight + 1) * 0.9),
                opacity: 0.18 + (e.weight / maxWeight) * 0.32,
                color: "source", curveness: 0,
              },
            })),
          }],
        }, true);
      })
      .catch(() => setLoading(false));
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
            <span className="card-meta">force graph · shared-track connections</span>
          </div>
        </div>
        <p style={cardDesc}>Each node is a tag, sized by how many tracks carry it; links connect tags that share tracks. Drag nodes to untangle, scroll to zoom.</p>
        <div className="echart-wrap tall" ref={elRef} style={{ display: loading ? "none" : "block" }} />
        {loading && <ChartLoading height={560} />}
      </div>
    </section>
  );
}

Object.assign(window, {
  TimelineChart, ArtistTrajectory, ListeningMap,
  AudioFeaturesChart, SaturationChart, TagConstellation,
});
