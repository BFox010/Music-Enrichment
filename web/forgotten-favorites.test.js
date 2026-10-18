"use strict";

// #35: ForgottenFavoritesPage refetches when a threshold control changes and
// when Refresh bumps refreshVersion, and shows its evidence and empty state.
// Same harness shape as charts-harness.test.js: the real .jsx in a vm realm.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const React = require("react");
const TestRenderer = require("react-test-renderer");
const { transformSync } = require("esbuild");

const COMPILED = transformSync(fs.readFileSync(path.join(__dirname, "echarts-charts.jsx"), "utf8"), {
  loader: "jsx", jsx: "transform",
  jsxFactory: "React.createElement", jsxFragment: "React.Fragment",
}).code;

const ROW = {
  artist: "Portishead", track: "Roads", release_year: 1994, genres: ["Trip-Hop"], moods: [],
  peak_year: 2016, peak_plays: 7, recent_plays: 0, total_plays: 8, score: 7.0, last_heard: 2017,
  last_played: "2017-03-10", silence_days: 1208, recent_start: 2019, recent_end: 2020,
  as_of: "2020-06-30", sparkline: [[2016, 7], [2017, 1]],
};

function makeRealm(rows) {
  const urls = [];
  const ctx = {
    React, console, Promise, Math, JSON, Object, Array, Number, String, Map, Set, Error, isFinite,
    Infinity, setTimeout, clearTimeout,
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    moodColor: () => "#888",
    fetch: async (url) => { urls.push(url); return { ok: true, json: async () => rows }; },
    document: { createElement: () => ({}), head: { appendChild() {} }, documentElement: {} },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(`(function(){\n${COMPILED}\n})();`, ctx, { filename: "echarts-charts.jsx" });
  return { Page: ctx.ForgottenFavoritesPage, urls };
}

const flush = () => new Promise((r) => setTimeout(r, 10));
const flat = (n) => (n == null ? "" : typeof n === "string" ? n
  : Array.isArray(n) ? n.map(flat).join("") : flat(n.children));
const text = (renderer) => flat(renderer.toJSON());

async function mount(Page, props) {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  let renderer;
  await TestRenderer.act(async () => { renderer = TestRenderer.create(React.createElement(Page, props)); });
  await TestRenderer.act(flush);
  return renderer;
}

test("threshold changes and Refresh each refetch; a plain re-render does not", async () => {
  const { Page, urls } = makeRealm([ROW]);
  const r = await mount(Page, { active: true, refreshVersion: 0 });
  assert.deepEqual(urls, ["/api/forgotten-favorites?top=100&min_peak=5&recent_years=2"]);

  const [peakSel, windowSel] = r.root.findAllByType("select");
  await TestRenderer.act(async () => { peakSel.props.onChange({ target: { value: "10" } }); });
  await TestRenderer.act(flush);
  await TestRenderer.act(async () => { windowSel.props.onChange({ target: { value: "1" } }); });
  await TestRenderer.act(flush);
  assert.equal(urls.at(-1), "/api/forgotten-favorites?top=100&min_peak=10&recent_years=1");

  await TestRenderer.act(async () => r.update(React.createElement(Page, { active: true, refreshVersion: 0 })));
  await TestRenderer.act(flush);
  assert.equal(urls.length, 3, "same version and thresholds: no refetch");

  await TestRenderer.act(async () => r.update(React.createElement(Page, { active: true, refreshVersion: 1 })));
  await TestRenderer.act(flush);
  assert.equal(urls.length, 4, "Refresh refetches with the thresholds still applied");
  assert.equal(urls.at(-1), "/api/forgotten-favorites?top=100&min_peak=10&recent_years=1");
  await TestRenderer.act(async () => r.unmount());
});

test("each row shows its evidence and the rule names the real window", async () => {
  const { Page } = makeRealm([ROW]);
  const r = await mount(Page, { active: true, refreshVersion: 0 });
  const t = text(r);
  for (const s of ["Peak 2016 · 7 plays", "Mar 2017", "Silent ", "3.3 yr", "2019–2020", "30 Jun 2020", "1 qualifies"]) {
    assert.ok(t.includes(s), `missing ${JSON.stringify(s)}`);
  }
  await TestRenderer.act(async () => r.unmount());
});

test("nothing qualifying explains itself and keeps the controls", async () => {
  const { Page } = makeRealm([]);
  const r = await mount(Page, { active: true, refreshVersion: 0 });
  assert.ok(text(r).includes("Nothing qualifies at these settings"));
  assert.equal(r.root.findAllByType("select").length, 2);
  await TestRenderer.act(async () => r.unmount());
});
