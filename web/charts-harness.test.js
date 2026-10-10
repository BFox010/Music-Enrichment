"use strict";

// #113: the real chart components from echarts-charts.jsx, rendered with
// react-test-renderer against a stub ECharts, to pin down how they behave when
// ECharts loads late or not at all.
//
// The .jsx is compiled with the repo's own esbuild and run in a vm context the
// way the bundle runs it in the browser: one IIFE, `React` as a global, and the
// top-level component functions exported onto `window`.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const React = require("react");
const TestRenderer = require("react-test-renderer");
const { transformSync } = require("esbuild");

const SRC = fs.readFileSync(path.join(__dirname, "echarts-charts.jsx"), "utf8");
const COMPILED = transformSync(SRC, {
  loader: "jsx", jsx: "transform",
  jsxFactory: "React.createElement", jsxFragment: "React.Fragment",
}).code;
const COMPONENTS = ["ListeningMap", "AudioFeaturesChart", "SaturationChart", "TagConstellation"];
// Plain functions exported alongside the components so they can be tested directly.
const HELPERS = ["layoutConstellation", "strongestPairings"];

// One API response per endpoint, shaped like app.main's.
const API = {
  "/api/time-of-day": { years: [2024], calendar: [["2024-03-01", 4]], hour_weekday: [[12, 0, 3]] },
  "/api/audio-features": {
    scatter: [{ artist: "A", track: "T", energy: 0.5, valence: 0.4, play_count: 3 }],
    histograms: { energy: [{ bin_start: 0.0, count: 2 }] },
  },
  "/api/saturation": [{ tier: "1", count: 5 }],
  "/api/tag-graph": { nodes: [{ tag: "dub", count: 20 }, { tag: "techno", count: 18 }],
    edges: [{ source: "dub", target: "techno", weight: 3 }] },
};

