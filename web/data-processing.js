/* ============================================================
   data-processing.js — pure data transforms shared by the main
   thread (dashboard.jsx) and the off-main-thread parser
   (data-worker.js, via importScripts). No React / DOM / JSX here
   so the same code runs inside a Web Worker.

   Loaded as a classic <script defer> BEFORE app.bundle.js, so these
   top-level declarations are globals the bundle references by name.
   ============================================================ */

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

/* Timeframe anchor.

   These windows used to be computed from the browser clock at module load.
   That silently broke whenever the library was not synced today: with data
   ending 2026-05-07 and a clock reading 2026-08-16, "This month" and "Last
   month" matched nothing and the dashboard rendered as though no music had
   ever been played.

   The anchor now follows the data. If the newest scrobble is recent the wall
   clock is used, which keeps "this month" meaning the real current month for
   anyone syncing regularly; otherwise windows are measured back from the last
   day with data, and `stale` lets the UI say so out loud. */
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
function buildPlayWindows(scrobbleRows) {
  computeAnchor(scrobbleRows);
  const map = new Map();
  for (const s of scrobbleRows) {
    const k = trackKey(s);
    let e = map.get(k);
    if (!e) { e = { py: {}, tm: 0, lm: 0, tw: 0, lw: 0, ts: 0, ls: 0 }; map.set(k, e); }
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
  const win = buildPlayWindows(scrobbleRows);
  for (const t of rawTracks) {
    const e = win.get(trackKey(t));
    if (e) {
      t.py = e.py; t.tm = e.tm; t.lm = e.lm;
      t.tw = e.tw; t.lw = e.lw; t.ts = e.ts; t.ls = e.ls;
    }
  }
  return rawTracks;
}

/* Cross-join scrobbles → tracks to count genres/moods/tracks per time slice
   (season, hour-of-day, day-of-week). Powers the overview drill-downs and the
   Seasonal Favorites page. Computed once at load from the in-memory rows. */
function buildDrill(rawTracks, scrobbleRows) {
  if (!rawTracks || !scrobbleRows || !scrobbleRows.length) return null;
  const info = new Map();
  for (const t of rawTracks) {
    info.set(trackKey(t), {
      genres: Array.isArray(t.genres) ? t.genres : [],
      moods: Array.isArray(t.mood_tags) ? t.mood_tags : (Array.isArray(t.moods) ? t.moods : []),
      label: `${t.artist || "Unknown"} — ${t.track || "Untitled"}`,
    });
  }
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
    const gi = info.get(trackKey(sc));
    if (!gi) continue;
    const se = sc.season || (sc.month ? SEASON_BY_MONTH[sc.month] : null);
    if (se && season[se]) { bump(season[se], gi); if (sc.hour != null) season[se].byHour[sc.hour]++; }
    if (sc.hour != null && hour[sc.hour]) bump(hour[sc.hour], gi);
    if (sc.day_of_week != null && dow[sc.day_of_week]) bump(dow[sc.day_of_week], gi);
  }
  return { season, hour, dow };
}

/* Single entry point used by every data-load path (initial fetch, refresh,
   manual file drop) so windowing + drill are always built consistently. */
function processLibrary(rawTracks, scrobbleRows) {
  const ns = (scrobbleRows && scrobbleRows.length) ? aggregateScrobbles(scrobbleRows) : null;
  let nt = null, drill = null;
  if (rawTracks && rawTracks.length) {
    attachWindows(rawTracks, scrobbleRows);
    drill = buildDrill(rawTracks, scrobbleRows);
    nt = rawTracks.map(normalizeTrack);
  }
  return { nt, ns, drill };
}
