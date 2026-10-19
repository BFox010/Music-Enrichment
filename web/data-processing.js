/* Pure transforms shared by dashboard.jsx and data-worker.js (via importScripts);
   motion.js and ambient.js also use readPref/writePref.
   No React/DOM/JSX — must run inside a Web Worker.
   Loaded as <script defer> BEFORE app.bundle.js; these top-level decls are globals. */

const SEASON_BY_MONTH = { 12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall" };
const SEASONS_LIST = ["winter", "spring", "summer", "fall"];

const _pad2 = (n) => String(n).padStart(2, "0");

/* ISO-8601 week key, e.g. "2025-W07". Weeks start Monday and the week
   containing the year's first Thursday is week 1, so a January date can
   legitimately belong to the previous year's final week. */
function isoWeekKey(d) {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;          // Sunday → 7
  t.setUTCDate(t.getUTCDate() + 4 - day);  // shift to the week's Thursday
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t - yearStart) / 86400000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${_pad2(week)}`;
}

function seasonKeyOf(d) {
  const season = SEASON_BY_MONTH[d.getUTCMonth() + 1];
  return `${d.getUTCFullYear()}-${season}`;
}

/* Timeframe anchor. Don't anchor on the wall clock alone: a library not synced
   recently then matches nothing in "This month" and reads as no listening.
   Recent data uses the wall clock; otherwise windows count back from the last
   day with data, and `stale` lets the UI say so. */
const FRESH_WITHIN_DAYS = 7;

/* Seeded from the wall clock so the object is coherent before any data has
   been parsed; computeAnchor() overwrites every field once scrobbles land. */
const ANCHOR = (() => {
  const now = new Date();
  return {
    date: now, dataEnd: null, stale: false,
    curYear: now.getUTCFullYear(), lastYear: now.getUTCFullYear() - 1,
    curMonthKey: `${now.getUTCFullYear()}-${_pad2(now.getUTCMonth() + 1)}`,
    lastMonthKey: "",
    curWeekKey: isoWeekKey(now), lastWeekKey: "",
    curSeasonKey: seasonKeyOf(now), lastSeasonKey: "",
  };
})();

function computeAnchor(scrobbleRows) {
  let maxStamp = "";
  for (const s of scrobbleRows || []) {
    const stamp = s.scrobbled_at || "";
    if (stamp > maxStamp) maxStamp = stamp;
  }
  const wall = new Date();
  const dataEnd = maxStamp ? new Date(maxStamp) : null;
  const ageDays = dataEnd ? (wall - dataEnd) / 86400000 : Infinity;
  const stale = !!dataEnd && ageDays > FRESH_WITHIN_DAYS;
  const anchor = stale ? dataEnd : wall;

  const prevMonth = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() - 1, 1));
  const prevWeek = new Date(anchor.getTime() - 7 * 86400000);
  const prevSeason = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() - 3, 1));

  ANCHOR.date = anchor;
  ANCHOR.dataEnd = maxStamp ? maxStamp.slice(0, 10) : null;
  ANCHOR.stale = stale;
  ANCHOR.curYear = anchor.getUTCFullYear();
  ANCHOR.lastYear = ANCHOR.curYear - 1;
  ANCHOR.curMonthKey = `${anchor.getUTCFullYear()}-${_pad2(anchor.getUTCMonth() + 1)}`;
  ANCHOR.lastMonthKey = `${prevMonth.getUTCFullYear()}-${_pad2(prevMonth.getUTCMonth() + 1)}`;
  ANCHOR.curWeekKey = isoWeekKey(anchor);
  ANCHOR.lastWeekKey = isoWeekKey(prevWeek);
  ANCHOR.curSeasonKey = seasonKeyOf(anchor);
  ANCHOR.lastSeasonKey = seasonKeyOf(prevSeason);
  return ANCHOR;
}

/* computeAnchor() normally runs in the worker, a separate realm with its own
   ANCHOR. The main thread must adopt the worker's copy here, or its KPIs,
   filter bar and "data through" note keep the wall-clock seed and label the
   data's month as the current one (#102). */
function adoptAnchor(a) {
  if (!a) return ANCHOR;
  Object.assign(ANCHOR, a);
  // structuredClone keeps a Date, but a JSON hop (or a test) may not.
  if (!(ANCHOR.date instanceof Date)) ANCHOR.date = new Date(ANCHOR.date);
  return ANCHOR;
}

/* The period a "this/last" timeframe actually covers, e.g. "2026-08" for
   month_this. Only worth showing when the anchor is stale, where "This month"
   no longer means the calendar month on the wall. */
function anchorPeriodKey(tf) {
  switch (tf) {
    case "year_this": return String(ANCHOR.curYear);
    case "year_last": return String(ANCHOR.lastYear);
    case "season_this": return ANCHOR.curSeasonKey;
    case "season_last": return ANCHOR.lastSeasonKey;
    case "month_this": return ANCHOR.curMonthKey;
    case "month_last": return ANCHOR.lastMonthKey;
    case "week_this": return ANCHOR.curWeekKey;
    case "week_last": return ANCHOR.lastWeekKey;
    default: return "";
  }
}

/* Preferences. localStorage throws when site storage is blocked, and these
   reads run during first render, so an unguarded call blanks the page (#113).
   A failed read means "no preference"; a failed write is dropped. */
function readPref(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ }
}

/* The library state after a load (live fetch, refresh or file drop).
   processLibrary() returns drill and cube only when it had both tracks and
   scrobbles, since both cross-join the two. Don't carry the previous load's
   drill/cube over a single-file load: its track indices point into the old
   tracks, so the charts and drill-down disagree with the KPIs (#113).
   Returns null when the load produced nothing. */
function loadedState(prevData, result) {
  if (!result || (!result.nt && !result.ns)) return null;
  const { nt, ns } = result;
  const meta = prevData.meta || {};
  return {
    data: {
      meta: { ...meta, isSample: false,
        trackCount: nt ? nt.length : meta.trackCount,
        scrobbleCount: ns ? ns.total : meta.scrobbleCount },
      tracks: nt || prevData.tracks,
      scrobbles: ns || prevData.scrobbles,
    },
    drill: result.drill || null,
    cube: result.cube || null,
  };
}

function normalizeTrack(raw, i) {
  return {
    i,
    artist: raw.artist || "Unknown",
    track: raw.track || "Untitled",
    album: raw.album || "",
    release_year: raw.release_year || null,
    genres: Array.isArray(raw.genres) ? raw.genres : [],
    tags: Array.isArray(raw.lastfm_tags) ? raw.lastfm_tags : (Array.isArray(raw.tags) ? raw.tags : []),
    styles: Array.isArray(raw.discogs_styles) ? raw.discogs_styles : (Array.isArray(raw.styles) ? raw.styles : []),
    moods: raw.mood_tags || raw.moods || null,
    mood_source: raw.mood_source || null,
    mood_confidence: raw.mood_confidence || null,
    play: Number(raw.play_count != null ? raw.play_count : (raw.play || 0)) || 0,
    py: raw.py || null,
    tm: raw.tm != null ? raw.tm : null,
    lm: raw.lm != null ? raw.lm : null,
    tw: raw.tw != null ? raw.tw : null,
    lw: raw.lw != null ? raw.lw : null,
    ts: raw.ts != null ? raw.ts : null,
    ls: raw.ls != null ? raw.ls : null,
    peak_year: raw.peak_year || null,
    first: raw.first_scrobbled || raw.first || null,
    last: raw.last_scrobbled || raw.last || null,
    mbid: !!(raw.musicbrainz_id || raw.mbid),
    spotify: !!(raw.spotify_id || raw.spotify),
    apple: raw.apple_music_available != null ? raw.apple_music_available : (raw.apple || null),
    af: raw.audio_features || raw.af || null,
    sources: raw.enrichment_sources || raw.sources || [],
    sat: raw.saturation_tier != null ? raw.saturation_tier : (raw.sat || null),
    playlists: raw.playlists || []
  };
}

/* "2016–2026" from aggregateScrobbles()'s byYear, or "" when there is none.
   meta.scrobbleRange is set only by the sample library (#120). */
function scrobbleYearRange(byYear) {
  const years = Object.keys(byYear || {}).map(Number).filter((y) => y > 0);
  if (!years.length) return "";
  const lo = Math.min(...years), hi = Math.max(...years);
  return lo === hi ? String(lo) : `${lo}–${hi}`;
}

/* The overview selection after a timeframe change: a selected season the new
   window has no plays in is dropped, or its inert card can't be clicked off. */
function dropEmptySeason(sel, bySeason) {
  if (!sel || !sel.season || (bySeason && bySeason[sel.season] > 0)) return sel;
  return { ...sel, season: null };
}

/* A dropped file's rows. A JSON array if the text opens with "[", else JSONL.
   Throws on a malformed array so the drop can say so instead of failing silently. */
function parseDropText(text) {
  const t = String(text || "").trim();
  if (!t.startsWith("[")) return parseJSONL(t);
  const rows = JSON.parse(t);
  if (!Array.isArray(rows)) throw new Error("not a JSON array");
  return rows;
}

/* "scrobbles", "tracks" or null, by the rows' shape rather than the file name:
   a scrobble row carries its timestamp (or hour + weekday), a track row its
   identity or lifetime play count. Majority of the first rows decides. */
function detectDropKind(rows) {
  let sc = 0, tr = 0, seen = 0;
  for (const r of rows || []) {
    if (!r || typeof r !== "object" || Array.isArray(r)) continue;
    if (r.scrobbled_at != null || (r.hour != null && r.day_of_week != null)) sc++;
    else if (r.canonical_track_id != null || r.play_count != null || r.track != null) tr++;
    if (++seen >= 50) break;
  }
  if (sc > tr) return "scrobbles";
  return tr > 0 ? "tracks" : null;
}

function aggregateScrobbles(rows) {
  const byHour = Array(24).fill(0), byDow = Array(7).fill(0), bySeason = { winter: 0, spring: 0, summer: 0, fall: 0 }, byYear = {};
  let total = 0;
  for (const s of rows) {
    total++;
    if (s.hour != null) byHour[s.hour]++;
    if (s.day_of_week != null) byDow[s.day_of_week]++;
    const season = s.season || (s.month ? SEASON_BY_MONTH[s.month] : null);
    if (season) bySeason[season] = (bySeason[season] || 0) + 1;
    if (s.year != null) byYear[s.year] = (byYear[s.year] || 0) + 1;
  }
  return { byHour, byDow, bySeason, byYear, total };
}

function parseJSONL(text) {
  const out = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try { out.push(JSON.parse(t)); } catch (e) { /* skip */ }
  }
  return out;
}

/* Per-track windowed play counts, computed by joining scrobbles → tracks.
   tracks.jsonl only carries a lifetime `play_count`, so without this every
   timeframe except "All time" reads as zero. Keyed on normalized identity. */
function trackKey(o) {
  const a = (o.artist_normalized || o.artist || "").toLowerCase();
  const t = (o.track_normalized || o.track || "").toLowerCase();
  return a + "\x00" + t;
}

/* The credit variants Phase 4e folded into a track (identity_aliases, shipped
   in /tracks.min.jsonl via MIN_TRACK_FIELDS, F-03). A play logged under a
   historical artist credit matches the current row only through one of these;
   without them the browser undercounts where app.metrics._track_index() does
   not. Kept apart from the primary key: the two carry different authority
   when they collide (see buildTrackIndex). */
function aliasKeys(o) {
  const keys = [];
  const aliases = o.identity_aliases;
  if (!Array.isArray(aliases)) return keys;
  for (const alias of aliases) {
    if (!Array.isArray(alias) || alias.length !== 2) continue;
    const a = String(alias[0] || "").toLowerCase().trim();
    const t = String(alias[1] || "").toLowerCase().trim();
    const k = a + "\x00" + t;
    if (keys.indexOf(k) === -1) keys.push(k);
  }
  return keys;
}
/* Alias-aware key → track-array-index map. Collision arbitration must match
   app.metrics._track_index(), or the dashboard and the API credit a scrobble
   to different tracks: a primary key is assigned unconditionally (owning a
   name beats another row's alias, whatever the row order), while an alias only
   fills a key nothing has claimed. Don't make both first-writer-wins: an
   earlier row's alias would shadow a later row's own name (F-03). */
function buildTrackIndex(rawTracks) {
  const idx = new Map();
  for (let i = 0; i < rawTracks.length; i++) {
    idx.set(trackKey(rawTracks[i]), i);
  }
  for (let i = 0; i < rawTracks.length; i++) {
    for (const k of aliasKeys(rawTracks[i])) {
      if (!idx.has(k)) idx.set(k, i);
    }
  }
  return idx;
}

/* Windowed counts per *track*, not per key: each scrobble resolves through the
   shared index to exactly one track, as in app.metrics._lookup(). Don't
   accumulate per key and sum each track's keys: a key two rows claim (one's
   name, the other's alias) would be counted into both. */
function buildPlayWindows(scrobbleRows, idx) {
  computeAnchor(scrobbleRows);
  const map = new Map();
  for (const s of scrobbleRows) {
    const ti = idx.get(trackKey(s));
    if (ti === undefined) continue;
    let e = map.get(ti);
    if (!e) { e = { py: {}, tm: 0, lm: 0, tw: 0, lw: 0, ts: 0, ls: 0 }; map.set(ti, e); }
    if (s.year != null) e.py[s.year] = (e.py[s.year] || 0) + 1;
    const stamp = s.scrobbled_at || "";
    if (!stamp) continue;
    if (stamp.slice(0, 7) === ANCHOR.curMonthKey) e.tm++;
    else if (stamp.slice(0, 7) === ANCHOR.lastMonthKey) e.lm++;

    const d = new Date(stamp);
    const wk = isoWeekKey(d);
    if (wk === ANCHOR.curWeekKey) e.tw++;
    else if (wk === ANCHOR.lastWeekKey) e.lw++;

    const sk = s.season != null && s.year != null
      ? `${s.year}-${s.season}`
      : seasonKeyOf(d);
    if (sk === ANCHOR.curSeasonKey) e.ts++;
    else if (sk === ANCHOR.lastSeasonKey) e.ls++;
  }
  return map;
}
function attachWindows(rawTracks, scrobbleRows) {
  if (!scrobbleRows || !scrobbleRows.length) return rawTracks;
  // Plays logged under a historical credit resolve through the track's alias
  // keys, so they land on the row that absorbed that credit.
  const win = buildPlayWindows(scrobbleRows, buildTrackIndex(rawTracks));
  for (let i = 0; i < rawTracks.length; i++) {
    const e = win.get(i);
    if (!e) continue;
    const t = rawTracks[i];
    t.py = e.py; t.tm = e.tm; t.lm = e.lm;
    t.tw = e.tw; t.lw = e.lw; t.ts = e.ts; t.ls = e.ls;
  }
  return rawTracks;
}

/* Cross-join scrobbles → tracks to count genres/moods/tracks per time slice
   (season, hour-of-day, day-of-week). Powers the Seasonal Favorites page; the
   overview drill-down reads the cube instead. Computed once at load. */
function buildDrill(rawTracks, scrobbleRows) {
  if (!rawTracks || !scrobbleRows || !scrobbleRows.length) return null;
  // Same index, same arbitration as the windows and the cube, so a scrobble
  // can never be attributed to one track here and a different one there.
  const idx = buildTrackIndex(rawTracks);
  const info = rawTracks.map((t) => ({
    genres: Array.isArray(t.genres) ? t.genres : [],
    moods: Array.isArray(t.mood_tags) ? t.mood_tags : (Array.isArray(t.moods) ? t.moods : []),
    label: `${t.artist || "Unknown"} — ${t.track || "Untitled"}`,
  }));
  const mk = () => ({ genres: {}, moods: {}, tracks: {}, total: 0 });
  const season = {}, hour = {}, dow = {};
  for (const s of SEASONS_LIST) { season[s] = mk(); season[s].byHour = new Array(24).fill(0); }
  for (let h = 0; h < 24; h++) hour[h] = mk();
  for (let d = 0; d < 7; d++) dow[d] = mk();
  const bump = (bucket, gi) => {
    bucket.total++;
    for (const g of gi.genres) bucket.genres[g] = (bucket.genres[g] || 0) + 1;
    for (const m of gi.moods) bucket.moods[m] = (bucket.moods[m] || 0) + 1;
    bucket.tracks[gi.label] = (bucket.tracks[gi.label] || 0) + 1;
  };
  for (const sc of scrobbleRows) {
    const ti = idx.get(trackKey(sc));
    if (ti === undefined) continue;
    const gi = info[ti];
    const se = sc.season || (sc.month ? SEASON_BY_MONTH[sc.month] : null);
    if (se && season[se]) { bump(season[se], gi); if (sc.hour != null) season[se].byHour[sc.hour]++; }
    if (sc.hour != null && hour[sc.hour]) bump(hour[sc.hour], gi);
    if (sc.day_of_week != null && dow[sc.day_of_week]) bump(dow[sc.day_of_week], gi);
  }
  return { season, hour, dow };
}

/* ── scrobble cube ──
   The overview charts cross-filter, and pre-baked buckets (byHour / byDow /
   buildDrill) cannot answer "hours, but only on Tuesdays, this month", so the
   per-scrobble facts are kept as parallel typed arrays: transferable from the
   worker without a structured clone, and re-aggregated in one linear pass.
   `tf` is a bitmask of the timeframe windows each scrobble falls in, computed
   from the same ANCHOR as playInWindow(), which keeps the charts consistent
   with the KPI row. */

const TF_BITS = {
  year_this: 1, year_last: 2, season_this: 4, season_last: 8,
  month_this: 16, month_last: 32, week_this: 64, week_last: 128,
};
const CUBE_NONE = 255;   // sentinel for a missing hour / dow / season

function buildCube(rawTracks, scrobbleRows) {
  if (!scrobbleRows || !scrobbleRows.length) return null;
  const n = scrobbleRows.length;
  const hour = new Uint8Array(n), dow = new Uint8Array(n), season = new Uint8Array(n);
  const tf = new Uint16Array(n), track = new Int32Array(n);

  const idx = rawTracks ? buildTrackIndex(rawTracks) : new Map();

  for (let i = 0; i < n; i++) {
    const s = scrobbleRows[i];
    hour[i] = s.hour != null ? s.hour : CUBE_NONE;
    dow[i] = s.day_of_week != null ? s.day_of_week : CUBE_NONE;
    const se = s.season || (s.month ? SEASON_BY_MONTH[s.month] : null);
    const si = se ? SEASONS_LIST.indexOf(se) : -1;
    season[i] = si >= 0 ? si : CUBE_NONE;
    const ti = idx.get(trackKey(s));
    track[i] = ti != null ? ti : -1;

    let bits = 0;
    if (s.year != null) {
      if (s.year === ANCHOR.curYear) bits |= TF_BITS.year_this;
      else if (s.year === ANCHOR.lastYear) bits |= TF_BITS.year_last;
    }
    const stamp = s.scrobbled_at || "";
    if (stamp) {
      const ym = stamp.slice(0, 7);
      if (ym === ANCHOR.curMonthKey) bits |= TF_BITS.month_this;
      else if (ym === ANCHOR.lastMonthKey) bits |= TF_BITS.month_last;

      const d = new Date(stamp);
      const wk = isoWeekKey(d);
      if (wk === ANCHOR.curWeekKey) bits |= TF_BITS.week_this;
      else if (wk === ANCHOR.lastWeekKey) bits |= TF_BITS.week_last;

      const sk = (s.season != null && s.year != null) ? `${s.year}-${s.season}` : seasonKeyOf(d);
      if (sk === ANCHOR.curSeasonKey) bits |= TF_BITS.season_this;
      else if (sk === ANCHOR.lastSeasonKey) bits |= TF_BITS.season_last;
    }
    tf[i] = bits;
  }
  return { n, hour, dow, season, tf, track };
}

/* Re-aggregate the cube for one (timeframe, selection) pair.

   Cross-filter rule: a chart is never filtered by its own dimension, so the
   hour chart shows "hours within the selected days" while still displaying
   every hour — otherwise picking an hour would collapse its own chart to a
   single bar. `slice` applies all three dimensions and is what the drill-down
   panel renders; it is only accumulated when something is selected. */
function aggregateCube(cube, timeframe, sel, tracks) {
  const byHour = new Array(24).fill(0);
  const byDow = new Array(7).fill(0);
  const bySeason = { winter: 0, spring: 0, summer: 0, fall: 0 };
  if (!cube || !cube.n) return { byHour, byDow, bySeason, total: 0, slice: null };

  const bit = TF_BITS[timeframe] || 0;
  const { n, hour, dow, season, tf, track } = cube;
  const selH = sel && sel.hour != null ? sel.hour : -1;
  const selD = sel && sel.dow != null ? sel.dow : -1;
  const selS = sel && sel.season ? SEASONS_LIST.indexOf(sel.season) : -1;
  const active = selH >= 0 || selD >= 0 || selS >= 0;

  const seasonN = [0, 0, 0, 0];
  const genres = active ? Object.create(null) : null;
  const moods = active ? Object.create(null) : null;
  const trackHits = active ? Object.create(null) : null;
  const sliceHours = active ? new Array(24).fill(0) : null;
  let total = 0;

  for (let i = 0; i < n; i++) {
    if (bit && !(tf[i] & bit)) continue;
    const h = hour[i], d = dow[i], s = season[i];
    const okH = selH < 0 || h === selH;
    const okD = selD < 0 || d === selD;
    const okS = selS < 0 || s === selS;

    if (okD && okS && h < 24) byHour[h]++;
    if (okH && okS && d < 7) byDow[d]++;
    if (okH && okD && s < 4) seasonN[s]++;
    if (!(okH && okD && okS)) continue;

    total++;
    if (!active) continue;
    if (h < 24) sliceHours[h]++;
    const ti = track[i];
    if (ti < 0) continue;
    const t = tracks && tracks[ti];
    if (!t) continue;
    const g = t.genres;
    if (g) for (let j = 0; j < g.length; j++) genres[g[j]] = (genres[g[j]] || 0) + 1;
    const m = t.moods;
    if (m) for (let j = 0; j < m.length; j++) moods[m[j]] = (moods[m[j]] || 0) + 1;
    const label = `${t.artist || "Unknown"} — ${t.track || "Untitled"}`;
    trackHits[label] = (trackHits[label] || 0) + 1;
  }

  for (let i = 0; i < 4; i++) bySeason[SEASONS_LIST[i]] = seasonN[i];
  const slice = active
    ? { genres, moods, tracks: trackHits, byHour: sliceHours, total }
    : null;
  return { byHour, byDow, bySeason, total, slice };
}

/* Single entry point used by every data-load path (initial fetch, refresh,
   manual file drop) so windowing + drill are always built consistently. */
function processLibrary(rawTracks, scrobbleRows) {
  const ns = (scrobbleRows && scrobbleRows.length) ? aggregateScrobbles(scrobbleRows) : null;
  let nt = null, drill = null, cube = null;
  if (rawTracks && rawTracks.length) {
    attachWindows(rawTracks, scrobbleRows);
    drill = buildDrill(rawTracks, scrobbleRows);
    cube = buildCube(rawTracks, scrobbleRows);
    nt = rawTracks.map(normalizeTrack);
  }
  return { nt, ns, drill, cube };
}

/* Trajectory picker colour slots, as { name: slotIndex }. A newly selected
   artist takes the lowest free slot; a deselected one frees only its own.
   Never reassign a surviving artist's slot: that recolours its line when
   someone else is toggled. Hashing names instead collides at 8 picks. */
function assignColorSlots(prev, selectedNames, size) {
  const next = {};
  const used = new Array(size).fill(0);
  for (const n of selectedNames) {
    const s = prev ? prev[n] : undefined;
    if (s != null && s < size) { next[n] = s; used[s]++; }
  }
  for (const n of selectedNames) {
    if (next[n] != null) continue;
    let slot = used.indexOf(0);
    // More picks than colours: share the least-used slot rather than move anyone.
    if (slot < 0) slot = used.indexOf(Math.min(...used));
    next[n] = slot;
    used[slot]++;
  }
  return next;
}

/* Picker chips: selected artists first, then the filter's matches, each group
   in the input's (play-count) order. Selected chips ignore the filter so a
   pick can always be clicked off; `limit` caps only the unselected tail. */
function orderPickerChips(ranked, selected, query, limit) {
  const q = (query || "").trim().toLowerCase();
  const picked = [], rest = [];
  for (const a of ranked) {
    if (selected && selected.has(a.name)) picked.push(a);
    else if (!q || a.name.toLowerCase().includes(q)) rest.push(a);
  }
  const room = Math.max(0, limit - picked.length);
  return { chips: picked.concat(rest.slice(0, room)), picked, hidden: Math.max(0, rest.length - room) };
}
