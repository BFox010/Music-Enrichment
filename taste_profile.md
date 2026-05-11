# FoxXg Taste Profile — AI Reference
<!-- v4.0 | 2026-05-05 | Source: Last.fm Feb 2010–Mar 2026, 13,136 scrobbles, 912 artists. Apple Music primary platform. Library audit: 1,332 tracks across 14 mood categories with Spotify audio metadata. -->
<!-- WEIGHT: 2025–2026 scrobbles + Apr 2026 library audit. Conversation feedback > play counts > metadata. Audio feature scores are RELATIVE within this library, not absolute (energy values run systematically low). -->
<!-- COMPANION FILE: foxXg-scrobble-reference.md — album affinities, era context, Tier 2 recency signal -->

---

## MASTER RULE
**Default = discovery.** Known artists/tracks below are **sonic fingerprints for matching, not recommendation candidates.** "Make me a moody playlist" = find what lives in that sonic space, not put Frank Ocean on it.
Include saturated artists/tracks only if: (a) listener explicitly asks for comfort/favorites, (b) genuine deep cut not in scrobble data, or (c) perfect for a specific transition with no equivalent alternative.
**Platform:** Apple Music only. Verify availability before suggesting. Spotify-only tracks cause friction.

---

## ECHO CHAMBER GUARD
On any playlist of 8+ tracks, include **one deliberate stretch** — a track that shares production DNA but challenges genre expectations (e.g., a jazz-inflected hip-hop cut in a psych-rock playlist, a post-punk track in a hip-hop mood set). Flag it as `[exploratory]`. Skip only if playlist intent is Comfort or strict Genre/Vibe lock.

---

## SATURATION TIERS

**🔴 TIER 1 — DO NOT INCLUDE** (unless deep cut / comfort request / irreplaceable fit)
| Artist | Plays | Recency |
|---|---|---|
| Tame Impala | 747 | Active — 347 plays 2024+ |
| Kanye West | 654 | Fading — only 97 plays 2024+ |
| Gorillaz | 632 | Active — 279 plays 2024+ |
| A$AP Rocky | 511 | Active — 279 plays 2024+ |
| Kendrick Lamar | 495 | Active — 273 plays 2024+ |
| Frank Ocean | 296 | Fading — 63 plays 2024+ |
| Magdalena Bay | 226 | Most active — 207 plays 2024+ |
| Tyler, The Creator | 220 | Fading — 63 plays 2024+ |
| Unknown Mortal Orchestra | 213 | Moderate — 74 plays 2024+ |
| The Strokes | 207 | Active — 102 plays 2024+ |

**Fading Tier 1 artists** (Kanye, Frank Ocean, Tyler): still loved, not active rotation — deeper cuts have more room than active artists.

**🟡 TIER 2 — 1 per playlist max, must earn its spot**
Tyler, The Creator (220) · UMO (213) · The Strokes (207) · Broken Bells (158) · Mac Miller (157) · Travis Scott (133) · Baby Keem (126) · $uicideboy$ (116) · Danny Brown (111) · Magdalena Bay (226) · Drake (37 but deep emotional impact)

**🟢 TIER 3 — Fair game** (verify specific track saturation first)
Kings of Leon · Clairo · Dehd · Beck · alt-J · Glass Animals · Everything Everything · Cage the Elephant · Vince Staples · Lil Yachty · DGD · ichika Nito · Steve Lacy · Oliver Tree · Twenty One Pilots

---

## TRACK BLACKLIST — NEVER RECOMMEND
| Plays | Track |
|---|---|
| 105 | Danny Brown – "Grown Up" |
| 69 | A$AP Rocky – "Goldie" |
| 63 | Gorillaz – "Severed Head" |
| 58 | Travis Scott – "MELTDOWN" |
| 55 | A$AP Rocky – "Lost and Found Freestyle 2019" |
| 54 | A$AP Rocky – "Sundress" |
| 52 | Metro Boomin & A$AP Rocky – "Feel The Fiyaaaah" |
| 51 | A$AP Rocky – "LPFJ2" |
| 49 | Freddie Dredd – "In My Blood" |
| 48 | $uicideboy$ – "Matte Black" |
| 47 | Broken Bells – "Good Luck" |
| 46 | Magdalena Bay – "Image" |
| 45 | Drake – "You Broke My Heart" |
| 44 | Danger Mouse & Black Thought – "Strangers" |
| 43 | The Strokes – "Machu Picchu" |
| 42 | Pharrell/Gunna/Nigo – "Functional Addict" / Mach-Hommy – "#RICHAXXHAITIAN" / A$AP Rocky – "Highjack" |
| 41 | UMO – "So Good at Being in Trouble" |
| 40 | Baby Keem – "STATS" |
| 39 | Tame Impala – "Why Won't They Talk to Me?" / A$AP Rocky – "Rich N***a Problems" / Nappy Roots – "No Static" |
| 37 | Clairo – "Sofia" / Calvin Harris – "Slide" / Dehd – "Disappear" / UMO – "Like Acid Rain" / Steve Lacy – "Basement Jack" |

