# Plan: Tag Constellation interactivity

Status: draft, nothing implemented. Scope is `TagConstellation` in
`web/echarts-charts.jsx` (lines ~791–960) and the `/api/tag-graph` data it
renders. Goal is interactability: a framed first paint, nodes that move when
dragged without dragging the whole graph, and a click that shows a node's
connections and stays put.

## 1. What the owner sees, and why

Three complaints, each traced to a specific mechanism. Numbers come from a
headless port of ECharts 5.5.1 `forceHelper.step()` plus the parameter mapping
in `forceLayout.ts`, run against the real `/api/tag-graph` output for each
field (see §7 for the method).

Graph sizes at today's cut-offs:

| field | min_count | nodes | edges | density | weak edges (<5% of max weight) |
|---|---|---|---|---|---|
| discogs_styles | 15 | 105 | 715 | 0.13 | 541 |
| lastfm_tags | 15 | 229 | 2014 | 0.08 | 1813 |
| mood_tags | 1 | 14 | 86 | 0.95 | 28 |

### 1a. "Starts super zoomed in, then pops out"

- Nodes are seeded on a ring of radius `0.46 × min(w,h)` (~285 px) and the
  series renders at `zoom: 1`. With 715 springs targeting 150–240 px the
  cluster expands well past the canvas within the first second. At the
  1.5 s fit, the simulated bounding box is ~2480×1920 px on a 1100×620 canvas.
  The user is looking at the middle of a cloud until `fitView` snaps zoom to
  ~0.27. That snap is the "pop".
- `fitView` itself re-heats the physics. ECharts rebuilds the force instance
  on every layout pass and `forceLayout()` resets `friction` to the initial
  value (`forceHelper.ts`, `initialFriction`). The `setOption({zoom, center})`
  patch is a layout pass, so at 1.5 s the sim restarts at full energy and runs
  another ~7 s (0.4 → 0.01 at ×0.992/step ≈ 459 steps × 16 ms).
- Fit timing is a guess (1.5 s) against a sim that takes ~7 s to settle, and
  the fit runs only once, so the framing drifts afterwards.

### 1b. "Tethers too strong; everything moves in step"

- **ECharts springs are positional constraints, not springs.** Each step moves
  a node by `w × (dist − edgeLength) × friction` toward the target length
  (`forceHelper.ts` step loop). With friction 0.4 that closes 40% of the
  length error per step per edge. Repulsion is `(rep1 + rep2) / d²`, which at
  150 px with rep ≈ 3700 is ~0.3 px/step. Springs out-muscle repulsion by
  roughly 75:1, so the 715 edges behave as rigid rods and the graph is an
  over-constrained truss. There is no stiffness knob in ECharts force; the only
  levers are `edgeLength`, per-edge `ignoreForceLayout`, node weight `w`, and
  which edges exist.
- **The per-node `repulsion` array is a trap.** `forceLayout.ts` does
  `isArray(repulsion) ? repulsion : [r, r]` and then
  `linearMap(value, extent, repulsionArr)`, which reads only elements `[0]`
  and `[1]`. The 105-element array we pass becomes the range
  `[rep(Indie Rock), rep(Pop Rap)]` ≈ `[3703, 2534]`: inverted (smallest node
  repels most) and nearly uniform. The comment above it describing size-scaled
  repulsion describes behaviour ECharts does not implement.
- **Rigid-body motion is free.** The only absolute anchor in the sim is gravity
  toward the view centre, so a stiff connected truss can rotate about that
  centre at zero cost. Dragging one node therefore rotates the whole graph.
  Simulated drag of a median-degree node by 200 px (discogs_styles):

  | scenario | physics edges | median move of every *other* node | non-neighbours |
  |---|---|---|---|
  | A current | 715 | 152 px | 154 px |
  | B fix repulsion range only | 715 | 830 px | 823 px |
  | C prune physics edges to top-3 per node ∪ weight ≥ p90 | 271 | 247 px | 247 px |
  | E prune to top-2 per node | 206 | 33 px | 32 px |
  | F2 fix range + pin top-8 hubs after settle | 715 | 16 px | 16 px |
  | freeze layout after settle (`layout: "none"`) | 0 | 0 px | 0 px |

  Non-neighbours move as much as neighbours in A–D: that is rigid rotation, not
  spring propagation. Parameter tuning alone cannot fix it; only anchoring
  (pinning hubs) or freezing does. lastfm_tags shows the same shape (A 126 px,
  F2 36 px).
