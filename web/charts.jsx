/* ============================================================
   charts.jsx — presentational chart components (React)
   All styling via themes.css classNames + CSS vars.
   ============================================================ */
const { useMemo, useState } = React;

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
function DrillRows({ items, accent, plain, wide }) {
  const max = items.length ? items[0][1] : 1;
  if (!items.length) return <div className="dr-empty">No tagged plays in this slice</div>;
  return (
    <div className={"drill-rows" + (wide ? " wide" : "")}>
      {items.map(([k, v]) => {
        // For tracks (wide+plain), split "Artist — Track" onto two lines so neither gets truncated to nothing.
        let nameNode = k;
        if (wide && plain) {
          const idx = k.indexOf(" — ");
          if (idx > 0) {
            const artist = k.slice(0, idx);
            const title = k.slice(idx + 3);
            nameNode = (
              <>
                <span className="dr-title">{title}</span>
                <span className="dr-artist">{artist}</span>
              </>
            );
          }
        }
        return (
          <div className="drill-row" key={k}>
            <span className={"dr-name" + (plain ? " plain" : "") + (wide ? " wide" : "")} title={k}>{nameNode}</span>
            <div className="dr-track"><div className="dr-fill" style={{ width: (v / max) * 100 + "%", background: accent || "var(--accent)" }}></div></div>
            <span className="dr-val num">{v.toLocaleString()}</span>
          </div>
        );
      })}
    </div>
  );
}
/* Radial (polar) clock of plays-by-hour for a slice — visually distinct from
   the linear "When the music plays" bars. Pure SVG, no ECharts. */
function RadialHours({ data, accent }) {
  const arr = (Array.isArray(data) && data.length === 24) ? data : new Array(24).fill(0);
  const total = arr.reduce((a, b) => a + b, 0);
  if (!total) return <div className="dr-empty">No time-of-day data in this slice</div>;
  const max = Math.max(...arr, 1);
  const peak = arr.indexOf(max);
  const cx = 108, cy = 108, innerR = 20, maxR = 86, tickR = 100;
  const pol = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const ang = (h) => (h / 24) * 2 * Math.PI - Math.PI / 2;
  const wedge = (h, v) => {
    const r = innerR + (maxR - innerR) * (v / max);
    const a0 = ang(h) + 0.014, a1 = ang(h + 1) - 0.014;
    const [x0, y0] = pol(innerR, a0), [x1, y1] = pol(r, a0);
    const [x2, y2] = pol(r, a1), [x3, y3] = pol(innerR, a1);
    return `M${x0},${y0} L${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2} L${x3},${y3} A${innerR},${innerR} 0 0 0 ${x0},${y0} Z`;
  };
  const ticks = [["12a", 0], ["6a", 6], ["12p", 12], ["6p", 18]];
  return (
    <div className="radial-hours">
      <svg viewBox="0 0 216 216" role="img" aria-label="Scrobbles by time of day">
        <circle cx={cx} cy={cy} r={maxR} className="rh-ring" />
        <circle cx={cx} cy={cy} r={innerR} className="rh-ring" />
        {arr.map((v, h) => (
          <path key={h} d={wedge(h, v)} className={"rh-wedge" + (h === peak ? " peak" : "")}
                style={{ fill: h === peak ? "var(--good)" : (accent || "var(--accent)"), fillOpacity: 0.35 + 0.6 * (v / max) }}>
            <title>{fmt12full(h)} — {v.toLocaleString()} plays</title>
          </path>
        ))}
        {ticks.map(([lab, h]) => {
          const [tx, ty] = pol(tickR, ang(h));
          return <text key={h} x={tx} y={ty} className="rh-tick" dominantBaseline="middle" textAnchor="middle">{lab}</text>;
        })}
      </svg>
    </div>
  );
}
function DrillPanel({ label, slice, onClose, views }) {
  const [view, setView] = useState("tags"); // "tags" | "tracks"
  const genres = _topN(slice && slice.genres, 6);
  const moods = _topN(slice && slice.moods, 6);
  const tracks = _topN(slice && slice.tracks, 8);
  const showTracks = views && view === "tracks";
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
      {views && (
        <div className="drill-views">
          <div className="seg" role="group">
            <button aria-pressed={view === "tags"} onClick={() => setView("tags")}>Genres &amp; moods</button>
            <button aria-pressed={view === "tracks"} onClick={() => setView("tracks")}>Tracks &amp; time</button>
          </div>
        </div>
      )}
      {showTracks ? (
        <div className="drill-cols">
          <div className="drill-col">
            <div className="drill-coltitle">Top tracks</div>
            <DrillRows items={tracks} accent="var(--accent-2)" plain wide />
          </div>
          <div className="drill-col">
            <div className="drill-coltitle">Scrobbles by time of day</div>
            <RadialHours data={slice && slice.byHour} accent="var(--accent)" />
          </div>
        </div>
      ) : (
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
      )}
    </div>
  );
}