---

## MOOD → SOUND MATRIX
*(Quantitative anchors are within-library averages — relative signal only. Energy values run systematically low across the dataset; treat the rankings, not the raw numbers.)*

| Context | Sound Profile | Audio Anchors | Key Fingerprint Artists | Avoid |
|---|---|---|---|---|
| **Late night** (10pm–3am) | Reverb-heavy, introspective, slow-mid tempo, headphones-in-the-dark | Slow tempo (~102 BPM), mid-low valence (~0.46), high cross-membership with Moody | Frank Ocean, UMO, Tame Impala, Beach House, Men I Trust | High-energy bangers |
| **Daytime** (10am–5pm) | Hard-hitting, confident, bass-heavy, swagger | Heavy Bass cluster: highest danceability (~0.74), tempo ~119, valence neutral (~0.46) | A$AP Rocky, Kendrick, Danny Brown, $uicideboy$, Three 6 Mafia | Mellow/ambient |
| **Morning** | Gentle, melodic, warm, low-to-mid energy | Slow + low-tempo (<100 BPM), Uplifting overlap (valence ~0.55) | ichika Nito, The Marías, Kid Cudi, DGD (comfort) | Jarring or aggressive |
| **Night drive** | Cinematic, forward-motion, atmosphere + momentum — NOT static | Fast + Moody pairing is the largest in the library (266 tracks) — tempo 126+, mid valence | Chromatics, Interpol, dark atmospheric hip-hop, Portishead | Static ambient, tension-holding, weak MCs |
| **Sulk / heavy weather** | Unmistakably sad, lush production, shuffle-friendly (every track must work as entry point) | Sad cluster: lowest valence in library (~0.30); Sad+Slow+Moody triplet = core sulk DNA | Soccer Mommy, James Blake, Cigarettes After Sex, Portishead, Deerhunter | Sweet ≠ sad; interesting ≠ sad; head-nodders; discovery picks that break mood |
| **Hype / workout** | Max intensity, aggressive production, build to peak, no cooldown | Hype profile is unusual: high tempo (~132), high valence (~0.62), but **low danceability (~0.50)** — confidence/aggression, not body music | Death Grips, Machine Girl, $uicideboy$, DGD, Turnstile | Cooldown songs; danceable trap (wrong texture) |
| **Cruise / weekend** | Sunny, groovy, warm indie + funk-adjacent hip-hop | Groove cluster: highest danceability (~0.76), mid valence — body music with restraint | Gorillaz, Broken Bells, Dehd, Nappy Roots, Khruangbin | Saccharine pop |
| **Love** | Full emotional spectrum — devotion, longing, vulnerability, sweetness | Love cluster spans the widest valence range; danceability skews high (~0.63); tempo ~124 | Blood Orange, The Marías, Mk.gee, Sampha, Thee Sacred Souls | Smooth R&B |

**Mood permeability ranking** (which moods are most likely to share tracks with others — strongest pairing partners listed first):
- **Moody** (100% of tracks share another category): pairs heaviest with Fast, Slow, Sad, Heartbreak — Moody is a texture, not a destination
- **Sad / Groove** (100% multi-category): always co-classified; never standalone signals
- **Slow / Heavy Bass / Dance / Hype / Happy** (97–99% multi-category): nearly always require a partner mood for context
- **Sunny / Uplifting / Heartbreak / Love** (92–95%): more likely to anchor a playlist on their own

The five most common cross-pairings — these are the actual sonic sweet spots, not isolated moods:
1. **Fast + Moody** (266) — driving texture; the night drive and post-hardcore lane
2. **Moody + Slow** (203) — late night and sulk-adjacent introspection
3. **Fast + Slow** (183) — fast tempo / slow emotional weight (post-hardcore, dark trap)
4. **Moody + Sad** (175) — heavy weather core
5. **Dance + Groove** (165) — the body-music sweet spot

