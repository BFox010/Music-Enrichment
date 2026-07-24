/* ============================================================
   datefilter.jsx — reusable per-page date-range control
   ============================================================ */
const { useMemo: useMemoDF } = React;

/* Inclusive from/to date-range picker with quick presets. Page-scoped: each
   page owns its own { from, to } and passes onChange to update it — one page's
   range never touches another's. `bounds` gives the min/max selectable dates
   (the library's scrobble span). When `disabled` (no live scrobble data yet)
   the control renders muted and inert. Emits ISO YYYY-MM-DD strings, matching
   both the client re-aggregation helpers and the API's start/end params. */
function DateFilter({ value, onChange, bounds, disabled, label }) {
  const from = (value && value.from) || "";
  const to = (value && value.to) || "";
  const min = (bounds && bounds.min) || undefined;
  const max = (bounds && bounds.max) || undefined;
  const active = !!(from || to);

  const set = (patch) => onChange({ from, to, ...patch });
  const clear = () => onChange({ from: "", to: "" });

  // Presets derived from the data's latest date so they track the real library
  // span rather than the wall clock.
  const presets = useMemoDF(() => {
    if (!max) return [];
    const y = parseInt(max.slice(0, 4), 10);
    const yearStart = `${y}-01-01`;
    let last12 = `${y - 1}${max.slice(4)}`;
    const d = new Date(max + "T00:00:00Z");
    if (!Number.isNaN(d.getTime())) {
      d.setUTCFullYear(d.getUTCFullYear() - 1);
      last12 = d.toISOString().slice(0, 10);
    }
    return [
      ["This year", yearStart, max],
      ["Last 12 months", last12, max],
    ];
  }, [max]);

  const isPreset = (f, t) => from === f && to === t;

  return (
    <div className={"datefilter" + (disabled ? " is-disabled" : "")}>
      <span className="df-label">{label || "Date range"}</span>
      <div className="df-inputs">
        <input type="date" className="df-input" value={from} min={min} max={to || max}
          disabled={disabled} onChange={(e) => set({ from: e.target.value })} aria-label="From date" />
        <span className="df-dash">–</span>
        <input type="date" className="df-input" value={to} min={from || min} max={max}
          disabled={disabled} onChange={(e) => set({ to: e.target.value })} aria-label="To date" />
      </div>
      {!disabled && presets.map(([lab, f, t]) => (
        <button key={lab} className={"df-preset" + (isPreset(f, t) ? " active" : "")}
          onClick={() => (isPreset(f, t) ? clear() : onChange({ from: f, to: t }))}>{lab}</button>
      ))}
      {active && !disabled && <button className="df-clear" onClick={clear}>Clear</button>}
      {disabled && <span className="df-note">Load your library to filter by date</span>}
    </div>
  );
}

Object.assign(window, { DateFilter });
