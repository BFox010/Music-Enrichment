import { api } from "../api.js";

let _chart = null;
let _currentField = "discogs_styles";
const _MIN_COUNT = 15;

export async function init(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  _chart = echarts.init(el, "dark");
  window.addEventListener("resize", () => _chart?.resize());
  await load(_currentField);
}

export async function load(field) {
  _currentField = field;

  // update toolbar active state
  document.querySelectorAll(".tag-field-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.field === field);
  });

  const minCount = field === "mood_tags" ? 1 : _MIN_COUNT;
  const data = await api.tagGraph(field, minCount);
  update(data);
}

export function update({ nodes, edges }) {
  if (!_chart || !nodes?.length) return;

  const maxCount = nodes[0]?.count || 1;
  const maxWeight = edges.reduce((m, e) => Math.max(m, e.weight), 1);

  // colour palette: hue sweeps across the sorted node list
  const nodeColor = (i, n) =>
    `hsl(${Math.round((i / Math.max(n - 1, 1)) * 260 + 200)}, 58%, 50%)`;

  const ecNodes = nodes.map((d, i) => ({
    name: d.tag,
    value: d.count,
    symbolSize: Math.max(14, Math.sqrt(d.count / maxCount) * 72),
    itemStyle: { color: nodeColor(i, nodes.length) },
    label: {
      show: d.count >= maxCount * 0.08,   // label top ~92th-percentile nodes
      fontSize: 11,
      color: "#ddd",
    },
  }));

  const ecEdges = edges.map(e => ({
    source: e.source,
    target: e.target,
    value: e.weight,
    lineStyle: {
      width: Math.max(0.5, Math.log2(e.weight + 1) * 0.9),
      opacity: 0.18 + (e.weight / maxWeight) * 0.32,
      color: "source",
      curveness: 0,
    },
  }));

  _chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      formatter(p) {
        if (p.dataType === "edge") {
          return `<b>${p.data.source}</b> ↔ <b>${p.data.target}</b><br>${p.data.value} shared tracks`;
        }
        return `<b>${p.data.name}</b><br>${p.data.value} tracks`;
      },
    },
    series: [{
      type: "graph",
      layout: "force",
      force: {
        repulsion: 420,
        gravity: 0.04,
        edgeLength: [30, 140],   // short edge = tight co-occurrence
        layoutAnimation: true,
        friction: 0.65,
      },
      roam: true,
      draggable: true,
      label: { show: false, formatter: "{b}" },
      emphasis: {
        scale: true,
        focus: "adjacency",
        label: { show: true, fontSize: 12, color: "#fff" },
        lineStyle: { opacity: 0.85, width: 2 },
      },
      data: ecNodes,
      edges: ecEdges,
    }],
  }, true);
}
