# Rules pilot: first 20 triage conversions

A batch-by-batch log, dated, that ends with batch 12 (204 converted). Batches 13 and 14 and the rest of the
conversions to 526 are summarised in [HANDOFF-PUZZLES.md](../../../docs/harness/HANDOFF-PUZZLES.md); the
current count of every result is `manifest.jsonl` (526 converted, 10 Forge disagreements, 8 multiple-review,
761 dropped).

Source: the ranked list in `eval/puzzles/rules/TRIAGE.md`. #2 was skipped by the constraints (its deciding choice
is how trample damage is divided, which the search arm splits evenly), so #21 was taken instead. Twenty questions
were attempted as `RP_001` to `RP_020`. One line per question is in `manifest.jsonl`; #2 has its own line too.
Gate: the search arm (budget 600, cap 60 s) must prove the puzzle with a unique solution, the pass arm must
fail, and the proven line must exercise the rule. Each puzzle's behaviour was also checked in random-arm game
logs (30 seeds).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 18 | 001, 002*, 003-010, 012-016, 018, 019, 020* |
| Forge disagreement | 1 | 011 |
| dropped | 2 | 017 (cannot be proven here), triage #2 (constraint, not built) |

\* These pass the gate with a caveat. The search arm never branches trigger order, so their "unique" does not
cover orders, and they are proven only because Forge's default order happens to be the right one. The misread
orders were confirmed to fail with the random arm, which does shuffle trigger order.

Uniqueness was exhaustive (exhausted = deviations) for every converted puzzle except RP_005 (6 of 8). The pass
arm was rejected on all 20. Rough effort: 104 minutes over 21 lines, about 5 minutes per puzzle. The time
included two rounds of diagnosing harness behaviour.

## Forge disagreements

