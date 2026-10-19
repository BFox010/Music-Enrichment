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

// #120: the app bar read meta.scrobbleRange, which only the sample library
// sets, so live data always showed "—".
test("scrobbleYearRange spans the years present", () => {
  assert.equal(scrobbleYearRange({ 2019: 4, 2016: 1, 2026: 9 }), "2016–2026");
  assert.equal(scrobbleYearRange({ 2024: 3 }), "2024");
  assert.equal(scrobbleYearRange({}), "");
  assert.equal(scrobbleYearRange(undefined), "");
});

// #113: drill and cube cross-join tracks with scrobbles, so they're valid only
// when both came from the same load. A single-file drop used to keep the
// previous load's cube and drill alongside the new tracks.
test("a single-file load clears the stale drill and cube", () => {
  const prev = {
    meta: { trackCount: 1, scrobbleCount: 1 },
    tracks: [{ artist: "Old" }],
    scrobbles: { total: 1 },
  };
  const tracksOnly = processLibrary([track("New", "Song")], null);
  assert.equal(tracksOnly.cube, null);

  const next = loadedState(prev, tracksOnly);
  assert.equal(next.cube, null);
  assert.equal(next.drill, null);
  assert.equal(next.data.tracks[0].artist, "New");
  assert.equal(next.data.scrobbles, prev.scrobbles, "the side not loaded is kept");
  assert.equal(next.data.meta.isSample, false);
});

test("a full load carries its own drill and cube", () => {
  const both = processLibrary(
    [track("A", "T")],
    [scrobble("A", "T", "2026-03-01")],
  );
  const next = loadedState({ meta: {} }, both);
  assert.ok(next.cube && next.drill);
  assert.equal(next.data.meta.scrobbleCount, 1);
});

test("an empty load changes nothing", () => {
  assert.equal(loadedState({ meta: {} }, { nt: null, ns: null }), null);
  assert.equal(loadedState({ meta: {} }, null), null);
});

// #113: localStorage throws when site storage is blocked, and the prefs were
// read during first render, so a blocked browser got a blank page.
test("preferences survive blocked storage", () => {
  const saved = globalThis.localStorage;
  globalThis.localStorage = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("SecurityError"); },
  };
  try {
    assert.equal(readPref("ml.density"), null);
    assert.doesNotThrow(() => writePref("ml.density", "compact"));
  } finally {
    globalThis.localStorage = saved;
  }
});

// #90: chip dot and chart line share a colour keyed to the artist, so a toggle
// must never repaint anyone else.
test("colour slots: new picks take the lowest free slot", () => {
  assert.deepEqual(assignColorSlots(null, ["A", "B", "C"], 8), { A: 0, B: 1, C: 2 });
});

test("colour slots: deselecting frees only that slot, survivors keep theirs", () => {
  const first = assignColorSlots(null, ["A", "B", "C", "D"], 8);
  const dropped = assignColorSlots(first, ["A", "C", "D"], 8);
  assert.deepEqual(dropped, { A: 0, C: 2, D: 3 });
  const added = assignColorSlots(dropped, ["E", "A", "C", "D"], 8);
  assert.deepEqual(added, { A: 0, C: 2, D: 3, E: 1 }, "E reuses B's freed slot");
});

test("colour slots: eight picks from eight colours never collide", () => {
  const s = assignColorSlots({ x: 5 }, ["a", "b", "c", "d", "e", "f", "g", "h"], 8);
  assert.equal(new Set(Object.values(s)).size, 8);
  assert.equal(s.x, undefined, "an unselected artist holds no slot");
});

test("colour slots: past the palette, extras share without moving anyone", () => {
  const eight = assignColorSlots(null, ["a", "b", "c", "d", "e", "f", "g", "h"], 8);
  const nine = assignColorSlots(eight, ["a", "b", "c", "d", "e", "f", "g", "h", "i"], 8);
  for (const k of Object.keys(eight)) assert.equal(nine[k], eight[k]);
  assert.equal(nine.i, 0);
});

test("picker chips: selected first by play count, then the filter's matches", () => {
  const ranked = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"].map((name, i) => ({ name, total: 50 - i }));
  const sel = new Set(["Epsilon", "Beta"]);
  const all = orderPickerChips(ranked, sel, "", 10);
  assert.deepEqual(all.chips.map((a) => a.name), ["Beta", "Epsilon", "Alpha", "Gamma", "Delta"]);
  assert.deepEqual(all.picked.map((a) => a.name), ["Beta", "Epsilon"]);

  const filtered = orderPickerChips(ranked, sel, "ta", 10);
  assert.deepEqual(filtered.chips.map((a) => a.name), ["Beta", "Epsilon", "Delta"],
    "a selected chip stays visible even when the filter excludes it");

  const capped = orderPickerChips(ranked, sel, "", 3);
  assert.deepEqual(capped.chips.map((a) => a.name), ["Beta", "Epsilon", "Alpha"]);
  assert.equal(capped.hidden, 2);
});

// #120: a season the new timeframe empties is inert on screen, so a selection
// on it could not be clicked off.
test("dropEmptySeason clears only a season the window has no plays in", () => {
  const sel = { hour: 9, dow: null, season: "summer" };
  assert.deepEqual(dropEmptySeason(sel, { summer: 0, fall: 4 }), { hour: 9, dow: null, season: null });
  assert.equal(dropEmptySeason(sel, { summer: 3 }), sel, "unchanged object, so React bails out");
  const none = { hour: null, dow: null, season: null };
  assert.equal(dropEmptySeason(none, { summer: 0 }), none);
});

// #120: a scrobble file not named "scrobble…" was loaded as tracks, and a
// malformed .json threw out of the drop handler with no message.
test("detectDropKind goes by row shape, not file name", () => {
  const sc = { artist: "A", track: "T", scrobbled_at: "2024-01-01T00:00:00Z", hour: 0, day_of_week: 0 };
  const tr = { canonical_track_id: "norm:a|t", artist: "A", track: "T", play_count: 3 };
  assert.equal(detectDropKind([sc, sc]), "scrobbles");
  assert.equal(detectDropKind([tr]), "tracks");
  assert.equal(detectDropKind([{ artist: "A", track: "T" }]), "tracks");
  assert.equal(detectDropKind([{ hour: 3, day_of_week: 2 }]), "scrobbles");
  assert.equal(detectDropKind([{ foo: 1 }, 7, null]), null);
  assert.equal(detectDropKind([]), null);
});

test("parseDropText reads JSONL or a JSON array and throws on a broken array", () => {
  assert.deepEqual(parseDropText('{"a":1}\n\n{"a":2}\n'), [{ a: 1 }, { a: 2 }]);
  assert.deepEqual(parseDropText(' [{"a":1}] '), [{ a: 1 }]);
  assert.throws(() => parseDropText('[{"a":1},'));
  assert.deepEqual(parseDropText(""), []);
});
