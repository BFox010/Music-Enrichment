# TuneMyMusic Batch Playbook

How to prep a track list for TuneMyMusic → Exportify so the match rate is high.
Built from real results: a naive 2,730-track upload produced heavy duplicates and
wrong matches; the refined process below took a 144-track retry to **140/144 (97%)**.

## The pipeline that works

1. **Start from `tracks.jsonl`**, not raw scrobbles. Only export tracks that are
   actually missing a `spotify_id` — don't re-send tracks already matched.

2. **Clean every artist and title** with `pipeline/normalize.py`:
   - `clean_artist_for_search()` — primary artist only, drop `& X` / `feat. X` collaborators.
     TuneMyMusic searches better on one artist (`Drake & 21 Savage` → `Drake`).
   - `clean_track_for_search()` — strip `(feat. …)`, `(Remastered)`, `(Radio Edit)`,
     `(Live …)`, `(Extended …)`, `(Bonus Track)`. **Keep remix identifiers** —
     a remix is a distinct track, not noise.

3. **Fix the comma-in-artist CSV bug.** TuneMyMusic's CSV parser splits on every
   comma regardless of quoting. Any artist with a comma corrupts the row.
   Known offender: `Tyler, The Creator` → write as `Tyler the Creator` (no comma).
   Spotify's fuzzy search still finds them.

4. **Deduplicate case-insensitively** on `(artist, track)`. Spotify/Exportify return
   the same track under multiple playlist appearances and casings.

5. **Drop the album column.** Including the album name caused more wrong-version
   matches than it fixed. Artist + title alone is cleaner.

6. **Owner review pass** (for large/important batches): generate a colour-coded
   XLSX (`green`=already matched, `yellow`=cleaning changed something, `red`=likely
   to fail) with three editable columns:
   - **Keep / Remove** — drop tracks not worth searching (mixtapes not on Spotify, etc.)
   - **Remix?** — flag intentional remixes so cleaning doesn't strip the identifier
   - **Comment** — free-text artist/title overrides ("should be Big Pun not Big Punisher")

7. **Apply comment overrides** before generating the final CSV. Real corrections that
   mattered: `Big Punisher`→`Big Pun`, `Starfucker`→`STRFKR`, romanize Japanese
   artist names, `J A Y E L E C T R O N I C A`→`Jay Electronica`.

## Residual failure patterns (the hard ~3%)

These four failed even after cleaning — they define the ceiling of automated matching:

| Track | Pattern | Why it fails |
|---|---|---|
| A$AP Rocky — **M'$** | Heavy special chars (`$`, `'`) | Search can't tokenize a 3-char symbolic title |
| Clipse — **EBITDA** | Acronym formatting | Spotify lists it as `E.B.I.T.D.A.` with periods; stripping them missed |
| Nappy Roots — **Me** | Ultra-generic / too short | One-word common-word title returns thousands of wrong hits |
| Tame Impala — **Led Zeppelin** | Title collides with a famous artist name | Search resolves to the *band* Led Zeppelin, not the song |

**Handling for these:** don't auto-clean. Either (a) leave as-is and accept the miss,
(b) search by Spotify track URI directly if known, or (c) for collision/acronym cases,
try the *exact* canonical Spotify spelling rather than a normalized form.

## Rule of thumb

- Clean aggressively for feat./noise → big win, safe.
- Never strip remix identifiers.
- Never trust quoted commas in the CSV.
- Accept that symbolic, ultra-short, and artist-name-collision titles are manual-only.
