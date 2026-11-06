# Harness as an RLVR environment: feasibility, read from the code and measured on the loopback (2026-11-06)

Idea 10 of [novel-ideas](2026-11-05-novel-ideas.md), narrowed to its first question: what would it take to expose
this harness as `reset(seed | puzzle) -> observation`, `step(action) -> observation, reward, done` for an outside
trainer, with engine-verified rewards (win, puzzle solved) and process rewards (the position is still winnable; step
regret)? Free: no model call, `OPENROUTER_ALLOW_EGRESS` unset, two zero-model JVMs at most. Read on `origin/main` at
18af95f (harness-epoch-83). `S=forge-overlay/forge-gui-desktop/src/main/java/forge/spike`. Nothing in the overlay
was changed.

## Answer so far

**Feasible with no JVM change for terminal rewards; one new Python adapter, about a day. Dense exact rewards are
feasible only offline (after the episode), not per step.**

- The inversion already exists. Every decision the engine asks of the seat ends in one blocking HTTP request to
  `OPENROUTER_BASE_URL`, which a loopback endpoint receives with no key and no egress consent. An external policy
  behind that endpoint is the env's `step`: the request body is the observation, the `answer` tool call is the
  action, the JVM's exit is `done`.
- The action space is typed and per decision: `AnswerTool.Spec` (kinds ONE, MANY, MANY_REPEAT, amounts, blocks,
  number, card, keep_untapped, yield) is a Gym-style discrete / multi-discrete space with a legal mask. The engine
  validates every answer, so an illegal move cannot happen.
- The loopback costs little: bridge latency 9 to 27 ms median per model decision on the stub, a warm puzzle attempt
  0.83 to 0.98 s, but a **JVM start is about 8 s** (the reset cost unless attempts share a JVM).
- Terminal rewards are free and exact: the game line's `winner`, the puzzle row's `status`.
- The process reward ("still winnable") is exact only where the search exhausts, which is near the end of a long
  board. On four failed splice attempts 41 of 70 position searches returned `not_found` and about 5 minutes each. It
  is a post-hoc label, not a per-step signal.
- What breaks a reset to the same game: the model's sampling (not the engine) and the wall-clock line. With a fixed
  policy the engine is a function of (seed, answers).

## 1. Where control flow is inverted, and what already inverts it

**Today (engine calls the seat).** `PlayerControllerLLM` implements the engine's decision hooks on the game thread.
Each builds the menu and calls the seat's bridge (`LLMBridge.ask` / `chat`, `LlmSpikeGui.java:92-118`), which blocks
until the reply. `OpenRouterBridge` (`:11890`) reads `OPENROUTER_BASE_URL` once at class-init (`:11894`); a loopback
endpoint is exempt from the egress gate and is never sent a key (`LLMBridgeFactory.isLoopbackEndpoint`, `:12015`,
`:12568`). A bridge that cannot answer returns null, the seat re-asks `AnswerTool.MAX_NUDGES` (2) times, then takes a
fixed legal option (CLAUDE.md, "Every decision is answered by a tool call").

**An env needs the opposite** (the trainer calls `step`). The pieces that already invert it:

