/* ============================================================
   charts.jsx — presentational chart components (React)
   All styling via themes.css classNames + CSS vars.
   ============================================================ */
const { useMemo } = React;

/* genre color ramp — derived from --accent at runtime via CSS color-mix-ish.
   We use fixed hues that read well on dark and harmonize per theme accent. */
function useGenreColors(genres) {
  return useMemo(() => {
    const hues = [262, 192, 152, 28, 330, 50, 280, 210, 96];
    const map = {};
    genres.forEach((g, i) => { map[g] = `oklch(0.72 0.13 ${hues[i % hues.length]})`; });
    return map;
  }, [genres.join("|")]);
}

/* ---- Ranked horizontal bars (Top Artists) ---- */
function HBars({ items, max, activeKey, onPick, unit }) {
  return (
    <div className="hbars">
      {items.map((it, i) => (
        <div
          key={it.key}
          className={"hbar" + (activeKey === it.key ? " active" : "")}
          onClick={() => onPick && onPick(it.key)}
          title={`${it.key} — ${it.value.toLocaleString()} ${unit}`}
        >
          <span className="rank num">{i + 1}</span>
          <div className="hbar-main">
            <div className="hbar-name">{it.key}</div>
            <div className="hbar-track"><div className="hbar-fill" style={{ width: (max ? (it.value / max) * 100 : 0) + "%" }}></div></div>
          </div>
          <div className="hbar-val num">{it.value.toLocaleString()}{it.sub != null && <small> · {it.sub}</small>}</div>
        </div>
      ))}
    </div>
  );
}

