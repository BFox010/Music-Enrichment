import { api } from "../api.js";

let _chart = null;

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  const data = await api.artistTrajectory(12);
  update(data);
}

export function update(data) {
  if (!_chart || !data?.data?.length) return;

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
    },
    legend: {
      type: "scroll",
      bottom: 0,
      textStyle: { color: "#ccc", fontSize: 11 },
    },
    singleAxis: {
      top: 50,
      bottom: 60,
      type: "time",
      axisLabel: { color: "#aaa" },
      axisLine: { lineStyle: { color: "#444" } },
      splitLine: { lineStyle: { color: "#333" } },
    },
    series: [{
      type: "themeRiver",
      emphasis: { focus: "adjacency" },
      label: { show: true, fontSize: 10 },
      data: data.data,
    }],
  });
}
