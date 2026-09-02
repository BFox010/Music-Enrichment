/* graph-layout.js — pure force-layout helpers for the Tag Constellation.

   No React, no DOM, no ECharts: loaded as a plain <script defer> BEFORE
   app.bundle.js (so these top-level declarations are globals the bundle
   reads) and importScripts()'d by data-worker.js, exactly like
   data-processing.js. Deliberately outside the esbuild bundle.

   Why the constellation owns its layout instead of letting ECharts run one:
   ECharts' force layout is live physics that never stops, and every
   setOption on a live force graph rebuilds the simulation and resets
   friction to its initial value (forceLayout.js re-creates the instance;
   forceHelper.js re-reads `opts.friction`). Framing, dragging and click
   highlighting therefore all fought the simulation — a fit re-heated it, a
   drag re-heated it and then pulled the node back, and a restyle re-heated
   it too. Settling here, up front, lets the chart render with
   layout:"none" and explicit coordinates: one node moves when you drag it,
   and a restyle is only a restyle.

   settleLayout() follows ECharts' own model (echarts 5.6
   chart/graph/forceHelper.js `step()` plus the parameter mapping in
   forceLayout.js) so the arrangement still looks like the one the page used
   to grow into. It is deterministic: nodes seed on a ring by index and the
   only random branch in ECharts' step — two nodes at the exact same point —
   is driven by a seeded PRNG here. */

const GRAPH_LAYOUT_DEFAULTS = {
  width: 1100,
  height: 620,
  // Real two-element [min, max] ranges. ECharts maps a node's `value` and an
  // edge's `weight` across these with linearMap; passing a per-node array
  // instead does NOT give per-node forces — forceLayout.js reads only
  // elements 0 and 1 and treats them as the range.
  //
  // Tuned against the live /api/tag-graph output for all three fields at all
  // three density presets, scoring how many node pairs overlap once ECharts
  // has fitted the result (see the note on aspectGravity for how it fits, and
  // the symbol scaling that goes with it). These land 6 overlapping pairs
  // across the seven graphs, all of them in the 268-node Dense preset and none
  // worse than 6px; the starting values left 173.
  repulsion: [800, 6000],
  edgeLength: [180, 460],
  gravity: 0.16,
  // ECharts pulls every node toward the view centre with the same force on
  // both axes, so the settled cloud comes out round. That wastes a wide card:
  // for a layout:"none" graph ECharts maps the node bounding box onto an
  // aspect-matched rect inside the canvas, so a round cloud is fitted into a
  // square and both sides of the card go empty — 45% of the canvas used, and
  // 86 overlapping pairs. Splitting gravity by the canvas aspect settles an
  // ellipse of the card's shape instead: 67% used, 6 pairs. Set false for a
  // round settle.
  aspectGravity: true,
  friction: 0.4,
  // Seed ring radius as a fraction of min(width, height).
  seedRadius: 0.46,
  // ECharts' own stopping rule and decay; maxSteps is only a runaway guard
  // (friction 0.4 decaying 0.992 a step reaches 0.01 in 460 steps).
  minFriction: 0.01,
  decay: 0.992,
  maxSteps: 4000,
  seed: 0x5eed,
};

/* linearMap over a possibly-degenerate domain, matching ECharts' behaviour of
   falling back to the midpoint of the range when every value is identical
   (there `linearMap` returns NaN and the caller substitutes the midpoint). */
function _linearMap(value, domain, range) {
  const d0 = domain[0], d1 = domain[1];
  const r0 = range[0], r1 = range[1];
  if (!isFinite(value) || d1 === d0) return (r0 + r1) / 2;
  return r0 + ((value - d0) / (d1 - d0)) * (r1 - r0);
}

/* mulberry32 — small, fast, and seeded, so a given graph always settles to
   the same arrangement. Only reached by the coincident-node branch below. */
