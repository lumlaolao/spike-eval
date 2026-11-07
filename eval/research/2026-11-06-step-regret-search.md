# Step regret on four failed splice attempts: the runner works, the exact search does not reach the step (2026-11-06)

**Question.** Idea 2 (`2026-11-05-novel-ideas.md`) labels a failed puzzle attempt with its regret step: the first
model decision after which the puzzle is no longer winnable, found by replaying the recorded attempt to decision K
(`ReplayBridge`, #365) and handing the rest of the game to the exact search arm. Can that label be computed today?

**Answer: not yet.** The replay and handover are exact (72 searches, 0 drift), but on all 4 attempts the search
leaves the middle of the attempt undecided: no attempt got an exact step, one got a bracket (K 4 to 10), and the floor
(the share of options at the step that keep the win) was computed for none.

## What was run (free, zero-model replay of recorded attempts)

Branch `feat/step-regret` (commits 6bae8ec, 6a02a38, d37da4c), overlay `c57ec70bb79d`, `replay-bridge-v2`.
`eval/puzzles/step_regret.py` searches the position before each model decision from the end until one is proven;
budget 600 rollouts, the boundary re-searched at 2400, Forge AI opponent, each attempt's recorded seed. Rows:
`runs/step-regret/{A-low-k1,C-low-k3}/step-regret.jsonl` (git-ignored). Per attempt dir:

```bash
python3 eval/puzzles/step_regret.py <attempt dir> --out runs/step-regret/<A-low-k1 or C-low-k3> --budget 600 \
  --refine-budget 2400 --refine-most 2 --jobs 2 --resume
python3 eval/puzzles/step_regret.py --summary runs/step-regret/A-low-k1 runs/step-regret/C-low-k3
```

| Attempt | Model decisions | Status | Last proven K | First unwinnable K | Searches | Search time |
|---|---|---|---|---|---|---|
| A seed 1 | 16 | never proven | none | 11 | 19 | 70 min |
| C seed 1 | 15 | bracket K 4 to 10 | 4 | 11 | 16 | 60 min |
| C seed 2 | 14 | never proven | none | 10 | 17 | 101 min |
| C seed 3 | 17 | never proven | none | 12 | 20 | 133 min |

Verdicts over all searches, each counted once (`--summary`; C seed 1's K=8 and 9 each hold one search written twice):

    searches 72: proven 1, unwinnable 30, not_found 41 (38 at 600, 3 at 2400), drift 0, error 0, refused 0

Every unwinnable search was exhausted with 0 capped rollouts. (An earlier version of this note said 28 unwinnable, a
hand tally that summed to 70.)
Every position more than about 7 decisions from the end was not found: the search proves late positions lost but
cannot settle the 12-plus-step middle, where the attempt was lost. About 6 CPU-hours.

## What the board model says (a cross-check, never the verdict)

`puzzle_gen.splice_best` puts the loss, in all four games, at casting the second Desperate Ritual before a Lava
Spike can carry it as a splice (the damage reachable before and after it: `2026-11-06-step-regret-board-model.md`). At that
decision only PASS keeps the win in A and C seed 2 (1 of 4 options), and either Lava Spike in C seed 1 (2 of 6,
assuming PASS loses). C seed 3 has A's menu; its state was not recomputed.

## What would settle it

- A cheaper proof that a position is lost: the board model as a sound bound (it is exact for splice boards), or a
  transposition table in the search.
- Bisection at a much larger budget at the bracket ends only, since the late positions are already decided.

## Not determined

- Whether the handover is exact inside multi-step hooks (it switches inside row K's write; single-choice decisions
  and plays are covered by `StepRegretTest`).
- The 72 searches ran on `replay-bridge-v2`, before each rollout checked its first decision after K against the
  recording's row K+1 (`replay-bridge-v3`); they were not re-run. The board model's 31 of 31 agreement is the only
  cross-check of them.
- Any board family other than splice.
