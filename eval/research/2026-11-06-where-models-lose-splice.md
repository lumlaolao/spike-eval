# Where models lose won splice puzzles: every recorded attempt, the splice board's model (2026-11-06)

**Question.** Across every recorded model attempt at a splice-family puzzle on this machine, where do models lose a
puzzle they could still win? For each failed attempt: does the splice board's model
(`eval/puzzles/step_regret_board.py`, `2026-11-06-step-regret-board-model.md`) cover it, and if so, which decision is
the regret step (the last position from which the puzzle is still winnable, before the first lost one), what kind of
decision it is, what the seat chose there, its floor (the share of legal options there that keep the win, which is
what a uniformly random choice at that step keeps), and how far it is from the end. As a check of the model, every
solved attempt must be winnable at every position.

**Answer so far: almost always the same mistake, early, at a narrow gate; the winning side of 59 of the 60 steps
is now engine-proven.** Of 119 recorded model attempts (21 models, Haiku 5.5 in two lanes; split by model, epoch and
run group), 15 solved and 104 failed. The model labels 75 (60 failed, 15 solved), refuses 43 (42 Luna epoch-70
attempts at splice2 boards holding cards outside the model; 1 Haiku attempt that left the main phase, a model error
the engine witnesses caught) and has nothing to label in 1 (no model decision). All 15 solved attempts are winnable
at every position (0 model errors). In the 60 labelled failures:

- **45 of 60: Desperate Ritual cast on its own while the winning line still splices it** (onto a Lava Spike or
  Glacial Ray, net +1 mana and the card stays in hand). In 21 a carrier was castable at that moment; in 24 the
  Ritual was cast in response (stack not empty), where the only winning options were to let the stack resolve.
- **10: a one-mana burn spell cast in response** (Shock 8, Lightning Bolt 1, Glacial Ray 1) to a pending Ritual,
  spending the mana the winning line's next splice needed; the winning line casts that spell last.
- **3: PASS with Thermo-Alchemist untapped over a pending untap trigger** (a ping lost). **2: at a splice question,
  Glacial Ray carried where carrying Desperate Ritual wins.** No regret step is a target choice or a pass with
  lethal on board.

