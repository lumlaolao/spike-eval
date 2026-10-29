# Benchmark sample v1: GPT-6 Luna, lookup tools off and on (2026-10-20)

The owner's plan (`docs/harness/WORK-PLAN.md`, "The plan", step 2): Luna on a sample of the rules puzzles, with the
tools off and on, to see whether the rules lookup changes the solve rate.

## Setup

- Suite: `eval/suites/puzzles-rules-sample-v1.json` (suite_sha256 `b652e12c4452`), frozen from the 36-puzzle
  sample `eval/puzzles/rules-pilot/benchmark-sample-v1.tsv` after the free baselines (`puzzle_baselines.py run`,
  50 random rollouts and the pass, first, floor and ai arms, harness-epoch-66). 7 were dropped because random play
  solved them more than 20% of the time (RP_041, RP_079, RP_194, RP_242, RP_268, RP_279, RP_487); 29 are active
  (9 tier 1, 19 tier 2, 1 tier 3).
- Model: `openrouter:openai/gpt-6-luna`, k=5 attempts a puzzle (seeds 1-5), `-j 2` per condition, both conditions at
  once.
- **Off:** no lookup tools (the answer tool and `set_plan` only). **On:** `--memory-tools --rules-tool`
  (`inspect`, the memory tools, `rules_lookup`). Each game log of the on arm shows `[RULES] rules-tool-v1 wired
  (3163 rules, 741 terms, effective September 25, 2026)`.