- **RP_011** (Vraska, Betrayal's Sting with Doubling Season). CR 616.1 lets the controller order Compleated and
  Doubling Season (10 or 8 loyalty). Forge never asks: both replacement-order calls have one option. Even so,
  the applied order differs between games with identical decisions: casts that paid life ended at 8 in some
  random-arm games and at 10 in others. The search proof depends on an order the engine picked, not on any
  choice the solver made.

## Dropped

- **RP_017** (City of Brass + Angel's Grace). The arms never get the winning line. Angel's Grace is not on the
  menu (castable 0 with an untapped City of Brass at 1 life). Floating a land's mana before casting is offered only
  to the faithful seat. The puzzle is kept for model evaluation, but it has no proof.
- **Triage #2** (deathtouch + trample). Not built: the deciding choice is the trample damage division.

## Grammar needs

None. Every acceptance state used `life`, `count`, `tokens`, `counters` or `winner`. One trap: tokens are named
`"Goblin Token"` and `"Treasure Token"` in the engine (Marit Lage keeps its own name), so the `tokens ... "Goblin"`
example in `eval/PUZZLES.md` never matches.

## What made puzzles fail the gate (and the fixes)

- **Second solutions from extra actions:**
  - An optional land drop from a drawn card (RP_006): fixed with a non-land library.
  - A spell cast and then answered by the solver's own sacrifice (RP_003, Unsummon): fixed by removing the spell.
  - Optional attacks: fixed by starting non-combat puzzles in MAIN2.
- **Hidden actions in the baseline arms' menu:**
  - Sacrifice-cost mana abilities such as Ashnod's Altar are offered only to the faithful seat. Fixed by using
    Spawning Pit.
  - Finale of Promise was hidden (RP_006); see the harness bugs below.
  - Vraska could not be cast with exactly 4+B available.
- **Requirements the arms ignore:** a Lure-forced block was declined, so RP_007's first proof ran through an
  illegal no-block branch. It was rebuilt with the solver defending.
- **Choices the search does not branch:** trigger order (RP_002 and RP_020 carry the caveat above), and damage
  division (the constraint).

## Harness and prover bugs found

1. **Trigger order is never searched.** `PuzzleArms.subset` with lo == hi == n always returns the identity order
   for the search arm. `orderSimultaneousSa` therefore never branches, and uniqueness cannot see a wrong order.
   Evidence: the RP_002 line `Solver:2:1 x3` holds only attack choices.
2. **Spells with optional targets are hidden.** `offerable` requires `targetCandidates >= max(1, minTargets)`
   and checks the targets at X = 0. Finale of Promise (TargetMin 0, targets with mana value up to X) was never
   offered even with six Mountains. Evidence: `scratchpad/rp/RP_006-rand/game-1-*.jsonl`, `castable:0`, with
   Dark Ritual and Empty the Warrens in the graveyard.
3. **The random and search arms ignore blocking requirements.** `declareBlockers` takes a 50/50 per blocker,
   and Forge does not re-check requirements for a controller. A creature lured by Lure went unblocked. Evidence:
   the first RP_007 proof had the opponent declining the block.
4. **Angel's Grace is not offered** with City of Brass at 1 life (`castable:0`), probably because
   `ComputerUtilMana.canPayManaCost` (AI mana planning) avoids the painful source. Evidence:
   `scratchpad/rp/RP_017-rand/game-1-*.jsonl`.
5. **The baseline arms' menu differs from the faithful seat's.** Costly mana abilities and floating mana are
   gated on `observer != null`, so a proof cannot use Ashnod's Altar or mana-first lines.

# Batch 2: thirty more questions (RP_021 to RP_047)

Source: the TRIAGE.md ranked list from #22 to #40, then 12 more `convert == "good"` questions from `triage.jsonl`
chosen for spread: triage lines 369, 571, 268, 501, 209, 224, 95, 145, 341, 220, 207 and 875. Ranked #28 (Giant
Warthog, a trampler whose blocker has left) was skipped without counting: it tests the same rule as RP_007. Ranked
#27, #35 and #37 were dropped without a board. Build: epoch 52 (scratchpad `epoch52-build.txt`). Gate as in batch 1:
search arm (budget 600, cap 60 s), then the pass arm. Results are in `manifest.jsonl` (30 rows after batch 1's 21).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 23 | 021-023, 025, 027-033, 035-044, 046, 047 |
| Forge disagreement | 1 | 024 |
| dropped | 6 | 026 (harness), 034 (prover limit), 045 (harness); ranked #27, #35, #37 (no board) |

The pass arm was rejected on all 27 boards. Uniqueness was exhaustive on 15 of the 23 conversions. Not exhaustive:
RP_022 (7/27), RP_025 (0/370), RP_028 (6/10), RP_029 (0/6), RP_030 (9/10), RP_032 (10/16), RP_035 (8/23),
RP_040 (3/17), RP_043 (9/10) and RP_046 (1/6). Their "unique" means none found within the budget. RP_044 is the
first puzzle whose proof branches trigger order. Its misread order was confirmed rejected by a random-arm seed.

Effort: about 110 minutes over 30 questions, about 3.7 minutes per question, plus one HOLD wait for the engine rebuild.
No run started while HOLD existed.

## Forge disagreements

- **RP_024** (Hooded Hydra, Hardened Scales, Corpsejack Menace). CR 616.1 lets the controller order the two
  counter replacements (12 or 11). Forge never asks: every ask is `replacement-order(forced-only-legal)`, as in
  RP_011. The accepted 12 is the engine's order. Turning a morph face up is also not a game-log action, so the
  proof's `actions` is empty.

## Dropped

- **RP_026** (Forge Anew + Kaldra Compleat). Harness: the equip ability is never offered when only Forge Anew's {0}
  alternative cost can pay it. The search log has attack choices and no play decision. With seven Wastes (printed
  {7} payable) the same line is proven (`rp2v/RP_026b`).
- **RP_034** (Fiery Confluence vs Welding Jar). Prover limit: it gives up after 4 solver lines that each lose to
  the Jar reply (`PZL_SEARCH_LINES` default 4). With 40 it is proven and unique (`rp2v/RP_034-lines40`).
- **RP_045** (General Leo Cristophe vs Scavenging Ooze). Harness: an "up to one target" trigger with one candidate
  is forced (`... <- General Leo Cristophe ... (only legal)`), so choosing no target is never offered and every line
  loses to the Ooze.
- **Ranked #27, #35, #37**: no board. Either no solver choice depends on the rule (Sorin's +2 under Doubling
  Season), or every board has two equal solutions (Twinning Staff: either Golem target gives five resolutions), or
  the state cannot be set (Ocelot Pride: city's blessing, tokens created this turn).

## Grammar needs

None. One limit: Twinning Staff's "number of resolutions" is observable only through damage.

## What made puzzles fail the gate (and the fixes)

- **Too many free choices for the budget**: RP_021's sacrifice outlet could sacrifice any creature at every priority
  stop of a whole turn. Fixed by moving to the opponent's upkeep and using Glimmer Bairn (sacrifices tokens only).
- **Extra actions as second solutions**: re-sacrificing the returned Clique (RP_030, fixed with an acceptance line)
  and the Druid's own untap ability (RP_038, fixed with `SummonSick`).
- **Avoided by design**: Walking Ballista's ping and pump give second X choices (Endless One instead, RP_025).
  Possible attacks: boards start in MAIN2.

## Harness and prover findings

1. **Alternative activation costs are not priced** (RP_026): `offerable()` checks the printed cost, so Forge Anew's
   {0} equip is never on the menu.
2. **A single-candidate "up to one" target is forced** (RP_045): `ensureTargetsSet` offers "No target (decline)"
   only when there are 2 or more candidates. The epoch-52 fix covers spells, not this path.
3. **Lines-per-point cap**: a puzzle whose opponent reply beats several attractive solver lines needs
   `PZL_SEARCH_LINES` above 4 (RP_034).
4. **Uniqueness compares game-log actions, and these choices are not in them**: modal modes (RP_034, RP_043),
   clone choices (RP_035), turning a morph face up (RP_024), and whether a sacrifice comes before or after a trigger
   resolves (RP_021). A second solution that differs only there would be called the same. For RP_021 the timing was
   checked another way (only one Snake ever existed).
5. **Replacement order still never asked** (RP_024, RP_011).

# Batch 3: forty more questions (RP_048 to RP_086)

Source: 40 unattempted `convert == "good"` questions from `triage.jsonl`, chosen for spread (triggers, SBAs,
layers, copies, replacement, costs, combat, counters, control). Questions that are near-duplicates of batch 1 and 2
rules were skipped (for example RP_031's spell-in-graveyard SBA (Tarmogoyf), RP_029's LKI counters, RP_039's exile
instead of dying). Questions needing damage division, replacement order, opponent actions or randomness were skipped
too. Build: epoch 53. RP_048 to RP_062 were first gated on the build from 10:08. The newer build (13:06; uniqueness
compares log actions plus hidden solver choices and reports `uniqueness_difference`) gated everything else, and
every result below is from it: RP_048 to RP_062 were re-run on it. Gate: search arm (budget 600, cap 60 s), then
the pass arm. No run started while HOLD existed. 40 rows are in `manifest.jsonl` (now 91).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 20 | 049-051, 054-057, 062-064, 066, 067, 070, 071, 075, 079-081, 083, 085 |
| multiple, outcome-neutral difference | 18 | 048, 052, 053, 058-061, 068, 069, 072-074, 076-078, 082, 084, 086 |
| Forge disagreement | 1 | 065 |
| dropped | 1 | Rotpriest question (no board) |

Every board was proven, except RP_065, and the pass arm was rejected on all 39. Uniqueness was exhaustive on 16 of
the 20 conversions. Not exhaustive: RP_055 (10/22), RP_062 (5/10), RP_066 (8/9) and RP_083 (4/5). The 18
"multiple" boards have a correct proven line. Their second line differs only in a choice that changes nothing,
listed per row in `note`. They are recorded as `multiple_neutral` and are not counted until the prover decides
what counts as a difference (see below).

## Forge disagreement

- **RP_065** (Command the Dreadhorde returning Dearly Departed and Squire together). Forge moves the targets one
  at a time in target order. With [Squire, Dearly Departed] the Squire gets its counter (random seed 3, and the
  graveyard-swapped variant `rp3v/RP_065s` proved unique). With [Dearly Departed, Squire] it gets none. The search
  arm does not branch target order, so the shipped board is "not found" after exhausting 41 rollouts. CR 614.12 makes
  the result independent of order.

## Dropped

- **ask-a-magic-judge-bb2d6af95202a723** (Venerated Rotpriest): no board. The solver would have to give its own
  creature hexproof, which does not stop its own spells, so every order of the two spells works.

## Board fixes during the gate

- RP_049: an Eldrazi Spawn's sac-for-mana ability is now on the baseline menu and is not a log action, so it gave a
  second line. The Spawn was replaced by Mist Intruder.
- RP_052: the Opt could be cast after attackers (a real second line). It is now Preordain, a sorcery.
- RP_056: the pass arm solved v1, because it auto-confirms an optional trigger with one target. In v2 the solver
  must sacrifice to feed the Ozolith. Acceptance now keeps Shalai, because sacrificing it with its trigger on the
  stack also won.
- RP_063, RP_075: drawn Islands gave a land drop. The libraries are now non-land.
- RP_051: Wild Mongrel's colour choice was replaced by Putrid Imp. RP_077: Opt's scry was replaced by Think Twice.
- RP_055: v1 (Murder between the two untap triggers) was not found in 600 rollouts, because land mana abilities on
  the menu multiply the tree. A scripted replay showed Forge does put two Treachery triggers on the stack. v2
  differed in how many lands each trigger untapped. v3 makes Kozilek need all ten lands.
- RP_086: the Packleader version differed in trigger order. It now uses Fling (only a creature can be flung).

## Grammar needs

None.

## Harness and prover findings

1. **The new uniqueness counts choices that change nothing** (18 boards). Kinds, with an example of each:
   - The order of cards moved together: RP_059, 072, 074, 076, 084 (`order: [...]`).
   - Which identical land paid: RP_048, 069, 073, 086 (`mana-sources: []` against a named Plains or Island).
   - A declined library pick, then "Cancel Search? NO", then the same pick: RP_061, RP_068.
   - The order of two triggers whose results commute: RP_053, RP_058.
   - A scry: RP_052, RP_060.
   - The order of two draw replacements that both double: RP_077.
   - The same targets described as tapped and as untapped: RP_082.
   - Setup-time creature-type picks: RP_078. Roaming Throne's choose-a-type replacement seems to be asked when the
     puzzle state is loaded, before `ChosenType:Goblin` is applied.
2. **RP_056: `uniqueness_difference` was empty (`[]`) for a real second line** that had an extra action.
3. **Target order is not branched** (RP_065), and Forge's result depends on it.
4. **Draw replacement order is now asked** (RP_077: Bard, King of Dale against Thought Reflection). Batch 1 and 2 never
   saw a replacement-order choice.
5. **A replicate copy is logged as "Solver cast ..."** (RP_067). Chalice still triggered only on the original.
6. **The pass arm auto-confirms an optional trigger** that has a single target (RP_056 v1 was solved by passing).
7. Process: two diagnostic JVMs (`rp3v/RP_055d`) ran while both lanes were busy, so three JVMs ran at once for a
   few minutes.

## Re-verification on the epoch 54 prover (2026-10-18)

Every puzzle was re-run after the uniqueness rule settled: a second solution is a line, proven against every
opponent reply, whose solver actions or outcome-bearing solver choices (modes as a set, numbers, card names, entity
picks, "No target") differ as a set. Order, mana payment, colour of an any-colour source, scry, confirm prompts,
declined searches and target labels (the log already names targets) are not compared.

| Result | Count |
|---|---|
| converted (proven, unique, do-nothing arm rejected) | 81 |
| Forge disagreement (RP_011, RP_024, RP_045, RP_065) | 4 |
| not built | 5 |
| multiple, for the author (RP_028: Gemrazer over or under Young Wolf both solve) | 1 |

The rule's history, for the record: game-log actions alone missed modes and clone picks; the final-state digest
counted the opponent's replies (RP_045, RP_007); noting every solver choice counted order and mana payment (18 of
batch 3). Known blind spot: a "choose a color" effect shares the colour ask, so a second solution differing only in
that colour is not seen.

# Batch 4: fifty more questions (RP_087 to RP_131)

Source: `triage.jsonl`, `convert == "good"` questions not yet attempted (41), then `maybe` questions with acceptance
and misread filled in (lines 18, 120, 339, 368, 404, 513, 598, 761, 955). Rule-level near-duplicates of batches 1 to
3 were skipped as well as the TRIAGE.md pairs (for example LKI power, Tarmogoyf, Blood Moon layers, trample
division, replacement ordering). Build: epoch 54 (`epoch54-build.txt`, re-read before every JVM). Gate: search arm
(budget 600, cap 60 s), then the pass arm. 50 rows in `manifest.jsonl` (now 141), each with `verified_on`.

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 38 | 087, 089-094, 096-102, 104-107, 109-115, 117-119, 121-126, 128-131 |
| multiple, same play in substance | 5 | 088, 108, 116, 120, 127 (`multiple_review`) |
| Forge disagreement | 2 | 095, 103 |
| dropped (no board) | 5 | Phage/Gather Specimens, Necroduality/Grimgrin, Pir/Doubling Season, Custodi Soulbinders, Zada/Twinning Staff |

The pass arm was rejected on all 45 boards. Uniqueness was exhaustive on 21 of the 38 conversions; the other 17 are
"unique within the budget" (`uniqueness.exhausted` < `deviations` in each row; weakest RP_114 0/2 and RP_128 2/9).
No JVM started while HOLD existed (every `.starts` line says `hold=N`). The two workers died once when the session
paused; RP_125 and RP_126 were re-run from scratch. Effort: about 3.5 hours of wall time.

## New setup tools (all from Forge's `GameState`)

- `p1putonstack=Murder->1`: an opponent spell already on the stack, targeting card `Id:1` (RP_094, RP_113). It is
  not a cast, so cast-dependent cards (Phage) cannot be built this way.
- `p1precast=Giant Growth->1`, `p1precast=The One Ring:TrigPump`: resolve an effect during setup (RP_114, RP_106).
- `p1counters=POISON=9`: player counters (RP_110, RP_114); `p0manapool=R R ...`: floating mana. Floating mana
  removes the land mana abilities from the search tree (RP_103) and makes mana expire at the end of the phase
  (RP_114).
- `activephase=CLEANUP` of the opponent's turn with `activephaseadvance=UPKEEP` reaches the solver's upkeep
  triggers; `activephase=UNTAP` is unscoreable.
- A `t:` token without a `Keywords:` field crashes the loader (`TokenInfo.makeOneToken` NPE); RP_104 uses
  Changeling Outcast as the Saproling.

## Forge disagreements

- **RP_095** (Tinybones, Fraying Omnipotence): Forge runs the spell as RepeatEach per player, so the caster
  sacrifices Tinybones before the opponent discards. The card has every player do each step in turn.
- **RP_103** (Yidaro): Forge counts resolved Yidaro triggers, not cycles. Cycling all four before any trigger
  resolves still shuffles three away (search not found in 600; random arm, 12 seeds: always one Yidaro, none in the
  graveyard).

## Harness and prover findings

1. **Choose-source menus list Forge's section headers as entities** (RP_108, Deflecting Palm): the solver was
   offered `--PERMANENTS:--` and `--SPELLS ON THE STACK:--` (`[null]`), picked 15 times in the search log, each
   followed by a second pick. The uniqueness check then calls a header pick a difference.
2. **Land plays are compared with their card id** (RP_120): `LAND: Solver played Forest (2)` against `Forest (3)`,
   two identical cards, is reported as a second solution.
3. **Entity-pick labels carry the object's current state** (RP_127): the same Camera Launcher picked before and
   after it got a counter (`Camera Launcher (3/3)[+1/+1 x1]`) counts as different.
4. **How a cost pick is recorded depends on order** (RP_116): exiling three of four identical Bears is
   `cards-for-effect`, the forced pick in the other order is a `choice`, so one play in two orders differs.
5. **An Equipment is offered a noncreature attach target** (RP_088): Field-Tested Frying Pan could attach to the
   Food token Peregrin Took added (CR 301.5c forbids it); the uniqueness check sees two lines.
6. **Forge offers a loyalty counter for a returned creature** (RP_097 v1, Elspeth Conquers Death's
   "P1P1,LOYALTY" choice); acceptance now pins the +1/+1 counter.
7. **Blocks are not always game-log actions**: RP_108's proven line blocks (`blockers(random)->1`) but its
   `actions` lists only the Palm, whereas RP_089's block is logged. A second solution that differs only in a block
   may not be seen in such a puzzle.
8. **Stony Silence did not stop The One Ring's tap ability** in RP_096 v3 (the ability was offered and tapped the
   Ring). Not followed up; the board was rebuilt without it.

