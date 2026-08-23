# Listening Atlas — "wow factor" ideas

Research pass over open-source portfolio pieces, animated component libraries,
and 2026 web-design writeups, filtered down to things that fit **this** app:
React 18 (UMD) + esbuild, ECharts, `themes.css` custom properties, a worker-fed
data layer, and ~2,700 tracks / ~13,700 scrobbles.

Selection rule used throughout: **the effect must carry information or reduce
friction.** Nothing that only exists to be looked at, nothing that fights the
user for control of the page.

**Status:** 2, 3, 4, 5 and 9 are built — see `web/motion.js`, `web/ambient.js`,
and the motion block at the bottom of `web/themes.css`. Both effect groups have
an opt-out in the Tweaks panel and all of it is inert under
`prefers-reduced-motion: reduce`.

---

## 1. Staged reveal on first paint (the "assembly" moment)

**What:** When a tab mounts, the panels don't just appear — KPI numbers count up
from 0 with an ease-out, bars grow from zero width, chart series draw left to
right, all on a ~40ms stagger down the page. Total choreography under 700ms,
then everything is static and stays static.

**Why it lands:** It's the single highest ratio of impression to effort. The
viewer's first two seconds are the whole first impression, and a staggered
assembly reads as "this thing is alive and it computed something" instead of
"here is a screenshot."

**Here:** `.hbar-fill`, `.m-fill`, `.cov-fill`, `.dr-fill` already animate their
`width` with `cubic-bezier(.2,.7,.2,1)` — they just animate on data change, not
on entry. Add an `IntersectionObserver` that flips a `data-revealed` attribute
on each `.card`, and drive `--reveal-delay` off the card's index. ECharts gets
this for free via `animationDelay: (i) => i * 12`. KPI count-up goes in `Kpi()`
in `dashboard.jsx:836`.

**Restraint:** Fire once per session per card, never on filter changes — a
number that re-rolls every time you click a genre chip is a slot machine.

---

## 2. Cross-tab shared-element transitions (View Transitions API)

**What:** Clicking a genre chip in one view and landing on the same genre
highlighted in another view morphs the element across the navigation instead of
cutting. Same for opening a track row into a detail modal.

**Why it lands:** This is the effect people describe as "it feels native." It's
also the one that most reliably makes a web dashboard feel unlike every other
web dashboard, and in 2026 it's plain CSS plus a `document.startViewTransition`
call — no library, no bundle cost.

**Here:** Tab switching in `App()` is a state swap, so it's a one-line wrap.
Give the shared element a `view-transition-name` derived from the genre/track
key. The existing `.modal-scrim` fade (`themes.css:472`) becomes the fallback
path for browsers without support.

**Restraint:** Cap at ~220ms. Anything longer and repeat navigation feels
sticky.

---

## 3. Ambient shader backdrop keyed to the library, not to nothing

**What:** Replace the two static `radial-gradient`s in `--bg-grad` with a very
slow animated mesh gradient whose colors are drawn from the current filter's
dominant genre hues, drifting at a speed you notice only if you stare.

**Why it lands:** Mesh/organic gradients are the ambient-background convention
right now, and the version that isn't a cliché is the one whose *colors mean
something*. Filter to shoegaze and the room changes color.

