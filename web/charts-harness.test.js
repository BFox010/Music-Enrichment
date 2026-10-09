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
        resize() {}, dispose() {}, on() {},
        getWidth: () => 800, getHeight: () => 400,
        getModel: () => ({ getSeriesByIndex: () => null }),
      };
      rec.instances.push(inst);
      return inst;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(
    `(function(){\n${COMPILED}\n;${COMPONENTS.map((n) => `window.${n}=${n};`).join("")}})();`,
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
