import { api } from "../api.js";

let _chart = null;

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  const data = await api.moods();
  update(data);
}

export function update(data) {
  if (!_chart || !data?.length) return;
  const sorted = [...data].sort((a, b) => a.count - b.count);
  const moods  = sorted.map(d => d.mood);
  const counts = sorted.map(d => d.count);
  const max    = counts[counts.length - 1] || 1;

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { top: 8, bottom: 8, left: 90, right: 16, containLabel: false },
    xAxis: {
      type: "value",
      axisLabel: { color: "#aaa" },
      splitLine: { lineStyle: { color: "#2a2a2a" } },
    },
    yAxis: {
      type: "category",
      data: moods,
      axisLabel: { color: "#ccc", fontSize: 12 },
      axisLine: { lineStyle: { color: "#444" } },
    },
    series: [{
      type: "bar",
      data: counts.map(c => ({
        value: c,
        itemStyle: {
          color: `hsl(${Math.round((c / max) * 80 + 260)}, 60%, 55%)`,
          borderRadius: [0, 3, 3, 0],
        },
      })),
      label: { show: true, position: "right", color: "#aaa", fontSize: 11 },
    }],
  });
}
