# Game loop log

One entry per match played under `.claude/skills/game-loop`: what the sheet and the reading found,
what was fixed, what is open. Newest last. Read the tail before playing.

### block of 2026-10-13  seeds 50-52  Mardu Energy (seat) vs Mardu Energy (Forge AI)  10 games started, 6 finished  $0.35  build a76bd78d6421

Played as two arms of a small block before the loop was one game at a time; read game by game.

- FIXED  One creature that can attack was attacked with, unasked (`attackers(forced-only-legal)`):
  TAKEN x6 in seed 50 swap 1 alone, the seat's Guide of Souls every turn. Test
  `aSingleCreatureThatCanAttackIsTheModelsDecisionNotAForcedAttack`. Commit 37cff95a.
- FIXED  Scry 1 / surveil 1 kept on top, unasked. Same cause: `n_options==1` read as "forced" for a
  subset decision. Test `aSingleSurveiledCardIsTheModelsDecision`. `defect_analyzer.py` no longer
  calls the correct behaviour a mislabel.
- FIXED  The retry of a play decision carried no state and no menu. Seed 50 swap 0 turn 6: the model
  used both tools searching its archive for the question; LOST after 56 s. Also seed 51 turn 12 (a
  PASS on its own main phase) and seed 51 turn 7. Test
  `theRetryOfAPlayDecisionCarriesTheStateAndTheMenuAgain`.
- FIXED  With its calls spent the model wrote call markup as text and the ask returned nothing. It
  is told once that the tools are spent. Test
  `aSeatThatWritesCallMarkupAfterItsToolsAreSpentIsAskedOnceMoreAndAnswers`.
- FIXED  Tool rounds were recorded as failed attempts (`first_failed_reply`, `retry_ms`), so a fifth
  of decisions looked retried.
- OPEN   The seat asks to preview `PLAY LAND` and `PASS` (bad-args: 13 and 6 of 92 requests in
  runs/lines/ctl2/native-lookahead). A line that starts with a land drop cannot be asked for.
- OPEN   `mulligan(heuristic-keep n/7 lands)`: the harness keeps 2-5 land hands without asking.
  The loop now plays with `MULLIGAN_HEURISTIC=0`; whether the default should change is undecided.
- OPEN   Smart-tier replies cut off at 4000 tokens (`truncated reply` x5 to x8 a game): the budget
  goes on reasoning and the retry costs 20-55 s. Not yet read closely.
- OPEN   `inspect "Cat Warrior Token"` finds nothing: a token has no card-database entry, and the
  model asked by name instead of by handle.

### g01  2026-10-13  seed 53  Mardu Energy (seat) vs Burn (Forge AI)  won T10  $0.03  build 4b4ecc16

Sheet: TAKEN 0, LOST 0, TOOL 0 (3 requests, all served). The fixes of the block held: the mulligan,
both surveils and every attack were asked of the model.

- FIXED  **A preview asked the model to play a game that did not exist.** Read in the log: before
  each `lookahead` line the seat was asked `attackers: 2 can attack` and `confirm-trigger [Guide of
  Souls]`, in its main phase, and an `[OBSERVATION]` banner appeared each time (8 in a one-seat
  game). The engine scores a previewed copy by simulating the coming combat on a second copy, and
  that copy's seat was built with the full model controller. The calls were real (money, 10-25 s a
  preview) and their answers were written to the decision log as attackers, confirm-trigger and
  damage-assign rows (7 attacker rows in a game with 5 attack steps). Every player the engine
  creates during a preview is now an engine-AI player. Test
  `everyPlayerCreatedDuringAPreviewIsCreatedInsideThePreview`; the `lookahead` acceptance stage
  fails on a second model controller; `match_review.py` reports it under LOG.
  Consequence for earlier numbers: decision counts, model calls per turn and cost in every
  `LOOKAHEAD=1` arm so far include these phantom decisions.
- clean  Turn-2 discard looked wrong (8 cards after a land drop) and was not: Goblin Guide's trigger
  had put a land in the seat's hand.
- OPEN   One smart-tier reply was cut off at 4000 tokens on a five-option turn (26 s).

### g02  2026-10-13  seed 54  Death & Taxes (seat) vs Grixis Reanimator (Forge AI)  won T12  $0.02  build b8bf473c

Sheet: TAKEN 0, LOST 0, SLOW 0. A decisive win: the seat kept a 5-land hand, played Stoneforge Mystic
into Batterskull, Wastelanded their Underground Sea, then attacked for lethal. The two TOOL rows were
the whole of the reading.

