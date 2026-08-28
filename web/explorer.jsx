/* FilterBar + TrackTable */
const { useState: useStateX, useMemo: useMemoX } = React;

/* ── Active filter bar + slicers ── */
const FB_KIND_LABEL = { genre: "genre", mood: "mood", tag: "tag", decade: "decade", artist: "artist", firstFrom: "heard ≥", firstTo: "heard ≤" };
function FilterBar({ filters, onRemove, onClear, sort, onSort, onToggle, onRange, decades, years, curYear }) {
  const entries = Object.entries(filters).filter(([, v]) => v);
  const decadeList = decades || [];
  const yearList = years || [];
  const fmtVal = (k, v) => (k === "decade" ? v + "s" : v);
  const newThisYear = curYear != null && String(filters.firstFrom) === String(curYear) && String(filters.firstTo) === String(curYear);
  const toggleNew = () => {
    if (newThisYear) { onRange("firstFrom", ""); onRange("firstTo", ""); }
    else { onRange("firstFrom", String(curYear)); onRange("firstTo", String(curYear)); }
  };
  return (
    <div className="filterbar">
      <div className="fb-row">
        <span className="fb-label">Filters</span>
        <div className="fb-chips">
          {entries.length === 0 && <span style={{ color: "var(--muted-s)", fontSize: 12.5 }}>None — showing the whole library. Click any chart element to filter.</span>}
          {entries.map(([kind, val]) => (
            <span className="fchip" key={kind}>
              <span className="fc-kind">{FB_KIND_LABEL[kind] || kind}</span>{fmtVal(kind, val)}
              <span className="x" onClick={() => onRemove(kind)} title="Remove" aria-label="Remove filter">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </span>
            </span>
          ))}
        </div>
        {entries.length > 0 && <button className="fb-clear" onClick={onClear}>Clear all</button>}
        <div className="fb-sort">
          <span className="fb-label">Sort</span>
          <select value={sort} onChange={(e) => onSort(e.target.value)}>
            <option value="plays">Most played</option>
            <option value="plays_asc">Least played</option>
            <option value="artist">Artist A–Z</option>
            <option value="track">Title A–Z</option>
            <option value="year_desc">Newest release</option>
            <option value="year_asc">Oldest release</option>
            <option value="recent">Recently played</option>
          </select>
        </div>
      </div>
      {(decadeList.length > 0 || yearList.length > 0) && (
        <div className="fb-row fb-controls">
          {decadeList.length > 0 && (
            <div className="fb-control">
              <span className="fb-label">Decade</span>
              <div className="seg seg-sm" role="group" aria-label="Release decade">
                {decadeList.map((d) => (
                  <button key={d} aria-pressed={String(filters.decade) === String(d)} onClick={() => onToggle("decade", String(d))}>{String(d).slice(2)}s</button>
                ))}
              </div>
            </div>
          )}
          {yearList.length > 0 && (
            <div className="fb-control">
              <span className="fb-label">First heard</span>
              <select className="fb-yr" value={filters.firstFrom || ""} onChange={(e) => onRange("firstFrom", e.target.value)}>
                <option value="">From…</option>
                {yearList.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              <span className="fb-dash">–</span>
              <select className="fb-yr" value={filters.firstTo || ""} onChange={(e) => onRange("firstTo", e.target.value)}>
                <option value="">To…</option>
                {yearList.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              {curYear != null && (
                <button className={"fb-quick" + (newThisYear ? " active" : "")} onClick={toggleNew} title={`First scrobbled in ${curYear}`}>
                  New this year
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Data-coverage dots ──
   One definition, two consumers: the "Data" cell on every row and the legend
   in dashboard.jsx. They used to be written out twice, which is why the legend
   listed five squares that were all the same accent colour — nothing in it
   could tell you which position was which, so a row could only be read by
   counting.

   Hue answers "which source"; fill answers "how confident". A hollow dot is a
   source that is present but lower-confidence, which today only the mood
   classifier can be. */
const COV_SOURCES = [
  { key: "tags",   color: "oklch(0.70 0.17 25)",  label: "Last.fm tags",  note: "community genre/style tags.",
    state: (t) => (t.tags && t.tags.length ? "on" : "") },
  { key: "mbid",   color: "oklch(0.76 0.14 60)",  label: "MusicBrainz ID", note: "canonical recording identifier.",
    state: (t) => (t.mbid ? "on" : "") },
  { key: "styles", color: "oklch(0.78 0.14 130)", label: "Discogs styles", note: "release styles from Discogs.",
    state: (t) => (t.styles && t.styles.length ? "on" : "") },
  { key: "af",     color: "oklch(0.75 0.13 200)", label: "Audio features", note: "danceability, energy, valence, tempo…",
    state: (t) => (t.af ? "on" : "") },
  { key: "apple",  color: "oklch(0.70 0.14 285)", label: "Apple Music",    note: "confirmed available on Apple Music.",
    state: (t) => (t.apple ? "on" : "") },
  { key: "moods",  color: "oklch(0.72 0.17 340)", label: "Mood tags",      note: "hand-labelled or classified moods.",
    state: (t) => (t.moods ? (t.mood_source === "centroid" ? "warn" : "on") : "") },
];

function CovDots({ track }) {
  return (
    <span className="covdots">
      {COV_SOURCES.map((c) => {
        const st = c.state(track);
        return <span key={c.key} className={"covdot" + (st ? " " + st : "")} style={{ "--cov": c.color }} title={c.label}></span>;
      })}
    </span>
  );
}

/* ── Track explorer table ── */
function TrackTable({ rows, sort, onSort, onPickArtist, playOf, timeframe }) {
  const [limit, setLimit] = useStateX(60);
  const shown = rows.slice(0, limit);
  const pf = playOf || ((t) => t.play);

  const th = (key, label, cls) => {
    const active = sort.startsWith(key);
    return (
      <th className={cls} aria-sort={active ? "true" : undefined} onClick={() => onSort(key)}>
        {label}<span className="sortcaret">{active && sort.endsWith("_asc") ? "▲" : "▼"}</span>
      </th>
    );
  };

  return (
    <div>
      <div className="tablewrap">
        <table className="tracks">
          <thead>
            <tr>
              <th style={{ width: 34 }} className="r">#</th>
              {th("track", "Track")}
              {th("artist", "Artist")}
              <th>Moods</th>
              <th>Genres</th>
              {th("year", "Year", "r")}
              <th className="r" title="Coverage: Last.fm · MusicBrainz · Discogs · Audio · Apple · Mood">Data</th>
              {th("plays", "Plays", "r")}
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr key={t.i}>
                <td className="td-year r">{i + 1}</td>
                <td className="td-track">{t.track}
                  {t.mood_source && <> <span className={"msrc " + t.mood_source}>{t.mood_source === "claude_batch" ? "claude" : t.mood_source}</span></>}
                </td>
                <td className="td-artist" style={{ cursor: "pointer" }} onClick={() => onPickArtist(t.artist)}>{t.artist}</td>
                <td>
                  <div className="cellmoods">
                    {(t.moods || []).slice(0, 3).map((m) => <span className="minimood" key={m} style={{ "--mood": moodColor(m) }}>{m}</span>)}
                    {(!t.moods || t.moods.length === 0) && <span style={{ color: "var(--faint)", fontSize: 11 }}>—</span>}
                  </div>
                </td>
                <td className="td-artist" style={{ fontSize: 12 }}>{(t.genres || []).slice(0, 2).join(", ")}</td>
                <td className="td-year r">{t.release_year || "—"}</td>
                <td className="r"><CovDots track={t} /></td>
                <td className="td-plays">{pf(t)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="empty">
            <div className="big">No tracks match</div>
            <div>Try removing a filter or clearing your search.</div>
          </div>
        )}
      </div>
      {rows.length > 0 && (
        <div className="tablefoot">
          <span>Showing {shown.length.toLocaleString()} of {rows.length.toLocaleString()} tracks</span>
          {limit < rows.length && <button className="linkbtn" onClick={() => setLimit(limit + 80)}>Load more ↓</button>}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { COV_SOURCES, CovDots, FilterBar, TrackTable });
