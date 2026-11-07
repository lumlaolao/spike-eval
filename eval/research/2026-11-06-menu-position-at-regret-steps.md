# Menu position at the regret step: no sign that the losing pick leans toward the top (2026-11-06)

**Question.** Language models are known to favour options by where they sit in a list. At the regret steps already
labelled (splice: `2026-11-06-where-models-lose-splice.md`; drain: `2026-11-06-where-models-lose-drain.md`), did the
seat's losing choice sit earlier on the menu than a uniformly random losing choice would?

**Answer: no detectable lean, and this data cannot settle it.** Given that the seat picked a losing option, it picked
the earliest losing option 23 times in 34 drain steps against 19.9 expected by chance, and 18 times in 55 splice steps
against 22.0. The two families point in opposite directions, and both are within a few counts of chance. The menu order
is fixed per board (casts, then activations, then PASS), so position and content are confounded. An option-order
shuffle arm is the experiment that would separate them; it is described below and not built.

## What was read (free: Python over recorded rows, no JVM, no model)

Command: `python3 eval/research_stats.py menu <board.jsonl> [...]` (tested in `eval/tests/test_research_stats.py`).
**The drain row is reproduced here exactly** (34 matched of 46, 23 against 19.9, 18 against 16.5, 13 top picks
against 7.0, mean position 0.42 / 0.52, 2 steps with several matching rows). **The splice row is not reproducible
here:** 14 of its 60 steps name a game file that is not on this machine (the Haiku terse and thinking-vs-sampling
runs), so its figures below (55 matched, 18 against 22.0, 40 against 38.1, 14 top picks against 11.5, mean position
0.41 / 0.73) come from a one-off run whose inputs are not all kept. On the 46 steps the script can read it gives 43
matched, 15 earliest-losing against 17.6, which is consistent with no lean.

The inputs are the board-model rows from the two notes above: drain `runs/where-lose-drain/board.jsonl` (68
attempts) and splice `runs/where-lose/board.jsonl` (119). Both directories are git-ignored and were read in the
worktrees that wrote them. Each row with a `floor` (106 regret steps) gives the seat's pick (`model_label`) and,
for each option at the step, whether the board model says it keeps the win.

The menu order comes from the attempt's own game JSONL (`source_game`). That is the `decision` row whose `options`
contain every option the floor names and whose integer `choice` is the model's pick. Where several rows match, the
one nearest `regret_k` is used (this happened in 2 drain steps). 89 of 106 steps matched (34 drain, 55 splice).
The other 17 (12 drain, 5 splice) were not matched: for these steps no decision row has every floor option and a
single integer pick, which is likely the multi-pick sacrifice and CANCEL questions. They were not read further.
Every matched step had at least one losing option, and menus ran from 2 to 9 options.

## Results (per family, never pooled)

| Family | Steps | Picked the earliest losing option | Chance (uniform over losing options) | Picked before every kept option | Chance | Mean position of pick / of kept options (0 = top, 1 = bottom) |
|---|---|---|---|---|---|---|
| drain | 34 | 23 | 19.9 | 18 | 16.5 | 0.42 / 0.52 |
| splice | 55 | 18 | 22.0 | 40 | 38.1 | 0.41 / 0.73 |

The models: drain is 33 openrouter attempts and 1 jev; splice is 40 openrouter, 14 claude-cli (dev lane, never a
score) and 1 jev. These are different models at different epochs. The table is descriptive only.

What the table shows. Against the naive baseline "uniform over all options", the seat picks the top option more often
(drain 13 against 7.0; splice 14 against 11.5). But the winning options sit low on these menus: in splice the
last kept option is PASS in 36 of 55 steps, and in drain a Swamp's mana ability in 19 of 34. So a losing pick is
early because the losing options are early. Conditioning on the pick being a loss removes that effect, and what is
left is near chance.

## What would settle it (not built)

The seat sees each decision's options in a fixed order. An exploratory arm could shuffle that order per decision with
a seeded permutation (the `PuzzleArms` subset pick already shuffles for its own candidates). The arm would record the
permutation in the row and map the model's index back before the engine sees it. Paired with the fixed-order arm on
the same puzzles, the difference in solve rate is the position effect; a per-step pick histogram over permuted
positions separates it from content. Paid games are needed for this: price the plan with `eval/cost_report.py --plan`
first. Whether to run it is the owner's decision. The arm
changes what the model sees, so it is its own condition with a version stamp, and never a score.

## Not determined

- Whether any single model has a position bias: per model, the samples are 1 to 40 steps.
- The 17 unmatched steps.
- Positions away from the regret step: the board model labels them winnable or not, but kept and losing options are
  labelled only at the step.
