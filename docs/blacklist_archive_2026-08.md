# Blacklist archive — 2026-08

Archived from `taste_profile.md`'s `## Blacklist` section when issue #63
removed the `blacklisted` field and its parser. Preserved for provenance only
— **this file is not read by any pipeline phase.**

## Why this was removed

`blacklisted` was a leftover from an earlier playlist-generator iteration of
this project: it marked tracks so oversaturated they'd flood any generated
playlist, so the generator could suppress them. There is no generator, and
nothing downstream ever consumed the field — it was computed by Phase 7 on
every run, validated by `pipeline/schema.py`, and included in the full
`/api/tracks` response, but deliberately excluded from `MIN_TRACK_FIELDS`
(`app/query.py`) so the dashboard never rendered it. No `.jsx` read it, no
filter used it, no aggregation counted it.

It was tempting to repurpose rather than delete — of the 28 entries below, 20
matched a track in the current library, and 19 of those 20 sit in the top 35
most-played, which reads as a real fatigue signal `play_count` alone can't
express. Rejected because there is no maintenance path: this list was curated
once, under the playlist-generator goal, and nothing has added to it since. A
field describing past listening fatigue forever is worse than no field — it
looks authoritative and silently goes stale. A fatigue facet built with a
real upkeep mechanism (the ongoing tagging chore in #62 is the natural host)
is a fair thing to build later; this static list inherited from a different
product goal is not it.

## The list

Top oversaturated tracks, in the order they appeared in `taste_profile.md`:

- "Grown Up" by Danny Brown
- "Goldie" by A$AP Rocky
- "Severed Head" by Gorillaz
- "MELTDOWN" by Travis Scott
- "Lost and Found Freestyle 2019" by A$AP Rocky
- "Sundress" by A$AP Rocky
- "Feel The Fiyaaaah" by Metro Boomin & A$AP Rocky
- "LPFJ2" by A$AP Rocky
- "In My Blood" by Freddie Dredd
- "Matte Black" by $uicideboy$
- "Good Luck" by Broken Bells
- "Image" by Magdalena Bay
- "You Broke My Heart" by Drake
- "Strangers" by Danger Mouse & Black Thought
- "Machu Picchu" by The Strokes
- "Functional Addict" by Pharrell Williams, Gunna & Nigo
- "#RICHAXXHAITIAN" by Mach-Hommy
- "Highjack" by A$AP Rocky
- "Why Won't They Talk to Me?" by Tame Impala
- "Rich N***a Problems" by A$AP Rocky
- "No Static" by Nappy Roots
- "STATS" by Baby Keem
- "So Good at Being in Trouble" by Unknown Mortal Orchestra
- "Sofia" by Clairo
- "Slide" by Calvin Harris
- "Disappear" by Dehd
- "Like Acid Rain" by Unknown Mortal Orchestra
- "Basement Jack" by Steve Lacy
