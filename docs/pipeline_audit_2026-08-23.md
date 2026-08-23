# Pipeline Audit — 2026-08-23

Audit of the enrichment pipeline and the library it produces, against the
committed data on `main` (`tracks.jsonl`, 3,336 tracks; `scrobbles.jsonl`,
16,549 scrobbles) and the committed manual documents (`taste_profile.md`,
`mood_audit.csv`).

Method: read every phase module, then re-derived each phase's stated contract
directly from the committed JSONL and re-parsed the manual documents with the
pipeline's own parsers. All 645 tests pass — none of the findings below are
covered by the suite, because the suite is unit-level and never asserts against
the real committed data.

---

## Summary

What is solid: the scrobble→track join is exact (0 of 16,549 scrobbles fail to
resolve to a track; every `play_count` equals its recomputed scrobble count),
alias resolution works end-to-end into the serving layer, and the cache/force
contract is implemented consistently across the API phases.

What is not: **the manual documents and the derived library disagree badly**.
23% of hand-curated playlist entries and 32% of blacklist entries silently match
nothing; 89% of the owner's committed mood labels are absent from the tracks
they name. None of this surfaces anywhere — no phase logs an unmatched entry.

Findings are ordered by impact on capture quality.

---

## 1. Manual documents vs. output

### 1.1 `taste_profile.md` — 310 curated entries match nothing, silently

Re-parsing the profile with `parse_taste_profile` and joining against
`tracks.jsonl`:

| | parsed | matched | unmatched |
|---|---|---|---|
| Playlist entries | 1,369 | 1,059 | **310 (22.6%)** |
| Blacklist tracks | 28 | 19 | **9 (32%)** |
| Saturation-tier artists | 32 | 32 | 0 |

The blacklist misses matter most: those are tracks the owner explicitly banned
from every curated playlist, and 9 of them carry `blacklisted: false`.

Breaking the 310 down by cause:

| cause | n | fixable? |
|---|---|---|
| artist present in library, track absent | 193 | no — genuinely not scrobbled |
| artist absent from library entirely | 60 | no |
| artist-string variant | 37 | **yes** |
| `feat` suffix present in profile, absent in library | 15 | **yes** |
| `feat` suffix present in library, absent in profile | 3 | **yes** |
| version suffix (`- Remastered`, etc.) | 2 | **yes** |

So ~253 are legitimate (the profile documents playlists built outside Last.fm),
and **~57 are real join failures**. Representative:

```
profile: "strfkr"                        library: "starfucker"
profile: "j roddy walston the business"  library: "j roddy walston and the business"
profile: "la roux gamper dadoni"         library: "la roux"
profile: "awolnation" / miracle man      library: "oliver tree" / miracle man   ← wrong artist in profile
```

Note `"j roddy walston & the business"` vs `"… and the business"`: `normalize_artist`
maps `&` to a space and leaves `and` intact, so the two forms never join. That
equivalence is missing from `pipeline/normalize.py` and affects every join in
the project, not just this one.

**The real problem is not the miss rate, it is the silence.** `apply()` logs how
many entries it *parsed* and how many tracks it *set*, never how many parsed
entries matched nothing. A typo in the profile is indistinguishable from a track
the owner has not scrobbled.

*Recommendation:* have Phase 7 write unmatched profile entries to
`taste_profile_unmatched.jsonl` (the same shape `resolve_identity` already uses
for `identity_review.jsonl`) and log the count. Add the `&`/`and` equivalence to
`normalize_artist`, and retry unmatched entries through
`name_variations.lookup_variations`, which already encodes the feat/paren/
first-artist rules Phase 4 uses.

### 1.2 Phase 7 never clears stale curation — 39 impossible records

`apply_manifest` sets `playlists`/`curation_state` only when the track is found
in the manifest:

```python
plist = manifest["playlists"].get(key)
if plist:
    track["playlists"] = list(plist["playlists"])
    track["curation_state"] = plist["curation_state"]
```