- **Every drag re-heats the whole graph for ~7 s.** `GraphView` calls
  `forceLayout.warmUp()` on drag (friction → 0.8 × initial) and `setUnfixed`
  on dragend, so the node you just placed is pulled straight back into the
  truss. That is the "can barely move anything" feel.

### 1c. "Clicking should better highlight connections"

- There is no click handler. The only highlight is hover
  (`emphasis.focus: "adjacency"`), which vanishes on mouseout, and labels are
  hidden on 75 of 105 nodes so a highlighted neighbour is often an unlabelled
  dot.
- Any highlight implemented via `setOption` on a live force graph re-heats the
  sim (1a), so selection styling and live physics fight each other. This is
  the second reason to freeze after settling.

### Other friction that compounds the above

- The data effect keys on `[active, field]`: every visit to the page refetches
  and re-lays out, so an arrangement the user untangled is thrown away.
- `chart.current` race on cold load (front-end audit finding 2): `setupChart`
  returns silently if ECharts is not loaded yet, leaving the wrap visible and
  empty.
- `roam: true` captures wheel events over the whole card, so page scrolling
  stalls on the chart. No `scaleLimit`, no "fit" affordance after the user
  zooms.
- Tooltip formatter builds HTML from third-party tag names (audit finding 8).
- 541 of 715 style edges are below 5% of max weight and drawn at ~0.18
  opacity; the haze itself makes nodes hard to grab.

## 2. Decision to make first

Commit `9f6f46e` deliberately kept `layout: "force"` running indefinitely so
nodes "bounce". The measurements above show that live physics is the root of
both the pop and the whole-graph drag, and that it blocks deterministic click
styling. Two options:

**Option A (recommended): settle off-screen, then freeze.** Compute the layout
to convergence before the first paint, set `layout: "none"` with explicit
`x`/`y`, frame it in the same `setOption`, then reveal. Drag moves exactly one
node and its edges. Selection styling is a cheap `setOption` with no side
effects. Bounce is gone, replaced by an optional "shake" button (§3, step 6).

**Option B: keep live physics, anchor it.** Fix the repulsion range, mark
weak edges `ignoreForceLayout`, pin the top-8 hubs with `fixed: true` after
the first settle, lower friction. Coupling drops from 152 px to ~16 px, but
every drag still re-heats for ~7 s, the placed node still snaps back unless we
re-pin it via another re-heating `setOption`, and click styling stays fragile.

The rest of this plan assumes Option A. If the bounce is non-negotiable, the
Option B parameter set is in §6 and steps 3–5 still apply.

## 3. Implementation steps

Order matters: each step is independently shippable and testable.

### Step 1: Extract pure graph helpers into `web/graph-layout.js`

Plain-global file next to `data-processing.js`, loaded from `index.html`
before the bundle, so it is testable under `node --test` and usable from a
worker. No React, no ECharts.

- `pruneEdges(edges, {perNode: 3, keepAbove})` returns the physics backbone:
  each node's strongest `perNode` edges plus every edge at or above
  `keepAbove` (default: 90th percentile weight). Styles: 715 → 271 edges;
  lastfm: 2014 → 670.
- `adjacency(nodes, edges)` returns `Map<tag, Array<{tag, weight}>>` sorted by
  weight desc. Used by selection and by the neighbour list.
- `settleLayout(nodes, edges, opts)` is the ~60-line port of
  `forceHelper.step()` used for §1's measurements: ring seed, spring,
  gravity, repulsion, friction decay, loop until `friction < 0.01`. Returns
  `Map<tag, [x, y]>`. Deterministic for a given input (ring seed, no random
  except the `d === 0` branch). Measured in node: ~160 ms for 105/271, ~800 ms
  for 229/670.
- `fitTransform(positions, w, h, margin = 1.2)` returns `{zoom, center}`, the
  maths currently inlined in `fitView`.

Why our own settle rather than ECharts `layoutAnimation: false`: ECharts'
synchronous mode also runs synchronously on every drag event
(`_startForceLayoutIteration` recurses without `setTimeout` when animation
is off), so it cannot be left on for interaction, and its positions are not
readable before the first paint. Owning the settle lets us run it before
`setOption` and later move it into the worker without touching the component.

### Step 2: Render frozen, framed, from state

In `TagConstellation`:

