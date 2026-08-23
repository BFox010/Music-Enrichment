# iTunes / Apple Music XML — field reference

What `inputs/apple_music_library.xml` (the iTunes "Export Library" XML) actually
carries, and how Phase A (`pipeline/enrich_apple_library.py`) maps it.

Salvaged from `SAVE_LOG.md` before that file was retired — this is the one part
of it that stayed true.

## Per-track keys worth reading

| XML key | Maps to | Notes |
|---|---|---|
| `Total Time` | `duration_ms` | milliseconds |
| `Year` / `Release Date` | `release_year` | |
| `Explicit` | `explicit` | boolean; **present only when true** |
| `Genre` | `itunes_genre` | supplements Last.fm tags, doesn't replace them |
| `Persistent ID` | `itunes_persistent_id` | see the trap below |
| `Play Count` | `itunes_play_count` | secondary cross-reference — Last.fm is source of truth |
| `Skip Count` | `itunes_skip_count` | |
| `Date Added` | `itunes_date_added` | when it entered the local library |
| `Kind` | `itunes_kind` | provenance of the file, see below |

`Kind` values distinguish how the track was obtained:
`Apple Music AAC audio file` = streaming · `Purchased AAC audio file` = bought ·
`MPEG audio file` = local rip.

## The Persistent ID trap

**`Persistent ID` is not `apple_music_id`.**

- `Persistent ID` is a UUID local to *this* iTunes library. It identifies a row
  in the user's own database and means nothing to any Apple service.
- `apple_music_id` comes from the iTunes Search API in Phase 5 and identifies a
  track in Apple's catalogue.

They are stored in separate fields and must never be substituted for one another.

## Why the match rate is low

Phase A matches only a small fraction of the library, and that is expected
rather than a bug: the local iTunes library holds a few hundred tracks (local
files and purchases), while Last.fm has scrobbled everything ever streamed. The
overlap is genuinely small.

Compute the current rate rather than quoting one — it moves whenever either side
grows. Issue #42 proposes matching on `apple_music_id` to improve it.

## Naming note

The original spec called for `apple_music_library.csv`. The actual iTunes export
is XML, and `pipeline/config.py` points at `apple_music_library.xml`.

## Playlists

The XML carries a playlists section after the tracks. Phase A does not read it.