| Piece | What it gives an env | Where |
|---|---|---|
| Fake model server | An OpenAI-compatible `/v1/chat/completions` endpoint on `127.0.0.1` (`ThreadingHTTPServer`, `--port 0`) that reads the offered `answer` schema and picks a legal value (`--policy first|pass|random|simulate`, `--answer-tool`). Replace the policy with a call into the trainer and the JVM is driven with **zero JVM changes** | `eval/fake_model_server.py:493`; loopback games in `eval/harness_acceptance.sh` (`faketool`, `answertool`, `scoring`) |
| `answer` tool schema | The action space: `option` (1..n), `options` (subset with min/max, optional order), `amounts` (sum S), `blocks` (attacker/blocker pairs), `number` (range), `card` (a name: free text), `keep` (untapped lands), `action` (propose arm) | `AnswerTool.definition` (`:578-660`) |
| `AnswerTool.Spec` handed to `LLMBridge.chat(..., spec)` | The typed legal values of the ask, so a bridge need not parse the prompt. `DecisionsBridge` (`jev:`) already answers from it and declines asks with no closed list (card name, wide number, large division) | `LlmSpikeGui.java:114-117`; `DecisionsBridge.java:37` |
| `ScriptBridge` (`script:`) and `ReplayBridge` (`replay:`) | Serve a written line or a recorded attempt's `answer` / `set_plan` calls back through the model seat; the first difference in the ask is a drift. A deterministic "reset to decision K" is `replay:` of a prefix | `S/ScriptBridge.java:41`, `S/ReplayBridge.java:43`; ARCHITECTURE.md, `replay:` row |
| Puzzle runner | `PZL_SEEDS=1..k` runs k attempts in one JVM; one row per attempt (`status`, `solver_actions`, `model_calls`, `model_ms`, `log_sha256`); `PUZZLE=<.pzl>` is the reset target | `S/LlmSpikePuzzle.java`, `eval/puzzles/puzzle_solve.py:275` |
| Search arm | `PuzzleArms.Search` + `PuzzleSearch`: an exact prover over the same menus, the source of "winnable" | `S/PuzzleArms.java:327`, `S/PuzzleSearch.java:114` |

**The adapter** (new, Python, no overlay edit): `HarnessEnv.reset(puzzle|seed)` starts the loopback server and the
JVM (`LlmSpikeHeadless` with `PUZZLE`/`GAME_SEED`, `CONTROLLER_A=llm`, `ANSWER_TOOL=1`, `SMART_MODEL_A=openrouter:x/y`,
`OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1`) and blocks until the first request. `step(action)` writes the tool
call into the parked HTTP handler, then blocks until the next request (observation) or the JVM exits (`done`, reward
from the row). The server needs one port per concurrent episode: the request carries no episode id, and `/v1` is
stateless apart from the messages array.

**What the adapter has to handle, from the code:**
1. *One request is not one decision.* Lookups (`memory_search`, `inspect`, `simulate`), `set_plan`, a corrective
   round after an invalid answer and a nudge after a reply with no call are all requests (`fake_requests.jsonl`
   shows `tool_calls: [memory_search]` then `[set_plan, answer]` for the first decision). The env's step is an
   `answer` call; the others are either extra actions with reward 0 or turned off (`MEMORY_TOOLS=0`, `NATIVE_TOOLS=0`).
2. *The deadline is the env's clock.* A reply later than `OPENROUTER_TIMEOUT_MS` (per model in `ModelCfg`) is a lost
   decision: the seat takes its fixed legal option and the trainer never sees that it did. The fake server's
   `--stall-call` run (9 s against a 4 s deadline) is the recorded case. The adapter must set the timeout above its
   own worst step, and log a `fallback` row as an error (`decision.fallback` is on the row).
3. *The observation is text.* `SeatObservation` renders the state as the prompt; there is no structured state. A
   trainer sees a chat transcript (messages array, resent in full each ask), not a vector. A structured observation
   would be a new overlay surface and a new version stamp.
4. *Variable action spaces.* Heterogeneous per step: Gym's `Discrete` does not fit; use a tagged `Dict`/`OneOf` with a
   mask, as `DecisionsBridge` already restricts to closed lists. `card` is free text and a wide `number` has no mask.
5. *Opponent and seat.* `CONTROLLER_B=ai` (the PUZ and STR-AI condition). Self-play (both seats external) would be two
   endpoints (`SMART_MODEL_B`); it is the PettingZoo shape, untested here.
6. *Mid-game reset is replay, not restore.* There is no JVM snapshot. "Reset to decision K" is `replay:` of the first
   K answers (exact; checked by `StepRegretTest` for single-choice decisions) or running the game again from the seed.

## 2. Loopback overhead, measured (free, zero-model)

