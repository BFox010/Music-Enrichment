# DESIGN.md

The visual brief for the Listening Atlas dashboard. Read this before changing
anything under `web/` — it is shared by every agent that works here (Claude via
`CLAUDE.md`, Codex via `AGENTS.md`), so the look stays one look instead of each
agent's house default.

**Status: direction not yet chosen.** Three candidates live in
`web/prototypes/` (see *Choosing a direction*). Once one is picked, fill in
*Tokens* below and delete the losing candidates' notes.

## What this is, visually

A personal record of one person's listening — looking back at their own taste,
not monitoring a system. It should feel like music: sleeves, names, faces, the
shape of a day. It should not feel like an admin panel.

Who reads it: the owner, on a desktop at night and on a phone via the tunnel.

## Principles

1. **Music is the content.** Album covers, artist photos and names carry the
   page. Numbers support them, not the other way round. Artwork URLs are
   already in the API caches (`.cache/deezer.json` → `album.cover_xl`,
   `artist.picture_xl`; `.cache/apple_music.json` → `artworkUrl100`, which
   upsizes by rewriting `100x100bb`).
2. **One focal point per page.** Lead with a single finding the data supports
   ("you're a 9pm listener"), not a row of equal tiles. Secondary totals go in
   a quiet strip.
3. **Spend boldness in one place.** One orchestrated motion moment per page,
   triggered by the user or by load — not ambient effects everywhere. The
   existing motion layer (`motion.js`, `ambient.js`) should be audited against
   this, not extended.
4. **Colour carries meaning.** A colour stands for a season, a mood, or the
   artwork in front of you. It is never decoration, and every chart is not the
   same accent.
5. **Each chart answers one question,** and says the answer in words next to it
   (annotate the peak instead of making the reader find it).

## Banned (name the pattern, not "don't look like AI")

- Violet/purple accent on near-black — the current default, and the most common
  model default there is.
- Space Grotesk, Inter, Manrope as the display face.
- A page that opens with N identical KPI cards.
- Tracked-out uppercase eyebrow labels on every element ("HOUR · CLICK TO
  EXPLORE"). Interactivity should be evident from affordance, not a caption.
- Middle-dot meta strings (`3,202 tracks · 16,796 scrobbles · —`).
- Every chart in one hue regardless of what it encodes.
- Other defaults to avoid: cream background + terracotta accent; near-black +
  acid green; SaaS card grids with identical radius and shadow; "01 / 02 / 03"
  labels where nothing is sequential; arrows appended to links.

## Charts

Follow the `dataviz` skill's rules (Claude has it built in; the substance for
any agent): one y-axis, never two; categorical colours assigned in a fixed
order and following the entity, never its rank; sequential = one hue light to
dark; text in text colours, never the series colour; a legend whenever there
are two or more series; a hover tooltip on every mark; recessive grid and axes.
Validate any categorical palette (season, mood) for colour-blind separation
before shipping it.

## Quality floor

- 390px phone width with no horizontal page scroll; 1440px desktop.
- Visible keyboard focus on every control.
- `prefers-reduced-motion` turns every animation off, not down.
- Body text meets WCAG AA contrast.
- Every artwork slot has a non-image fallback (initials, a colour block) and
  reserves its size so nothing shifts when images load.

## Workflow for agents

- Screenshot at 1440 and 390 after any visual change and look at it before
  calling it done (Claude: Claude in Chrome; Codex: `$playwright-interactive`).
  Browser windows, headless ones included, won't size below about 500px, so a
  "390px" window screenshot is really a crop of a wider layout. Check phone
  width through `web/prototypes/phone-check.html`, which frames pages at a true
  390px and puts each page's `scrollWidth/innerWidth` in its title.
- New visual ideas start as a standalone page in `web/prototypes/`, not as an
  edit to the live dashboard.
- `.jsx` edits need `npm run build` and a committed `app.bundle.js` (see
  `CLAUDE.md` → Commands).

## Choosing a direction

`python scripts/build_prototype_data.py`, then with the server running open
`http://127.0.0.1:8000/prototypes/`. Each candidate is the Overview page only,
on real data.

| | Liner Notes | Sleeve | Groove |
|---|---|---|---|
| Feel | Editorial, a music magazine about you | Immersive, artwork-led, the flashiest | A data instrument, the record as chart |
| Hero | One sentence about your listening, beside your top artist's portrait | Your most-played track's sleeve, blurred into the whole page; pick another and the page re-tints | A vinyl record whose grooves are years and whose angle is the hour of day |
| Type | Fraunces + Geist | Archivo (expanded display) | IBM Plex Sans Condensed + Plex Mono |
| Colour | Warm black, paper white, one vermilion | Pulled live from the artwork on screen | Black, one amber ramp |
| Risk | Quiet; flash comes from type and imagery, not effects | Depends on artwork quality; legibility over busy covers | Novel chart needs its legend to land |

Mixing is fine — "Groove's hero inside Liner Notes' typography" is a valid
answer.

## Tokens

*To fill in once a direction is chosen:* named colours (4–6) with their
roles, type roles and scale, spacing scale, radii, motion durations and
easings, and the season/mood palettes with their validation result.