- Fetch → `setGraph({nodes, edges})` (state), not a direct `setupChart` call.
- One render effect keyed on `[graph, chart.current]` (fixes the cold-load
  race): `positions = settleLayout(...)`, `{zoom, center} = fitTransform(...)`,
  then a single `setOption` with `layout: "none"`, `x/y` per node,
  `zoom`, `center`, `draggable: true`, `roam: "move"` initially (see step 5).
  Reveal the wrap only after this call, so the first frame is already framed.
- Keep `positions` in a ref. ECharts has no dragend event at the chart level
  (`graphroam` covers pan/zoom only), so on `chart.on("mouseup")` over a node
  read every node's layout back with the existing `getItemLayout` loop and
  write it into the ref. Later `setOption` merges then carry the moved
  positions instead of resetting them.
- Cache `{graph, positions}` per field in a ref. Re-entering the page with a
  cached field does `resize()` and nothing else. Invalidate on
  `refreshVersion` like Timeline/Trajectory do.
- Edge rendering: all edges drawn, but weak ones (below `keepAbove`) at
  opacity ~0.06 and width 0.5; backbone edges keep the current
  log-width/opacity ramp. A "weak links" toggle in the seg row hides them
  entirely.
- Tooltip: `renderMode: "richText"`, drop the HTML string.
- Node `symbolSize` unchanged; labels shown for the top 30 by count as now,
  plus every node in the current selection's adjacency (step 3).

### Step 3: Click to select, sticky adjacency highlight

- `chart.on("click", p)`: node → `setSelected(p.data.name)` (same node again
  clears); edge → select its heavier endpoint; blank canvas
  (`chart.getZr().on("click")` with no target) → clear. `Escape` clears.
- Selection is applied as **base styles**, not emphasis: non-adjacent nodes
  `itemStyle.opacity 0.12`, non-adjacent edges `0.03`, adjacent edges at full
  ramp and `width × 1.5`, adjacent nodes full opacity with labels on,
  selected node with an accent-coloured border. One `setOption` merge of
  `data`/`edges` arrays, positions preserved from the ref. Hover emphasis
  stays on so hovering a dimmed node still previews its own adjacency.
- Why not `dispatchAction({type: "highlight"})`: its blur state is cleared by
  ECharts on the next mouseout of any element, so the selection would flicker
  off the moment the pointer moves. Base styles are deterministic.
- Neighbour panel (React, beside the chart on wide screens, below on mobile):
  selected tag, its play count, and neighbours sorted by shared plays with a
  proportional bar. Click a neighbour → it becomes the selection and the view
  pans to it (`setOption({series: [{center}]})`, cheap when frozen). Panel
  hidden when nothing is selected; the description line changes to
  "Click a tag to see what it travels with."
- Optional wire to cross-filter: `mood_tags` → `setFilter("mood", tag)`,
  `lastfm_tags` → `setFilter("tag", tag)`; `discogs_styles` has no filter
  kind. Ship as a "Filter library" button inside the panel, not on the node
  click, so exploring the graph never mutates global state by accident.

### Step 4: Density control and per-field cut-offs

- Replace the hardcoded `min_count` with per-field presets behind a
  Sparse/Normal/Dense seg: styles 30/15/8, lastfm 40/20/12, moods 1/1/1.
  Default lastfm to 20 so the initial settle stays well under 500 ms.
- `perNode` for pruning stays 3; `keepAbove` = p90 of the returned edges.

### Step 5: Zoom and roam hygiene

- `scaleLimit: {min: 0.3, max: 6}`. Add a "Fit" button that reapplies
  `fitTransform` to current positions, and double-click on blank canvas doing
  the same.
- `roam: "move"` by default so the wheel scrolls the page; zoom via the
  buttons and pinch (touch pinch is delivered as `scale` regardless). If wheel
  zoom is missed, `roam: true` with `Ctrl`/`⌘`-wheel only is a follow-up.
- Mobile: panel stacks under the chart; `.echart-wrap.tall` stays 460 px.

### Step 6: Optional "shake"

Button that runs `settleLayout` again from the *current* positions with a
short animated pass (ECharts `layout: "force"`, `layoutAnimation: true`,
pinned selection, ~1.5 s), then reads positions back and refreezes. Gives the
bounce back on demand without paying for it during interaction. Lowest
priority; only if the owner misses the motion.

### Step 7: Worker offload (only if step 4 defaults are not enough)