## Board fixes during the gate

- RP_097: acceptance pins the +1/+1 counter (finding 6). RP_109: Jace Beleren added so every attack picks a
  defender. RP_114: floating B, because a Swamp let the Wound be cast on the opponent's turn on the 1/1 Scryer (a
  real second solution). RP_117: Capridor tapped, because an extra block was a real second line. RP_118 and RP_120:
  draws could find a land or a castable Bears. RP_096: four versions (see its row). RP_103: floating mana.

### Batch 4 findings, handled (2026-10-18)

- RP_108: Forge's "choose a source" divider cards were offered as options; fixed for every seat (harness-epoch-55,
  `choiceListDividersAreNotOptions`).
- RP_108: a block after a "didn't block" line in the same combat entry was not read as a solver action; fixed
  (`aBlockAfterADidntBlockLineIsASolverAction`).
- RP_120, RP_127: uniqueness now drops card ids from log actions and an object's size and counters from pick labels,
  so the identical Forest (2)/(3) or the same creature before and after a counter is one play.
- RP_088: with Peregrin Took, Forge's Field-Tested Frying Pan trigger remembers the extra Food tokens with the Halfling
  and offers them as attach targets for the Equipment (a noncreature attach is not legal). A Forge issue, recorded, not
  filtered by the harness.