---

## EXISTING PLAYLIST DNA (avoid re-suggesting locked tracks)

**Summer:** Indie rock → psych-pop → hip-hop → pop-funk → nostalgic edge. Locked: Dehd "Disappear," Pond "Tasmania," Wet Leg "Chaise Longue," Nappy Roots "Awnaw," Remi Wolf "Photo ID," Khruangbin "Maria También," Steve Lacy "N Side," Vince Staples "Yeah Right," Melody's Echo Chamber "I Follow You," Parcels "Withorwithout."

**Night Drive:** Cinematic momentum. Locked: Chromatics "Shadow," Blood Orange "You're Not Good Enough," Tourist "Run," Phantogram "When I'm Small." Rejected: Night Lovell (weak MC), Khruangbin "A Hymn" (too static), Com Truise "Flightwave," SOHN "Ransom Notes" (holds tension).

**Heavy Weather (sulk):** Shuffle-friendly sadness. Locked: Men I Trust "Tailwhip," Cigarettes After Sex "Apocalypse," The Neighbourhood "Cherry Flavoured," Soccer Mommy "circle the drain," Deerhunter "Helicopter," Title Fight "Numb, But I Still Feel It," Beach House "Used to Be," Syd "Shake Em Off," Kali Uchis "Dead to Me," James Blake "The Wilhelm Scream," Bon Iver "Holocene," Portishead "Roads," Perfume Genius "Hood," Phoebe Bridgers "Funeral," The National "Sorrow," Mazzy Star "Fade Into You," Tame Impala "List of People." Rejected: Lana Del Rey "Video Games" (sweet), Slowdive "When the Sun Hits" (head-nodder), alt-J "Dissolve Me" (upbeat), Fleet Foxes "Mykonos" (too iconic, disrupts cohesion), Grouper "Vapor Trail" (experimental not sad), Daughter "Youth" (personal dislike).

**Workout (LIFT):** Spine: A$AP Rocky "Goldie" (opener) · Yeah Yeah Yeahs "Heads Will Roll" · The Chemical Brothers "Setting Sun" · $uicideboy$ "Us Vs. Them" · Freddie Dredd "In My Blood" · Danny Brown "Grown Up" · Machine Girl "Ghost" · Bring Me the Horizon "Shadow Moses" · Travis Scott "MELTDOWN" · Metro Boomin & A$AP Rocky "Feel The Fiyaaaah" · Dance Gavin Dance "One in a Million." Discovery picks accepted: Turnstile "BLACKOUT," Death Grips "Guillotine," Denzel Curry "SUMO," Ho99o9 "Street Power," King Gizzard & The Lizard Wizard "Rattlesnake," Clipping. "Work Work," Injury Reserve "Jailbreak the Tesla."

**Love (💕):** Known: The Strokes "Selfless," Tyler, The Creator "I THINK," Yeah Yeah Yeahs "Maps," Lana Del Rey "Margaret," Beck "Girl," Frank Ocean "Ivy," Broken Bells "Love On the Run," Cam'ron "Hey Ma," Magdalena Bay "Vampire in the Corner," The Marías "Calling U Back," Blood Orange "YNGNG," Mk.gee "You (Can Count On Me)," Sampha "Cherish," Ravyn Lenae "Skin."

---

## KEY FEEDBACK SIGNALS

### Accept reasons (what makes picks land)
- Deerhunter "Helicopter" — 9-min psych-rock grief. Long form earned by emotional payoff.
- Chromatics "Shadow" — synth-noir fills a gap nothing else covered (night drive).
- Perfume Genius "Hood" — surprise "wow" reaction. Unexpected but emotionally precise.
- Death Grips "Guillotine" / King Gizzard "Rattlesnake" / Injury Reserve — earned workout spots through raw energy, not novelty.
- Blood Orange — accepted across multiple playlists; textural melodic vocals, groove + momentum.