function _rng(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* Weight at the p-th percentile of an edge list (p in 0..1). Used to pick the
   threshold that separates the physics backbone from the haze; the caller
   passes the result to both pruneEdges() and the weak-edge styling so the two
   always agree on which edges are weak. */
function edgePercentile(edges, p) {
  if (!edges || !edges.length) return 0;
  const w = edges.map((e) => e.weight || 0).sort((a, b) => a - b);
  const i = Math.min(w.length - 1, Math.max(0, Math.round((w.length - 1) * p)));
  return w[i];
}

/* The subset of edges the simulation runs on: every node's `perNode`
   strongest links, plus every link at or above `keepAbove`.

   The full edge set is an over-constrained truss — ECharts springs close 40%
   of their length error per step while repulsion at that distance is ~75x
   weaker, so with 715 edges across 105 nodes the graph moves as one rigid
   body. Pruning to a backbone is what lets the settled shape actually spread.
   Every edge is still *drawn*; only the physics sees the subset.

   `keepAbove` defaults to the input's 90th-percentile weight. Pass it
   explicitly (from edgePercentile) for a stable threshold — recomputing the
   percentile over an already-pruned list yields a higher one, so the default
   is not idempotent under re-pruning. */
function pruneEdges(edges, opts) {
  const o = opts || {};
  const list = edges || [];
  const perNode = o.perNode == null ? 3 : o.perNode;
  const keepAbove = o.keepAbove == null ? edgePercentile(list, 0.9) : o.keepAbove;

  const keep = new Set();
  const byNode = new Map();
  for (let i = 0; i < list.length; i++) {
    const e = list[i];
    const w = e.weight || 0;
    if (w >= keepAbove) keep.add(i);
    for (const end of [e.source, e.target]) {
      let arr = byNode.get(end);
      if (!arr) byNode.set(end, (arr = []));
      arr.push(i);
    }
  }
  if (perNode > 0) {
    for (const arr of byNode.values()) {
      // Index breaks weight ties so the result never depends on sort stability.
      arr.sort((a, b) => (list[b].weight || 0) - (list[a].weight || 0) || a - b);
      for (let k = 0; k < Math.min(perNode, arr.length); k++) keep.add(arr[k]);
    }
  }
  return list.filter((_, i) => keep.has(i));
}

/* Map<tag, [{tag, weight}, …]> sorted by weight desc, symmetric, with an
   entry for every node (isolated ones map to an empty array). Edges naming a
   tag that is not a node are dropped from both sides rather than one. */
function adjacency(nodes, edges) {
  const adj = new Map();
  for (const n of nodes || []) adj.set(n.tag, []);
  for (const e of edges || []) {
    const a = adj.get(e.source), b = adj.get(e.target);
    if (!a || !b || e.source === e.target) continue;
    a.push({ tag: e.target, weight: e.weight });
    b.push({ tag: e.source, weight: e.weight });
  }
  for (const arr of adj.values()) {
    arr.sort((x, y) => y.weight - x.weight || (x.tag < y.tag ? -1 : x.tag > y.tag ? 1 : 0));
  }
  return adj;
}

/* Run the force model to convergence and return Map<tag, [x, y]>, centred on
   the origin (gravity pulls there). Only the relative geometry matters —
   ECharts maps whatever bounding box comes out onto the canvas.

   `nodes` are {tag, count}, `edges` {source, target, weight} — pass the
   pruned set. Options override GRAPH_LAYOUT_DEFAULTS; two extras drive the
   "shake" path: `start` (Map<tag, [x, y]> to seed from instead of the ring)
   and `fixed` (iterable of tags to hold in place). */
function settleLayout(nodes, edges, opts) {
  const o = Object.assign({}, GRAPH_LAYOUT_DEFAULTS, opts || {});
  const out = new Map();
  const n = (nodes || []).length;
  if (!n) return out;

  const idx = new Map();
  for (let i = 0; i < n; i++) idx.set(nodes[i].tag, i);

  // Node repulsion, and the spring weight `w`, both come from the play count
  // mapped across the repulsion range — the same value serves as both in
  // ECharts (`{w: rep, rep: rep}`), so a big hub both pushes harder and
  // yields less to its springs.
  let vMin = Infinity, vMax = -Infinity;
  for (let i = 0; i < n; i++) {
    const v = nodes[i].count || 0;
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
  }
  const rep = new Float64Array(n);
  for (let i = 0; i < n; i++) rep[i] = _linearMap(nodes[i].count || 0, [vMin, vMax], o.repulsion);

  // Edge rest lengths: heavier pairs sit closer, so the range is inverted
  // before the map (ECharts does the same swap).
  const src = [], dst = [], rest = [];
  let wMin = Infinity, wMax = -Infinity;
  for (const e of edges || []) {
    const w = e.weight || 0;
    if (w < wMin) wMin = w;
    if (w > wMax) wMax = w;
  }
  const lenRange = [o.edgeLength[1], o.edgeLength[0]];
  for (const e of edges || []) {
    const a = idx.get(e.source), b = idx.get(e.target);
    if (a === undefined || b === undefined || a === b) continue;
    src.push(a);
    dst.push(b);
    rest.push(_linearMap(e.weight || 0, [wMin, wMax], lenRange));
  }
  const eLen = src.length;

  const px = new Float64Array(n), py = new Float64Array(n);
  const ppx = new Float64Array(n), ppy = new Float64Array(n);
  const fixed = new Uint8Array(n);
  const R = Math.min(o.width, o.height) * o.seedRadius;
  for (let i = 0; i < n; i++) {
    const seeded = o.start && o.start.get(nodes[i].tag);
    if (seeded && isFinite(seeded[0]) && isFinite(seeded[1])) {
      px[i] = seeded[0];
      py[i] = seeded[1];
    } else if (n === 1) {
      px[i] = 0;
      py[i] = 0;
    } else {
      const a = (2 * Math.PI * i) / n;
      px[i] = R * Math.cos(a);
      py[i] = R * Math.sin(a);
    }
    ppx[i] = px[i];
    ppy[i] = py[i];
  }
  if (o.fixed) for (const tag of o.fixed) { const i = idx.get(tag); if (i !== undefined) fixed[i] = 1; }

  // A cloud's spread along an axis responds to that axis's gravity as roughly
  // its -2/3 power (measured across all six presets), so the gravity ratio
  // has to be the target aspect to the 3/2 to land on it. Clamped so a freak
  // container shape cannot settle the graph into a line.
  const ar = o.height > 0 ? Math.min(2.5, Math.max(0.4, o.width / o.height)) : 1;
  const skew = o.aspectGravity ? Math.pow(ar, 1.5) : 1;
  const gx = skew < 1 ? o.gravity / skew : o.gravity;
  const gy = skew > 1 ? o.gravity * skew : o.gravity;

  const rand = _rng(o.seed);
  let friction = o.friction;
  let steps = 0;
  for (;;) {
    // Springs: positional constraints, each closing `friction` of its own
    // length error per step, split between the two ends by node weight.
    for (let k = 0; k < eLen; k++) {
      const i = src[k], j = dst[k];
      let dx = px[j] - px[i], dy = py[j] - py[i];
      const len = Math.sqrt(dx * dx + dy * dy);
      const d = len - rest[k];
      if (len > 0) { dx /= len; dy /= len; } else { dx = 0; dy = 0; }
      let w = rep[j] / (rep[i] + rep[j]);
      if (!isFinite(w)) w = 0;
      if (!fixed[i]) { px[i] += dx * w * d * friction; py[i] += dy * w * d * friction; }
      if (!fixed[j]) { px[j] -= dx * (1 - w) * d * friction; py[j] -= dy * (1 - w) * d * friction; }
    }
    // Gravity toward the origin. The only absolute anchor in the model, which
    // is why a stiff graph can rotate about it for free — see pruneEdges.
    for (let i = 0; i < n; i++) {
      if (fixed[i]) continue;
      px[i] -= px[i] * gx * friction;
      py[i] -= py[i] * gy * friction;
    }
    // Repulsion accumulates into the previous-position vector with the
    // opposite sign, and the integration step below turns that into a push
    // apart. Effective falloff is 1/d: the vector is not normalised, so the
    // 1/d² factor cancels one power of the distance.
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = px[j] - px[i], dy = py[j] - py[i];
        let d = Math.sqrt(dx * dx + dy * dy);
        if (d === 0) { dx = rand() - 0.5; dy = rand() - 0.5; d = 1; }
        const f = (rep[i] + rep[j]) / d / d;
        if (!fixed[i]) { ppx[i] += dx * f; ppy[i] += dy * f; }
        if (!fixed[j]) { ppx[j] -= dx * f; ppy[j] -= dy * f; }
      }
    }
    for (let i = 0; i < n; i++) {
      if (fixed[i]) continue;
      px[i] += (px[i] - ppx[i]) * friction;
      py[i] += (py[i] - ppy[i]) * friction;
      ppx[i] = px[i];
      ppy[i] = py[i];
    }
    friction *= o.decay;
    if (friction < o.minFriction || ++steps >= o.maxSteps) break;
  }

  for (let i = 0; i < n; i++) {
    out.set(nodes[i].tag, [
      isFinite(px[i]) ? px[i] : 0,
      isFinite(py[i]) ? py[i] : 0,
    ]);
  }
  return out;
}