**New run.** Build `runs/harness-acceptance/accept-b3d31f8` (overlay `19356828609b`, epoch 76; the checkout is epoch
83, so `--allow-stale-build`: a transport measurement, never a score). Fake model server, policy `first`, answer tool,
two open puzzles, k=3 attempts each, one JVM per puzzle, `-j 1`. The server and the run were started from a scratch
directory (not under `runs/`, so no row of it is kept in the repository):

```
python3 eval/fake_model_server.py --port 0 --policy first --answer-tool --log <scratch>/fake_requests.jsonl
env -u OPENROUTER_ALLOW_EGRESS -u OPENROUTER_API_KEY OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1 \
  FORGE_HOME=$FORGE_HOME python3 eval/puzzles/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json \
  --split open --ids forge-PP20,forge-PP04 --k 3 --model openrouter:fake/harness-model --budget-usd 0.01 \
  --out <scratch>/puzzle --build runs/harness-acceptance/accept-b3d31f8 --allow-stale-build --forge $FORGE_HOME -j 1
```

Whole call 24.4 s wall for 2 JVMs and 6 attempts, cost $0.000. Rows are `puzzle-results.jsonl`:

| Puzzle | Seed | Wall of the attempt | Model decisions | `model_ms` (sum) | ms per model call | Engine decision rows |
|---|---|---|---|---|---|---|
| forge-PP04 | 1 / 2 / 3 | 2586 / 979 / 853 ms | 8 | 193 / 80 / 70 | 24 / 10 / 9 | 8 |
| forge-PP20 | 1 / 2 / 3 | 2166 / 872 / 829 ms | 8 | 193 / 92 / 87 | 24 / 12 / 11 | 14 (6 harness-auto) |

- **Per model decision**: 9 to 12 ms on the warm attempts, 24 ms on the first attempt of a JVM (class load). That is
  the bridge's HTTP round trip to a Python stdlib stub, JSON build and parse included.
- **Per attempt, all in**: 0.83 to 0.98 s warm (engine setup, 8 decisions, game log), 2.2 to 2.6 s the first. An upper
  bound of about 0.11 s per model decision with the engine included.
- **JVM start**: about 8 s per JVM (24.4 s minus the 8.3 s of attempts, over 2 JVMs). Attempts that share a JVM
  (`PZL_SEEDS`) amortise it; one JVM per `reset` would not.
- **Decisions per attempt**: 8 model decisions and 4 to 5 `solver_actions`. These are losing attempts of a `first`
  policy (all 6 `failed`: PP04 `SpellEffect`, PP20 `LifeReachedZero`), so they are short; a solved attempt is longer.

**Existing acceptance runs** (a game, not a puzzle; `latency_ms` is the decision row's, request gap is from
`fake_requests.jsonl`; the engine part is the floor seat and the AI):

| Run dir under `runs/harness-acceptance/accept-b3d31f8/` | Game | Model decisions | HTTP requests | Bridge ms median / mean / max | Game wall |
|---|---|---|---|---|---|
| `part1/answertool` | llm vs floor, 7 turns | 20 | 24 | 18 / 19 / 40 | 8.3 s |
| `part1/lookahead` | llm vs floor, 11 turns | 25 | 37 | 18 / 28 / 209 | 12.3 s |
| `part3/faketool` | llm vs floor, 108 turns, one 9 s stall | 30 | 61 | 18.5 / 26.7 / 275 | 53.9 s |
| `part3/faithful` | llm vs floor, 108 turns | 20 | 40 | 27 / 41 / 381 | 74.9 s |
| `part4/gaming-garbage` | llm vs AI, 12 turns | 10 | 48 | 17 / 18 / 34 | 16.0 s |

The same sweep in `accept-0025b62` gives 12.5 ms median on `answertool` and 22.5 ms on `faketool`. The 108-turn games
have 1558 decision rows but only 20 to 30 reach the model: the rest are windows and harness-auto rows, and the wall
time is the engine, not the transport. A model's real decision costs seconds, so the loopback is 2 to 3 orders of
magnitude below the policy it serves. The one real-game figure on file is "a finished game is 14 to 34 calls"
([thinking-vs-sampling](2026-11-05-thinking-vs-sampling.md)).