### Reject patterns (rules extracted from failures)
| Rule | Evidence |
|---|---|
| Weak MC = dealbreaker, even great beat | Night Lovell rejected despite strong production |
| Vibes ≠ playlist fit; must match specific energy | Khruangbin "A Hymn" great but too static for night drive |
| Static songs fail driving playlists | SOHN "Ransom Notes" held tension; Com Truise didn't fit |
| Sweet ≠ sad | Lana "Video Games" cut — romantic, not grief (confirmed: classified Love/Slow/Moody, not Heartbreak/Sad) |
| Interesting ≠ sad | Grouper "Vapor Trail" — experimental, provoked curiosity not feeling |
| Head-nodders fail sulk playlist | Slowdive "When the Sun Hits" — great but not sad |
| PHC picks need personal nostalgia | SWS "Roger Rabbit" fell flat without attachment |
| Discovery picks break mood playlists | Lil Yachty "the BLACK seminole" — felt like "check this out" not sadness |
| Iconic songs disrupt cohesion | Fleet Foxes "Mykonos" sticks out even when sonically right |
| Availability: Apple Music only | Corbin "Leaving" rejected — not on platform |
| **Bright production over dark content = Dark/Moody, not Happy** | Foster The People "Pumped Up Kicks" reclassified Dark/Moody despite bright melody (lyrics narrate a school shooter). Pattern generalizes — see Production Rules. |
| **Tempo/danceability surface ≠ emotional content** | $uicideboy$ "...And to Those I Love, Thanks for Sticking Around" reclassified Moody/Dark/Sad despite dance-tempo signal — emotional content overrides genre cues |
| **Title-surface matching is a known failure mode** | MyGrain "Veil Of Sun" pulled out of Sunny — title implied warmth but the metalcore production and content land Dark/Fast |
| **High-energy queer love anthems land in Love** | Tegan and Sara "Closer" classified Love/Dance/Fast — emotional sincerity, not just slow-tempo devotionals, qualifies for Love |

---

## DISCOVERY VECTORS (confidence-ranked, updated against library audit)

Confidence is now anchored to two signals: (1) prior conversation feedback, (2) genre breadth in the library audit — i.e. how many of the 14 mood categories the genre touches. A genre that lives in 10+ moods is densely woven into taste; one that lives in 1–2 is situational at best.

**HIGHEST:** Neo-psych / shoegaze — Spiritualized, Kikagaku Moyo, Levitation Room, Dungen, Wooden Shjips, Melody's Echo Chamber (confirmed), Pond (confirmed), Deerhunter (confirmed). Indie rock and alternative rock are the two largest single-genre buckets in the library (185 + 83 tracks, both touching 13–14 categories).

**HIGHEST:** Contemporary R&B / textural soul — Ravyn Lenae, Mk.gee, Sampha (all confirmed), Durand Jones, Surprise Chef. Contemporary R&B is one of the broadest genres in the library (88 tracks across 14 categories) — confirmed core taste, not just mood-specific.

**HIGHEST:** Conscious / abstract / experimental hip-hop — billy woods, Armand Hammer, Mavi, Navy Blue, Quelle Chris (Death Grips/Clipping. confirmed adjacent). Conscious rap (78 tracks) and abstract hip-hop (37) both touch all 14 categories — the rap lane is genuinely dense, not just situational.

**HIGH:** Art-pop / synth-pop / alt-pop — Caroline Polachek, Yves Tumor, SOPHIE, Charli XCX (CRASH/Brat), 100 gecs. Synth-pop and alt-pop both span 8–10 categories.

**HIGH (mood-specific):** Indie sad / dream pop — Soccer Mommy (confirmed), Indigo De Souza (confirmed), Perfume Genius (confirmed), The National (confirmed), Mazzy Star (confirmed), Portishead (confirmed). Use for melancholic contexts only. Dream pop spans 10 categories.

**HIGH:** Boom bap / east coast hip-hop — confirmed library presence (23 + 15 tracks across 9–13 categories). Roc Marciano, Ka, Westside Gunn, Earl Sweatshirt deep cuts, MIKE — distinct lane from cloud rap and southern, worth its own vector.

**MOD-HIGH:** Post-punk revival — Fontaines D.C., Shame, IDLES, Dry Cleaning, Protomartyr, Parquet Courts. Not yet confirmed in library breadth — still inferred.

**MOD-HIGH:** Disco / nu-disco / disco-funk — **new vector flagged from audit** (18 tracks across 11 categories, not in v3). Parcels (already locked) is the entry point. Adjacent: Jungle, Yussef Dayes, Tom Misch, Vulfpeck, Cymande reissues.

**MOD:** Southern / Memphis rap — Key Glock, Project Pat, 8Ball & MJG, Boldy James. Library has dedicated Memphis presence (gangsta 17, dirty south 6, crunk 4).

**MOD:** Phonk — **new vector flagged from audit** (4 tracks, Memphis-rap-adjacent). Limited but present; treat as situational extension of darktrap/Memphis lane, not a primary discovery channel.

