# Taste Profile — Template

Copy this file to `taste_profile.md` and fill it in. The pipeline reads it
on every run to derive `saturation_tier`, `blacklisted`, `playlists`, and
`curation_state` for each track.

The parser tolerates several formats — if you'd rather use your own layout,
update `pipeline/apply_taste_profile.py` to match.

## Saturation Tiers

Defines how often an artist should appear in playlists. Lower tier = more
saturated (you've heard them a lot, use sparingly).

### Tier 1 — heavy rotation
- Tame Impala
- Kanye West

### Tier 2 — moderate use
- Gorillaz
- A$AP Rocky

### Tier 3 — limited / special-context only
- Frank Ocean

## Blacklist

Never include these in any curated playlist. Whole artists go on their own
line; specific tracks use the format `"Track" by Artist` or `Track — Artist`.

- Ed Sheeran
- "Wonderwall" by Oasis
- Hey Soul Sister — Train

## Playlists

Each subsection defines a playlist slug and its curation state in
parentheses. State values: `locked`, `approved`, `rejected`. Bullet items
are tracks in the playlist.

### soak (locked)
- "Roads" by Portishead
- "Glory Box" by Portishead

### night_drive (approved)
- "Crystalised" by The xx
- "Reptilia" by The Strokes

### summer (approved)
- "Cherry-coloured Funk" by Cocteau Twins
