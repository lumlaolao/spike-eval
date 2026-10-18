# Rules pilot: first 20 triage conversions

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
