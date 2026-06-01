import { api } from "../api.js";

let _scatterChart = null;
let _histChart    = null;

const _HIST_FEATURES = [
  { key: "energy",       label: "Energy",       color: "#e040fb" },
  { key: "valence",      label: "Valence",       color: "#40c4ff" },
  { key: "danceability", label: "Danceability",  color: "#69f0ae" },
  { key: "acousticness", label: "Acousticness",  color: "#ffab40" },
];

export async function init(scatterId, histId) {
  const scEl = document.getElementById(scatterId);
  const hiEl = document.getElementById(histId);
  if (!scEl || !hiEl) return;

  _scatterChart = echarts.init(scEl, "dark");
  _histChart    = echarts.init(hiEl, "dark");
  window.addEventListener("resize", () => {
    _scatterChart?.resize();
    _histChart?.resize();
  });

  const data = await api.audioFeatures();
  updateScatter(data.scatter);
  updateHistograms(data.histograms);
}

function updateScatter(scatter) {
  if (!_scatterChart || !scatter?.length) return;

  const maxPlays = Math.max(...scatter.map(d => d.play_count || 1));
  const scData   = scatter.map(d => ({
    value: [d.energy, d.valence, d.play_count],
    name: `${d.artist} — ${d.track}`,
    symbolSize: Math.max(4, Math.sqrt(d.play_count / maxPlays) * 18),
  }));

  _scatterChart.setOption({
    backgroundColor: "transparent",
    title: { text: "Energy × Valence", textStyle: { color: "#ddd", fontSize: 13 }, top: 4 },
    tooltip: {
      formatter: p => `<b>${p.data.name}</b><br>Energy: ${p.data.value[0].toFixed(2)}<br>Valence: ${p.data.value[1].toFixed(2)}<br>Plays: ${p.data.value[2]}`,
    },
    grid: { top: 40, bottom: 36, left: 42, right: 16 },
    xAxis: {
      name: "Energy",
      type: "value",
      min: 0, max: 1,
      nameTextStyle: { color: "#aaa" },
      axisLabel: { color: "#aaa" },
      splitLine: { lineStyle: { color: "#2a2a2a" } },
    },
    yAxis: {
      name: "Valence",
      type: "value",
      min: 0, max: 1,
      nameTextStyle: { color: "#aaa" },
      axisLabel: { color: "#aaa" },
      splitLine: { lineStyle: { color: "#2a2a2a" } },
    },
    series: [{
      type: "scatter",
      data: scData,
      itemStyle: { color: "#7b5ea7", opacity: 0.65 },
      emphasis: { itemStyle: { opacity: 1.0 } },
    }],
  });
}

function updateHistograms(histograms) {
  if (!_histChart || !histograms) return;
  const features = _HIST_FEATURES.filter(f => histograms[f.key]?.length);
  if (!features.length) return;

  const cols = features.length;
  const gridW = Math.floor(100 / cols);

  const grids   = [];
  const xAxes   = [];
  const yAxes   = [];
  const series  = [];

  features.forEach((f, i) => {
    const left  = `${i * gridW + 1}%`;
    const width = `${gridW - 2}%`;
    grids.push({ left, width, top: 42, bottom: 32 });
    xAxes.push({
      gridIndex: i, type: "category",
      data: histograms[f.key].map(b => b.bin_start.toFixed(1)),
      axisLabel: { color: "#888", fontSize: 9, rotate: 45 },
      axisLine: { lineStyle: { color: "#444" } },
    });
    yAxes.push({
      gridIndex: i, type: "value",
      axisLabel: { show: i === 0, color: "#aaa", fontSize: 10 },
      splitLine: { lineStyle: { color: "#2a2a2a" } },
    });
    series.push({
      type: "bar",
      xAxisIndex: i, yAxisIndex: i,
      data: histograms[f.key].map(b => b.count),
      itemStyle: { color: f.color, opacity: 0.85, borderRadius: [2, 2, 0, 0] },
      name: f.label,
    });
  });

  _histChart.setOption({
    backgroundColor: "transparent",
    title: features.map((f, i) => ({
      text: f.label,
      textStyle: { color: "#ccc", fontSize: 12, fontWeight: "normal" },
      left: `${i * gridW + gridW / 2}%`,
      top: 12,
      textAlign: "center",
    })),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
    },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series,
  });
}
