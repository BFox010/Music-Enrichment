# Taste Profile — Template

Copy this file to `taste_profile.md` and fill it in.

**This file is truth; the JSONL is derived.** Phase 7
(`pipeline/apply_taste_profile.py`) reads it on every run to set
`saturation_tier`, `blacklisted`, `playlists` and `curation_state` on each
track. Nothing in the pipeline ever writes back to it — edit it by hand.

Those four fields are **dashboard metadata**, not instructions to a playlist
generator. They drive the Coverage view's saturation breakdown and let you mark
tracks so the dashboard can group or exclude them. `playlists` is a grouping
label read out of the sections below — no playlist is generated or pushed
anywhere.

The parser tolerates several formats — if you'd rather use your own layout,
update `pipeline/apply_taste_profile.py` to match.

## Saturation Tiers

How heavily you've listened to an artist. Lower tier = more saturated. Surfaces
as the saturation breakdown on the dashboard's Coverage view.

### Tier 1 — heavy rotation
- Tame Impala
- Kanye West

### Tier 2 — moderate use
- Gorillaz
- A$AP Rocky

### Tier 3 — limited / special-context only
- Frank Ocean

## Blacklist

Sets `blacklisted: true` so the dashboard can exclude these. Whole artists go on
their own line; specific tracks use `"Track" by Artist` or `Track — Artist`.

- Ed Sheeran
- "Wonderwall" by Oasis
- Hey Soul Sister — Train

## Groupings

Each subsection is a grouping slug plus its curation state in parentheses —
`locked`, `approved`, or `rejected`. Bullet items are the tracks in it. These
land in the track's `playlists` list and `curation_state`; the heading name is
historical.

### soak (locked)
- "Roads" by Portishead
- "Glory Box" by Portishead

### night_drive (approved)
- "Crystalised" by The xx
- "Reptilia" by The Strokes

### summer (approved)
- "Cherry-coloured Funk" by Cocteau Twins