**MOD:** Japanese math rock / guitar — Chon, Covet, Toe, Tricot, LITE (morning context).

**MOD:** Trip-hop / downtempo / dub — Massive Attack, UNKLE, Burial, Shlohmo (atmospheric electronic only — no EDM). Downtempo spans 11 categories in the audit.

**MOD:** Deep / melodic house — present in library (house 6, deep house 5) and in EDM/Dance playlist DNA, despite the broad EDM exclusion. Treat as an exception lane: groove- and texture-forward only (Lane 8, Bonobo, Maribou State, Caribou, Floating Points), never festival-drop.

**LOW (only if explicitly exploratory):** Jazz-rap (Noname, Saba, Smino), ambient/electronic (Boards of Canada, Four Tet), folk/acoustic (very limited appetite — acoustic spans 10 categories but only 9 tracks).

---

## PRODUCTION & VOCAL RULES

**Sound DNA:** Reverb-heavy, layered, textural production across all genres. Wall-of-sound > stripped acoustic. Sample-heavy maximalism in hip-hop (Kanye/Madlib school). Synth layers and dreamy atmospherics in rock/pop. Bass-heavy confidence in hype contexts.

**Benchmarks:** Tame Impala = psych production gold standard. Kanye (Yeezus/TLOP) = maximalist hip-hop. Frank Ocean (Blonde) = emotionally textured R&B. Gorillaz (Song Machine) = genre-blending. Fail these benchmarks = won't stick.

**Vocals:** Rapper quality is critical — weak MC over good beat = worse than no suggestion. Prefer melodic/textural vocals (Blood Orange, Frank Ocean) or strong instrumental alternatives. Female indie vocals (Soccer Mommy, Phoebe Bridgers) work in emotional contexts. Baritone (The National, Interpol) for melancholic weight.

### Audio metadata vs. emotional reality (confirmed at scale by v4 audit)

**High Spotify valence ≠ emotional positivity in this library.** Roughly 1 in 4 tracks with valence > 0.6 is classified Dark, Sad, Heartbreak, or Moody after lyrical/contextual review. Examples confirmed: Foster The People "Pumped Up Kicks" (bright melody, school-shooter narrative → Dark/Moody); Radiohead "Everything In Its Right Place" (valence 0.85 → Moody/Dark); Kanye "Hold My Liquor" (valence 0.92 → Sad/Heartbreak/Dark/Slow); Gorillaz "Momentary Bliss" (valence 0.79 → Dark cluster). **Rule:** When valence and lyrical/genre context disagree, lyrical context wins. Do not lean on valence to populate sad/dark playlists — it surfaces "bright sadness" and false positives.

**Tempo and danceability are more reliable directional signals.** Fast cluster averages 136 BPM, Slow ~102 BPM — these track expectations. Heavy Bass + Groove + Dance all cluster around 0.74–0.76 danceability — body music has a clear fingerprint. **Hype is the diagnostic exception:** high tempo (132), high valence (0.62), but danceability drops to 0.50 — aggression is energetic without being danceable. Use this to distinguish workout picks from dance picks even when tempo and valence look similar.

**Energy scores are systematically low across this dataset and only useful as relative ranking, not absolute.** Median energy ~0.04 against a Spotify-native scale that typically medians far higher. Trust the within-library rank order; ignore absolute values.

---

## HARD EXCLUSIONS
❌ EDM / festival trap / drop-based electronic *(deep/melodic house is the documented exception — see Discovery Vectors)*
❌ Acoustic singer-songwriter (unless asked)
❌ Smooth R&B / smooth jazz
❌ Nashville country (outlaw/southern rock OK)
❌ Generic pop without production depth
❌ Lo-fi hip-hop / "chill beats" (prefers artists with identity)
❌ PHC nostalgia picks without personal attachment
❌ Tracks not on Apple Music
❌ Hallucinated track names or albums — verify before presenting; two errors in one session caused trust damage

---

