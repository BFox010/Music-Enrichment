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
function HourChart({ data }) {
  const max = Math.max(...data, 1);
  const peak = data.indexOf(max);
  return (
    <div>
      <div className="hours">
        {data.map((v, h) => (
          <div className={"hcol" + (h === peak ? " peak" : "")} key={h} title={`${fmt12full(h)} — ${v.toLocaleString()} plays`}>
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
function DowChart({ data }) {
  const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const max = Math.max(...data, 1);
  return (
    <div className="dow">
      {data.map((v, i) => (
        <div className="dowrow" key={i}>
          <span className="dlabel">{labels[i]}</span>
          <div className="dtrack"><div className="dfill" style={{ width: (v / max) * 100 + "%" }}></div></div>
          <span className="dval num">{v.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Seasons ---- */
function Seasons({ data, total }) {
  const order = [["winter", "❄"], ["spring", "✿"], ["summer", "☀"], ["fall", "🍂"]];
  const max = Math.max(...Object.values(data), 1);
  return (
    <div className="seasons">
      {order.map(([s, glyph]) => {
        const v = data[s] || 0;
        return (
          <div className="season" key={s}>
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

Object.assign(window, { HBars, TrackList, HourChart, DowChart, Seasons, MoodBars, GenreDonut, TagCloud, CoverageBars, useGenreColors });