/** A browser-ish realm with the chart module loaded and every side effect recorded. */
function makeRealm() {
  const rec = { scripts: [], instances: [], events: [], pollTimers: 0 };
  const listeners = {};
  const ctx = {
    React, console, Promise, Math, JSON, Object, Array, Number, String, Map, Set, Error, isFinite,
    Infinity,
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    requestAnimationFrame: (cb) => setTimeout(cb, 0),
    cancelAnimationFrame: (id) => clearTimeout(id),
    setTimeout: (fn, ms, ...a) => {
      // The old hook re-armed a 50 ms poll until window.echarts appeared.
      if (ms === 50) rec.pollTimers++;
      return setTimeout(fn, ms, ...a);
    },
    clearTimeout,
    CustomEvent: class { constructor(type) { this.type = type; } },
    addEventListener: (t, fn) => { (listeners[t] ||= []).push(fn); },
    removeEventListener: (t, fn) => { listeners[t] = (listeners[t] || []).filter((f) => f !== fn); },
    dispatchEvent: (e) => { rec.events.push(e.type); (listeners[e.type] || []).forEach((f) => f(e)); },
    fetch: async (url) => {
      const key = Object.keys(API).find((k) => url.startsWith(k));
      return { ok: true, json: async () => API[key] };
    },
    document: {
      createElement: () => ({}),
      head: { appendChild: (s) => rec.scripts.push(s) },
      documentElement: {},
    },
  };
  ctx.window = ctx;
  ctx.echartsStub = {
    init: () => {
      const inst = {
        options: [],
        setOption(o) { this.options.push(o); },
        resize() {}, dispose() {},
        handlers: {},
        on(name, fn) { this.handlers[name] = fn; },
        off(name) { delete this.handlers[name]; },
        getWidth: () => 800, getHeight: () => 400,
        // Tests that need ECharts' live item layouts (a dragged node) set this.
        itemLayout: null,
        getModel() {
          const inst = this;
          return { getSeriesByIndex: () => (inst.itemLayout
            ? { getData: () => ({ getItemLayout: (i) => inst.itemLayout(i) }) }
            : null) };
        },
      };
      rec.instances.push(inst);
      return inst;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(
    `(function(){\n${COMPILED}\n;${[...COMPONENTS, ...HELPERS].map((n) => `window.${n}=${n};`).join("")}})();`,
    ctx, { filename: "echarts-charts.jsx" },
  );
  return { ctx, rec };
}

const flush = () => new Promise((r) => setTimeout(r, 20));

/** Re-check `cond` inside act() until it holds or ~1 s passes. */
async function waitFor(cond) {
  for (let i = 0; i < 50 && !cond(); i++) await TestRenderer.act(flush);
  return cond();
}

async function mount(realm, name) {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  let renderer;
  await TestRenderer.act(async () => {
    renderer = TestRenderer.create(React.createElement(realm.ctx[name], { active: true }), {
      createNodeMock: () => ({ clientWidth: 800, clientHeight: 400 }),
    });
  });
  // Two passes: the Listening Map learns its year list before fetching a year.
  await TestRenderer.act(flush);
  await TestRenderer.act(flush);
  return renderer;
}

for (const name of COMPONENTS) {
  test(`${name} draws once ECharts finishes loading after its data arrived`, async () => {
    const realm = makeRealm();
    const renderer = await mount(realm, name);
    // Unmount even on failure: the old hook's poll loop otherwise keeps the
    // test process alive, which is the very bug under test.
    try {
      assert.equal(realm.rec.instances.length, 0, "no instance before the script loads");

      // The CDN script lands only now, after every fetch has resolved.
      await TestRenderer.act(async () => {
        realm.ctx.echarts = realm.ctx.echartsStub;
        realm.rec.scripts.forEach((s) => s.onload && s.onload());
      });
      // Tag Constellation lays out two animation frames after its instance exists.
      const allDrawn = () => realm.rec.instances.length > 0
        && realm.rec.instances.every((i) => i.options.length > 0);
      assert.ok(await waitFor(allDrawn), `every ${name} chart drew after the late init`);
    } finally {
      await TestRenderer.act(async () => renderer.unmount());
    }
  });
}

test("a failed ECharts download is announced once and nothing polls", async () => {
  const realm = makeRealm();
  const renderer = await mount(realm, "ListeningMap");
  try {
    await TestRenderer.act(async () => {
      realm.rec.scripts.forEach((s) => s.onerror && s.onerror());
      await new Promise((r) => setTimeout(r, 250));
    });
    assert.deepEqual(realm.rec.events.filter((e) => e === "ml:echarts-failed"), ["ml:echarts-failed"]);
    assert.equal(realm.rec.pollTimers, 0, "no 50 ms poll loop left running");
    assert.equal(realm.rec.instances.length, 0);
  } finally {
    await TestRenderer.act(async () => renderer.unmount());
  }
});

// #114: ECharts renders a string formatter's output as HTML, and track, artist
// and tag names come from Last.fm's crowd-sourced data.
const EVIL = '<img src=x onerror="alert(1)">';

test("names in every tooltip render as text, not markup", async () => {
  const saved = { ...API };
  API["/api/audio-features"] = {
    scatter: [{ artist: EVIL, track: "T", energy: 0.5, valence: 0.4, play_count: 3 }],
    histograms: {},
  };
  API["/api/tag-graph"] = { nodes: [{ tag: EVIL, count: 20 }, { tag: "dub", count: 18 }],
    edges: [{ source: EVIL, target: "dub", weight: 3 }] };
  API["/api/saturation"] = [{ tier: EVIL, count: 5 }];
  try {
    const shown = [];
    for (const name of ["AudioFeaturesChart", "TagConstellation", "SaturationChart"]) {
      const realm = makeRealm();
      realm.ctx.echarts = realm.ctx.echartsStub;
      const renderer = await mount(realm, name);
      try {
        await waitFor(() => realm.rec.instances.some((i) => i.options.length));
        const opt = realm.rec.instances.flatMap((i) => i.options).find((o) => o.tooltip);
        const fmt = opt.tooltip.formatter;
        const series = opt.series[0];
        if (name === "AudioFeaturesChart") shown.push(fmt({ data: series.data[0] }));
        if (name === "SaturationChart") shown.push(fmt({ name: series.data[0].name, value: 5, percent: 100 }));
        if (name === "TagConstellation") {
          shown.push(fmt({ dataType: "node", data: series.data[0] }));
          shown.push(fmt({ dataType: "edge", data: series.edges[0] }));
        }
      } finally {
        await TestRenderer.act(async () => renderer.unmount());
      }
    }
    assert.equal(shown.length, 4);
    for (const html of shown) {
      assert.ok(!html.includes("<img"), html);
      assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"), html);
    }
  } finally {
    Object.assign(API, saved);
  }
});

// #92: the Tag Constellation's layout is computed once, before the first draw,
// and handed to ECharts as fixed positions. The old force sim started zoomed
// in, re-fitted the camera on a 1.5 s timer and never stopped moving.

/** Three scenes (one disconnected), most-played first like the API returns. */
function sceneGraph() {
  const nodes = [];
  const edges = [];
  const add = (scene, n, base) => {
    for (let i = 0; i < n; i++) nodes.push({ tag: `s${scene}t${i}`, count: base - i * 3, scene });
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      if ((i + j) % 3 !== 0) edges.push({ source: `s${scene}t${i}`, target: `s${scene}t${j}`, weight: 5, strength: 0.4 });
    }
  };
  add(0, 14, 120); add(1, 10, 90); add(2, 4, 20);
  edges.push({ source: "s0t0", target: "s1t0", weight: 1, strength: 0.05 });  // scene 2 stays disconnected
  nodes.sort((a, b) => b.count - a.count);
  return { nodes, edges };
}

test("the constellation layout is deterministic", () => {
  const { ctx } = makeRealm();
  const { nodes, edges } = sceneGraph();
  const a = ctx.layoutConstellation(nodes, edges, 1.8);
  const b = ctx.layoutConstellation(nodes, edges, 1.8);
  assert.deepEqual(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)));
});

