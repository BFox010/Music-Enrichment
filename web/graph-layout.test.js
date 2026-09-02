"use strict";

// Tests for the Tag Constellation's layout helpers. The constellation renders
// with ECharts `layout: "none"` and coordinates settled here, so these
// functions are the layout — a regression in them is a regression in what the
// page draws, and nothing else would catch it (the chart itself is only
// checkable by eye).
//
// graph-layout.js has no module system by design, for the same reason
// data-processing.js has none: it is loaded as a plain global via
// <script defer> on the main thread and importScripts() inside the worker, so
// a bundler/ESM step would defeat the point. vm.runInThisContext executes its
// source the way a browser <script> tag would, putting its top-level
// declarations in this realm's global scope.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "graph-layout.js"), "utf8");
vm.runInThisContext(SRC, { filename: "graph-layout.js" });

/* A deterministic stand-in for a real tag graph: `hubs` heavily-played tags
   that link to each other, each with `spokes` smaller tags hanging off it, and
   a thin haze of cross-cluster links — the shape /api/tag-graph actually
   returns, where ~75% of the edges are near-worthless. */
function fixture(hubs = 4, spokes = 8) {
  const nodes = [];
  const edges = [];
  for (let h = 0; h < hubs; h++) {
    nodes.push({ tag: `hub${h}`, count: 4000 - h * 300 });
    for (let s = 0; s < spokes; s++) nodes.push({ tag: `h${h}s${s}`, count: 400 - s * 30 });
  }
  for (let h = 0; h < hubs; h++) {
    for (let g = h + 1; g < hubs; g++) edges.push({ source: `hub${h}`, target: `hub${g}`, weight: 900 - (h + g) * 50 });
    for (let s = 0; s < spokes; s++) {
      edges.push({ source: `hub${h}`, target: `h${h}s${s}`, weight: 300 - s * 20 });
      // haze: every spoke also brushes every other hub, and its siblings, barely
      for (let g = 0; g < hubs; g++) if (g !== h) edges.push({ source: `h${h}s${s}`, target: `hub${g}`, weight: 1 + ((s + g) % 3) });
      for (let t = s + 1; t < spokes; t++) edges.push({ source: `h${h}s${s}`, target: `h${h}s${t}`, weight: 2 + ((s * t) % 5) });
    }
  }
  return { nodes, edges };
}

const W = 1100, H = 620;

/* ── edgePercentile ── */

test("edgePercentile picks the weight at the requested rank", () => {
  const edges = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((w, i) => ({ source: `a${i}`, target: `b${i}`, weight: w }));
  assert.equal(edgePercentile(edges, 0), 1);
  assert.equal(edgePercentile(edges, 1), 10);
  assert.equal(edgePercentile(edges, 0.5), 6);   // round((n-1)*p) = 5 → 0-indexed
  assert.equal(edgePercentile([], 0.9), 0);
});

/* ── pruneEdges ── */

test("pruneEdges keeps every node's strongest links", () => {
  const { nodes, edges } = fixture();
  const kept = pruneEdges(edges, { perNode: 3, keepAbove: Infinity });
  const strongest = adjacency(nodes, edges);
  const keptAdj = adjacency(nodes, kept);
  // Compared by weight, not by tag: a node's 3rd and 4th links can be tied,
  // and which of the two survives is only a tiebreak, not a promise.
  for (const [tag, all] of strongest) {
    const kept = keptAdj.get(tag).slice(0, 3).map((x) => x.weight);
    assert.deepEqual(kept, all.slice(0, 3).map((x) => x.weight), `${tag} lost one of its strongest links`);
  }
});

test("pruneEdges keeps every edge at or above the threshold", () => {
  const { edges } = fixture();
  const keepAbove = edgePercentile(edges, 0.9);
  const kept = new Set(pruneEdges(edges, { perNode: 0, keepAbove }));
  for (const e of edges) assert.equal(kept.has(e), e.weight >= keepAbove, `weight ${e.weight} vs ${keepAbove}`);
});

test("pruneEdges never strands a node that has an edge", () => {
  const { nodes, edges } = fixture();
  const before = adjacency(nodes, edges);
  const after = adjacency(nodes, pruneEdges(edges, { perNode: 1, keepAbove: Infinity }));
  for (const [tag, all] of before) {
    if (all.length) assert.ok(after.get(tag).length > 0, `${tag} was stranded`);
  }
});

test("pruneEdges is idempotent for a fixed threshold", () => {
  const { edges } = fixture();
  const keepAbove = edgePercentile(edges, 0.9);
  const once = pruneEdges(edges, { perNode: 3, keepAbove });
  const twice = pruneEdges(once, { perNode: 3, keepAbove });
  assert.deepEqual(twice, once);
});

test("pruneEdges drops the haze the physics cannot carry", () => {
  const { edges } = fixture();
  const kept = pruneEdges(edges, { perNode: 3, keepAbove: edgePercentile(edges, 0.9) });
  assert.ok(kept.length < edges.length / 2, `${kept.length} of ${edges.length} survived`);
});

/* ── adjacency ── */