There is no `else` branch. A track removed from `taste_profile.md` keeps its old
curation forever, which contradicts invariant 2 ("markdown is truth … JSONL is
the derived index, regenerated each run"). `saturation_tier` and `blacklisted`
*are* reset unconditionally on the lines above — the inconsistency is within one
function.

On disk this has already produced 39 tracks with `curation_state: "locked"` or
`"rejected"` and `playlists: []` — a state the parser cannot emit, since a
curation state only ever comes from a playlist section.

```
Aesop Rock — Rings                   playlists=[]  curation_state=locked
alt-J — Dissolve Me                  playlists=[]  curation_state=rejected
BØRNS — 10,000 Emerald Pools         playlists=[]  curation_state=locked
```

`update_tracks._merge_with_existing` compounds it: its rule 4 ("new value wins
unless new is None/empty") means even a corrected empty list from Phase 7 would
lose to the stale existing value.

*Recommendation:* reset both fields to `[]`/`None` when the track is absent from
the manifest, and add `playlists`/`curation_state` to the merge's
overwrite-with-empty set alongside the mood fields.

### 1.3 `mood_audit.csv` — 89% of the owner's committed labels are not on the tracks

`mood_audit.csv` (377 rows) is committed as the fallback training/label source
when the gitignored `inputs/existing_audit.csv` is absent — the path a fresh
clone and CI both take. Joining it to `tracks.jsonl`:

- 95 rows (25%) match no track at all
- of the 282 that match, **only 31 (11%) have all of the owner's labels present**
- 87 matched rows have `mood_source: null` — the owner labelled them, the track
  carries no mood
- 27 were assigned by `centroid`, directly against the stated invariant that a
  centroid pass must never overwrite an owner label
- 12 were overwritten by `claude_batch`

Labels dropped, by mood:

```
Sunny 98   Happy 92   Love 47   Dark 43   Moody 39
Dance 26   Hype 26    Heavy Bass 22   Sad 10   Heartbreak 8
```

The dropped set is almost exactly the moods `mood_eval.json` withholds from the
centroid (precision < 0.45). So the committed audit is precisely the document
that would rescue the moods the classifier cannot predict — and it is not
reaching the output.

Phase 6's precedence logic is correct in isolation (audit beats centroid). The
divergence is that the production run used `inputs/existing_audit.csv`, a
different and uncommitted file, so `mood_audit.csv` was never consulted. The
consequence is that **the committed manual document and the committed output
describe different libraries**, and re-running Phase 6 from a clean clone would
relabel 114 tracks.

*Recommendation:* decide which file is canonical. Either commit the real audit
(it is the owner's own judgement — the most valuable artefact in the repo, and
currently the only copy lives on one machine), or delete `mood_audit.csv` and
have Phase 6 fail loudly rather than train on a stale 377-row subset. A test
asserting `mood_audit.csv ⊆ tracks.jsonl` would have caught the drift.

### 1.4 `claude_batch` outranks `audit` — two modules disagree

`classify_moods` checks `claude_index` before `audit_index`, so model output
overwrites the owner's own labels (12 tracks on disk). `resolve_identity`
encodes the opposite order in `_SOURCE_RANK` (`audit: 4, claude_batch: 3`), as
does CLAUDE.md ("`mood_source: "audit"` is the owner's own labelling — the
training signal the whole classifier is built on").

*Recommendation:* swap priorities 1 and 2 in `classify_moods`, making
`_SOURCE_RANK` the single definition of trust order.

### 1.5 The rich-format parser is dead code

`parse_rich_taste_profile` (~180 lines: table parsing, dot-lists, prose
segments, `_PLAYLIST_SLUG_MAP`) is gated on `_is_rich_format`, which requires
`TIER 1`/`TIER 2`/`TIER 3` *and* a markdown table. The committed profile is the
simple format and returns `False`. Its slug map targets playlists (`summer`,
`night_drive`, `heavy_weather`, `workout`) that do not exist in the current
14-mood scheme.

*Recommendation:* if the v4 format is retired, delete it — it is carrying a
taxonomy that no longer exists and will mislead the next reader.

---

## 2. Tag capture quality

### 2.1 The distributions

| field | coverage | distinct | total | top-10 share | singletons |
|---|---|---|---|---|---|
| `genres` | 78.9% | 10 | 6,016 | 100% | 0 |
| `discogs_styles` | 56.7% | 184 | 4,334 | 48% | 29% of distinct |
| `lastfm_tags` | **26.0%** | 588 | 3,890 | 37% | **60% of distinct** |
| `lastfm_artist_tags` | 19.0% | 775 | 5,994 | — | 49% of distinct |
| `itunes_genre` | 3.5% | — | — | — | — |

**Top end** — healthy and unsurprising: `rap`/`Hip-Hop`/`hip hop` (530 combined),
`pop` 168, `rock` 168, `alternative` 139, `indie` 136. Discogs is the better
signal: `Indie Rock` 334, `Alternative Rock` 304, `Pop Rap` 266, `Trap` 226 —
release-level, consistently cased, genuinely genre-bearing.

**Bottom end** — this is where the noise lives. 354 of 588 `lastfm_tags` occur
exactly once, and the tail is not long-tail genre, it is junk:

```
-1001703352985                                   Telegram channel IDs
955 WKQI                                         radio station (no frequency)
Chronically On Air with DJames and DJ Double You radio show
Fave Indie Pop, Favs Feb, :4star, 2NP           personal playlist labels
500 Days of Summer, Breaking Bad                 media titles
Bruno Mars, Britney Spears, Chris Brown          artist-as-tag
Associate s Degree, Drums please Fab             free text
```

### 2.2 `tag_filter` is not applied to `lastfm_artist_tags`

`filter_tags` is called in exactly one place — `enrich_metadata.py:124`, on
`lastfm_tags`. Phase 4d writes `lastfm_artist_tags` with no filtering at all.
Running the filter over the committed data finds **125 noise tags still on disk
in `lastfm_artist_tags`** (62 distinct) against 2 in `lastfm_tags`:

```
jay-z 19   Kanye West 7   my top songs 5   kid cudi 5   Red Hot Chili Peppers 4
Drake 2    Eminem 1       Selena Gomez 1   daniel tosh 1
```

Since `_genres_from_tags` runs over these unfiltered names, an artist-as-tag
that happens to collide with a genre key would propagate straight into `genres`.

*Recommendation:* one line in `enrich_genre_backfill` — build the artist block
once and `filter_tags(la_tags, artist_block)` before assigning.

### 2.3 The artist-block rule both over- and under-blocks

The block is built from the library's own artists, which produces two failure
modes:

- **Over-blocking:** `jungle` (3 occurrences) is a real genre, dropped because
  the band Jungle is in the library. `bronx` (3), a location tag, likewise. The
  `_PROTECTED_RAW` allowlist exists to prevent exactly this but covers only 60
  words and misses `jungle`, `garage`, `swing`, `soul` variants, `dub`, `bass`.
- **Under-blocking:** `Britney Spears`, `Demi Lovato`, `Selena Gomez` survive
  because those artists are not in this library. The rule's coverage is a
  function of library composition, not of what a tag is.

*Recommendation:* the protected list should be derived from `GENRE_TAG_MAP`'s
keys plus the 14 mood names rather than hand-maintained — they are already the
authoritative vocabulary of "words that are genres". That closes the
over-blocking side automatically as the map grows.

### 2.4 31% of tag occurrences never map to a genre

`_genres_from_tags` does an exact lowercase dict lookup. Across all three tag
fields, 4,408 occurrences are unmapped vs 9,810 mapped. Many of the unmapped are
plainly genre-bearing:

```
underground hip-hop 119   east coast hip hop 41   alternative hip-hop 36
Dirty South 35            phonk 34                math rock 31
memphis rap 29            Horrorcore 28           southern rap 27
Neo-Psychedelia 35        trip-hop 18             Industrial 17
```

Two distinct defects:

1. **Separator sensitivity.** The map has `trip hop` but not `trip-hop`,
   `hardcore hip hop` but not `hardcore hip-hop` in every position,
   `southern hip hop` but not `southern hip-hop`. Normalising the key to
   `[^a-z0-9]+ → space` on both sides recovers **82 occurrences for free**, with
   no new entries.
2. **Genuine vocabulary gaps** — the regional hip-hop family
   (`east coast`/`memphis`/`southern`/`underground`) is absent entirely despite
   being the library's densest genre.

Adding separator-insensitive lookup plus ~20 obvious entries gives **110 tracks
at least one additional genre**. Modest, but it is free and it improves the
dashboard's genre facet where the library is thickest.

Also worth noting: 9 map keys never appear in the data at all (`bebop`,
`motown`, `psytrance`, `thrash metal`, `smooth jazz`, …) — harmless, but a
reminder the map was written from imagination rather than from the tag census.

### 2.5 703 tracks have no genre — and 370 could be fixed with zero API calls

21% of the library has `genres: []`. Of those 703, **695 have no tag data at
all** — empty `lastfm_tags` *and* empty `lastfm_artist_tags`. These are not
obscure tracks:

```
19 plays  Men I Trust — Tailwhip
18        Rainbow Kitten Surprise — Never Have I Ever
 7        A$AP Rocky — A$AP Forever (feat. Moby, T.I. & Kid Cudi)
 7        Kanye West — FML (feat. The Weeknd)
 7        Drake — With You (feat. PARTYNEXTDOOR)
 7        Flume — The Difference (feat. Toro y Moi)
```

Last.fm unquestionably has artist tags for Kanye West and Drake — and this
library already holds them, on other rows:

- **204** of the 703 have an artist that already carries `lastfm_artist_tags` on
  a different row; 196 of those tag sets map to a genre.
- **370** (53%) have an artist that already carries non-empty `genres` on a
  different row.

Phase 4d fetches per *track* (HTTP-cached per artist, so no repeat network
cost), but it never consults the in-memory result already computed for a sibling
row, and it never propagates a genre an artist already has elsewhere in the
library. Whatever caused the original call to come back empty — a run
interrupted before these rows, a transient error cached as a negative, an
`autocorrect` miss on a collab credit — the row stays empty permanently even
though the answer is sitting in the same file.

*Recommendation:* after Phase 4d's fetch loop, add an artist-level propagation
pass: for any track still empty, adopt the genres of the highest-coverage row by
the same `artist_normalized`. Zero API calls, recovers ~370 tracks, and is
strictly safe — it can only fill blanks. Record it as
`enrichment_sources += ["artist_propagation"]` so it stays distinguishable from
a direct fetch.

### 2.6 Phase 4d writes no provenance

The enrichment-phase contract requires per-field `source`, `retrieved_at`,
`pipeline_phase`, `confidence`. Phase 4d records none: `enrichment_sources` on
the committed data contains `itunes_search`, `musicbrainz`, `exportify`,
`discogs`, `mood_classifier`, `lastfm_tags`, `itunes_xml` — no artist-backfill
marker. There is no way to distinguish "4d ran and Last.fm had nothing" from
"4d never ran on this row", which is exactly the ambiguity blocking the
diagnosis in 2.5.

`musicbrainz_genres` has the same problem in reverse: Phase 4d writes it, but it
is absent from `FIELD_DEFAULTS`, so it survives on only 20 rows and is dropped
elsewhere by the schema write order.

---

## 3. Identity resolution

### 3.1 The MBID veto rejects the exact case the module was written for

`is_credit_variant` vetoes a merge when both rows carry a MusicBrainz ID and
they differ. Last.fm routinely returns *different recording MBIDs for the same
recording* under different credit strings, so the veto fires on true positives.
Nine credit-variant pairs remain unmerged on disk, every one of them vetoed by
`musicbrainz_id` — including the module docstring's own headline example:

```
Clipse (2 plays)          | Clipse, Pharrell Williams, Pusha T & Malice (22)  — So Far Ahead
JPEGMAFIA (1)             | JPEGMAFIA & Danny Brown (21)                      — Kingdom Hearts Key
Big Data (1)              | Big Data & Joywave (11)                           — Dangerous
Bob Seger (1)             | Bob Seger & The Silver Bullet Band (6)            — Night Moves
Justice (1)               | Justice & Tame Impala (2)                         — Neverender
```

In every case the split is lopsided (1–2 plays against 6–22), which is the
signature of a scrobbling-client credit change, not two different recordings.

*Recommendation:* keep the ISRC veto (ISRCs genuinely identify recordings) and
demote the MBID veto to a tiebreak — or ignore it when one side's play count is
below a small threshold and the titles are identical. Route the demoted pairs
into the existing `identity_review.jsonl` rather than merging blind.

### 3.2 Feature-in-title variants are never even compared

`is_credit_variant` requires identical `track_normalized`, so the largest
duplicate class in the library is invisible to it: the feature credited in the
*title* rather than the artist.

**86 clusters, 90 excess rows:**

```
Highjack (1)                          | Highjack (feat. Jessica Pratt) (46)
A$AP Forever (feat. Moby) (4)         | A$AP Forever (feat. Moby, T.I. & Kid Cudi) (7)
Setting Sun (6) | Setting Sun (feat. Noel Gallagher) (1) | … [Radio Edit] (1)
Jukebox Joints (… Joe Fox & Kanye West) (1) | (… Joe Fox x Kanye West) (8)
```

Consequences: play counts split (so the dashboard's top-tracks ranking is
wrong), each half enriched separately at full API cost, and the halves often
disagree — one gets moods, the other does not.

`normalize.py` already collapses `feat.`/`ft.`/`featuring` to the token `feat`
but never truncates at it. `name_variations.lookup_variations` has a
`strip_parens` rule that does exactly the needed truncation and is used by
Phase 4 for lookups only.

*Recommendation:* compare on a feat-stripped title inside `is_credit_variant`
(keeping the full title for display), reusing `name_variations`' rule so there
is one definition of "the same title with a guest credit". Merge only when the
stripped titles match *and* the artist test already passes, which keeps the
conservative bias.

---

## 4. Robustness

### 4.1 Phase 4 can null out MBIDs it did not fetch (latent)

`_extract_lastfm_fields` always returns all three keys, `None`-filled on error
or no-match, and the caller applies them unconditionally:

```python
track.update(fields)   # enrich_metadata.py, in the main loop
```

Phase 2 (`dedupe`) already sets `musicbrainz_id` and `artist_mbid` from the
Last.fm export's own `mbid` field — 2,375 track pairs and 2,578 artist pairs in
`scrobbles.jsonl` carry one. So on any run where Last.fm errors or fails to
match, Phase 4 discards a free, authoritative identifier it never fetched. Since
`canonical_track_id` is `mbid:<x>` for 2,497 of 3,336 tracks, that also churns
canonical IDs between runs.

Currently no loss is observable (0 of 2,375 lost) — Phase 4 happened to match
every row that had one, unsurprising since both values come from Last.fm. It is
latent, and it bites on the first transient-error run.

*Recommendation:* merge rather than assign — only overwrite when the new value
is non-null, matching the discipline `update_tracks._merge_with_existing`
already applies at Phase 8.

### 4.2 `_is_actionable` conflates "no tags" with "no match"

`_lookup_with_variations` stops retrying name variations as soon as a response
yields an MBID *or* a tag. A track that returns an MBID but zero tags is
"actionable", so the feat-stripped and first-artist variations — which might
have returned tags — are never tried. Given that `lastfm_tags` coverage is 26%
against 78% MBID coverage, this is plausibly a meaningful share of the tag gap.

*Recommendation:* keep trying variations while `lastfm_tags` is empty, even
after an MBID lands; take the union across variations rather than first-hit.

### 4.3 BOM in `taste_profile.md`

The file begins with `﻿`. `read_text(encoding="utf-8")` preserves it, so
line 1 reaches `_HEADER_RE` as `﻿# Taste Profile — FoxXg`. Harmless today
(the H1 is not a section marker) but it would silently swallow a section if the
document is ever reordered. Read with `encoding="utf-8-sig"`.

### 4.4 Tests pass but assert nothing about the real data

645 tests, all green, none of which would catch anything in sections 1–3. Every
finding here came from joining committed artefacts against each other. Three
cheap data-integrity tests would have caught most of it:

- every `mood_audit.csv` row resolves to a track, and its labels are present
- every `taste_profile.md` blacklist entry resolves to a track
- no track has `curation_state` set with `playlists == []`

These run offline against committed files, so they fit the suite's
self-contained constraint.

---

## Priority

| # | Finding | Impact | Effort |
|---|---|---|---|
| 1 | Owner mood labels absent from output (1.3) | destroys the highest-value manual input | decide canonical file; add integrity test |
| 2 | Unmatched profile entries are silent (1.1) | 9 blacklisted tracks unflagged; no feedback loop | log + review file + `&`/`and` |
| 3 | Artist genres not propagated (2.5) | ~370 tracks recover a genre, zero API calls | one pass in 4d |
| 4 | Feat-in-title duplicates (3.2) | 90 rows, split play counts, wrong rankings | reuse `name_variations` |
| 5 | Phase 7 stale curation (1.2) | 39 impossible records | add the `else` branch |
| 6 | `lastfm_artist_tags` unfiltered (2.2) | 125 noise tags on disk | one call |
| 7 | MBID veto too strict (3.1) | 9 unmerged pairs | demote to tiebreak |
| 8 | `claude_batch` outranks `audit` (1.4) | 12 owner labels overwritten | swap two blocks |
| 9 | Genre map separator-sensitive (2.4) | 110 tracks gain a genre | normalise keys |
| 10 | Phase 4 nulls MBIDs (4.1) | latent, bites on transient error | merge not assign |