**Here:** [`paper-design/shaders`](https://github.com/paper-design/shaders) is
zero-dependency canvas WebGL with a `MeshGradient` component and a vanilla
`createShader` entry point — it drops into the esbuild bundle without pulling in
Three.js. Feed it the `genreColorMap` OKLCH hues already computed at
`dashboard.jsx:265`.

**Restraint:** Opacity ~0.35, blur it, put it behind `--bg` at 60–70% mix, and
hard-disable under `prefers-reduced-motion` and on battery-saver. If you can
read a KPI less easily with it on, it's too strong.

---

## 4. Cursor-proximity magnetism on interactive elements

**What:** Buttons, chips, and chart legend swatches lean ~3–4px toward the
cursor as it approaches, on a damped spring, and settle back when it leaves.
Chart data points swell slightly when the cursor is near but not yet over them.

**Why it lands:** It makes the whole surface feel responsive *before* you click
anything — the interface acknowledges you. Done at 3px it registers
subconsciously; done at 15px it's a toy.

**Here:** One `pointermove` listener on `document`, rAF-throttled, writing
`--mx/--my` custom properties on elements within radius; CSS does the transform.
Applies cleanly to `.fchip`, `.icon-btn`, `.glegend`, `.season.clickable`.

**Restraint:** Pointer-fine only (`@media (hover: hover) and (pointer: fine)`),
zero work when idle, and never on anything the user is currently dragging.

---

## 5. Spotlight follow on cards

**What:** A soft radial highlight tracks the cursor across the hovered card,
lighting its border where the cursor is closest — the card appears to be lit by
a lamp you're holding.

**Why it lands:** It's the cheapest possible depth cue and it makes a grid of
flat panels stop looking flat. Costs one pseudo-element and two custom
properties per card, no JS per frame beyond the shared pointer listener from #4.

**Here:** `.card` already has an inset white hairline in `--card-shadow`. Add
`.card::before` with `radial-gradient(400px at var(--mx) var(--my), color-mix(in
oklab, var(--accent) 12%, transparent), transparent 60%)`, opacity 0 → 1 on
hover.

**Restraint:** One card lit at a time. A grid where every card glows is a
Christmas tree.

---

## 6. The Tag Constellation as the centerpiece, upgraded

**What:** `TagConstellation` (`echarts-charts.jsx:660`) is the piece with the
most inherent visual drama — a force-directed graph of the library's tag space.
Push it: gentle continuous drift so the graph breathes, edge-bundling so the
links read as currents rather than spaghetti, node glow scaled by play count,
and a hover state that dims everything except the hovered node's neighborhood.

**Why it lands:** Every music dashboard has a bar chart of top artists. Almost
none have a navigable map of their own taste. This is the screenshot that goes
in the portfolio header, and it's already 80% built.

**Here:** ECharts `graph` series supports `force.friction` tuning for the drift
and `emphasis: { focus: 'adjacency', blurScope: 'series' }` for the neighborhood
dim — mostly config, not new code.

**Restraint:** Freeze the layout after settling; a graph that never stops moving
can't be read or clicked.

---

## 7. Scrollytelling intro on the timeline

**What:** A short scroll-driven narrative above the main grid: as you scroll,
the listening timeline draws itself year by year, and 3–4 annotations pin and
release — "2019: 412 new artists," "the shoegaze year," "the six-month gap."

**Why it lands:** It turns a dashboard into a story, which is what separates a
portfolio piece from a tool. Cross-browser CSS `scroll-timeline` support means
this is now `animation-timeline: view()` rather than a scroll-listener rig.

**Here:** Pure CSS over the existing `TimelineChart`
(`echarts-charts.jsx:92`), with the annotation copy generated from the real
aggregates in `agg` (`dashboard.jsx:319`) so it's never stale.

**Restraint:** Keep it to roughly one viewport of scroll, and make the grid
reachable without scrolling through it (a "skip to data" affordance, or make it
the Overview tab's header only).

---

## 8. Canvas-rendered track table

**What:** Swap the DOM `<table class="tracks">` for a canvas-rendered grid so
scrolling 2,700 rows is glass-smooth, with momentum, sticky headers, and inline
sparkline cells that a DOM table can't afford.

**Why it lands:** Snappiness *is* a wow factor, and it's the one reviewers feel
without being able to name. A table that never drops a frame while you flick
through it reads as engineering quality.

**Here:** [`glide-data-grid`](https://github.com/glideapps/glide-data-grid)
renders to canvas and holds 60fps at millions of rows; at 2,700 rows the
simpler win may just be virtualizing the existing table. Measure first —
`PERFORMANCE_MAP.md` is the right place to record the before/after.

**Restraint:** Canvas grids need explicit accessibility work (keyboard nav, a
screen-reader shadow DOM). If that's not going in, virtualize the DOM table
instead and keep the semantics.

---

## 9. Live filter morphs instead of re-renders

**What:** When a filter is applied, bars don't blink to their new values — they
*travel* there, rows that drop out fade and collapse, rows that enter slide in.
The chart re-sorts with elements animating to their new rank positions.

**Why it lands:** This is the highest-value item on the list for actual usability.
Animated re-ranking lets you *see* what the filter did — which artists survived,
which collapsed — instead of comparing two static frames from memory. It's also
the effect that makes an app feel expensive.

**Here:** ECharts does this natively with `universalTransition: true` plus stable
series `id`s. For the DOM lists, a FLIP pass (or `view-transition-name` per row,
reusing #2's machinery) gets the same result.

**Restraint:** 300–400ms, ease-out, and never queue: a second filter click
interrupts the first animation rather than waiting in line.

---

## 10. Theme switch as a wipe, and a "now" pulse

**What:** Two small signature moments. (a) The theme switcher in
`tweaks-panel.jsx` cross-fades via a circular wipe originating at the toggle
button, using View Transitions — one of the most-shared micro-interactions of the
last two years. (b) A single quiet pulse on the live-sync pill when new scrobbles
land, so the page shows that it's connected to a real, still-running pipeline.

**Why it lands:** Signature moments are what people remember and re-demo. Both
are cheap, both are self-contained, and (b) advertises the thing that actually
distinguishes this project — there's a live pipeline behind it, not a CSV.

**Here:** (a) wraps the existing theme apply in `dashboard.jsx:191` in
`startViewTransition` with a `clip-path` keyframe. (b) reuses `pill-pulse`
(`themes.css:111`) fired once on a successful `ScrobbleSync`
(`dashboard.jsx:758`) that returns new rows.

**Restraint:** Once per event, never looping. A permanently pulsing element is
an alarm.

---

## Cross-cutting rules

- **`prefers-reduced-motion: reduce` turns all of it off** — every idea above
  degrades to its static end state, not to a shorter animation.
- **Animate `transform` and `opacity` only.** The existing `width` transitions on
  bars are the exception worth keeping (they're the information); everything new
  should be compositor-only.
- **Nothing above the fold blocks interaction.** Reveal animations must be
  click-through from frame one.
- **Budget:** if any of this pushes interaction latency past ~100ms or drops the
  scroll below 60fps on the track table, it loses. Snappiness beats spectacle.

## Suggested order

Highest impact per hour of work: **9 → 1 → 5 → 2 → 6**, then the ambient and
narrative pieces (3, 7), with 4, 8, and 10 as polish.

## Sources

- [react-bits](https://github.com/DavidHDev/react-bits) — animated React components
- [paper-design/shaders](https://github.com/paper-design/shaders) — zero-dependency canvas shaders
- [glide-data-grid](https://github.com/glideapps/glide-data-grid) — canvas data grid
- [GitHub `awwwards` topic](https://github.com/topics/awwwards) and [`micro-interactions` topic](https://github.com/topics/micro-interactions)
- [Web Design Trends 2026 + code examples](https://studiomeyer.io/en/blog/webdesign-trends-2026)
- [Figma — web design trends](https://www.figma.com/resource-library/web-design-trends/)
