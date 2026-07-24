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

// Derive the calendar windows from "now" rather than hardcoding a year, so the
// timeframe slicer keeps working as the calendar rolls over.
const _NOW = new Date();
const CUR_YEAR = _NOW.getFullYear(), LAST_YEAR = CUR_YEAR - 1;
const _pad2 = (n) => String(n).padStart(2, "0");
const CUR_MONTH_KEY = `${CUR_YEAR}-${_pad2(_NOW.getMonth() + 1)}`;
const _lastMonth = new Date(_NOW.getFullYear(), _NOW.getMonth() - 1, 1);
const LAST_MONTH_KEY = `${_lastMonth.getFullYear()}-${_pad2(_lastMonth.getMonth() + 1)}`;

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
  const map = new Map();
  for (const s of scrobbleRows) {
    const k = trackKey(s);
    let e = map.get(k);
    if (!e) { e = { py: {}, tm: 0, lm: 0 }; map.set(k, e); }
    if (s.year != null) e.py[s.year] = (e.py[s.year] || 0) + 1;
    const ym = (s.scrobbled_at || "").slice(0, 7);
    if (ym === CUR_MONTH_KEY) e.tm++;
    else if (ym === LAST_MONTH_KEY) e.lm++;
  }
  return map;
}
function attachWindows(rawTracks, scrobbleRows) {
  if (!scrobbleRows || !scrobbleRows.length) return rawTracks;
  const win = buildPlayWindows(scrobbleRows);
  for (const t of rawTracks) {
    const e = win.get(trackKey(t));
    if (e) { t.py = e.py; t.tm = e.tm; t.lm = e.lm; }
  }
  return rawTracks;
}

/* Filter raw scrobble rows to an inclusive [from, to] date window. Both bounds
   are optional ISO YYYY-MM-DD strings; comparison is lexicographic on the date
   prefix, which is valid for zero-padded ISO dates. Rows with no parseable date
   are dropped from any bounded window. Returns the input unchanged when neither
   bound is set (no filter). Used by the per-page date filters to re-aggregate. */
function scrobblesInRange(rows, from, to) {
  if (!rows || (!from && !to)) return rows || [];
  return rows.filter((s) => {
    const d = (s.scrobbled_at || "").slice(0, 10);
    if (!d) return false;
    if (from && d < from) return false;
    if (to && d > to) return false;
    return true;
  });
}

/* Min / max scrobble date (YYYY-MM-DD) across raw rows, used to bound the
   date-input pickers so a range can't be set outside the data. */
function scrobbleDateBounds(rows) {
  let min = null, max = null;
  for (const s of rows || []) {
    const d = (s.scrobbled_at || "").slice(0, 10);
    if (!d) continue;
    if (min == null || d < min) min = d;
    if (max == null || d > max) max = d;
  }
  return { min, max };
}

/* Per-track play counts within a scrobble slice, keyed by normalized identity
   (trackKey). The date-filtered analog of buildPlayWindows — lets play-based
   metrics (KPIs, top tracks/artists, albums) be scoped to a date window. */
function buildRangeCounts(scrobbleRows) {
  const map = new Map();
  for (const s of scrobbleRows) {
    const k = trackKey(s);
    map.set(k, (map.get(k) || 0) + 1);
  }
  return map;
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
  // `sc` carries the raw scrobble rows back to the caller so the per-page date
  // filters can re-aggregate arbitrary windows on demand (aggregateScrobbles /
  // buildDrill / buildRangeCounts discard nothing here — the rows are retained).
  return { nt, ns, drill, sc: (scrobbleRows && scrobbleRows.length) ? scrobbleRows : null };
}
