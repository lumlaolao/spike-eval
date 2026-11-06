# Failures the interface's own words invite: a lethal clock after the last combat, and a yield that says more than it does (2026-11-06)

**Question.** Reading the labelled drain regret steps (`2026-11-06-where-models-lose-drain.md`), several failing
seats explained a pass with something the harness had told them. Two lines are the harness's own text, not the
board's. How often does a seat act on each one, and against what baseline?

**Answer.** Both statements are wrong in the situation where seats act on them. Both are now issues:
- **#398, a fix on branch `fix/puzzle-facts-turn-limit` (not yet merged).** The FACTS line's `your next combat: ... = LETHAL`
  appeared on a 1-turn puzzle's last turn after combat in 311 games. Seats there cited a later combat in 10.6% of
  games, against 0.9% where the clock was not lethal and 0% with no clock.
- **#399, filed.** The `yield` field says "your own main phases always ask too". Under a yield, the harness passes
  the seat's own main phase whenever its own items are on the stack: 638 windows in 49 games.

## What was read (free: Python over the local game JSONL, no JVM, no model)

The input is every `game-*.jsonl` under the main checkout's `runs/puzzles/`: 2828 puzzle games, all models, harness
epochs 70 to 72 where recorded (1448 of the 2161 counted below at 70, 584 unrecorded). Conditions are mixed, so the rates below are descriptive. A per-condition split is possible but was
not made.

### The clock after the last combat (#398)

The decisions read are those on turn 1 in `MAIN2` or `END_OF_TURN` (2161 of the 2828 games have one; the other 667
have no decision after combat and are in no group). On the hard 1-turn boards (`Turns:1`), the
solver's last combat is over there. A decision "cites a later combat" when its `raw_reply` matches
`next combat|next attack|next turn|combat estimate|attack for lethal|lethal (in|next) combat|future turn|following turn`.

| The solver's FACTS clause in those decisions | Games | Cite a later combat | Wilson 95% |
|---|---|---|---|
| `your next combat: ... = LETHAL` | 311 | 33 (10.6%) | 7.7% to 14.5% |
| a clock, not lethal | 577 | 5 (0.9%) | 0.4% to 2.0% |
| no clock (no attacker) | 1273 | 0 (0.0%) | 0% to 0.3% |

32 of the 33 games that cited a later combat failed. This is an association, not a measured cause: a lethal clock
needs attackers, so the three groups are different boards. The regex finds only the stated reason, so 33 is a lower
bound. The replies are explicit. One read "the next combat is lethal per the state's combat estimate" with the
opponent at 4 and the puzzle ending at that cleanup.

**Was the position still winnable when they cited it? In drain, no.** 31 games cited a later combat at a decision
whose own prompt read LETHAL. 18 are drain attempts the exact board model (`board_drain.py`) labels. In all 14 that
map to a labelled position, the position was already lost before the citing pass, which came after the attempt's
regret step. The other 4 did not map, and the 13 non-drain games have no model. So the line explained passes in
positions already lost. No loss in drain is shown to come from it.

The fix reads the last turn off the Puzzle Goal card's trigger, and a side with no combat left reads `none before the
puzzle's turn limit`. Checked zero-model: on the fixed build, the written line of `drain2_eaca60c560dc` solved (script
arm, seed 1). All 50 of its post-combat decisions read `your next combat: none before the puzzle's turn limit | their
next combat: none before the puzzle's turn limit`.

### The yield's promise (#399)

The window rows read are those with `reason` `yield:*`, `active: true`, phase `MAIN1`/`MAIN2`, `stack > 0` and
`castable > 0`: 638 windows in 49 games, 46 of them puzzles (23 solved, 23 failed) and 3 demo games. No harm is
shown: the solved and failed games split evenly, and there is no comparison group.

One adjacent case: a yield of `end-of-turn` made in the end step lasts the rest of that turn. A seat yielded there
with "Let the stack resolve the three drain triggers before doing further sacrifices", expecting to be asked again,
and was not.

## What this suggests (not tested)

- Text the harness labels as engine fact carries weight with the seat. A line that is true in a game and false in a
  puzzle needs the puzzle's frame (its last turn) wherever the seat reads it. The same check applies to any other
  FACTS or estimate line.
- After #398 lands, the citation rate in the LETHAL group should fall toward the 0.9% group. The drain check above
  predicts little change in the solve rate. Only paid runs at the new epoch would show it (PUZ conditions never pool
  across the epoch change).

## Not determined

- Cause: neither issue has a controlled comparison. A paired run of the same boards at epoch 87 against 88 would give
  one for #398.
- Per-model rates: the groups pool every model and epoch, so they describe the corpus, not a model.