# Batch 5: seventy more questions (RP_132 to RP_162)

Source: every remaining `convert == "good"` question in `triage.jsonl` (80 after the five TRIAGE.md near-duplicates
whose pair was already attempted), then `maybe` questions with acceptance and misread filled in (lines 12, 40, 142,
163, 225, 254, 287, 300, 409, 495, 505, 621). 70 rows in `manifest.jsonl` (now 211). Builds: epoch 55 for RP_132 to
RP_141 (and the RP_134, RP_136 rebuilds), epoch 56 (`epoch56-build.txt`, re-read before every JVM) for everything
after; each row's `verified_on` names the build of its search run. Gate as before: search arm (budget 600, cap 60 s),
then the pass arm. No JVM started while HOLD existed (every `.starts` line says `hold=N`).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 22 | 132-139, 141, 142, 144, 145, 147-155, 161 |
| multiple, same play in substance | 2 | 146 (the countered original Grapeshot's target), 162 (repeated free Reconnaissance) |
| Forge disagreement | 2 | 140, 143 |
| harness bug (damage division) | 5 | 156-160 |
| dropped (no board) | 39 | see each row's `note` |

The pass arm was rejected on all 31 boards. Uniqueness was exhaustive on 12 of the 22 conversions; weakest RP_132
(8/39) and RP_152 (9/20).

Skipped without a row as rule-level near-duplicates of converted puzzles: LKI power (RP_016: two questions), a spell
or ability whose targets all became illegal (RP_094: four), a blocked trampler whose blocker left (RP_007: three),
LKI counters (RP_029/RP_032: two), Tarmogoyf (RP_031), CDA loss to Dress Down (RP_014), Life and Limb under Blood Moon
(RP_010: two), priority between damage steps (RP_029), sequential modes and regeneration (RP_039: two), damage stays
marked (RP_005), and the in-batch duplicates Murktide/Dress Down (RP_136), rebound order (557), Omnath/Yarok (RP_144).

## Forge disagreements

- **RP_140** (Lich and Exquisite Archangel): Forge's Lich trigger runs LosesGame when the damage exceeds the permanents
  and skips the sacrifice, so Lich and Archangel stay on the battlefield (random arm, seeds 2 and 4: AllOpponentsLost
  with both still there). CR 609.3 has both sacrificed first.
- **RP_143** (Magus of the Moon, Turn): after Turn, Forge stops applying Magus's land-type change, so Mishra's Factory
  can animate. With the Magus tapped (`rp5v/RP_143t.pzl`) "Turn on Magus, animate, attack" is a second solution, which
  CR 613.5 forbids; the shipped board reads unique only because the Turned 0/1 Magus blocks.

## Harness bug: damage-division lethal (epoch 56)

Revisiting the nine questions dropped for damage division: five boards were built (RP_156 to RP_160) and four are the
same rules (rows updated). All five hit one bug. The amounts ask in `LlmSpikeGui.java` (near line 7083) takes each
blocker's lethal damage from `ComputerUtilCombat.getEnoughDamageToKill`, Forge's AI prediction, instead of CR 702.19b
lethal (toughness minus marked damage, 1 with deathtouch):
- shield counter (RP_156) and protection (RP_158): `damage-assign: all 6 -> <blocker> (forced)`, nothing tramples;
- indestructible with deathtouch (RP_160): `all 5 -> Zodiark, Umbral God (forced)`; legal is 1 and 4 over;
- Zilortha (RP_159): the blocker always gets at least 1; legal is 0 and all 3 over;
- Gratuitous Violence (RP_157): the illegal `Opponent=2, Grizzly Bears=1` is offered, and the proof rests on it.

The boards are kept: once lethal follows the rule they are ready gates (expected: proven, unique).

## Board fixes during the gate

- Acceptance: `winner p0` holds for any survived Survive puzzle, so "win this turn" boards use `life p1 <= 0`
  (RP_132's first pass arm solved).
- RP_134: Gather Courage left mana for a redundant fizzling activation (a second line); Bestow Greatness spends it.
- RP_135: Putrid Imp's free repeatable discard blew up the tree (not found in 600); Insolent Neonate discards once.
- RP_136: the opponent's Spike Feeder could remove its own counters and die (pass arm solved); Hagra Constrictor.
- RP_149: the main line also sacrificed Dauthi Voidwalker to cast the exiled Myr; acceptance keeps the Voidwalker.

### Batch 5 follow-up (2026-10-18)

The damage-division rebuilds (RP_156-RP_160) exposed that the epoch-56 amounts ask took "lethal" from Forge AI's kill
estimate (ComputerUtilCombat.getEnoughDamageToKill), which counts indestructible, protection, shields and doublers.
Epoch 57 uses the rule (CR 702.19b: toughness minus marked damage, 1 with deathtouch; power under Zilortha), and all
five are proven unique with the do-nothing arm rejected (`lethalDamageForAssignmentIsTheRulesNotWhatKills`).

# Batch 6: one hundred "maybe" questions (RP_163 to RP_188)

Source: `triage.jsonl` questions with `convert == "maybe"` (all have acceptance and misread filled in), screened for a
solver choice the rule decides, no reliance on an opponent action, two players and no randomness. 26 boards were
built and 74 questions were dropped with a reason in each row's `note`. 100 rows in `manifest.jsonl` (now 311). Build:
epoch 58 (`epoch58-build.txt`, re-read before every JVM; the prover rules are epoch 57's). Gate as before: search arm
(budget 600, cap 60 s), then the pass arm. No JVM started while HOLD existed (every `.starts` line says `hold=N`).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 23 | 163-176, 178, 180-184, 186-188 |
| Forge disagreement | 2 | 177, 179 |
| harness bug (block requirements) | 1 | 185 |
| dropped (no board) | 74 | see each row's `note` |

The pass arm was rejected on all 26 boards. Uniqueness was exhaustive on 14 of the 23 conversions; weakest RP_183
(5/26), RP_165 (4/12) and RP_173 (4/13). About 50 more maybe questions were screened and judged unbuildable for the
same reasons (opponent action, no solver choice, replacement order) but left without a row to keep the batch at 100.
Skipped without a row as rule-level near-duplicates: X on the stack (RP_070), all targets illegal (RP_094), LKI power
(RP_015), copies not cast (RP_067), CR 614.12 enters-with checks (RP_001, RP_136), your own trigger order (RP_002).

## Forge disagreements

- **RP_177** (Possessed Portal and Underrealm Lich): CR 616.1 lets the drawing player choose which replacement
  applies. Forge never asks (no `replacement-order` prompt in 600 rollouts) and always applies the Lich, so the
  choice is not the solver's. Unlike RP_077, one of the two effects is a `Prevent$ True` replacement.
- **RP_179** (Snapcaster Mage under Yixlid Jailer): CR 613.7 applies the two layer-6 effects in timestamp order, so
  the later flashback stays. Forge removes it: Lightning Bolt is never offered after Snapcaster targets it (0 casts,
  129 rollouts, exhausted). The control `rp6v/RP_179nj` without the Jailer is proven and unique.

## Harness and prover findings

1. **Block requirements are met by rejection sampling, which the search arm cannot prove through** (RP_185, Lure).
   `declareBlockers` (random-arm branch) redraws a block set up to 64 times; every redraw is new `nextBoolean`/`nextInt`
   draws, which the search arm records as opponent choice points, and after 64 failures the illegal set is kept
   (`[BLOCK-INVALID] arm: no legal block set in 64 draws`, `blockers(random)->1 (draw 64)` in
   `rp6/old/RP_185-four-search`). The prover therefore has to beat every redraw sequence, one of which ends in an
   illegal partial block. The winning line (the Foundations 1-1-1 split, then Pyroclasm) is offered and solves, but
   the search is not_found at 600 rollouts (and at 2100). Fix: enumerate the legal block sets for the arm.
2. **A creature land's activation is not offered when its own mana is the only source** (RP_175 v1): after playing
   Mishra's Factory with no other land, the {1} animate was never on the menu (no decision at all after the land
   play). With a Wastes added, the second activation paid by the Factory itself was offered. RP_175 ships with
   Treetop Village and two Forests.
3. **Turn 1 has no draw for the starting player**, so draw-step puzzles must start on turn 2 with `Turns:2` (RP_177 and
   RP_178 v1 drew nothing).
4. Process: when RP_185 was re-queued, both lanes and a diagnostic JVM ran together for about 20 seconds (three JVMs);
   the stale run and the diagnostic were killed and RP_185 was gated on the remaining clean run.

## Board fixes during the gate

- RP_180: the pass arm solved v1 (its default discard is the first card in hand, the Baloth); Colossal Dreadmaw now
  comes first.
- RP_173: the misread order (return the Angel with its trigger on the stack) was checked with the random arm (seeds 1
  and 3 in `rp6/RP_173-rand`): no prompt, life stays 20. Forge agrees with the judge.

# Batch 7: one hundred more "maybe" questions (RP_189 to RP_204)

Source: `triage.jsonl` questions with `convert == "maybe"` not yet attempted, screened as in batch 6 (a solver choice
the rule decides, no reliance on an opponent action, two players, no randomness, no replacement-order questions).
16 boards were built and 84 questions were dropped with a reason in each row's `note`. 100 rows in `manifest.jsonl`
(now 411). Build: epoch 59 (`epoch59-build.txt`, re-read before every JVM); epoch 60 arrived after the last gate and
changes nothing these boards use. Gate as before: search arm (budget 600, cap 60 s), then the pass arm. No JVM started
while HOLD existed (every `rp7/*.starts` line says `hold=N`), and at most two of this batch's JVMs ran at once.

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 14 | 189-196, 199-204 |
| harness bug | 2 | 197 (free-cast tax), 198 (sacrifice during payment) |
| dropped (no board) | 84 | see each row's `note` |

The pass arm was rejected on all 16 final boards. Uniqueness was exhaustive on 9 of the 14 conversions; the weakest
are RP_192 (3/4), RP_189 (4/5) and RP_194 (8/12). Skipped without a row as rule-level near-duplicates: layer 6
timestamps (RP_152: Kasmina's Transmutation, Melira and Inkmoth), trample assignment before doubling (RP_157: Obosh),
copied permanent spells not created (RP_040: Ivy), all targets illegal (RP_094: Skullbomb), Dauthi exile (RP_149),
Yidaro counts (RP_103), additional costs on a free cast (RP_166: Vadrok kicker), resolution-time checks (RP_175:
Tribute to the World Tree), face-down replacements (RP_124: Nexus of Fate twice, manifest), LKI power (RP_015:
Elenda), your own trigger order (RP_002: Font and Puzzle Box), indestructible and marked damage (RP_004), draws on
empty library (RP_016, RP_093), CR 614.12 enters checks (RP_001, RP_136, RP_172: Arixmethes, Rusted Relic, Solemnity,
Three Tree City, Conspiracy, Erebos, Humility), layer 4 timestamps (RP_098: Iroas and Opalescence), Magus under
ability loss (RP_143), Dearly Departed (RP_065), Dryad Militant (RP_122), marked damage then a toughness drop (RP_050),
sacrifice during payment (RP_198: Teshar and Lurrus), and the in-batch pairs Reprobation/Gemrazer (RP_189) and Green
Slime APNAP (RP_190).

## Harness and prover findings

1. **A free cast's tax is paid automatically; declining is never offered** (RP_197). Omen Machine casts Terminus
   "without paying its mana cost if able"; under the opponent's Sphere of Resistance the {1} was paid by tapping a
   Mountain with no seat decision (pass arm: `MANA: Mountain (7)` then `Solver cast Terminus`). CR 118.3c and the
   judge's answer let the player decline, which is the puzzle's solution. The search arm also ended after one rollout
   with `goal_enforcement_installed=false`, `turns=-1` and no game log: the Omen Machine prompts came before goal
   enforcement (board starts at turn 2 upkeep). Evidence: `rp7/RP_197-search`, `rp7/RP_197-pass`.
2. **A sacrifice-cost mana ability used during payment picks its sacrifice without asking** (RP_198). Fury Sliver cast
   from Hostage Taker's exile with five Mountains: Thermopod's "Sacrifice a creature: Add {R}" was activated inside
   payment and Hostage Taker sacrificed, with no ROUTER sacrifice line (random arm `rp7/RP_198-rand` game 1). From the
   menu the same ability asks (`sacrifice [Thermopod]: Hostage Taker`). The seat never chooses what the payment
   sacrifices, so uniqueness cannot see the Thermopod-sacrifices-itself line. Forge agrees with the judge on the rule.
3. **An opponent's pointless mana tap opens branches where a misread line wins** (RP_192 v1). Llanowar Elves tapped
   for mana before blocks let the Bears token through, so uniqueness reported a second solution under that opponent
   reply. Not a prover bug (the proof is a strategy over replies), but boards should give the opponent blockers with
   no mana abilities.
4. **Land mana abilities on the menu starve combat lines** (RP_204 v1): with Vengeant Earth on a land, the search
   tapped the animated land for mana before attacks in nearly every rollout and stopped at 373 rollouts not_found;
   floating mana fixed it. The new block-requirement enumeration then worked: the opponent's only legal block set
   put Savannah Lions on the animated Memnite (`blockers(search)->... legal sets`).
5. **A face-down permanent counts under its real name in acceptance** (RP_199 v1): `count p0 Battlefield "Exalted
   Angel" = 1` held for the face-down card, so the pass arm solved. Megamorph's counter was used instead. Turning a
   permanent face up is not a game-log action (`actions: []`).
6. Minor: the opponent was offered Mind Stone's "{1}, {T}, Sacrifice: Draw" with only Mind Stone as a source
   (`FAILED to pay for Mind Stone ... nothing was played`, `rp7/RP_189-search`): an unpayable option on the menu.

## Board fixes during the gate

- RP_190: the misread (Murder on your own turn) logs the same action as the solution, so it was checked with
  `rp7v/RP_190t1.pzl` (Turns:1): not_found, exhausted in 27 rollouts.
- RP_191: Lightning Bolt to Flame Rift (no targets) after v1 was not found in 600 rollouts.
- RP_192: Llanowar Elves to Savannah Lions (finding 3). RP_199: Exalted Angel to Hidden Dragonslayer (finding 5).
  RP_204: lands to floating mana and Memnite (finding 4).

# Batch 8: one hundred more "maybe" questions (RP_205 to RP_210)

Source: the remaining `convert == "maybe"` questions in `triage.jsonl`, screened as in batches 6 and 7. The pool left
after batch 7 is mostly opponent-driven, choice-free or replacement-order questions: 6 boards were built and 94
questions were dropped with a reason in each row's `note`. 100 rows in `manifest.jsonl` (now 511). Build: epoch 60
(`epoch60-build.txt`, re-read before every JVM). Gate as before; at most two of this batch's JVMs at once and none
while HOLD existed (every `rp8/*.starts` line says `hold=N`). Boards that would depend on the fixes in progress
(sacrifice during payment, the free-cast tax, face-down names in acceptance) were not built: the mutate-on-a-face-down
question (L61) and the Sphere free-cast question (L542, a near-duplicate of RP_197) wait for them.

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 4 | 205, 206, 208, 210 |
| multiple, same play in substance | 1 | 209 (`multiple_review`) |
| harness bug | 1 | 207 (setup name choice) |
| dropped (no board) | 94 | see each row's `note` |

The pass arm was rejected on all 6 boards. Uniqueness was exhaustive on RP_206 and RP_210; RP_205 6/7, RP_208 9/17.
Forge agreed with the judge on every converted board, including two that looked like possible disagreements:
returning Hardened Scales to Rescuer Sphinx still gives two counters (RP_210), and the city's blessing from a tenth
permanent survives the legend rule (RP_208). Skipped without a row as rule-level near-duplicates (in addition to
batch 7's list): Extus targets before the spell resolves (RP_087), Containment Priest and Grist and Alpine Moon's
enter checks (CR 614.12), Roaming Throne's once-per-turn limit (RP_078), resolving permanent-spell copies (RP_040),
LKI power (RP_015).

## Harness and prover findings

1. **A setup-time name choice overrides `NamedCard:`** (RP_207). Pithing Needle's "as this enters, choose a card name"
   is asked of the opponent while the state loads and answered at random
   (`[Opponent] [AUTO-LEGAL] card-name(random)->A-Capenna Express`), so `NamedCard:Endbringer` is lost, most games have
   no lock, and the search branches over card names (394 opponent replies, not_found at 600). Same class as RP_078's
   `ChosenType`. Evidence: `rp8/RP_207-search/jvm.log`.
2. **Declining a "you may cast it" is offered twice** (RP_209). Suspend's cast asks "Do you want to play Living End?"
   and, after YES, the play-from-effect menu offers "Do not cast it"; uniqueness reports the two declines as two
   solutions (`second: play-from-effect: Do not cast it`).
3. **Counter names in setup are case-sensitive for keyword counters** (RP_205 v1): `Counters:FLYING=1` loads a custom
   counter shown as `[FLYING x1]` that grants nothing; `Counters:Flying=1` is the keyword counter. Not a harness bug,
   but nothing warns at load.

# Batch 9: the last 27 "good" questions and 73 "maybe" questions (RP_211 to RP_213)

Source: first the 27 `convert == "good"` questions that had no manifest row, then `maybe` questions. 100 rows in
`manifest.jsonl` (now 611; every good question now has a row). Build: epoch 61 (`epoch61-build.txt`, re-read before
every JVM). Gate as before; at most two of this batch's JVMs at once and none while HOLD existed. Maybes whose answer
turns on the opponent's choice, or that have no solver decision, were dropped without a board, as asked.

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 3 | 211, 212, 213 |
| dropped: good questions, near-duplicates | 27 | see each row's `note` |
| dropped: maybe questions | 70 | see each row's `note` |

**The 27 good questions.** All 27 had been skipped in batch 5 as rule-level near-duplicates of converted puzzles,
without a row; each now has a row naming the puzzle it repeats: LKI power and abilities (RP_015, RP_104), an only
target gone (RP_094, RP_155: five questions), a trampler whose blocker left or that has none in the second damage
step (RP_007: three), Murktide under Dress Down (RP_136), simultaneous counter annihilation and death (RP_029, RP_125:
two), Death's Shadow (RP_080), Tarmogoyf (RP_031), unpreventable damage against protection (RP_106), a CDA lost to
ability loss (RP_013, RP_014), Life and Limb with Blood Moon (RP_009, RP_010: three), rebound order (RP_002),
priority between damage steps (RP_026), regeneration within one spell (RP_034, RP_039: two), Omnath doubled
(RP_144), marked damage after a boost ends (RP_050), toxic (RP_110). One (Hardened Scales with Doubling Season) is a
replacement-order question, which Forge never asks (RP_011, RP_024). None was missing a card.

**The maybe questions.** Still deferred for the fixes in progress, and given rows saying so: the face-down questions
(L292, L425, L477, L491, L500). Five replacement- or cost-order questions were dropped for the known reason (Forge
never offers the order). The three boards: God-Eternal Kefnet's copy of Cyclonic Rift cast for overload (RP_211),
storm copies keeping a splice (RP_212), and Twincast copying a kicked Burst Lightning (RP_213). Forge agreed with the
judge on all three; the pass arm was rejected on all three.

## Harness and prover findings

None new. Uniqueness hit the 600-replay budget on RP_211 (1/7 exhausted) and RP_212 (2/10); both are "unique within
the budget". Process note: RP_211 was edited (Kefnet made SummonSick) just after its first run started, so it was
queued again; the two runs overlapped for a few seconds and the shipped result is the second run's (its `starts`
file, start-search 1792385643).

# Batch 10: the epoch 60-62 re-gates, the face-down questions and three new boards (RP_214 to RP_217)

Source: the four batch 7-8 boards that waited on harness fixes (RP_197, RP_198, RP_207, RP_209), the deferred
face-down questions (L61, L292, L425, L477, L491, L500) and the Sphere free-cast question (L542). Build: epoch 62
(`epoch62-build.txt`, a fresh isolated build in this container) for everything except RP_207, which ends on epoch 63
(`epoch63-build.txt`, finding 1). Gate as before: search arm (budget 600, cap 60 s), then the pass arm, through
`scripts/puzzle-gate/gate10.sh`; at most two of this batch's JVMs at once and none while HOLD existed.

## Counts

| result | n | puzzles |
|---|---|---|
| converted (re-gated) | 4 | 197, 198, 207, 209 |
| converted (new) | 4 | 214 (L61), 215 (L425), 216, 217 (L491) |
| dropped: near-duplicates | 3 | L292, L477 (RP_214), L542 (RP_197) |
| dropped: no solver choice | 1 | L500 |

Every board: search proven and accepted, pass arm rejected, Forge agreeing with the judge. Uniqueness exhaustive on
RP_197 (6/6), RP_207 (8/8), RP_209 (4/4), RP_214 (4/4), RP_216 (5/5) and RP_217 (7/7); RP_198 8/12, RP_215 6/14. The
three batch 7-8 harness bugs are fixed: RP_197 declines Sphere's tax (epoch 62), RP_198's payment sacrifice is the
seat's (epoch 62), RP_209's suspend decline is asked once and is no longer "multiple" (epoch 62).

## Harness and prover findings

1. **The seat was offered plays a static forbids** (RP_207, fixed in epoch 63). On epoch 62 RP_207 read "multiple":
   the search's first rollout activated Endbringer under a Pithing Needle naming it, and "ping, then mutate" was a
   second line (`rp10/keep/RP_207-search-e62`). The Needle did name exactly Endbringer (epoch 62's NamedCard restore
   works; `PuzzleSetupTest`). Forge's `canPlay()` leaves out the statics that forbid a play, which it checks only as
   a play is made (`checkRestrictions`), and the menu never asked. This is not a puzzle-only bug: Pithing Needle, Null
   Rod, Gaddock Teeg, Rule of Law and Grafdigger's Cage were all open to the seat in real games. Epoch 63 asks it the
   way Forge's AI does (a spell as an LKI copy on the stack cast from its zone; asked about the card in hand, Drannith
   Magistrate would hide every hand spell), and checks again once the card is on the stack. RP_207 on epoch 63 is
   proven and unique, and its ping comes from the merged Pouncing Shoreshark.
2. **A face-down cast always costs {3}** (RP_217 v1): with two Forests, Zoetic Cavern's face-down cast was never
   offered and the search exhausted in 6 rollouts. Not a harness bug; the board was wrong (morph {2} is the cost to
   turn it face up).
3. Process: the two gate chains waited forever on their first start because `gate10.sh` counted any process whose
   command line held its JVM pattern, the launching shells included; it now counts `java` processes only.

## Board notes

- L61 is RP_214 and L425 is RP_215 (drafted in the old container). L292 and L477 are RP_214's rule (the top card of a
  merged permanent decides face up or down, and with the face-down card on top there are no abilities and no mutate
  trigger). L500 (Samurai of the Pale Curtain exiles a dying manifest) has no solver choice; RP_215 is its converse.
- Face-down acceptance: none of these boards names a face-down permanent, so revealing face-down cards on a loss
  (`Game.onPlayerLost`) cannot decide them.

# Batch 11: the rest of the backlog (74 maybe and 221 no questions)

Source: every `triage.jsonl` question that still had no row after batch 10. 295 rows; `manifest.jsonl` now has 996,
one per triage question (199 converted, 10 Forge disagreements, 8 multiple-review, 779 dropped, no harness bug left).

## Counts

| triage | result | n |
|---|---|---|
| maybe | converted (RP_216, its own question) | 1 |
| maybe | dropped: rule-level near-duplicate, the puzzle named | 62 |
| maybe | dropped: no solver choice | 9 |
| maybe | dropped: replacement-order question (Forge never offers the order) | 2 |
| no | dropped: legality question | 81 |
| no | dropped: unobservable in the final state | 62 |
| no | dropped: multiplayer or commander | 54 |
| no | dropped: procedure outside a position | 12 |
| no | dropped: hidden or random information | 6 |
| no | dropped: random, a missing card (Zethi) or no observable difference | 6 |

**The maybes.** Most had been named as near-duplicates in batches 5 to 9's reports without a row; each row now names
the converted puzzle with the same rule (layer 6 timestamps RP_152, CR 614.12 enters-with checks RP_172, LKI power
RP_015, a draw on an empty library RP_016, double strike per damage step RP_075, and so on). The nine with no solver
choice are the new-mechanic questions whose outcome follows with nothing to decide (offspring, gift, Aveline,
Rayblade) and becoming a copy.

**The no's.** Each row gives its bucket's reason with the rule. A legality question ("crew can't be activated from the
graveyard") cannot be a puzzle here: the engine offers only legal plays, so no menu option expresses the misread and
the board would test Forge's legality check, not a choice. An unobservable one leaves the same final state under both
readings, so no acceptance state tells them apart. Multiplayer and commander questions need more than the two-player
format; procedure questions (Karn's restart, companions, mulligans) have no position to start from.

No board was built in this batch, so nothing was gated; the rows were written from the triage and the converted
puzzles (`verified_on` names epoch 63, the build in use).

# Batch 12: legality and "unobservable" drops re-screened (RP_218 to RP_222)

Batch 11 dropped 81 legality and 62 unobservable questions on their bucket's reason. That reason holds for a pure "can
I do X" question, but not when a misread makes the solver skip a legal play or take a worse one. All 143 were read
again with their source question and answer. Five have that shape and became boards; the rest keep their rows, since
the misread either costs nothing (the right line stays open to a misreader: Gaddock Teeg and Conflagrate, a Treasure
for escape, Goblin Engineer, Seal of the Guildpact) or leaves no trace in the state. Build: epoch 64
(`epoch64-build.txt`); gate as before.

## Counts

| puzzle | triage | rule | uniqueness |
|---|---|---|---|
| RP_218 | L724 | suspending is not casting, so Rule of Law still allows Lightning Bolt | exhaustive 6/6 |
| RP_219 | L716 | under Painter's Servant the colourless Diary of Dreams pays Snapback's "exile a blue card" | exhaustive 2/2 |
| RP_220 | L979 | a noncreature artifact taps the turn it arrives (Mind Stone pays for Signal Pest) | exhaustive 6/6 |
| RP_221 | L588 | Fire cast alone is red, so Iona naming blue does not stop it | exhaustive 10/10 |
| RP_222 | L973 | a card can be suspended with no legal target for when it is cast | exhaustive 2/2 |

Every board: search proven and accepted, pass arm rejected, Forge agreeing with the judge. `manifest.jsonl`: 204
converted, 10 Forge disagreements, 8 multiple-review, 774 dropped.

## Harness and prover findings

1. **A divided spell's split could give a target 0** (RP_221, fixed in epoch 64). On epoch 63 RP_221 read "multiple":
   Fire at the opponent and Iona was split `[2 0]` (`rp10/V_iona-search`), which CR 601.2d forbids (each target at
   least 1; triage L489 is that rule). The seat's and the prover's splits now hold it, the question says so, and a
   divided spell takes no more targets than its total (review: Rolling Thunder for 2 at three targets fell back to
   `[1 1 0]`). On epoch 64 RP_221 is unique, exhaustive.
