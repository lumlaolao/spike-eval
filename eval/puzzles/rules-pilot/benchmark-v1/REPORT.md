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
  the game JSONL was in `runs/puzzles/bench-v1/` (git-ignored). Tool counts: `tool-use.txt`.

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
   The puzzles where the arms differ (RP_017 2/5 vs 0/5, RP_098 4/5 vs 5/5, RP_443 1/5 vs 2/5, RP_477 0/5 vs 3/5,
   RP_529 5/5 vs 2/5) had no rules lookup in either arm: that spread is run-to-run noise (the opponent AI's and the
   model's own randomness), up to 3 solves in 5 on one puzzle.
3. **Found on the way:** `--rules-tool` without `--memory-tools` recorded `RULES_TOOL=1` but never offered the tool
   (the pilot, `runs/puzzles/bench-pilot/on`). The launchers now refuse that combination (this PR).

4. **Found on the way, open:** the seat is told Forge's goal line (`Puzzle.getGoalDescription`: name, `Difficulty`,
   `Goal`, turn limit, description). 148 of the 526 converted puzzles say `Goal:Survive` (the turn bound, `eval/PUZZLES.md`)
   while their acceptance and description ask for the opponent at 0 life, so the prompt reads "Goal: Survive" above
   "Reduce the opponent to 0 life". In this sample Luna still solved 90%; RP_443 drew the one `report_unclear_decision`
   (it misread which creatures could attack, not the goal). Fixing it changes what the seat sees: a version bump and a
   re-run of this sample (about $0.40, 45 minutes).

Next, to make the comparison mean something: harder puzzles (tier 3 and the puzzles Luna fails here), and a look at
why Luna does not reach for the lookup (the tool description, or a prompt that tells it when a rule decides the
puzzle). Both are owner decisions.