**Engine witnesses** (zero-model script arm, the attempt's seed: the recorded decisions up to the step, then the
model's line with the option it says keeps the win): 55 of the 56 new steps proven, 0 failed, 1 drift (a propose-arm
recording whose proposal carried a target, so the replay was asked a question the recording never shows). With the 4
tvs steps of the earlier note, 59 of 60 regret steps have an engine-proven winning side; every kind is covered (R 44
of 45, B 10, P 3, S 2). The lost side (the position after the seat's choice) and the floors' other options are still
the model's.

**The prompt did not show what the main mistake turns on.** Every attempt here predates harness epoch 86 (#391),
which added the `YOUR CARDS` block: the puzzle prompts never gave the seat its cards' rules text, so Desperate
Ritual read `CAST Desperate Ritual @3 1{R} [Add {R}{R}{R}.]` with no "Splice onto Arcane {1}{R}" (details below). The
R mistake and the missing text co-occur; whether the text causes it is not tested (an arm on the same boards at
epoch 86 or later would). A second, separate candidate: after an unpayable splice set the engine undoes the cast and
the harness drops that card from the play menu for the window, with no notice (details below).

The step comes early: median model decision 3 (range 0 to 9), 21% of the way through the attempt (IQR 14% to 27%),
followed by a median 12 more decisions (IQR 10 to 15, minimum 5); only 3 of 60 steps are within 7 decisions of the
end, which is why the exact search, which decides only the last 7 or so, found none. The floor averages 0.37 (median
1/3, range 1/6 to 6/7): a uniformly random choice at the step would keep the win about a third of the time. Per
condition, only Luna epoch 70 has enough attempts for a rate (below); every other model has 1 failed attempt, n too
small for a claim about that model.

## What was run (free: no model; the witnesses are zero-model JVM games)

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
on where a model loses only. A row's `log_sha256` fingerprints Forge's engine log without its wall-clock line
(`LlmSpikePuzzle.logFingerprint`), not the game file, so two games with the same engine log share it as designed (4
values are shared, each within one puzzle and one status, such as 5 solves of `splice2_325cd33e0a57`); attempts were
told apart by their game file. Rows: `runs/where-lose/board.jsonl` (git-ignored, 0.4 MB). The taxonomy reads each
regret row's decision, the options kept and the model's line from that position.

Excluded: 9 game logs on splice boards with no result row (unfinished or unscored: 2 in `frontier5-fill`, 4 in
`frontier5-long`, a kimi int4 run, a jev-router pilot, `tvs/B-max-k1`).

The witnesses (56 games, 2 JVMs, under the shared Forge lock, about 7 minutes):

```bash
python3 eval/puzzles/puzzle_baselines.py --build <the attempt's build> --allow-stale-build line --pzl <the attempt's
  .pzl, sha-checked against its result row> --script runs/where-lose/scripts/<step>.txt --seeds <seed> \
  --out-dir runs/where-lose/witness/<step>
```

Each script is `step_regret_board.script_for` at the regret row (the recorded model decisions as script lines, then
`puzzle_gen.written_line` of the model's line from the step, at a splice question the first kept splice set), and
`witness_verdict` calls a step proven only when every row up to it asked the same question with the same options, the
state read at the step equals the recording's, the witness took the kept option there, and the line played to a
solve. Builds: the attempt's own where it still exists (epoch 72: 8 steps; 75: 4; 79: 9); no epoch-70 or epoch-78
build is left, so those 35 ran on the epoch-82 build the earlier note's witnesses used (overlay `c57ec70bb79d`).
Every one of the 35 matched its recording question for question up to the step, so the build difference changed
nothing up to there. Outputs: `runs/where-lose/witness/` (16 MB with the logs, git-ignored),
`runs/where-lose/witness-verdicts.json`.

### Model behaviour these attempts rely on (`step_regret_board.py`, `step-regret-board-v2`)

- A splice set the pool cannot pay is undone, not lost (two attempts, both after their regret step). At a splice
  question the floor counts the payable sets of offered cards, each copy its own option; two regret steps are splice
  questions (both Haiku).
- A permanent other than Thermo-Alchemist, a position outside a main phase, an empty hand and an attempt with no
  model decision are refused or read as such (status `refused` / `no_model_decision`); a propose-arm attempt that
  passed into the end step (`splice_06dad20af5b3`, terse b2-detailed) is refused for that reason.
- Each row carries the attempt's model, epoch and status. `script_for` writes the first kept splice set at a splice
  question, and `witness_verdict` compares a splice row by its picks.

## Coverage

| Model | Lane | Epoch | Run group | Attempts | Solved | Failed | Refused | No decision | Regret step |
|---|---|---|---|---|---|---|---|---|---|
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-check | 21 | 1 | 20 | 0 | 0 | 20 |
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-top10 | 8 | 0 | 8 | 0 | 0 | 8 |
| GPT-6 Luna | openrouter | 70 | hard-gen2-luna | 48 | 0 | 48 | 42 | 0 | 6 |
| GPT-6 Luna | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | 0 | 0 | 1 |
| 20 other models (Sonnet 5.5, Grok 4.6 twice) | openrouter, jev | 72 | frontier5, -fill, -long | 22 | 14 | 8 | 0 | 1 | 7 |
| Claude Haiku 5.5 | CLI | 75 | propose menu / propose | 2 / 2 | 0 | 4 | 0 | 0 | 4 |
| Claude Haiku 5.5 | CLI | 78, 79 | terse smoke / detailed / terse | 1 / 5 / 5 | 0 | 11 | 1 | 0 | 10 |
| Claude Haiku 5.5 | CLI | 80 | tvs A-low-k1 / C-low-k3 | 1 / 3 | 0 | 4 | 0 | 0 | 4 |
| **All** | | | | **119** | **15** | **104** | **43** | **1** | **60** |

One refusal is the end-step attempt above. The other 42 name the first card outside the model: Rite of Flame 28,
Gitaxian Probe 6, Grapeshot 4, Pyretic Ritual 3, Seething Song 1. The no-decision attempt (GLM 5.3 Prime,
`frontier5`) made 2 model calls and both decisions fell to the fixed legal option. The 14 solved frontier attempts
are 13 models (Claude Sonnet 5.5 twice) at `splice2_325cd33e0a57`, the one splice2 board inside the model; the 15th
is Luna at `splice_c92133204c97`.

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
| Haiku, CLI | 79 | terse b2, detailed | 4 | R 4 (now 2, resp 2) | 2/5 5/7 1/5 4/9 | 0.44 | 9/33 7/19 7/18 0/22 |
| Haiku, CLI | 79 | terse b2, terse | 5 | R 4 (now 4), S | 2/6 1/3 5/6 6/8 4/9 | 0.54 | 3/14 7/26 6/18 7/25 0/18 |
| Haiku, CLI | 80 | tvs C-low-k3 | 3 | R 3 (now 1, resp 2) | 2/6 1/4 1/4 | 0.28 | 4/15 3/14 4/17 |
| Haiku, CLI | 80 | tvs A-low-k1 | 1 | R (resp) | 1/4 | 0.25 | 3/16 |
| **All** | | | **60** | **R 45, B 10, P 3, S 2** | | **0.37** | median 3 / 17.5 |

The tvs rows reproduce the earlier note's steps exactly (decision rows K = 4, 5, 4, 5). Rates, only where a cell
has 6 or more attempts (Wilson 95%): Luna epoch 70 `hard-gen-luna-check` R 13/20 = 0.65 (0.43 to 0.82), B 7/20 =
0.35 (0.18 to 0.57); `hard-gen-luna-top10` R 6/8 (0.41 to 0.93), but it replays 4 of the check's boards at 2 seeds
each, so its attempts are not independent of the check's; `hard-gen2-luna` R 6/6 (0.61 to 1.00), on the 6 of 48
boards the model covers, a selected subset. Every other cell: n too small.

The kind follows the board more than the model. `splice2_325cd33e0a57`: R in 8 of the 9 labelled failures (6
models), P in 1. `splice_06dad20af5b3`: R 7, P 2 (Luna and Haiku). `splice_05517e8eccf0`: B 4 (Luna 3, Haiku 1), S 2,
R 1: its hand holds a Shock, and the winning line opens with Desperate Ritual carrying the other Ritual (4 mana), so
a Shock cast in response to the first Ritual leaves 3. The 10 B steps are on 7 boards; in 9 the spell is a Shock or
Lightning Bolt.

At every regret step the position is exactly winnable (shortfall 0) and the step leaves it 1 to 6 short (3 in 37 of
60), so the seat was on a winning line until that one decision.

Along the recorded line before the step, a uniform chooser at the same positions would have kept the win at every one
with probability median 0.23 (IQR 0.10 to 0.35; 56 attempts with every earlier position inside the model). This is
along the seat's own path, not a random player's, and it is measured on failed attempts only: it says the gates
before the step were not trivial, not how a random player scores.

## The model's assumptions here

Those of the earlier note (all mana red, only the pool, Mountains and Desperate Ritual; an opponent that cannot act;
no line gains by casting in response; one ping per untap; a spell at a target or splice question is committed), with
assumption 5 now excepting an unpayable splice set (undone). Every labelled board is Thermo-Alchemist plus 2 or 3
Mountains in the second main phase, the cards all in `puzzle_gen.SPLICE_CARDS`. A pass on an empty stack is valued
as the end of the turn, though the engine goes on to the end step, where instants can still be cast: that makes the
model a lower bound there, and a kept-option count that treats such a pass as losing may undercount by one. The B
and S kinds rest on splice payment at cast time, which the earlier witnesses did not exercise; this note's witnesses
did (10 B and 2 S steps proven).

## The witnesses, per step

| Kind | Steps | Proven (this note) | Proven (tvs, earlier) | Failed | Drift |
|---|---|---|---|---|---|
| R, carrier castable then | 21 | 20 | 1 | 0 | 0 |
| R, cast in response | 24 | 20 | 3 | 0 | 1 |
| B | 10 | 10 | 0 | 0 | 0 |
| P | 3 | 3 | 0 | 0 | 0 |
| S | 2 | 2 | 0 | 0 | 0 |
| **All** | **60** | **55** | **4** | **0** | **1** |

By build: epoch 72 build 8 of 8 proven; epoch 75 build 4 of 4; epoch 79 build 8 of 9 (the drift); epoch 82 build for
the epoch-70 and epoch-78 attempts 35 of 35. The drift (`splice_1267400f74df`, terse b2-detailed, propose arm): the
recording's proposals carry their targets (`"targets":["opponent"]` in the reply), so no target row was recorded and
the script has no target line; the witness, not on the propose arm, was asked for Lightning Bolt's target at row 2.
It is a limit of replaying a propose-arm recording, not a verdict on the model; the other 12 propose-arm steps
replayed. 0 failures: no winnable verdict at a regret step was wrong.

## What the prompt showed at the step (6 steps read)

1. **R, carrier castable** (`runs/puzzles/frontier5-fill/openai_gpt-6-luna__hard-splice2-325cd33e0a57/`, decision
   row 5, epoch 72): pool `{R:3}`, empty stack, menu `CAST Desperate Ritual @3 1{R} [Add {R}{R}{R}.]` and three
   `CAST Lava Spike ... [Lava Spike deals 3 damage to target player or planeswalker.]`. Nothing in the message says
   Desperate Ritual has Splice onto Arcane {1}{R} or that Lava Spike is Arcane; the system prompt has no
   `<your_deck>` block and says "You have no lookup tools: everything you need is in the message". The reply:
   "The second Desperate Ritual increases the pool from 3 to 4 net mana".
2. **R, in response** (`runs/puzzles/hard-gen-luna-check/hard-splice-06dad20af5b3/`, row 4, epoch 70): pool `{R:3}`,
   a ping on the stack, both Lava Spikes marked "(payable, not castable now)", so the wait is visible; the splice
   text is not.
3. **B** (`runs/puzzles/hard-gen-luna-check/hard-splice-05517e8eccf0/`, row 3, epoch 70): pool empty, 1 untapped
   Mountain, a Ritual on the stack; the Rituals and Glacial Ray read "(need 1 more mana)". The pool, the pending
   Ritual and the costs are shown; that a splice is paid in full as the spell is cast (so the Shock's mana is the
   4th mana a Ritual-carrying-Ritual needs) is rules knowledge, not on the screen, and splice itself is not shown.
4. **The splice question, unpayable picks**
   (`runs/puzzles/frontier5-fill/typesafe_jev-1.14__hard-splice2-325cd33e0a57/`, row 9, epoch 72;
   `runs/puzzles/hard-gen-luna-check/hard-splice-f3bc218b2fb5/`, row 10, epoch 70): the question shows the splice
   reminder text, the pool (`{R:4}`, `{R:3}`) and each card as `Glacial Ray 1{R} @2` (its mana cost; the splice cost
   happens to be the same {1}{R}), and asks "between 0 and 2". It does not say which sets are payable, but the
   arithmetic is on the screen, and the target question that follows names the total (`cost: {2}{R}{R}{R}` against a
   pool of 4). A model mistake, with no missing fact.
5. **After the unpayable pick** (same two runs, row 13 in both): the engine undid the cast (same hand and pool; the
   card comes back as `Lava Spike @16 (was @4) (castable)`), no message says the cast failed, and the play menu no
   longer offers that Lava Spike for the rest of the window (`failedInWindow`, "a play that failed to pay in this
   window is not offered again"), though it is castable with no splice or a payable one. jev then made the same
   unpayable pick on each other Lava Spike in turn (rows 15, 21) until the menu offered no Lava Spike at all (row
   25: Glacial Ray, Glacial Ray, PASS, with three Lava Spikes "(castable)" in hand). In these attempts it came after
   the regret step, so it decided nothing here.

**Harness defect candidate** (not filed; the rules-text gap of these prompts was fixed at epoch 86):
- *A cast undone for an unpayable splice set hides a legal play and says nothing.* After the undo the card is dropped
  from the play menu for the window though a payable cast exists (no splice, or a cheaper set), against the owner rule
  that a legal action is never hidden; the seat is not told the cast failed or why.

## Not determined

- **The lost side of each step and the other options' verdicts.** Witnessed: the kept option keeps the win. That the
  seat's choice loses (and the floors' other losing options) is the model's: a loss needs every line played.
- **The 42 refused Luna splice2 attempts** (Rite of Flame, Gitaxian Probe, Grapeshot, Pyretic Ritual, Seething Song).
  `puzzle_gen.red_search` models those cards; wiring it in as the value would cover them. Also the end-step attempt.
- **A model's ability.** Every model except Luna epoch 70 has 1 or 2 labelled failures per condition; the Haiku
  cells differ by arm and epoch and are never pooled. No claim that one model loses differently from another.
- **Whether the missing rules text causes the R mistake.** Untested: the fix (`YOUR CARDS`, epoch 86) postdates every
  attempt, so an arm on the same boards at epoch 86 or later would settle it. Some seats wrote splice into their own
  notes from recall (30 of 60 steps), and still cast the Ritual alone.