test("adjacency is symmetric and sorted by weight desc", () => {
  const { nodes, edges } = fixture();
  const adj = adjacency(nodes, edges);
  assert.equal(adj.size, nodes.length);
  for (const [tag, list] of adj) {
    for (let i = 1; i < list.length; i++) assert.ok(list[i - 1].weight >= list[i].weight, "unsorted");
    for (const n of list) {
      const back = adj.get(n.tag).find((x) => x.tag === tag);
      assert.ok(back, `${n.tag} does not list ${tag} back`);
      assert.equal(back.weight, n.weight);
    }
  }
});

test("adjacency gives isolated nodes an empty list, not undefined", () => {
  const adj = adjacency([{ tag: "lonely", count: 5 }, { tag: "a", count: 9 }, { tag: "b", count: 9 }],
                        [{ source: "a", target: "b", weight: 2 }]);
  assert.deepEqual(adj.get("lonely"), []);
});

test("adjacency ignores an edge naming a tag that is not a node", () => {
  // A node cut-off can leave an edge pointing at a tag that did not survive it;
  // counting one end and not the other would make the panel asymmetric.
  const adj = adjacency([{ tag: "a", count: 9 }], [{ source: "a", target: "gone", weight: 2 }]);
  assert.deepEqual(adj.get("a"), []);
  assert.equal(adj.has("gone"), false);
});

/* ── settleLayout ── */

test("settleLayout terminates with finite, separated positions", () => {
  const { nodes, edges } = fixture();
  const pos = settleLayout(nodes, pruneEdges(edges, { perNode: 3 }), { width: W, height: H });
  assert.equal(pos.size, nodes.length);
  const pts = [];
  for (const p of pos.values()) {
    assert.ok(isFinite(p[0]) && isFinite(p[1]), `non-finite ${p}`);
    pts.push(p);
  }
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      assert.ok(Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) > 1, "two nodes landed on the same point");
    }
  }
});

test("settleLayout is deterministic", () => {
  const { nodes, edges } = fixture();
  const phys = pruneEdges(edges, { perNode: 3 });
  const a = settleLayout(nodes, phys, { width: W, height: H });
  const b = settleLayout(nodes, phys, { width: W, height: H });
  assert.deepEqual([...b], [...a]);
});

test("settleLayout holds a fixed node exactly in place", () => {
  // The shake button re-settles from the current arrangement with the
  // selection pinned; if a pin drifted, the selected node would slide out from
  // under the panel that describes it.
  const { nodes, edges } = fixture();
  const phys = pruneEdges(edges, { perNode: 3 });
  const start = settleLayout(nodes, phys, { width: W, height: H });
  const pinned = start.get("hub0").slice();
  const after = settleLayout(nodes, phys, { width: W, height: H, start, fixed: ["hub0"] });
  assert.deepEqual(after.get("hub0"), pinned);
  assert.notDeepEqual(after.get("hub1"), start.get("hub1"));
});

test("settleLayout seeds from `start` rather than the ring", () => {
  const { nodes, edges } = fixture();
  const phys = pruneEdges(edges, { perNode: 3 });
  const settled = settleLayout(nodes, phys, { width: W, height: H });
  // One step of decay from an already-settled arrangement should barely move
  // anything, where a fresh ring seed would move everything a long way.
  const nudged = settleLayout(nodes, phys, { width: W, height: H, start: settled, friction: 0.02 });
  let worst = 0;
  for (const [tag, p] of settled) {
    const q = nudged.get(tag);
    worst = Math.max(worst, Math.hypot(p[0] - q[0], p[1] - q[1]));
  }
  assert.ok(worst < 60, `a warm restart moved a node ${worst.toFixed(0)}px`);
});

test("settleLayout settles to the canvas aspect, not a circle", () => {
  // Guards the aspect-split gravity: a round cloud in a 16:9 card wastes the
  // sides and costs the zoom the node count needs.
  const { nodes, edges } = fixture(5, 9);
  const phys = pruneEdges(edges, { perNode: 3 });
  const box = (pos) => {
    const xs = [], ys = [];
    for (const p of pos.values()) { xs.push(p[0]); ys.push(p[1]); }
    return (Math.max(...xs) - Math.min(...xs)) / (Math.max(...ys) - Math.min(...ys));
  };
  const wide = box(settleLayout(nodes, phys, { width: W, height: H }));
  const round = box(settleLayout(nodes, phys, { width: W, height: H, aspectGravity: false }));
  assert.ok(round > 0.8 && round < 1.25, `the round settle is not round: ${round.toFixed(2)}`);
  assert.ok(wide > round * 1.25, `aspect ${wide.toFixed(2)} vs round ${round.toFixed(2)}`);
  assert.ok(wide < W / H * 1.6, `aspect ${wide.toFixed(2)} overshoots the canvas's ${(W / H).toFixed(2)}`);
});