- FIXED  `simulate` refused a PLAY LAND option, so the seat could not preview the line it kept asking
  for (TOOL bad-args, line 14, T6): it called `simulate {"option":4,"then":[3]}` — PLAY LAND Wasteland,
  then Stoneforge Mystic's ability — and got "`option` must be the number of a CAST or ACTIVATE option
  (1..3)". The tool's numbers are now the decision's own menu numbers (CAST/ACTIVATE then PLAY LAND;
  PASS is not previewable), and a land drop may start a line, played on the copy so it can pay for a
  later step. 13 of 92 preview requests named a PLAY LAND option in runs/lines/ctl2/native-lookahead
  and all were refused. Test `aLineThatStartsWithALandDropIsPreviewed`. Stamps: seat-tools-v3,
  lookahead-preview-v3.
- OPEN   the other half of item 1 is unchanged: a spell that was not already on the menu when the land
  is played (the spell the land enables) still cannot be named in one call, so "land, then that spell"
  is only previewable if the spell was a menu option at the time of the decision.
- FIXED  the land preview first went through `GameSimulator.simulateSpellAbility` and the copy's land
  branch recursed with the LIVE ability, so the AI picker replayed it and the heap ran out
  (acceptance nativetool stage, 2 of 19 previews, OutOfMemoryError in `Spell.<init>`). The copy's own
  land ability is played directly instead.
- OPEN   the same turn's other tool call, `memory_search "ForgeAI deck"`, returned Matches=0 (line 15,
  T6). The opponent's deck identity is hidden information and not in the seat's archive; a word-split
  fallback would return the entries that mention "ForgeAI". Low; not fixed.
- OPEN   one smart-tier reply was cut off at 4000 tokens on the T6 five-option turn, retried with
  reasoning disabled: 66 s + 20 s on that one decision (log line 57). Open item 3, unchanged.
- NOTE   plan_audit reports one HOLD-VIOLATION at T6 and it is a false positive: the clause "play
  Wasteland and hold Swords to Plowshares" holds Swords, and the splitter does not split on "and".
- clean  mulligan (asked, 5 lands kept), all three attack steps, targets, the served Wasteland preview.

### g03  2026-10-13  seed 55  Goblins (seat) vs UR Delver (Forge AI)  lost T14  $0.05  build 5ae9bd3b

Sheet: TAKEN 0, LOST 0, SLOW 0; one TOOL row. Two flipped Delvers and a Bolt closed it out; the
seat's own line (Aether Vial, Goblin Matron into Krenko, Wasteland on a Volcanic Island) was sound.

- FIXED  the seat's `simulate {"option":3}` on a menu whose 3 was PASS was refused, and the refusal
  was counted as a form `TOOL REFUSED` because the message shipped in #86 opens with a backticked
  argument name ("ERROR: `option` ..."), which `tool_audit.FORM_ERRORS` claims. A call naming a menu
  option the preview does not cover is a bad argument, not a form refusal. Test in
  `test_tool_audit.test_a_refusal_is_about_form_not_about_what_the_tool_found` (runs/loop/g03:211).
- FIXED  `plan_audit` reported a HOLD-VIOLATION at T9 that was a false positive, the second in two
  games (g02 T6 was the first). The clause splitter did not split "play Wasteland and hold Swords",
  so the hold on Swords was charged to Wasteland; and a bare "later" ("use it later") counted as a
  hold. It now splits an `and` that joins two verbs, keeps an `and` that joins two objects of one
  hold, and no longer treats "later" alone as a hold. Test
  `test_plan_audit.test_a_play_the_plan_endorses_is_not_a_hold_violation`. On the recorded arms
  (`eval/plan_audit.py runs/lines/ctl2/<arm>`): textv2 12 -> 7 of 131, native 18 -> 15 of 173,
  native-lookahead 10 -> 7 of 162; every case removed in `native` was the same "cast X and hold Y"
  false positive.
- OPEN   item 1's other half, PASS: the seat asked to preview PASS (the tool says PASS cannot be
  previewed; 6 of 92 requests in runs/lines/ctl2/native-lookahead, 1 here). Whether the harness should
  answer "PASS: no change" or keep refusing is a call for the owner.
- NOTE   engine noise, not the harness: `X Did not have activator set in SpellAbilityRestriction.canPlay()`
  (Force of Will, Scalding Tarn, Delver of Secrets) is the ForgeAI enumerating its own hand, and the
  engine self-heals by setting the activator. 4 here, 1 in g01, 0 in g02.
- clean  mulligan (asked, 5 lands kept), every land drop, the Vial / Matron / Krenko sequence, the
  Wasteland target, all attack steps.