/* ---- Top tracks list ---- */
function TrackList({ items, max }) {
  return (
    <div className="tracklist">
      {items.map((t, i) => (
        <div className="trk" key={t.i}>
          <span className="trk-rank num">{i + 1}</span>
          <div style={{ minWidth: 0 }}>
            <div className="trk-name">{t.track}</div>
            <div className="trk-artist">{t.artist}</div>
          </div>
          <div className="trk-plays">
            <div className="mini"><span style={{ width: (max ? (t.play / max) * 100 : 0) + "%" }}></span></div>
            <span className="pc num">{t.play}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---- Hour-of-day chart (24 bars), 12-hour am/pm labels ---- */
function fmt12(h) {
  const ap = h < 12 ? "am" : "pm";
  let hr = h % 12; if (hr === 0) hr = 12;
  return hr + ap;
}
function fmt12full(h) {
  const ap = h < 12 ? "AM" : "PM";
  let hr = h % 12; if (hr === 0) hr = 12;
  return hr + ":00 " + ap;
}
function HourChart({ data, onPick, activeKey }) {
  const max = Math.max(...data, 1);
  const peak = data.indexOf(max);
  return (
    <div>
      <div className={"hours" + (onPick ? " interactive" : "")}>
        {data.map((v, h) => (
          <div
            className={"hcol" + (h === peak ? " peak" : "") + (activeKey === h ? " sel" : "")}
            key={h}
            onClick={() => onPick && onPick(h)}
            title={`${fmt12full(h)} — ${v.toLocaleString()} plays`}
          >
            <div className="hbarv" style={{ height: (v / max) * 100 + "%" }}></div>
          </div>
        ))}
      </div>
      <div className="hours-axis">
        {data.map((_, h) => <span key={h}>{h % 3 === 0 ? fmt12(h) : ""}</span>)}
      </div>
    </div>
  );
}

/* ---- Day of week ---- */
function DowChart({ data, onPick, activeKey }) {
  const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const max = Math.max(...data, 1);
  return (
    <div className="dow">
      {data.map((v, i) => (
        <div
          className={"dowrow" + (onPick ? " clickable" : "") + (activeKey === i ? " sel" : "")}
          key={i}
          onClick={() => onPick && onPick(i)}
        >
          <span className="dlabel">{labels[i]}</span>
          <div className="dtrack"><div className="dfill" style={{ width: (v / max) * 100 + "%" }}></div></div>
          <span className="dval num">{v.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Seasons ---- */
function Seasons({ data, total, onPick, activeKey }) {
  const order = [["winter", "❄"], ["spring", "✿"], ["summer", "☀"], ["fall", "🍂"]];
  const max = Math.max(...Object.values(data), 1);
  return (
    <div className="seasons">
      {order.map(([s, glyph]) => {
        const v = data[s] || 0;
        return (
          <div
            className={"season" + (onPick ? " clickable" : "") + (activeKey === s ? " sel" : "")}
            key={s}
            onClick={() => onPick && onPick(s)}
          >
            <div className="s-top">
              <span className="s-name">{s}</span>
              <span className="s-glyph">{glyph}</span>
            </div>
            <div className="s-val num">{v.toLocaleString()}</div>
            <div className="s-bar"><span style={{ width: (v / max) * 100 + "%" }}></span></div>
            <div className="s-pct num">{total ? Math.round((v / total) * 100) : 0}% of plays</div>
          </div>
        );
      })}
    </div>
  );
}

/* ---- Drill-down: top genres/moods for a time slice (overview cards) ---- */
function _topN(obj, n) {
  return Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).slice(0, n);
}
function DrillRows({ items, accent }) {
  const max = items.length ? items[0][1] : 1;
  if (!items.length) return <div className="dr-empty">No tagged plays in this slice</div>;
  return (
    <div className="drill-rows">
      {items.map(([k, v]) => (
        <div className="drill-row" key={k}>
          <span className="dr-name">{k}</span>
          <div className="dr-track"><div className="dr-fill" style={{ width: (v / max) * 100 + "%", background: accent || "var(--accent)" }}></div></div>
          <span className="dr-val num">{v.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
function DrillPanel({ label, slice, onClose }) {
  const genres = _topN(slice && slice.genres, 6);
  const moods = _topN(slice && slice.moods, 6);
  return (
    <div className="drill-panel">
      <div className="drill-head">
        <span className="drill-eyebrow">Drill-down</span>
        <span className="drill-title">{label}</span>
        <span className="drill-sub num">{((slice && slice.total) || 0).toLocaleString()} plays</span>
        <button className="drill-x" onClick={onClose} aria-label="Close drill-down">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M18 6L6 18M6 6l12 12" /></svg>
        </button>
      </div>
      <div className="drill-cols">
        <div className="drill-col">
          <div className="drill-coltitle">Top genres</div>
          <DrillRows items={genres} accent="var(--accent)" />
        </div>
        <div className="drill-col">
          <div className="drill-coltitle">Top moods</div>
          <DrillRows items={moods} accent="var(--good)" />
        </div>
      </div>
    </div>
  );
}

/* ---- Audio-feature extremes: top/bottom tracks per feature ----
   Front-end only: reads per-track audio features off the normalized `tracks`
   array (t.af), so no extra fetch/endpoint is needed. Colors mirror the
   histogram colors used by the Audio Features charts. */
const AUDIO_FEATURES = [
  ["energy",       "Energy",       "#e040fb"],
  ["valence",      "Valence",      "#40c4ff"],
  ["danceability", "Danceability", "#69f0ae"],
  ["acousticness", "Acousticness", "#ffab40"],
];
function AfxRows({ items, color }) {
  if (!items.length) return <div className="dr-empty">No tracks with this feature</div>;
  return (
    <div className="afx-rows">
      {items.map((t, i) => (
        <div className="afx-row" key={t.i}>
          <span className="afx-rank num">{i + 1}</span>
          <div className="afx-meta">
            <div className="afx-track" title={t.track}>{t.track}</div>
            <div className="afx-artist" title={t.artist}>{t.artist}</div>
          </div>
          <div className="afx-bar"><span style={{ width: Math.round(t._v * 100) + "%", background: color }}></span></div>
          <span className="afx-val">{t._v.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}
function AudioFeatureExtremes({ tracks, n = 12 }) {
  return (
    <>
      {AUDIO_FEATURES.map(([key, label, color]) => {
        const rated = (tracks || [])
          .filter((t) => t.af && t.af[key] != null && isFinite(+t.af[key]))
          .map((t) => ({ ...t, _v: +t.af[key] }))
          .sort((a, b) => b._v - a._v);
        const top = rated.slice(0, n);
        const bottom = rated.slice(-n).reverse(); // lowest first
        return (
          <section className="block" key={key}>
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">{label}</h3>
                <span className="card-meta">top &amp; bottom {n} · {rated.length.toLocaleString()} rated tracks</span>
              </div>
              <div className="afx-grid">
                <div>
                  <div className="afx-col-title">Most {label.toLowerCase()}</div>
                  <AfxRows items={top} color={color} />
                </div>
                <div>
                  <div className="afx-col-title">Least {label.toLowerCase()}</div>
                  <AfxRows items={bottom} color={color} />
                </div>
              </div>
            </div>
          </section>
        );
      })}
    </>
  );
}

/* ---- Seasonal favorites: 4-up genres/moods/tracks per season ---- */
function SeasonalFavorites({ drill }) {
  const order = [["winter", "❄", "Winter"], ["spring", "✿", "Spring"], ["summer", "☀", "Summer"], ["fall", "🍂", "Fall"]];
  if (!drill || !drill.season) {
    return <div className="empty"><div className="big">No seasonal data yet</div><div>Load your library (tracks + scrobbles) to see seasonal favorites.</div></div>;
  }
  return (
    <div className="grid g-2 seasonal-grid">
      {order.map(([key, glyph, name]) => {
        const s = drill.season[key] || { genres: {}, moods: {}, tracks: {}, total: 0 };
        const genres = _topN(s.genres, 4), moods = _topN(s.moods, 4), tracks = _topN(s.tracks, 5);
        const tmax = tracks.length ? tracks[0][1] : 1;
        return (
          <div className={"card season-card season-" + key} key={key}>
            <div className="card-head norule">
              <h3 className="card-title">{name} <span className="season-glyph">{glyph}</span></h3>
              <span className="card-meta">{(s.total || 0).toLocaleString()} plays</span>
            </div>
            <div className="season-sub">Top genres</div>
            <div className="season-chips">
              {genres.length ? genres.map(([g, n]) => <span className="tagchip" key={g}>{g}<span className="tc-n num">{n}</span></span>) : <span className="dr-empty">—</span>}
            </div>
            <div className="season-sub">Top moods</div>
            <div className="season-chips">
              {moods.length ? moods.map(([m, n]) => <span className="tagchip mood-chip" key={m}>{m}<span className="tc-n num">{n}</span></span>) : <span className="dr-empty">—</span>}
            </div>
            <div className="season-sub">Most played</div>
            <div className="tracklist mini-tl">
              {tracks.length ? tracks.map(([label, plays], i) => {
                const parts = label.split(" — ");
                const artist = parts.shift(); const title = parts.join(" — ");
                return (
                  <div className="trk" key={label}>
                    <span className="trk-rank num">{i + 1}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="trk-name">{title || artist}</div>
                      <div className="trk-artist">{title ? artist : ""}</div>
                    </div>
                    <div className="trk-plays">
                      <div className="mini"><span style={{ width: (plays / tmax) * 100 + "%" }}></span></div>
                      <span className="pc num">{plays}</span>
                    </div>
                  </div>
                );
              }) : <div className="dr-empty">—</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---- Mood distribution ---- */
function MoodBars({ items, max, activeKey, onPick }) {
  return (
    <div className="moods">
      {items.map((m) => (
        <div
          key={m.key}
          className={"moodrow" + (activeKey === m.key ? " active" : "")}
          onClick={() => onPick && onPick(m.key)}
          title={`${m.key} — ${m.value.toLocaleString()} tracks`}
        >
          <span className="m-name">{m.key}</span>
          <div className="m-track"><div className="m-fill" style={{ width: (max ? (m.value / max) * 100 : 0) + "%", background: m.color || "var(--accent)" }}></div></div>
          <span className="m-val num">{m.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Genre donut (SVG) + legend ---- */
function GenreDonut({ items, total, colors, activeKey, onPick, size = 132 }) {
  const r = size / 2;
  const stroke = size * 0.17;
  const radius = r - stroke / 2 - 1;
  const circ = 2 * Math.PI * radius;
  let offset = 0;
  const segs = items.map((it) => {
    const frac = total ? it.value / total : 0;
    const seg = { ...it, frac, dash: frac * circ, offset };
    offset += frac * circ;
    return seg;
  });
  return (
    <div className="donut-wrap">
      <div className="donut" style={{ width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle cx={r} cy={r} r={radius} fill="none" stroke="var(--track-bg)" strokeWidth={stroke} />
          {segs.map((s) => (
            <circle
              key={s.key}
              cx={r} cy={r} r={radius} fill="none"
              stroke={colors[s.key] || "var(--accent)"}
              strokeWidth={activeKey === s.key ? stroke + 3 : stroke}
              strokeDasharray={`${Math.max(s.dash - 1.5, 0)} ${circ}`}
              strokeDashoffset={-s.offset}
              style={{ transition: "stroke-width .2s, stroke-dasharray .5s", cursor: "pointer", opacity: activeKey && activeKey !== s.key ? 0.4 : 1 }}
              onClick={() => onPick && onPick(s.key)}
            />
          ))}
        </svg>
        <div className="donut-center">
          <div className="dc-num num">{items.length}</div>
          <div className="dc-lab">genres</div>
        </div>
      </div>
      <div className="donut-legend">
        {segs.map((s) => (
          <div key={s.key} className={"glegend" + (activeKey === s.key ? " active" : "")} onClick={() => onPick && onPick(s.key)}>
            <span className="sw" style={{ background: colors[s.key] }}></span>
            <span className="gname">{s.key}</span>
            <span className="gval num">{Math.round(s.frac * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---- Tag cloud (ranked chips) ---- */
function TagCloud({ items, activeKey, onPick }) {
  return (
    <div className="tagcloud">
      {items.map((t) => (
        <div key={t.key} className={"tagchip" + (activeKey === t.key ? " active" : "")} onClick={() => onPick && onPick(t.key)}>
          {t.key}<span className="tc-n num">{t.value}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Coverage bars ---- */
function CoverageBars({ rows, total }) {
  return (
    <div className="coverage">
      {rows.map((r) => {
        const pct = total ? (r.value / total) * 100 : 0;
        const color = pct >= 75 ? "var(--good)" : pct >= 50 ? "var(--accent)" : "var(--warn)";
        return (
          <div className="covrow" key={r.label}>
            <div className="cov-top">
              <span className="cov-label">{r.label}</span>
              <span className="cov-pct num">{pct.toFixed(1)}% <small>· {r.value.toLocaleString()}</small></span>
            </div>
            <div className="cov-track"><div className="cov-fill" style={{ width: pct + "%", background: color }}></div></div>
          </div>
        );
      })}
    </div>
  );
}

Object.assign(window, { HBars, TrackList, HourChart, DowChart, Seasons, MoodBars, GenreDonut, TagCloud, CoverageBars, useGenreColors, DrillPanel, SeasonalFavorites });