- Results: `results/off/` and `results/on/` here (each puzzle's `puzzle-results.jsonl` and `batch-manifest.json`);
  the game JSONL was in `runs/puzzles/bench-v1/` (git-ignored). Tool counts: `tool-use.txt`, written by
  `python3 eval/puzzles/puzzle_tool_use.py runs/puzzles/bench-v1`.

## Results

| | Off | On |
|---|---|---|
| attempts solved | 130/145 (89.7%) | 130/145 (89.7%) |
| PUZ headline, mean(pass@1 - random rate) | 0.809 [0.709, 0.888] | 0.809 [0.717, 0.883] |
| puzzles solved (c >= k/2) | 26/29 | 26/29 |
| pass^k (all 5 attempts) | 0.793 | 0.793 |
| `rules_lookup` calls | none offered | **0** in 145 attempts |
| other lookups | none offered | `inspect` 40, `report_unclear_decision` 1 |
| cost | $0.18 | $0.20 |
| median model time a call | 4.3 s | 4.6 s |

Floors (`puzzle_score.py`): pass@1 random 0.088, PASS 0.000, first option 0.207; Forge's AI solves 0.302.

## What it says

1. **Luna never looked up a rule.** With the lookup offered on every decision, it called it 0 times in 145 attempts
   (and `inspect` 40 times). So this sample cannot measure what the rules lookup is worth: the two arms differ
   only in tools Luna did not use for rules.
2. **The sample is near Luna's ceiling.** 90% of attempts solve, and 26 of 29 puzzles are solved in both arms.
   The five puzzles where the arms differ (`tool-use.txt`) had no rules lookup in either arm: that spread is run-to-run noise (the opponent AI's and the
   model's own randomness), up to 3 solves in 5 on one puzzle.
3. **Found on the way:** `--rules-tool` without `--memory-tools` recorded `RULES_TOOL=1` but never offered the tool
   (the pilot, `runs/puzzles/bench-pilot/on`). The launchers now refuse that combination (PR #272).
4. **Found on the way (fixed by epoch 67, below):** the seat is told Forge's goal line (`Puzzle.getGoalDescription`: name, `Difficulty`,
   `Goal`, turn limit, description). 148 of the 526 converted puzzles say `Goal:Survive` (the turn bound, `docs/eval/PUZZLES.md`)
   while their acceptance and description ask for the opponent at 0 life, so the prompt reads "Goal: Survive" above
   "Reduce the opponent to 0 life". In this sample Luna still solved 90%; RP_443 drew the one `report_unclear_decision`
   (it misread which creatures could attack, not the goal).

Next, to make the comparison mean something: harder puzzles (tier 3 and the puzzles Luna fails here), and a look at
why Luna does not reach for the lookup (the tool description, or a prompt that tells it when a rule decides the
puzzle). Both are owner decisions.

## Epoch 67: the same sample with a neutral brief (2026-10-20)

harness-epoch-67 (PR #273) tells a rules puzzle's solver only its author's description and turn limit, on the
`<puzzle>` block and on the Puzzle Goal card that `inspect` reads. Forge's Name ("Rules pilot 243 - The trigger
outlives its source"), Difficulty and "Goal: Survive" lines are gone; each result row's `goal_oracle` shows the
brief the seat got. Everything else is as above: same suite, model, k=5, seeds 1-5, `-j 2` a condition, both at
once, isolated build `runs/puzzles/builds/e67`. Results: `results-e67/off/` and `results-e67/on/`; game JSONL in
`runs/puzzles/bench-v1-e67/` (git-ignored); tool counts `tool-use-e67.txt` (`eval/puzzles/puzzle_tool_use.py`).

| | Off, epoch 66 | Off, epoch 67 | On, epoch 66 | On, epoch 67 |
|---|---|---|---|---|
| attempts solved | 130/145 | **119/145** | 130/145 | 127/145 |
| PUZ headline, mean(pass@1 - random rate) | 0.809 [0.709, 0.888] | 0.733 [0.608, 0.841] | 0.809 [0.717, 0.883] | 0.788 [0.691, 0.867] |
| puzzles solved (c >= k/2) | 26/29 | 24/29 | 26/29 | 26/29 |
| pass^k | 0.793 | 0.690 | 0.793 | 0.724 |
| `rules_lookup` calls | none offered | none offered | 0 | **0** |
| other lookups | none offered | none offered | `inspect` 40, unclear 1 | `inspect` 47, unclear 2 |
| cost | $0.18 | $0.17 | $0.20 | $0.20 |

Paired by puzzle (10,000 bootstrap resamples of the 29 puzzles, 95% interval), change in pass@1; written by
`puzzle_score.py --paired-with` (puzzle ids sorted, seed 7; the last row is `results/on --paired-with
results-e67/on --against results/off results-e67/off`), for example, from the
repository root with `D=eval/puzzles/rules-pilot/benchmark-v1`, `python3 eval/puzzles/puzzle_score.py --suite
eval/suites/puzzles-rules-sample-v1.json --split open --k 5 $D/results/off --paired-with $D/results-e67/off`:

| comparison | mean | interval |
|---|---|---|
| off, epoch 67 minus epoch 66 | -0.076 | [-0.145, -0.021] |
| on, epoch 67 minus epoch 66 | -0.021 | [-0.069, 0.021] |
| epoch 67, on minus off | +0.055 | [0.007, 0.110] |
| epoch 66, on minus off | 0.000 | [-0.069, 0.069] |
| on drop minus off drop | +0.055 | [0.000, 0.124] |

What it says:

1. **The name was a hint.** Without the tools, Luna solves fewer attempts once the name is hidden, and the interval
   excludes zero. The largest drop is RP_243 (5/5 to 2/5 off, 5/5 to 3/5 on), whose name states its trick: every
   failed attempt but one let Awoken Horror's transform trigger bounce Grizzly Bears. RP_151 ("Count the snow first") and
   RP_385 ("The copy keeps haste, not counters") fell too. The earlier 130/145 overstated what Luna can do.
2. **With the tools on, the drop is smaller, but not separably so:** the on arm's drop minus the off arm's is
   +0.055 [0.000, 0.124]. On minus off is now positive. Luna still made 0 rules
   lookups; the difference is in `inspect` (47 calls) and the arm's own noise. Three puzzles flip 2 or more solves
   in 5 between the epoch-66 off and on arms (tools offered against not; no rules lookup made in either), so one
   sample of 29 does not separate these from noise well: the "on minus off" interval barely clears zero and is one
   of five comparisons made here.
3. The two `report_unclear_decision` calls came from RP_385 and RP_443.

Next is `docs/harness/BENCHMARK-V2.md`: measured tiers and harder puzzles, since 24 of 29 still solve with no tools.