## 3. Reward signals today, and their cost

| Signal | Kind | Where it comes from | Cost per episode | Exact? |
|---|---|---|---|---|
| Game won / lost / capped | terminal | game line `winner`, `win_condition`; a capped game is invalid, never a draw | free (the game) | yes (the engine ends the game) |
| Puzzle solved | terminal | puzzle row `status`, `goal`, `goal_oracle`; the goal is enforced by the engine (`goal_enforcement_installed`) | free (the attempt) | yes |
| Opponent life / own life at the end | terminal, graded | the log; the basis of idea 10's hindsight items ("bring the opponent to L") | free | yes, not yet extracted |
| Winnable after decision k (`W(k)`) | process | `replay:` the first k answers, then `PuzzleSearch` (UCT, `exhausted` marks a proof) | see below | exact only when the search exhausts |
| Step regret (first k with `W` true then false) and the floor (share of options at k that keep the win) | process, offline | `eval/puzzles/step_regret.py` on branch `feat/step-regret` (not on main) | see below | bracket, not a step, on long boards |
| Legality | process | the engine refuses an illegal action; the propose arm logs `REFUSED` | free | yes; but with the menu shown, illegality cannot happen |
| Life difference as a forecast | dense proxy | a feature of the log | free | no: Brier about 0.21 on Luna turns against 0.245 for the base rate, no signal on turns 1 to 3 ([forecast-floors](2026-11-06-forecast-floors.md)) |

**Exact-search cost** (from the step-regret work; the branch note is `eval/research/2026-11-06-step-regret-search.md`
on `feat/step-regret`, commit 80814a6, and the feasibility note
[step-regret-feasibility](2026-11-05-step-regret-feasibility.md) on main):
- A rollout takes a median 0.70 s. A proven search averages 99 rollouts and 75 s; a `not_found` one 283 rollouts and
  211 s (166 searches in `runs/puzzles`; median 115 s, p90 334 s per search).
- Four failed splice attempts (14 to 17 model decisions, budget 600 rollouts, the boundary at 2400): 72 searches in
  about 6 CPU-hours (70, 60, 101 and 133 minutes), roughly 5 minutes a search. Of the 70 verdicts listed, 1 proven, 28
  unwinnable (all exhausted, 0 capped rollouts) and **41 not found (59%)**. Positions more than about 7 decisions
  from the end all came back `not_found`. No attempt got an exact regret step; one got a bracket (K 4 to 10).
- The monotone property (once `W` is false it stays false against a deterministic opponent) makes the late side
  cheap; the early side, where the model went wrong, is where the search runs out of budget.

So the **process reward is a label, not a signal**: minutes of CPU per position, not available inside a step, and
unknown (`not_found`) for most early positions. It is usable as an offline relabelling of a stored trajectory (credit
assignment, a verifier of a "this move lost the game" claim), not as a per-step reward during an online rollout.

**What would be dense and cheap**, none built:
1. *Terminal shaping from the log*: opponent life at the end, own life, turns survived (a puzzle's `Turns Limit` is
   in `goal_oracle`). Free, exact, but they reward progress, not the win.
2. *Potential from a proven witness*: when the attempt solves, every decision on its line is a proven `W` true for
   one replay (about one rollout, under a second). That gives a free positive label on solved attempts.
3. *Late-position exhaustion*: `W` for the last 5 to 7 decisions is exact in seconds. A reward on the final stretch
   only.
4. *Hindsight items* (idea 10): a failed attempt's final board becomes an easier verified item. Replayable, free.
5. *Rules-probe labels* ([rules-probe](2026-11-05-rules-probe.md)): single-step exact items, a supervised or
   one-step RL target with a legal-option mask.

## 4. Determinism and reset

