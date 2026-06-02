import { api } from "../api.js";

let _chart = null;

const _TIER_LABELS = {
  "1": "Tier 1 — Heavy rotation",
  "2": "Tier 2 — Regular",
  "3": "Tier 3 — Deep cuts",
  "unranked": "Unranked",
};

const _TIER_COLORS = {
  "1": "#e040fb",
  "2": "#7b5ea7",
  "3": "#40c4ff",
  "unranked": "#444",
};

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  const data = await api.saturation();
  update(data);
}

export function update(data) {
  if (!_chart || !data?.length) return;

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      formatter: p => `${p.name}<br>${p.value} tracks (${p.percent}%)`,
    },
    legend: {
      orient: "vertical",
      right: 10,
      top: "center",
      textStyle: { color: "#ccc", fontSize: 12 },
    },
    series: [{
      type: "pie",
      radius: ["45%", "72%"],
      center: ["40%", "50%"],
      avoidLabelOverlap: false,
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 14, fontWeight: "bold", color: "#fff" },
        itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.5)" },
      },
      data: data.map(d => ({
        name: _TIER_LABELS[d.tier] || d.tier,
        value: d.count,
        itemStyle: { color: _TIER_COLORS[d.tier] || "#666" },
      })),
    }],
  });
}
