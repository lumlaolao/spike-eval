# Where models lose won splice puzzles: every recorded attempt, the splice board's model (2026-11-06)

**Question.** Across every recorded model attempt at a splice-family puzzle on this machine, where do models lose a
puzzle they could still win? For each failed attempt: does the splice board's model
(`eval/puzzles/step_regret_board.py`, `2026-11-06-step-regret-board-model.md`) cover it, and if so, which decision is
the regret step (the last position from which the puzzle is still winnable, before the first lost one), what kind of
decision it is, what the seat chose there, its floor (the share of legal options there that keep the win, which is
what a uniformly random choice at that step keeps), and how far it is from the end. As a check of the model, every
solved attempt must be winnable at every position.

**Answer so far: almost always the same mistake, early, at a narrow gate; model-derived, not engine-proven.** Of 119
recorded model attempts (21 models, Haiku 5.5 in two lanes; split by model, epoch and run group), 15 solved and 104
failed. The model labels 76 (61 failed, 15 solved), refuses 42 (all Luna epoch-70 attempts at splice2 boards holding
cards outside the model) and has nothing to label in 1 (no model decision). All 15 solved attempts are winnable at
every position (0 model errors). In the 61 labelled failures:

- **46 of 61: Desperate Ritual cast on its own while the winning line still splices it** (onto a Lava Spike or
  Glacial Ray, net +1 mana and the card stays in hand). In 21 a carrier was castable at that moment; in 25 the
  Ritual was cast in response (stack not empty), where the only winning options were to let the stack resolve.
- **10: a one-mana burn spell cast in response** (Shock 8, Lightning Bolt 1, Glacial Ray 1) to a pending Ritual,
  spending the mana the winning line's next splice needed; the winning line casts that spell last.
- **3: PASS with Thermo-Alchemist untapped over a pending untap trigger** (a ping lost). **2: at a splice question,
  Glacial Ray carried where carrying Desperate Ritual wins.** No regret step is a target choice or a pass with
  lethal on board.

The step comes early: median model decision 3 (range 0 to 9), 21% of the way through the attempt (IQR 15% to 27%),
followed by a median 12 more decisions (IQR 10 to 15, minimum 5); only 3 of 61 steps are within 7 decisions of the
end, which is why the exact search, which decides only the last 7 or so, found none. The floor averages 0.37 (median
1/3, range 1/6 to 6/7): a uniformly random choice at the step would keep the win about a third of the time. Per
condition, only Luna epoch 70 has enough attempts for a rate (below); every other model has 1 failed attempt, n too
small for a claim about that model.

## What was run (free: Python only, no JVM, no model)

```bash
find <each worktree>/runs -name puzzle-results.jsonl   # rows whose id has "splice", solver_arm llm, a model lane
python3 eval/puzzles/step_regret_board.py <the 113 attempt dirs> --out runs/where-lose/board.jsonl
```

The attempt dirs (one board each; several seeds in some), named relative to the worktree that played them:

- the main checkout: `runs/puzzles/hard-gen-luna-check/`, `runs/puzzles/hard-gen-luna-top10/`,
  `runs/puzzles/hard-gen2-luna/` (Luna, epoch 70); `runs/puzzles/frontier5/`, `runs/puzzles/frontier5-fill/`,
  `runs/puzzles/frontier5-long/` (one attempt per model at `splice2_325cd33e0a57`, epoch 72);
  `runs/propose/e75-haiku-hard-menu/`, `runs/propose/e75-haiku-hard-propose/` (Haiku, CLI lane, epoch 75);
- an agent worktree of the terse study: `runs/terse/b2-detailed/`, `runs/terse/b2-terse/`,
  `runs/terse/smoke-64dc1f33-detailed/` (Haiku, CLI lane, epochs 78 and 79);
- the thinking-vs-sampling worktree: `runs/tvs/A-low-k1/`, `runs/tvs/C-low-k3/` (Haiku, CLI lane, epoch 80).

Model, epoch and status come from each attempt's own result row; the condition (prompt profile, tools, propose arm,
effort) from its `batch-manifest.json`. Every attempt had the same tools off (`MEMORY_TOOLS=0`, `RULES_TOOL=0`,
`LOOKAHEAD=0`, `v4-faithful`); the Haiku cells differ by propose arm, terse prompt and effort, so they are never
pooled. The CLI-lane Haiku attempts are development runs and never a score (owner rule); they are reported as data
on where a model loses only. A row's `log_sha256` is not the game file's hash (0 of 119 match, and 5 different games
share one), so attempts were told apart by their game file. Rows: `runs/where-lose/board.jsonl` (git-ignored, 0.4
MB). The taxonomy reads each regret row's decision, the options kept and the model's line from that position.

Excluded: 9 game logs on splice boards with no result row (unfinished or unscored: 2 in `frontier5-fill`, 4 in
`frontier5-long`, a kimi int4 run, a jev-router pilot, `tvs/B-max-k1`).

### What changed in `step_regret_board.py` (now `step-regret-board-v2`; each with a test written red first)