test("no two tags overlap, and every scene is one region", () => {
  const { ctx } = makeRealm();
  const { nodes, edges } = sceneGraph();
  const { pos, rad } = ctx.layoutConstellation(nodes, edges, 1.8);
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const d = Math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]);
    assert.ok(d >= rad[i] + rad[j] - 0.5, `${nodes[i].tag} overlaps ${nodes[j].tag}`);
  }
  // Each scene's members fit in a disc around their centroid that holds no
  // other scene's tag: colour regions never interleave.
  for (const s of new Set(nodes.map((n) => n.scene))) {
    const idx = nodes.map((n, i) => (n.scene === s ? i : -1)).filter((i) => i >= 0);
    const cx = idx.reduce((t, i) => t + pos[i][0], 0) / idx.length;
    const cy = idx.reduce((t, i) => t + pos[i][1], 0) / idx.length;
    const R = Math.max(...idx.map((i) => Math.hypot(pos[i][0] - cx, pos[i][1] - cy) + rad[i]));
    nodes.forEach((n, i) => {
      if (n.scene !== s) assert.ok(Math.hypot(pos[i][0] - cx, pos[i][1] - cy) > R, `${n.tag} sits inside scene ${s}`);
    });
  }
});

test("a disconnected scene is packed beside the rest, not flung to the edge", () => {
  const { ctx } = makeRealm();
  const { nodes, edges } = sceneGraph();
  const { pos, rad } = ctx.layoutConstellation(nodes, edges, 1.8);
  const lone = nodes.map((n, i) => (n.scene === 2 ? i : -1)).filter((i) => i >= 0);
  const rest = nodes.map((n, i) => (n.scene !== 2 ? i : -1)).filter((i) => i >= 0);
  const gap = Math.min(...lone.flatMap((i) => rest.map((j) =>
    Math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]) - rad[i] - rad[j])));
  assert.ok(gap < 80, `disconnected scene sits ${Math.round(gap)} px from everything else`);
});

