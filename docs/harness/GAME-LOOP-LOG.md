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