- **Seed to game.** The engine is seeded from `GAME_SEED` (`LlmSpikeGui.applySeed`) and a puzzle attempt from
  `PZL_SEEDS`. A zero-model game is a fixed function of its seed: 10 of 10 same-seed AI-vs-AI games replayed with the
  same winner and turn count ([seed-control-variate](2026-11-05-seed-control-variate.md), `runs/cv/ai-vs-ai`
  against `ai-vs-ai-rep`, seeds 1000 to 1009). Canaries pin seed 42 (floor mirror, A wins in 108 turns).
- **New evidence here.** The three attempts of each puzzle above ended with the same `log_sha256`
  (PP04 `8e4dda235c...` on seeds 1, 2, 3; PP20 `07fb5a8ab8...`). The raw engine logs differ in one line only, the
  wall-clock `GAME_OUTCOME: Match Duration 0m : 01s` against `00s` (seed 1 against seed 2; seeds 2 and 3 are
  byte-identical, sha256 `dc4455b4...`). `LlmSpikePuzzle.logFingerprint` drops that line (`:308-317`), which is why
  the hash matches. A reward computed from the raw log would flip with the clock.
- **Puzzle reset.** `PUZZLE=<file>` plus an attempt seed; the `.pzl` is hashed (`pzl_sha256`) and a score refuses a
  changed file. A puzzle with no randomness (as these two) gives the same game for every seed under a fixed policy;
  seeds matter for shuffles and the AI's `MyRandom` draws.
- **What breaks it.**
  1. *Policy sampling.* The env is deterministic given (seed, answers); an external policy that samples makes the
     episode a function of its RNG too. Log the answers (`TRANSCRIPTS=1` keeps the exact HTTP bodies) and the
     episode replays with `replay:`.
  2. *Wall-clock lines* (above), and `GAME_CAP_MS` / `OPENROUTER_TIMEOUT_MS`: a cap or a late reply changes the
     outcome (a fallback option, an invalid game). Cap rollouts by draws, not by time (the search note's rule).
  3. *The build.* The menus must be byte-identical to a recording; `replay:` refuses on the first difference. A stale
     build (this run) is fine for transport and wrong for any score.
  4. *Hidden extra RNG draws.* A search seat draws one `MyRandom.nextLong()` at construction, a model seat none, so a
     search rollout is not the model's game; hence the `ReplayBridge` handover design.
  5. *Concurrency.* One JVM per game; jobs share a machine and only the wall clock moves.

## 5. Prior art (background from memory, not verified)

- OpenAI Gym / Gymnasium: `reset(seed) -> obs, info`, `step(a) -> obs, reward, terminated, truncated, info`, typed
  `spaces`. The shape asked for here; its single-agent, fixed-space assumption is what the harness bends.
- PettingZoo: the multi-agent API (agent-iteration and parallel forms). The relevant one for a two-seat self-play
  setup, with an `action_mask` in the observation as the convention for illegal actions.
- Prime Intellect `verifiers` and the Environments Hub: environments as packages with a rubric of verifiable rewards,
  for RLVR trainers. The packaging target for a puzzle suite.
- Reasoning Gym (arXiv:2505.24760): procedurally generated tasks with programmatic verifiers; cited already in idea 10.
- Poker and board-game libraries (OpenSpiel, RLCard): the engine as the environment, with a legal-action list per
  step, the closest relative of an engine-validated menu.
- Process reward models (for example "Let's Verify Step by Step", Lightman et al. 2023): per-step labels that are
  learned, where the engine's exact search labels are verified but sparse.

## Not determined

- A real policy's round trip: the stub answers in microseconds; a trainer's inference, queueing and the 4-second-class
  deadline are not measured.
- Whether `step` should be an `answer` call or any request; lookups as actions were not tried.
- A long solved attempt's decision count (both puzzles here failed under `first`), and a full-game episode with a
  non-trivial policy on the current epoch.
- Per-step exact reward at the decision where it matters: the search leaves the middle of 12-plus-step boards open.
- Self-play (two external seats), an in-JVM many-episode server (no JVM change was made or tried), and a structured
  observation.
- The cost of the adapter itself: estimated, not built.