- A hand of `(empty)` (how the prompt writes it; every solved attempt ends there) was a crash; it reads as empty.
- An attempt with no model decision is status `no_model_decision`, not a crash.
- A permanent of the seat's other than Thermo-Alchemist (splice2's Kiln Fiend, Goblin Electromancer) is refused.
  None of the 76 labelled attempts has one; this closes a hole, it changes no verdict.
- **A splice set the pool cannot pay is undone, not lost.** Two attempts (jev, one Luna) picked splice sets costing
  more than they had; the engine undid the cast (the next prompt has the same hand and pool). The model valued that
  committed cast at 0, a lost dip inside the attempt: the internal check (the shortfall never shrinks) failed on
  exactly those two. Fixed; both dips were after the regret step, so no step moved. The check now holds on all 76.
- At a splice question the floor counts the payable sets of offered cards (each copy its own option). Two regret
  steps are splice questions (both Haiku), against the earlier note's assumption that none is.
- Each row carries the attempt's model, epoch and status, and a board outside the model is a `refused` row.

## Coverage

| Model | Lane | Epoch | Run group | Attempts | Solved | Failed | Refused | No decision | Regret step |
|---|---|---|---|---|---|---|---|---|---|
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-check | 21 | 1 | 20 | 0 | 0 | 20 |
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-top10 | 8 | 0 | 8 | 0 | 0 | 8 |
| GPT-6 Luna | openrouter | 70 | hard-gen2-luna | 48 | 0 | 48 | 42 | 0 | 6 |
| GPT-6 Luna | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | 0 | 0 | 1 |
| 20 other models (Sonnet 5.5, Grok 4.6 twice) | openrouter, jev | 72 | frontier5, -fill, -long | 22 | 14 | 8 | 0 | 1 | 7 |
| Claude Haiku 5.5 | CLI | 75 | propose menu / propose | 2 / 2 | 0 | 4 | 0 | 0 | 4 |
| Claude Haiku 5.5 | CLI | 78, 79 | terse smoke / detailed / terse | 1 / 5 / 5 | 0 | 11 | 0 | 0 | 11 |
| Claude Haiku 5.5 | CLI | 80 | tvs A-low-k1 / C-low-k3 | 1 / 3 | 0 | 4 | 0 | 0 | 4 |
| **All** | | | | **119** | **15** | **104** | **42** | **1** | **61** |

The 42 refusals name the first card outside the model: Rite of Flame 28, Gitaxian Probe 6, Grapeshot 4, Pyretic
Ritual 3, Seething Song 1. The no-decision attempt (GLM 5.3 Prime, `frontier5`) made 2 model calls and both
decisions fell to the fixed legal option. The 14 solved frontier attempts are 13 models (Claude Sonnet 5.5 twice) at
`splice2_325cd33e0a57`, the one splice2 board inside the model; the 15th is Luna at `splice_c92133204c97`.

## The regret steps

R = Desperate Ritual cast alone while the winning line still splices it (now: a carrier castable then; resp: cast in
response), B = one-mana burn cast in response, spending the next splice's mana, P = PASS with the Alchemist
untapped over an untap trigger, S = splice question, Glacial Ray carried where Desperate Ritual wins. K is the model
decision's index in the attempt / the attempt's model decisions; the floor is options kept / legal options at K.

| Model | Epoch | Run group | Labelled | Kinds | Floors | Mean floor | K / decisions |
|---|---|---|---|---|---|---|---|
| GPT-6 Luna | 70 | hard-gen-luna-check | 20 | R 13 (now 4, resp 9), B 7 | 1/2 2/6 2/3 1/5 2/6 1/4 1/6 2/3 1/5 1/2 2/6 1/4 2/6 2/3 1/5 2/6 1/5 1/5 1/5 2/6 | 0.34 | 7/18 7/19 2/17 3/14 1/14 3/14 4/15 2/18 6/20 5/17 1/14 6/12 4/17 3/19 3/15 4/12 3/20 3/16 6/18 1/17 |
| GPT-6 Luna | 70 | hard-gen-luna-top10 | 8 | R 6 (now 4, resp 2), B 2 | 2/3 2/3 2/7 1/5 2/7 1/5 6/7 1/6 | 0.42 | 2/18 3/19 4/19 3/15 4/20 3/14 5/22 7/18 |
| GPT-6 Luna | 70 | hard-gen2-luna | 6 | R 6 (now 1, resp 5) | 1/6 1/4 1/6 2/7 1/6 2/7 | 0.22 | 3/14 3/11 3/16 1/12 3/15 4/15 |
| GPT-6 Luna | 72 | frontier5-fill | 1 | R (now) | 3/7 | 0.43 | 4/15 |
| Grok 4.6 | 72 | frontier5; frontier5-long | 1; 1 | R (now); R (now) | 3/7; 3/7 | 0.43 | 6/24; 3/19 |
| Grok 4.3, Grok Build 0.1 | 72 | frontier5 | 1 each | R (resp) each | 1/4 each | 0.25 | 3/16; 3/14 |
| Claude Haiku 5.5 | 72 | frontier5 | 1 | R (now) | 3/7 | 0.43 | 3/18 |
| jev 1.14 | 72 | frontier5-fill | 1 | R (now) | 3/7 | 0.43 | 3/29 |
| Mistral Medium 3.5 | 72 | frontier5-fill | 1 | P | 1/2 | 0.50 | 1/18 |
| Haiku, CLI | 75 | propose menu | 2 | R (resp), B | 1/4 2/3 | 0.46 | 3/21 2/21 |
| Haiku, CLI | 75 | propose | 2 | P, S | 1/2 1/3 | 0.42 | 1/16 8/21 |
| Haiku, CLI | 78 | terse smoke, detailed | 1 | P | 1/2 | 0.50 | 1/20 |
| Haiku, CLI | 79 | terse b2, detailed | 5 | R 5 (now 2, resp 3) | 1/4 2/5 5/7 1/5 4/9 | 0.40 | 6/17 9/33 7/19 7/18 0/22 |
| Haiku, CLI | 79 | terse b2, terse | 5 | R 4 (now 4), S | 2/6 1/3 5/6 6/8 4/9 | 0.54 | 3/14 7/26 6/18 7/25 0/18 |
| Haiku, CLI | 80 | tvs C-low-k3 | 3 | R 3 (now 1, resp 2) | 2/6 1/4 1/4 | 0.28 | 4/15 3/14 4/17 |
| Haiku, CLI | 80 | tvs A-low-k1 | 1 | R (resp) | 1/4 | 0.25 | 3/16 |
| **All** | | | **61** | **R 46, B 10, P 3, S 2** | | **0.37** | median 3 / 17 |

