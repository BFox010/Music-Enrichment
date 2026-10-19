"use strict";

// #119: cross-filter targets are divs, spans and table cells, so keyboard
// access comes from pressable() in charts.jsx. These render the real
// components (compiled like charts-harness.test.js) and drive them by key.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const React = require("react");
const TestRenderer = require("react-test-renderer");
const { transformSync } = require("esbuild");

function compile(name) {
  const src = fs.readFileSync(path.join(__dirname, name), "utf8");
  const code = transformSync(src, {
    loader: "jsx", jsx: "transform",
    jsxFactory: "React.createElement", jsxFragment: "React.Fragment",
  }).code;
  return `(function(){\n${code}\n})();`;
}

function realm() {
  const ctx = { React, console, Math, Object, Array, String, Number, JSON, Map, Set };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(compile("charts.jsx"), ctx, { filename: "charts.jsx" });
  vm.runInContext(compile("explorer.jsx"), ctx, { filename: "explorer.jsx" });
  return ctx;
}

const key = (k) => ({ key: k, preventDefault() {} });

test("pressable fires once on Enter or Space and ignores other keys", () => {
  const { pressable } = realm();
  let n = 0;
  const p = pressable(() => n++, { pressed: false });
  assert.equal(p.role, "button");
  assert.equal(p.tabIndex, 0);
  assert.equal(p["aria-pressed"], false);
  p.onKeyDown(key("Enter"));
  p.onKeyDown(key(" "));
  p.onKeyDown(key("a"));
  assert.equal(n, 2);
  assert.equal(pressable(() => {}, { role: null }).role, undefined, "a cell keeps its own role");
});

function renderTable(ctx, sort, onSort, onPickArtist) {
  const rows = [{ i: 0, track: "T", artist: "A", play: 3, tags: [], styles: [], genres: [], moods: [] }];
  let r;
  TestRenderer.act(() => {
    r = TestRenderer.create(React.createElement(ctx.TrackTable, { rows, sort, onSort, onPickArtist }));
  });
  return r;
}

test("table headers carry valid aria-sort, only on the active column", () => {
  const ctx = realm();
  const sortOf = (sort) => {
    const r = renderTable(ctx, sort, () => {}, () => {});
    const out = {};
    for (const th of r.root.findAllByType("th")) {
      if (th.props.tabIndex === 0) out[th.children[0]] = th.props["aria-sort"];
    }
    return out;
  };
  // "plays" is most-first; "artist" and "track" are A–Z.
  assert.deepEqual(sortOf("plays"), { Track: undefined, Artist: undefined, Year: undefined, Plays: "descending" });
  assert.equal(sortOf("plays_asc").Plays, "ascending");
  assert.equal(sortOf("artist").Artist, "ascending");
  assert.equal(sortOf("artist_desc").Artist, "descending");
  assert.equal(sortOf("year_desc").Year, "descending");
  assert.deepEqual(Object.values(sortOf("shuffle")), [undefined, undefined, undefined, undefined]);
});

test("Enter on a header sorts, and on the artist cell filters, exactly once", () => {
  const ctx = realm();
  const sorted = [], picked = [];
  const r = renderTable(ctx, "plays", (k) => sorted.push(k), (a) => picked.push(a));
  const artistTh = r.root.findAllByType("th").find((th) => th.children[0] === "Artist");
  artistTh.props.onKeyDown(key("Enter"));
  assert.deepEqual(sorted, ["artist"]);
  const span = r.root.findByProps({ "aria-label": "Filter by A" });
  assert.equal(span.props.onClick, undefined, "the cell's click handles the mouse; a span onClick would double-fire");
  span.props.onKeyDown(key("Enter"));
  assert.deepEqual(picked, ["A"]);
});