/* The camera for an ECharts graph series showing `positions`.

   Deliberately NOT a fit: for a layout:"none" graph ECharts already fits. Its
   createView.js takes the bounding box of the nodes' own x/y, builds a view
   rect of that same aspect inside the canvas, and maps one onto the other, so
   `zoom: 1` is the fit and anything smaller shrinks the graph inside it. The
   chart used to compute a canvas-relative zoom of its own and hand that to the
   same field, which multiplied the two fits together and drew the whole graph
   at a quarter size in the middle of an empty card.

   So the zoom here is framing only, and it is slightly above 1. ECharts fits
   node *centres* to the rect edges, leaving the outermost symbols and their
   labels hanging over the side, so `margin` pulls everything in far enough to
   clear them — but that rect is itself only 80% of the card, because
   getLayoutRect falls back to 80% of the binding dimension whenever it derives
   a rect from an aspect with no explicit width or height. Dividing that back
   out is what makes `margin` the real margin instead of compounding with it
   and leaving the graph in the middle of a half-empty card.

   The centre is the bounding box's, which is also ECharts' default — passing
   it explicitly is what lets the Fit button undo a pan. */
const ECHARTS_VIEW_FILL = 0.8;

function fitCamera(positions, margin) {
  const m = margin > 0 ? margin : 1.18;
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  if (positions) {
    for (const p of positions.values()) {
      if (!p || !isFinite(p[0]) || !isFinite(p[1])) continue;
      if (p[0] < xMin) xMin = p[0];
      if (p[0] > xMax) xMax = p[0];
      if (p[1] < yMin) yMin = p[1];
      if (p[1] > yMax) yMax = p[1];
    }
  }
  const zoom = 1 / (ECHARTS_VIEW_FILL * m);
  if (!isFinite(xMin)) return { zoom, center: [0, 0] };
  return { zoom, center: [(xMin + xMax) / 2, (yMin + yMax) / 2] };
}
