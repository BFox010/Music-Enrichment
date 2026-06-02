import { api } from "../api.js";

let _chart = null;
let _currentBy = "year";

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  await load("year");
}

export async function load(by) {
  _currentBy = by;
  const data = await api.timeline(by);
  update(data);
}

function update(data) {
  if (!_chart || !data?.length) return;
  const periods = data.map(d => d.period);
  const plays   = data.map(d => d.plays);

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { top: 20, bottom: 36, left: 48, right: 16 },
    xAxis: {
      type: "category",
      data: periods,
      axisLabel: { color: "#aaa", rotate: periods.length > 24 ? 45 : 0, fontSize: 11 },
      axisLine: { lineStyle: { color: "#444" } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#aaa" },
      splitLine: { lineStyle: { color: "#2a2a2a" } },
    },
    series: [{
      type: "line",
      data: plays,
      smooth: true,
      symbol: "circle",
      symbolSize: 4,
      lineStyle: { color: "#7b5ea7", width: 2 },
      areaStyle: {
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: "rgba(123,94,167,0.4)" },
            { offset: 1, color: "rgba(123,94,167,0.02)" },
          ],
        },
      },
      itemStyle: { color: "#a78bfa" },
    }],
  });
}