test("settleLayout handles the degenerate graphs", () => {
  assert.equal(settleLayout([], []).size, 0);
  const one = settleLayout([{ tag: "solo", count: 3 }], [], { width: W, height: H });
  assert.deepEqual(one.get("solo"), [0, 0]);
  // Identical counts make every linearMap domain degenerate; ECharts falls back
  // to the middle of the range rather than emitting NaN, and so must we.
  const flat = settleLayout(
    [{ tag: "a", count: 7 }, { tag: "b", count: 7 }, { tag: "c", count: 7 }],
    [{ source: "a", target: "b", weight: 5 }, { source: "b", target: "c", weight: 5 }],
    { width: W, height: H },
  );
  for (const p of flat.values()) assert.ok(isFinite(p[0]) && isFinite(p[1]), `non-finite ${p}`);
});

test("settleLayout ignores an edge whose endpoints are not both nodes", () => {
  const pos = settleLayout(
    [{ tag: "a", count: 9 }, { tag: "b", count: 4 }],
    [{ source: "a", target: "ghost", weight: 5 }, { source: "a", target: "a", weight: 5 }],
    { width: W, height: H },
  );
  assert.equal(pos.size, 2);
  for (const p of pos.values()) assert.ok(isFinite(p[0]) && isFinite(p[1]));
});

/* ── fitCamera ── */

test("fitCamera returns margin, not a fit", () => {
  // ECharts fits a layout:"none" graph itself, so zoom must stay a fixed
  // margin: making it canvas-relative is exactly the bug that drew the whole
  // graph at a quarter size in the middle of an empty card.
  const near = fitCamera(new Map([["a", [-100, -50]], ["b", [100, 50]]]));
  const far  = fitCamera(new Map([["a", [-9000, -4500]], ["b", [9000, 4500]]]));
  assert.equal(near.zoom, far.zoom);
  // Just above 1: the margin, divided back out of the 80% box ECharts derives
  // from an aspect. Below 1 would mean the two are compounding again.
  assert.ok(near.zoom > 1 && near.zoom < 1.2, `zoom ${near.zoom} is not a framing margin`);
  assert.equal(fitCamera(new Map(), 1.25).zoom, 1);
});

test("fitCamera centres on the bounding box", () => {
  assert.deepEqual(fitCamera(new Map([["a", [400, 200]], ["b", [600, 300]]])).center, [500, 250]);
  assert.deepEqual(fitCamera(new Map([["a", [5, 7]]])).center, [5, 7]);
});

test("fitCamera falls back to the origin with nothing to centre on", () => {
  assert.deepEqual(fitCamera(new Map()).center, [0, 0]);
});

test("fitCamera skips non-finite points instead of poisoning the box", () => {
  const pos = new Map([["a", [-100, -50]], ["bad", [NaN, 3]], ["b", [100, 50]]]);
  assert.deepEqual(fitCamera(pos).center, [0, 0]);
});

/* ── the tuned defaults ── */

test("the settled graph is roughly the shape of the canvas it is drawn in", () => {
  // ECharts maps the node bounding box onto a rect of the same aspect inside
  // the card, so a settle whose aspect drifts from the card's leaves both
  // sides empty and packs the nodes tighter than they need to be.
  const { nodes, edges } = fixture(6, 12);
  const phys = pruneEdges(edges, { perNode: 3 });
  const pos = settleLayout(nodes, phys, { width: W, height: H });
  const xs = [], ys = [];
  for (const p of pos.values()) { xs.push(p[0]); ys.push(p[1]); }
  const aspect = (Math.max(...xs) - Math.min(...xs)) / (Math.max(...ys) - Math.min(...ys));
  const canvas = W / H;
  assert.ok(aspect > canvas * 0.6 && aspect < canvas * 1.5, `aspect ${aspect.toFixed(2)} vs canvas ${canvas.toFixed(2)}`);
});

test("the settled graph separates nodes by more than they are drawn", () => {
  // The headline property of the tuned defaults, in the units that decide it:
  // ECharts scales spacing by the fit but symbols only by (zoom-1)*0.6+1, so
  // the two are compared here at the sizes they actually paint at.
  const { nodes, edges } = fixture(6, 12);
  const phys = pruneEdges(edges, { perNode: 3, keepAbove: edgePercentile(edges, 0.9) });
  const pos = settleLayout(nodes, phys, { width: W, height: H });
  const P = nodes.map((d) => pos.get(d.tag));
  const xs = P.map((p) => p[0]), ys = P.map((p) => p[1]);
  const bw = Math.max(...xs) - Math.min(...xs), bh = Math.max(...ys) - Math.min(...ys);
  const zoom = fitCamera(pos).zoom;
  const viewW = (W / H > bw / bh) ? H * (bw / bh) : W;
  const spacing = (viewW / bw) * ECHARTS_VIEW_FILL * zoom;
  const symScale = (zoom - 1) * 0.6 + 1;
  const maxCount = nodes[0].count;
  const rad = nodes.map((d) => Math.max(8, Math.sqrt(d.count / maxCount) * 40) * symScale / 2);
  let overlaps = 0;
  for (let i = 0; i < P.length; i++) {
    for (let j = i + 1; j < P.length; j++) {
      if (Math.hypot(P[i][0] - P[j][0], P[i][1] - P[j][1]) * spacing < rad[i] + rad[j]) overlaps++;
    }
  }
  assert.equal(overlaps, 0, `${overlaps} node pairs overlap on screen`);
});