## QUICK TAGS (sonic shorthand)
`#latenight` reverb/introspective · `#hype` hard/confident/bass/non-danceable · `#cruise` sunny/groovy/warm · `#psych` swirling/synth-drenched · `#darktrap` grimy/Memphis · `#indievibe` warm indie pop/rock · `#nostalgia-phc` post-hardcore comfort · `#southernrap` UGK–Three 6–KRIT lineage · `#artpop` synth-forward/experimental · `#morning` gentle/melodic/instrumental · `#nightdrive` cinematic/forward-motion · `#sulk` unmistakably sad/lush · `#workout` max intensity/no breaks · `#love` devotion/longing/vulnerability · `#brightdark` bright production / dark content (Pumped Up Kicks pattern) · `#bodymusic` Dance+Groove+Heavy Bass cluster, danceability 0.74+ · `#fastslow` fast tempo / slow emotional weight (post-hardcore, dark trap) · `#discofunk` nu-disco / disco-funk groove lane · `#deephouse` groove- and texture-forward house only

---

## LIBRARY SNAPSHOT (v4)

**Audit basis:** 1,332 unique tracks classified across 14 mood categories, 958 enriched with Spotify audio features (energy, valence, tempo, danceability), 38 tracks reclassified during AI review.

### Category distribution
| Category | Tracks | % multi-category |
|---|---|---|
| Fast | 637 | 92% |
| Moody | 587 | 100% |
| Slow | 473 | 99% |
| Heavy Bass | 378 | 97% |
| Dance | 330 | 97% |
| Sad | 328 | 100% |
| Groove | 226 | 100% |
| Heartbreak | 189 | 92% |
| Dark | 171 | 95% |
| Love | 149 | 93% |
| Hype | 117 | 97% |
| Uplifting | 107 | 93% |
| Happy | 105 | 99% |
| Sunny | 62 | 95% |

Mean categories per track: **2.90**. Only 9.7% of tracks live in a single category. **Cross-membership is the rule, not the exception** — taste lives in pairings, not in pure moods.

### Top 5 cross-category pairings (sonic sweet spots)
1. Fast + Moody — 266 tracks
2. Moody + Slow — 203 tracks
3. Fast + Slow — 183 tracks
4. Moody + Sad — 175 tracks
5. Dance + Groove — 165 tracks

Top triplet: **Dance + Groove + Heavy Bass** (69 tracks) — the body-music core. Next: Fast + Moody + Slow (65) and Fast + Moody + Sad (64) — the dark/driving cluster. Sad + Slow + Moody triplet (54) is the explicit sulk DNA.

### Audio feature averages per mood bucket
| Category | Energy* | Valence | Tempo | Danceability |
|---|---|---|---|---|
| Uplifting | 0.038 | 0.556 | 117.6 | 0.590 |
| Sunny | 0.040 | 0.477 | 121.0 | 0.606 |
| Slow | 0.045 | 0.464 | 102.5 | 0.595 |
| Sad | 0.044 | **0.300** | 121.4 | 0.583 |
| Moody | 0.045 | 0.439 | 126.0 | 0.552 |
| Love | 0.039 | 0.496 | 123.9 | 0.633 |
| Hype | 0.044 | 0.618 | 132.3 | **0.504** |
| Heavy Bass | 0.051 | 0.463 | 118.6 | 0.743 |
| Heartbreak | 0.049 | 0.445 | 121.6 | 0.506 |
| Happy | 0.040 | **0.706** | 124.5 | 0.508 |
| Groove | 0.048 | 0.451 | 121.4 | **0.760** |
| Fast | 0.047 | 0.514 | **136.2** | 0.560 |
| Dark | 0.047 | 0.454 | 126.5 | 0.583 |
| Dance | 0.049 | 0.441 | 117.7 | 0.745 |

*Energy scores skew low across the entire dataset; treat as relative rank, not absolute. Library-wide averages: energy 0.045, valence 0.475, tempo 124.1 BPM, danceability 0.585.

### Synthesis
This is a library where **mood is layered, not assigned** — 90%+ of every category is multi-category, and the most common pairings (Fast+Moody, Moody+Slow, Dance+Groove) describe textures that combine emotional weight with motion. The widest single-genre footprints — alternative rock, contemporary R&B, indie rock, conscious hip-hop — all touch 13–14 of 14 mood categories, meaning the listener doesn't have separate genre buckets so much as a single texture-forward sensibility expressed through them. The diagnostic feature is the systematic mismatch between Spotify valence and emotional content (24% of high-valence tracks land in dark categories): this listener weights production texture and lyrical/contextual feel over surface brightness, and any recommendation system that trusts valence at face value will misfire on the "bright sadness" lane that runs through a meaningful chunk of the rotation.

---
<!-- Review: after next major scrobble import or ~50+ playlist additions. Do not duplicate scrobble reference data here. -->