test("the constellation draws its final layout once, with no physics and no later re-fit", async () => {
  const saved = API["/api/tag-graph"];
  const g = sceneGraph();
  API["/api/tag-graph"] = { ...g, scenes: [0, 1, 2].map((id) => ({ id, name: `scene ${id}`, tags: 1, plays: 1 })) };
  try {
    const realm = makeRealm();
    realm.ctx.echarts = realm.ctx.echartsStub;
    const renderer = await mount(realm, "TagConstellation");
    try {
      await waitFor(() => realm.rec.instances.some((i) => i.options.length));
      await TestRenderer.act(() => new Promise((r) => setTimeout(r, 1700)));  // past the old 1.5 s re-fit
      const opts = realm.rec.instances.flatMap((i) => i.options);
      const series = opts[0].series[0];
      assert.equal(series.layout, "none", "positions are fixed, not simulated");
      assert.equal(series.draggable, true, "a dragged tag moves alone; nothing is tethered by physics");
      assert.equal(series.roam, "scale", "no background panning until the reader zooms in");
      const isAnchor = (d) => String(d.name).startsWith("\u0000");
      const real = series.data.filter((d) => !isAnchor(d));
      assert.equal(real.length, g.nodes.length);
      assert.ok(real.every((d) => Number.isFinite(d.x) && Number.isFinite(d.y)));
      for (const o of opts.slice(1)) {
        const s = (o.series || [])[0] || {};
        assert.ok(s.zoom === undefined && s.center === undefined, "nothing re-fits the camera after the first draw");
      }
      // The anchors that pin the fitted box can't be hovered or clicked.
      const anchors = series.data.filter(isAnchor);
      assert.equal(anchors.length, 2);
      assert.ok(anchors.every((a) => a.symbolSize === 0 && a.tooltip.show === false && a.emphasis.disabled));
    } finally {
      await TestRenderer.act(async () => renderer.unmount());
    }
  } finally {
    API["/api/tag-graph"] = saved;
  }
});