The tvs rows reproduce the earlier note's steps exactly (decision rows K = 4, 5, 4, 5). Rates, only where a cell
has 6 or more attempts (Wilson 95%): Luna epoch 70 `hard-gen-luna-check` R 13/20 = 0.65 (0.43 to 0.82), B 7/20 =
0.35 (0.18 to 0.57); `hard-gen-luna-top10` R 6/8 (0.41 to 0.93), but it replays 4 of the check's boards at 2 seeds
each, so its attempts are not independent of the check's; `hard-gen2-luna` R 6/6 (0.61 to 1.00), on the 6 of 48
boards the model covers, a selected subset. Every other cell: n too small.

The kind follows the board more than the model. `splice2_325cd33e0a57`: R in 8 of the 9 labelled failures (6
models), P in 1. `splice_06dad20af5b3`: R 8, P 2 (Luna and Haiku). `splice_05517e8eccf0`: B 4 (Luna 3, Haiku 1), S 2,
R 1: its hand holds a Shock, and the winning line opens with Desperate Ritual carrying the other Ritual (4 mana), so
a Shock cast in response to the first Ritual leaves 3. The 10 B steps are on 7 boards; in 9 the spell is a Shock or
Lightning Bolt.

At every regret step the position is exactly winnable (shortfall 0) and the step leaves it 1 to 6 short (3 in 38 of
61), so the seat was on a winning line until that one decision.

Along the recorded line before the step, a uniform chooser at the same positions would have kept the win at every one
with probability median 0.23 (IQR 0.10 to 0.34; 57 attempts with every earlier position inside the model). This is
along the seat's own path, not a random player's, and it is measured on failed attempts only: it says the gates
before the step were not trivial, not how a random player scores.

## The model's assumptions here

Those of the earlier note (all mana red, only the pool, Mountains and Desperate Ritual; an opponent that cannot act;
no line gains by casting in response; one ping per untap; a spell at a target or splice question is committed), with
assumption 5 now excepting an unpayable splice set (undone). Every labelled board is Thermo-Alchemist plus 2 or 3
Mountains in the second main phase, the cards all in `puzzle_gen.SPLICE_CARDS`. Two kinds of verdict rest on
mechanics the engine witnesses of the earlier note did not exercise: B (a splice must be paid from the pool and
untapped lands as the spell is cast, so mana a Ritual on the stack will add is not there yet) and S.

## Not determined

- **Engine proof for anything but the 4 tvs attempts.** The earlier note witnessed every winnable position of those
  4 in the engine; the other 57 regret steps, their kept options and all lost sides are the model's alone. A witness
  per regret step (the recorded prefix, then the model's line, `--scripts` / `--witnesses`) is free but needs the
  JVM, which this note did not run: 57 games.
- **The 42 refused Luna splice2 attempts** (Rite of Flame, Gitaxian Probe, Grapeshot, Pyretic Ritual, Seething Song).
  `puzzle_gen.red_search` models those cards; wiring it in as the value would cover them.
- **A model's ability.** Every model except Luna epoch 70 has 1 or 2 labelled failures per condition; the Haiku
  cells differ by arm and epoch and are never pooled. No claim that one model loses differently from another.
- **Why the seat casts the Ritual alone.** The prompts and replies at the 46 R steps were not read for the reason;
  the `harness-audit` skill would separate a model mistake from something the seat was not shown.
- **Whether an unpayable splice pick is a prompt gap.** The splice question lists the cards and their costs but not
  which sets the pool can pay; two attempts picked unpayable sets (jev 4 times, Luna twice) and lost no position by
  it.
