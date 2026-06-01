import { api } from "../api.js";

let _chart = null;

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  const data = await api.genres(60);
  update(data);
}

export function update(data) {
  if (!_chart || !data?.length) return;
  const maxCount = data[0].count || 1;

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      formatter: p => `<b>${p.data.name}</b><br>${p.data.value} tracks`,
    },
    series: [{
      type: "graph",
      layout: "force",
      force: {
        repulsion: 250,
        gravity: 0.06,
        edgeLength: 40,
        layoutAnimation: true,
        friction: 0.6,
      },
      roam: true,
      label: {
        show: true,
        fontSize: 11,
        formatter: "{b}",
      },
      emphasis: { scale: true, focus: "self" },
      data: data.map(d => ({
        name: d.genre,
        value: d.count,
        symbolSize: Math.max(18, Math.sqrt(d.count / maxCount) * 70),
        itemStyle: {
          color: `hsl(${Math.round((d.count / maxCount) * 220 + 180)}, 55%, 48%)`,
        },
        label: { show: d.count >= 15 },
      })),
      edges: [],
    }],
  });
}