test("holding a tag shows every scene undimmed, then it's pulled home and the view returns", async () => {
  const saved = API["/api/tag-graph"];
  const g = sceneGraph();
  API["/api/tag-graph"] = { ...g, scenes: [0, 1, 2].map((id) => ({ id, name: `scene ${id}`, tags: 1, plays: 1 })) };
  try {
    const realm = makeRealm();
    realm.ctx.echarts = realm.ctx.echartsStub;
    const renderer = await mount(realm, "TagConstellation");
    try {
      await waitFor(() => realm.rec.instances.some((i) => i.options.length));
      const inst = realm.rec.instances.find((i) => i.options.length);
      const home = inst.options[0].series[0].data.map((d) => [d.x, d.y]);
      const heldTag = inst.options[0].series[0].data[0].name;
      const partners = new Set(g.edges.flatMap((e) => (e.source === heldTag ? [e.target] : e.target === heldTag ? [e.source] : [])));
      const moved = [home[0][0] + 200, home[0][1] + 120];
      inst.itemLayout = (i) => (i === 0 ? moved : home[i]);

      // Press on tag 0 and move: that's a hold.
      inst.handlers.mousedown({ dataType: "node", dataIndex: 0, event: { event: { clientX: 0, clientY: 0 } } });
      await TestRenderer.act(async () => {
        realm.ctx.dispatchEvent({ type: "mousemove", clientX: 30, clientY: 10 });
        await new Promise((r) => setTimeout(r, 20));
      });
      const held = inst.options[inst.options.length - 1].series[0];
      const real = held.data.filter((d) => !String(d.name).startsWith("\u0000"));
      assert.ok(real.every((d) => d.itemStyle.opacity === 0.95), "nothing dims while a tag is held");
      assert.equal(held.emphasis.disabled, true, "hover highlighting is off while holding");
      assert.deepEqual([real[0].x, real[0].y], moved, "the held tag stays under the pointer");
      for (const d of real) {
        assert.equal(d.label.show, d.name === heldTag || partners.has(d.name), `label for ${d.name}`);
      }
      assert.ok(held.edges.every((e) => e.lineStyle.opacity === 0), "resting links step aside for the tethers");

      // Let go: one redraw pulls it home with the magnet easing...
      const n = inst.options.length;
      await TestRenderer.act(async () => {
        realm.ctx.dispatchEvent({ type: "mouseup" });
        await new Promise((r) => setTimeout(r, 30));
      });
      const pull = inst.options[n];
      assert.equal(typeof pull.animationEasingUpdate, "function", "the pull-back uses the magnet easing");
      assert.ok(pull.animationEasingUpdate(0.25) < 0.25 && pull.animationEasingUpdate(1) === 1,
        "slow to leave, arrives exactly home");
      assert.deepEqual(pull.series[0].data.slice(0, home.length - 2).map((d) => [d.x, d.y]), home.slice(0, home.length - 2),
        "every tag, the held one included, is handed its home position");

      // ...and once it has landed, the normal view comes back.
      await TestRenderer.act(() => new Promise((r) => setTimeout(r, 650)));
      const rest = inst.options[inst.options.length - 1].series[0];
      assert.notEqual(rest.emphasis.disabled, true, "hover highlighting is back");
      assert.ok(rest.edges.some((e) => e.lineStyle.opacity > 0), "resting links are back");

      // A press that doesn't move is a click: no hold, no redraw.
      inst.itemLayout = (i) => home[i];
      const m = inst.options.length;
      inst.handlers.mousedown({ dataType: "node", dataIndex: 1, event: { event: { clientX: 0, clientY: 0 } } });
      await TestRenderer.act(async () => {
        realm.ctx.dispatchEvent({ type: "mousemove", clientX: 2, clientY: 1 });
        realm.ctx.dispatchEvent({ type: "mouseup" });
        await new Promise((r) => setTimeout(r, 30));
      });
      assert.equal(inst.options.length, m, "a click is not a hold");
    } finally {
      await TestRenderer.act(async () => renderer.unmount());
    }
  } finally {
    API["/api/tag-graph"] = saved;
  }
});

test("resting links: every tag is joined to its strongest pairing, and nothing else", () => {
  const { ctx } = makeRealm();
  const nodes = ["a", "b", "c", "d", "e"].map((tag) => ({ tag }));
  const edges = [
    { source: "a", target: "b", strength: 0.9 },
    { source: "a", target: "c", strength: 0.5 },
    { source: "c", target: "d", strength: 0.2 },  // d's only tie, and c's best is a
    { source: "b", target: "c", strength: 0.1 },
    { source: "d", target: "e", strength: 0.2 },  // ties d's other tie: broken by name
  ];
  const keep = ctx.strongestPairings(nodes, edges);
  const k = (e) => `${e.source}-${e.target}`;
  assert.deepEqual(keep.map(k).sort(), ["a-b", "a-c", "c-d", "d-e"]);
  for (const n of nodes) assert.ok(keep.some((e) => e.source === n.tag || e.target === n.tag), `${n.tag} has a line`);
  assert.ok(!keep.some((e) => k(e) === "b-c"), "a tie that is nobody's strongest is left for the hold view");
  assert.deepEqual(ctx.strongestPairings(nodes, edges.slice().reverse()).map(k).sort(), keep.map(k).sort(),
    "the same graph draws the same lines whatever order the edges arrive in");
});

test("the chart source holds no stray control characters", () => {
  // Two separator escapes were once written out as raw NUL / 0x01 bytes. They
  // still ran, but git treated the file as binary and its diffs went blank.
  const bad = [...SRC].map((ch, i) => [ch.charCodeAt(0), i]).filter(([c]) => c < 32 && c !== 9 && c !== 10 && c !== 13);
  assert.deepEqual(bad, []);
});