/* ---- Audio-feature extremes: top/bottom tracks per feature ----
   Front-end only: reads per-track audio features off the normalized `tracks`
   array (t.af), so no extra fetch/endpoint is needed. Colors mirror the
   histogram colors used by the Audio Features charts. */
const AUDIO_FEATURES = [
  ["energy",       "Energy",       "#e040fb", "How intense and active a track feels — loud, fast, and noisy scores high; calm and mellow scores low."],
  ["valence",      "Valence",      "#40c4ff", "The musical positivity a track conveys — cheerful and upbeat scores high; sad or moody scores low."],
  ["danceability", "Danceability", "#69f0ae", "How suited a track is for dancing, based on tempo, rhythm steadiness, and beat strength."],
  ["acousticness", "Acousticness", "#ffab40", "Confidence that a track is acoustic — organic, unplugged recordings score high; electronic ones score low."],
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
function AudioFeatureExtremes({ tracks, n = 100 }) {
  const [feat, setFeat] = useState("energy");
  const [key, label, color, desc] = AUDIO_FEATURES.find(([k]) => k === feat) || AUDIO_FEATURES[0];
  const { top, bottom, count } = useMemo(() => {
    const rated = (tracks || [])
      .filter((t) => t.af && t.af[key] != null && isFinite(+t.af[key]))
      .map((t) => ({ ...t, _v: +t.af[key] }))
      .sort((a, b) => b._v - a._v);
    return {
      top: rated.slice(0, n),
      bottom: rated.slice(-n).reverse(), // lowest first
      count: rated.length,
    };
  }, [tracks, key, n]);

  return (
    <section className="block">
      <div className="card">
        <div className="card-head">
          <h3 className="card-title">Audio-feature extremes</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
            <div className="seg seg-sm" role="group">
              {AUDIO_FEATURES.map(([k, l]) => (
                <button key={k} aria-pressed={feat === k} onClick={() => setFeat(k)}>{l}</button>
              ))}
            </div>
            <span className="card-meta">top &amp; bottom {n} · {count.toLocaleString()} rated tracks</span>
          </div>
        </div>
        <p style={{ margin: "0 0 16px", fontSize: 12.5, lineHeight: 1.55, color: "var(--muted-s)", maxWidth: 640 }}>{desc}</p>
        <div className="afx-grid">
          <div>
            <div className="afx-col-title">Most {label.toLowerCase()}</div>
            <div className="afx-scroll"><AfxRows items={top} color={color} /></div>
          </div>
          <div>
            <div className="afx-col-title">Least {label.toLowerCase()}</div>
            <div className="afx-scroll"><AfxRows items={bottom} color={color} /></div>
          </div>
        </div>
      </div>
    </section>
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
  /* Each bar is split into the share you labelled by hand and the share a
     classifier inferred. Worth showing plainly: the classifier is only allowed
     to emit the few moods it can actually predict, so those moods draw on one
     more source than the withheld ones do. Without the split, that asymmetry
     would read as a fact about your taste. */
  return (
    <div className="moods">
      {items.map((m) => {
        const pct = (v) => (max ? (v / max) * 100 : 0);
        const owned = m.owned != null ? m.owned : m.value;
        const inferred = Math.max(0, m.value - owned);
        const inferredPct = m.value > 0 ? Math.round((inferred / m.value) * 100) : 0;
        return (
          <div
            key={m.key}
            className={"moodrow" + (activeKey === m.key ? " active" : "")}
            onClick={() => onPick && onPick(m.key)}
            title={`${m.key} — ${Math.round(m.value).toLocaleString()} plays`
              + (inferred > 0 ? ` · ${inferredPct}% inferred by classifier` : " · all hand-labelled")}
          >
            <span className="m-name">{m.key}</span>
            <div className="m-track">
              <div className="m-fill" style={{ width: pct(owned) + "%", background: m.color || "var(--accent)" }}></div>
              <div
                className="m-fill m-fill-inferred"
                style={{
                  width: pct(inferred) + "%",
                  background: m.color || "var(--accent)",
                  opacity: 0.38,
                }}
              ></div>
            </div>
            <span className="m-val num">{Math.round(m.value).toLocaleString()}</span>
          </div>
        );
      })}
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
