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

test("aliasKeys returns every distinct alias key, normalized", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [
    ["Clipse", " So Far Ahead "],
    ["clipse", "so far ahead"], // same key once normalized
  ]);
  assert.deepEqual(aliasKeys(t), ["clipse\x00so far ahead"]);
});

test("aliasKeys ignores malformed alias entries", () => {
  const t = track("A", "song", [["only-one-element"], "not-an-array", null, [1, 2, 3]]);
  assert.deepEqual(aliasKeys(t), []);
});

test("aliasKeys is empty when the field is missing entirely", () => {
  // The slim payload omits identity_aliases for rows with nothing to say.
  const t = track("A", "song", []);
  delete t.identity_aliases;
  assert.deepEqual(aliasKeys(t), []);
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

// ── Server/client arbitration parity ──
//
// Being alias-aware is not enough: both sides must resolve a contested key to
// the *same* track. app.metrics._track_index() assigns primary keys
// unconditionally and only setdefaults aliases, so owning a name outright
// always beats another row claiming it as an alias. Mirroring that with
// first-writer-wins for both — as the first pass at F-03 did — makes the
// winner depend on row order, and the dashboard then contradicts the API it
// was built to agree with.

test("buildTrackIndex gives a name's owner precedence regardless of row order", () => {
  const real = track("A", "song", []);
  const merged = track("A B", "song", [["a", "song"], ["a b", "song"]]);

  for (const rows of [[real, merged], [merged, real]]) {
    const idx = buildTrackIndex(rows);
    assert.equal(rows[idx.get("a\x00song")], real, "owner must win either order");
    assert.equal(rows[idx.get("a b\x00song")], merged);
  }
});

test("buildTrackIndex keeps the first claimant of a key nobody owns", () => {
  const first = track("X", "one", [["ghost", "song"]]);
  const second = track("Y", "two", [["ghost", "song"]]);
  const rows = [first, second];
  const idx = buildTrackIndex(rows);
  assert.equal(rows[idx.get("ghost\x00song")], first);
});

// ── One scrobble counts once ──
//
// Summing every key a track claims credited a play on a contested key to both
// the row that owns the name and the row listing it as an alias, inflating
// totals rather than merely misattributing them. Each scrobble resolves
// through the index to exactly one track, as app.metrics._lookup() does.

test("attachWindows credits a contested key to one track only", () => {
  const real = track("A", "song", []);
  const merged = track("A B", "song", [["a", "song"]]);
  const scrobbles = [scrobble("A", "song", "2020-01-01")];

  attachWindows([real, merged], scrobbles);

  const plays = (t) => Object.values(t.py || {}).reduce((a, b) => a + b, 0);
  assert.equal(plays(real) + plays(merged), 1);
  assert.equal(plays(real), 1, "the row owning the name takes the play");
  assert.equal(merged.py, undefined);
});

test("attachWindows totals still match the scrobble count across alias splits", () => {
  const t = track("Clipse, Pharrell Williams", "So Far Ahead", [["clipse", "so far ahead"]]);
  const scrobbles = [
    scrobble("Clipse", "So Far Ahead", "2020-01-01"),
    scrobble("Clipse", "So Far Ahead", "2020-02-01"),
    scrobble("Clipse, Pharrell Williams", "So Far Ahead", "2020-03-01"),
  ];

  attachWindows([t], scrobbles);

  assert.equal(Object.values(t.py).reduce((a, b) => a + b, 0), scrobbles.length);
});

test("buildDrill counts a contested scrobble once, against the name's owner", () => {
  const scrobbles = [
    scrobble("A", "song", "2020-01-01", { season: "winter", hour: 3, day_of_week: 1 }),
  ];

  // Both orders: the answer must come from who owns the name, not who is first.
  for (const reversed of [false, true]) {
    const real = track("A", "song", []);
    real.genres = ["Rock"];
    const merged = track("A B", "song", [["a", "song"]]);
    merged.genres = ["Jazz"];

    const drill = buildDrill(reversed ? [merged, real] : [real, merged], scrobbles);

    assert.equal(drill.season.winter.total, 1);
    assert.equal(drill.season.winter.genres.Rock, 1);
    assert.equal(drill.season.winter.genres.Jazz, undefined);
  }
});

test("buildDrill and buildCube agree with attachWindows on the same fixture", () => {
  const real = track("A", "song", []);
  const merged = track("A B", "song", [["a", "song"]]);
  const rows = [real, merged];
  const scrobbles = [
    scrobble("A", "song", "2020-01-01", { season: "winter", hour: 3, day_of_week: 1 }),
    scrobble("A B", "song", "2020-01-02", { season: "winter", hour: 3, day_of_week: 1 }),
  ];

  const cube = buildCube(rows, scrobbles);
  const drill = buildDrill(rows, scrobbles);
  attachWindows(rows, scrobbles);

  assert.deepEqual(Array.from(cube.track), [0, 1]);
  assert.equal(drill.season.winter.total, 2);
  const plays = (t) => Object.values(t.py || {}).reduce((a, b) => a + b, 0);
  assert.equal(plays(real) + plays(merged), scrobbles.length);
});