`settleLayout` is pure and global, so `data-worker.js` can `importScripts`
it. Post `{nodes, edges}` in, positions out, show the skeleton meanwhile. Not
needed at the step-4 defaults (~160 ms for styles).

## 4. Files

| file | change |
|---|---|
| `web/graph-layout.js` (new) | pure helpers: `pruneEdges`, `adjacency`, `settleLayout`, `fitTransform` |
| `web/graph-layout.test.js` (new) | node tests, see §5 |
| `web/index.html` | `<script src="graph-layout.js">` before the bundle |
| `web/echarts-charts.jsx` | `TagConstellation` rewrite per steps 2–5 |
| `web/themes.css` | neighbour panel, selected-node border colour, seg row wrapping |
| `web/app.bundle.js` | rebuilt (`npm run build`), committed |
| `CLAUDE.md` gotchas | two lines: ECharts `force.repulsion` array means `[min, max]` only; any `setOption` on a live force graph rebuilds the sim and resets friction |
| `scripts/build_frontend.mjs` | no change (graph-layout.js is a global, not bundled) |

`/api/tag-graph` is unchanged. If step 4 wants a server-side node cap, add a
`top` query param mirroring `/api/forgotten-favorites` and a test in
`tests/test_app_api.py::TestTagGraph`.

## 5. Tests

`web/graph-layout.test.js`, `node --test` like `data-processing.test.js`:

- `pruneEdges` keeps every node's top-k, keeps every edge ≥ threshold, never
  drops a node's only edge, is idempotent.
- `adjacency` sorts by weight desc and is symmetric.
- `settleLayout` on the mood fixture (14 nodes, 86 edges): finite positions,
  terminates, deterministic across two runs, no two nodes within 1 px.
- `fitTransform` on a known bounding box returns the expected zoom and
  centre; a degenerate box returns `zoom: 1`.
- Coupling regression (the headline metric from §1b): settle the styles
  fixture, then move one node 200 px with `fixed` and re-settle with the
  same helper; assert the median displacement of the others is below 20 px
  **for Option B parameters**. For Option A the assertion is trivially 0 and
  lives in the component (no layout pass on drag), covered by a manual check.

Manual acceptance, on the real library:

1. Cold load of the Constellation page: first visible frame is already
   framed; no zoom change follows.
2. Drag any node 200 px: no other node moves. Release: it stays.
3. Click a node: its neighbours light up and stay lit until Esc, blank
   click, or another node; panel lists neighbours by shared plays.
4. Leave the page and return: same arrangement, no refetch.
5. Wheel over the chart scrolls the page; Fit restores framing.
6. Cold load with ECharts arriving after the API response still renders.

## 6. Parameters

Option A (frozen), used only for the off-screen settle:

- `edgeLength: [90, 260]` (heavy pairs closer than today's 150 floor is fine
  once nothing is live to overlap), `gravity: 0.08`, `friction: 0.4`,
  `repulsion: [800, 6000]` as a real two-element range, physics edges from
  `pruneEdges(perNode 3, p90)`. Tune by eye on the three fields; the numbers
  above give a settled box of ~2560×2300 px for styles, which `fitTransform`
  frames.

Option B (live), if chosen instead:

- `repulsion: [420, 3703]` (two elements), `edgeLength: [150, 240]`,
  `gravity: 0.13`, `friction: 0.25`, weak edges `ignoreForceLayout: true`,
  top-8 hubs `fixed: true` after the first settle, `layoutAnimation: true`.
  Measured coupling 16 px median vs 152 px today. Re-pin a dragged node on
  dragend by patching its `fixed: true`, accepting the re-heat.

## 7. Method for the numbers in §1

`forceHelper.step()` (spring → gravity → pairwise repulsion into `pp` →
`p += (p − pp) × friction` → `friction ×= 0.992`, finish at `< 0.01`) and the
`forceLayout.ts` mapping (`repulsion` and `edgeLength` via `linearMap` over
the value extents, edge lengths inverted so heavier = shorter, node `w = rep`)
were ported to a node script and fed the live `/api/tag-graph` output for
each field, seeded on the same ring `TagConstellation` uses at a 1100×620
canvas. "Drag" = fix one node, offset it 200 px, `friction = 0.8 × initial`
(the `warmUp` value), run to convergence, measure every other node's
displacement. Re-run the harness before changing parameters; it is the fast
way to compare settings without rebuilding the bundle.
