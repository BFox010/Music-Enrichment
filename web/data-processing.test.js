"use strict";

// F-03: the browser must resolve a scrobble through every identity the track
// carries (its own name pair plus every alias Phase 4e folded into it), the
// same way app.metrics._track_index() already does server-side. Without
// this, a play logged under a historical artist credit passes the server's
// alias-aware integrity check but silently disappears from client-computed
// windows, drill-downs, and cross-filter totals.
//
// data-processing.js has no module system by design (it is loaded as a plain
// global via <script defer> on the main thread and importScripts() inside
// the worker, so a bundler/ESM step would defeat the point). vm.runInThisContext
// executes its source the same way a browser <script> tag would, attaching
// its top-level `function` declarations to the Node process's global object.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "data-processing.js"), "utf8");
vm.runInThisContext(SRC, { filename: "data-processing.js" });

function track(artist, trackName, aliases) {
  return {
    artist,
    track: trackName,
    artist_normalized: artist.toLowerCase(),
    track_normalized: trackName.toLowerCase(),
    identity_aliases: aliases || [],
    genres: [],
    mood_tags: [],
  };
}

function scrobble(artist, trackName, stamp, extra) {
  const year = Number(stamp.slice(0, 4));
  const month = Number(stamp.slice(5, 7));
  return Object.assign(
    {
      artist,
      track: trackName,
      artist_normalized: artist.toLowerCase(),
      track_normalized: trackName.toLowerCase(),
      scrobbled_at: `${stamp}T12:00:00Z`,
      year,
      month,
    },
    extra
  );
}

test("trackKeys returns the primary key plus every distinct alias key", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [
    ["clipse", "so far ahead"],
    ["clipse, pharrell williams", "so far ahead"], // duplicate of the primary
  ]);
  const keys = trackKeys(t);
  assert.equal(keys.length, 2);
  assert.ok(keys.includes("clipse, pharrell williams\x00so far ahead"));
  assert.ok(keys.includes("clipse\x00so far ahead"));
});

test("trackKeys ignores malformed alias entries", () => {
  const t = track("A", "song", [["only-one-element"], "not-an-array", null, [1, 2, 3]]);
  assert.deepEqual(trackKeys(t), ["a\x00song"]);
});

test("buildTrackIndex resolves a scrobble that only matches through an alias", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [["clipse", "so far ahead"]]);
  const idx = buildTrackIndex([t]);
  const s = scrobble("Clipse", "So Far Ahead", "2025-01-01");
  assert.equal(idx.get(trackKey(s)), 0);
});

test("buildTrackIndex never lets an alias shadow a track that owns that name outright", () => {
  const real = track("A", "song", []);
  const merged = track("A B", "song", [["a", "song"], ["a b", "song"]]);
  const idx = buildTrackIndex([real, merged]);
  assert.equal(idx.get("a\x00song"), 0);
  assert.equal(idx.get("a b\x00song"), 1);
});

test("attachWindows sums plays logged under an alias into the same track", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [["clipse", "so far ahead"]]);
  const scrobbles = [
    scrobble("Clipse", "So Far Ahead", "2025-01-01"),
    scrobble("Clipse, Pharrell Williams", "So Far Ahead", "2025-01-02"),
  ];
  attachWindows([t], scrobbles);
  const totalPy = Object.values(t.py).reduce((a, b) => a + b, 0);
  assert.equal(totalPy, 2);
});

test("attachWindows leaves a track untouched when nothing scrobbled matches it", () => {
  const t = track("Nobody", "Nothing", []);
  const scrobbles = [scrobble("Somebody Else", "Something Else", "2025-01-01")];
  attachWindows([t], scrobbles);
  assert.equal(t.py, undefined);
});

test("buildDrill counts an alias-only scrobble against the track", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [["clipse", "so far ahead"]]);
  t.genres = ["Rap"];
  const scrobbles = [
    scrobble("Clipse", "So Far Ahead", "2025-01-01", { season: "winter", hour: 10, day_of_week: 2 }),
  ];
  const drill = buildDrill([t], scrobbles);
  assert.equal(drill.season.winter.genres.Rap, 1);
  assert.equal(drill.hour[10].genres.Rap, 1);
  assert.equal(drill.dow[2].genres.Rap, 1);
});

test("buildCube resolves the track index for an alias-only scrobble", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [["clipse", "so far ahead"]]);
  const scrobbles = [scrobble("Clipse", "So Far Ahead", "2025-01-01", { hour: 5, day_of_week: 1 })];
  const cube = buildCube([t], scrobbles);
  assert.equal(cube.track[0], 0);
});

test("buildCube marks an unmatched scrobble with no track index", () => {
  const t = track("Somebody", "Something", []);
  const scrobbles = [scrobble("Nobody", "Nothing", "2025-01-01", { hour: 5, day_of_week: 1 })];
  const cube = buildCube([t], scrobbles);
  assert.equal(cube.track[0], -1);
});
