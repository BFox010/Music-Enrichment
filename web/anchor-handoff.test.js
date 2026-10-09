"use strict";

// #102: computeAnchor() runs inside the worker, a separate realm with its own
// ANCHOR. Before the worker posted it back, the main thread kept its
// wall-clock seed, so a stale library showed last month's plays under "This
// month" and the "data through" note never rendered. Each realm here is its
// own vm context, the way the browser gives the worker its own global scope.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PROCESSING = fs.readFileSync(path.join(__dirname, "data-processing.js"), "utf8");
const WORKER = fs.readFileSync(path.join(__dirname, "data-worker.js"), "utf8");

function workerRealm() {
  const posted = [];
  const ctx = vm.createContext({ Date, Math, JSON, Object, Array, Map, Set, Number, String,
    Uint8Array, Uint16Array, Uint32Array, Int32Array, Float32Array, Float64Array });
  ctx.self = { postMessage: (msg) => posted.push(msg) };
  ctx.importScripts = () => vm.runInContext(PROCESSING, ctx, { filename: "data-processing.js" });
  vm.runInContext(WORKER, ctx, { filename: "data-worker.js" });
  return { ctx, posted };
}

function mainRealm() {
  const ctx = vm.createContext({ Date, Math, JSON, Object, Array, Map, Set, Number, String });
  vm.runInContext(PROCESSING, ctx, { filename: "data-processing.js" });
  return ctx;
}

const track = { artist: "A", track: "T", artist_normalized: "a", track_normalized: "t",
  genres: [], mood_tags: [] };
// Years in the past, so the anchor is stale on any clock this test will meet.
const scrobble = (stamp) => ({ artist: "A", track: "T", artist_normalized: "a",
  track_normalized: "t", scrobbled_at: `${stamp}T12:00:00Z`,
  year: Number(stamp.slice(0, 4)), month: Number(stamp.slice(5, 7)) });

test("the main thread adopts the anchor the worker computed", () => {
  const worker = workerRealm();
  worker.ctx.self.onmessage({ data: { trRows: [track],
    scRows: [scrobble("2020-08-01"), scrobble("2020-08-15")] } });
  assert.equal(worker.posted.length, 1);
  assert.ok(worker.posted[0].ok, worker.posted[0].error);

  const main = mainRealm();
  // What postMessage does to the payload on its way across realms.
  const msg = structuredClone(worker.posted[0]);
  vm.runInContext("adoptAnchor", main)(msg.anchor);
  const anchor = vm.runInContext("ANCHOR", main);

  assert.equal(anchor.stale, true);
  assert.equal(anchor.dataEnd, "2020-08-15");
  assert.equal(anchor.curMonthKey, "2020-08");
  assert.equal(anchor.curYear, 2020);
  assert.ok(anchor.date instanceof Date || Object.prototype.toString.call(anchor.date) === "[object Date]");
  assert.equal(vm.runInContext("anchorPeriodKey('month_this')", main), "2020-08");
  assert.equal(vm.runInContext("anchorPeriodKey('month_last')", main), "2020-07");
});

test("without the hand-off the main thread keeps its wall-clock seed", () => {
  // Documents the failure mode the hand-off exists for.
  const main = mainRealm();
  const anchor = vm.runInContext("ANCHOR", main);
  assert.equal(anchor.stale, false);
  assert.equal(anchor.curYear, new Date().getUTCFullYear());
});

test("adoptAnchor revives a date that lost its type", () => {
  const main = mainRealm();
  vm.runInContext("adoptAnchor", main)({ date: "2020-08-15T12:00:00.000Z", stale: true });
  assert.equal(vm.runInContext("ANCHOR.date instanceof Date", main), true);
});
