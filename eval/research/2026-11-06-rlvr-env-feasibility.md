# Harness as an RLVR environment: feasibility, read from the code and measured on the loopback (2026-11-06)

Idea 10 of [novel-ideas](2026-11-05-novel-ideas.md), narrowed to its first question: what would it take to expose
this harness as `reset(seed | puzzle) -> observation`, `step(action) -> observation, reward, done` for an outside
trainer, with engine-verified rewards (win, puzzle solved) and process rewards (the position is still winnable; step
regret)? Free: no model call, `OPENROUTER_ALLOW_EGRESS` unset, two zero-model JVMs at most. Read on `origin/main` at
18af95f (harness-epoch-83). `S=forge-overlay/forge-gui-desktop/src/main/java/forge/spike`. Nothing in the overlay
was changed.

## Answer so far

- **Verdict.** Feasible for terminal rewards (win, puzzle solved) with no JVM change and one new Python adapter (about
  a day, an estimate; nothing built). Process rewards are offline labels, not per-step signals.
- **What inverts control flow.** Every decision the engine asks of the seat makes at least one blocking HTTP request
  to `OPENROUTER_BASE_URL`; a loopback endpoint is exempt from the egress gate and the key. An external policy behind
  it is `step`: the request is the observation, the `answer` call the action, and the end of the episode (the JVM
  exit, or a result row when `PZL_SEEDS` runs several attempts in one JVM) is `done`. The legal set arrives as prompt
  text under the fixed `answer` schema, so a mask has to be parsed out of it (as the fake server does) or taken from
  the per-decision schema arm, which is a different stamped condition.
- **Cost.** The loopback adds 12 to 26 ms per model call on a stub; a JVM start is about 10 s (an upper bound). The
  exact winnability label is unmerged work (`feat/step-regret`, 80814a6): about 5 minutes a position, and 41 of its 72
  searches were `not_found`.
- **Determinism limits.** The engine is a function of (seed, answers); an external policy's sampling, the wall-clock
  log line, a late reply (a fallback option) and a build whose menus differ break the reset.

## 1. Where control flow is inverted, and what already inverts it

**Today (engine calls the seat).** `PlayerControllerLLM` implements the engine's decision hooks on the game thread.
Each builds the menu and calls the seat's bridge (`LLMBridge.ask` / `chat`, `S/LlmSpikeGui.java:92-118`), which blocks
until the reply. `OpenRouterBridge` (`:11890`) reads `OPENROUTER_BASE_URL` once at class-init (`:11894`); a loopback
endpoint is exempt from the egress gate and is never sent a key (`LLMBridgeFactory.isLoopbackEndpoint`, `:12015`,
`:12568`). A null reply is re-asked twice more (3 attempts, 500 ms then 2000 ms backoff, `BRIDGE_RETRY_BACKOFF_MS`,
`:8088`, `:8390-8402`), each with a fresh timeout; only then does the seat take its fixed legal option (CLAUDE.md,
"Every decision is answered by a tool call"; `AnswerTool.MAX_NUDGES` is 2 for a reply that calls nothing).

**An env needs the opposite** (the trainer calls `step`). The pieces that already invert it:

| Piece | What it gives an env | Where |
|---|---|---|
| Fake model server | An OpenAI-compatible `/v1/chat/completions` endpoint on `127.0.0.1` (`ThreadingHTTPServer`, `--port 0`) that picks a legal value (`--policy first|pass|random|simulate`, `--answer-tool`). Replace the policy with a call into the trainer and the JVM is driven with **zero JVM changes** | `eval/fake_model_server.py:493`; loopback games in `eval/harness_acceptance.sh` (`faketool`, `answertool`, `scoring`) |
| `answer` tool | The action: one fixed schema by default (`AnswerTool.fixedDefinition`, `:477`: an untyped `option` integer, an `options` integer array and the other fields). The fields are `option`, `options`, `amounts`, `blocks`, `number`, `card`, `keep` and, on the propose arm, `action` | `S/AnswerTool.java:477` |
| Legal values | Under the fixed schema they are **prompt text** (a numbered list and the bounds in the question); `fake_model_server.py` recovers them by regex (`decision_props`, `:240-275`). A typed schema built from each decision (`definition(Spec)`, `:579`, `Kind` = ONE, MANY, MANY_REPEAT, ALLOC, KEEP, BLOCKS, NUMBER, CARD, ACTION, ATTACK_PROPOSAL, BLOCK_PROPOSAL, `:259`) is the `ANSWER_SCHEMA=per-decision` measurement arm (`:86-88`), a different stamped condition. `LLMBridge.chat(..., spec)` (`LlmSpikeGui.java:114-117`) is ignored by every bridge but `DecisionsBridge` (`jev:`), which answers from the typed values and declines asks with no closed list | `S/AnswerTool.java`, `S/DecisionsBridge.java:37` |
| `ScriptBridge` (`script:`), `ReplayBridge` (`replay:`) | Serve a written line or a recorded attempt's calls back through the model seat; the first difference in the ask is a drift. "Reset to decision K" is `replay:` of a prefix | `S/ScriptBridge.java:41`, `S/ReplayBridge.java:43`; ARCHITECTURE.md, `replay:` row |
| Puzzle runner | `PZL_SEEDS=1..k` runs k attempts in one JVM, one row per attempt (`status`, `solver_actions`, `model_calls`, `model_ms`, `log_sha256`); `PUZZLE=<.pzl>` is the reset target | `S/LlmSpikePuzzle.java`, `eval/puzzles/puzzle_solve.py:275` |
| Search arm | `PuzzleArms.Search` + `PuzzleSearch`: an exact prover over the same menus, the source of "winnable" | `S/PuzzleArms.java:327`, `S/PuzzleSearch.java:114` |

**The adapter** (new, Python, no overlay edit): `reset(puzzle|seed)` starts the loopback server and the JVM
(`LlmSpikeHeadless`, `PUZZLE`/`GAME_SEED`, `CONTROLLER_A=llm`, `ANSWER_TOOL=1`, `SMART_MODEL_A=openrouter:x/y`,
`OPENROUTER_BASE_URL` at the server) and blocks until the first request. `step(action)` writes the tool call into the
parked HTTP handler, then blocks until the next request (observation) or the episode ends (reward from the row). One
port per concurrent episode: the request carries no episode id. One JVM per `reset` costs the JVM start; running the
attempts of one puzzle through `PZL_SEEDS` shares it but makes `done` a result row, not a process exit.

**What the adapter has to handle, from the code:**
1. *At least one request per decision.* Lookups (`memory_search`, `inspect`, `simulate`), `set_plan`, a corrective
   round after an invalid answer, a nudge, and a retry after a null reply are all requests. A retry repeats the same
   messages array, so the adapter dedupes by it. The env's step is an `answer` call; the rest are extra actions with
   reward 0 or are turned off (`MEMORY_TOOLS=0`, `NATIVE_TOOLS=0`).
2. *The deadline is the env's clock.* A reply later than `OPENROUTER_TIMEOUT_MS` (per model in `ModelCfg`) is a lost
   request; the seat re-asks (the trainer sees duplicates) and takes the fixed legal option only after the third
   attempt, about three timeouts later. The fake server's `--stall-call` run (9 s against a 4 s deadline) is the
   recorded case, and `eval/harness_acceptance.sh:556` asserts a retried attempt. The adapter sets the timeout above
   its worst step and treats a row with `fallback` set as an error.
3. *The observation is text.* `SeatObservation` renders the state as the prompt; there is no structured state. The
   trainer sees a chat transcript, resent in full each ask.
4. *The mask is parsed or the stamp changes.* Under the fixed schema the legal set is in the prompt (see the table).
   `ANSWER_SCHEMA=per-decision` would put it in the schema but is its own condition and changes the stamp. `card` is
   free text and a wide `number` has no closed list.
5. *Opponent and seat.* `CONTROLLER_B=ai` is the PUZ and STR-AI condition. Two external seats would be two endpoints
   (`SMART_MODEL_B`), untested here.
6. *Mid-game reset is replay, not restore.* There is no JVM snapshot. "Reset to decision K" is `replay:` of the first
   K answers (`StepRegretTest` and `eval/puzzles/step_regret.py`, both on the unmerged `feat/step-regret`) or a rerun
   from the seed.

## 2. Loopback overhead, measured (free, zero-model)

**Run.** Needs a built Forge at `$FORGE_HOME` (`/root/forge` here) and a compiled build. The only build on disk is
`runs/harness-acceptance/accept-b3d31f8` (overlay `19356828609b`, harness-epoch-76; the checkout is epoch 83 and no
build under `runs/harness-acceptance/` is at 83), so the run uses `--allow-stale-build`: a transport measurement,
never a score. Rows are kept in `runs/rlvr-env/loopback/` (git-ignored):

```
O=runs/rlvr-env/loopback; mkdir -p $O
python3 eval/fake_model_server.py --port 0 --policy first --answer-tool --log $O/fake_requests.jsonl \
  > $O/fake_server.out 2>&1 &
FAKE_PID=$!
PORT=""; for i in $(seq 1 20); do PORT=$(awk '/^LISTENING/{print $2}' $O/fake_server.out); [ -n "$PORT" ] && break; sleep 0.5; done
env -u OPENROUTER_ALLOW_EGRESS -u OPENROUTER_API_KEY OPENROUTER_BASE_URL=http://127.0.0.1:$PORT/v1 \
  python3 eval/puzzles/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open \
  --ids forge-PP20,forge-PP04 --k 3 --model openrouter:fake/harness-model --budget-usd 0.01 \
  --out $O/puzzle --build runs/harness-acceptance/accept-b3d31f8 --allow-stale-build --forge $FORGE_HOME -j 1
kill $FAKE_PID
```

The whole call took 30.1 s wall for 2 JVMs and 6 attempts, cost $0.000, 48 requests. Rows are
`runs/rlvr-env/loopback/puzzle/forge-*/puzzle-results.jsonl`:

| Puzzle | Seed | Attempt wall | Model calls | `model_ms` | ms per call |
|---|---|---|---|---|---|
| forge-PP04 | 1 / 2 / 3 | 3495 / 1331 / 1027 ms | 8 | 227 / 207 / 105 | 28 / 26 / 13 |
| forge-PP20 | 1 / 2 / 3 | 2778 / 1070 / 783 ms | 8 | 256 / 112 / 94 | 32 / 14 / 12 |

- **Per model call** (`model_ms` / `model_calls` of the result rows): 12 to 26 ms on the warm attempts, 28 to 32 ms
  on a JVM's first. This is the bridge's HTTP round trip to a Python stdlib stub, JSON included. It varies run to
  run (an earlier, unrecorded run of the same command was faster), so read it as "tens of milliseconds at most".
- **Per attempt, all in**: 0.78 to 1.33 s warm, 2.8 to 3.5 s the first: on a warm attempt at most about 0.17 s per
  model decision with the engine included (about 0.44 s on a JVM's first).
- **JVM start**: (30.1 s wall - 10.5 s of attempts) / 2 JVMs = about 10 s each. This is wall minus attempts, so it
  includes the launcher's overhead and is an upper bound on the JVM start.
- **Decisions per attempt**: 8 model calls and 4 to 5 `solver_actions`. All 6 attempts `failed` under the `first`
  policy (PP04 `SpellEffect`, PP20 `LifeReachedZero`), so they are short; a solved attempt is longer.
- **Existing acceptance games** (`runs/harness-acceptance/accept-b3d31f8/part1/answertool`, `part3/faketool`,
  `part3/faithful`): 20 to 30 model decisions in 24 to 61 requests, bridge latency 18 to 27 ms median. The two
  108-turn games (`part3`, `turns=108` in its `summary.txt`) hold 1881 and 1901 JSONL rows, of which only 20 to 30
  are model decisions, and run 49 and 70 s (`Match Duration`): that time is the engine, not the transport;
  `part1/answertool` ends after 7 turns (its `outcome` line) with a 4 s `Match Duration`. A
  real model decision costs seconds, so the loopback is 2 to 3 orders of magnitude below the policy it serves. The
  one real-game figure on file is "a finished game is 14 to 34 calls"
  ([thinking-vs-sampling](2026-11-05-thinking-vs-sampling.md)).

## 3. Reward signals today, and their cost

| Signal | Kind | Where it comes from | Cost per episode | Exact? |
|---|---|---|---|---|
| Game won / lost / capped | terminal | game line `winner`, `win_condition`; a capped game is invalid, never a draw | free | yes |
| Puzzle solved | terminal | puzzle row `status`, `goal`, `goal_oracle`; the goal is enforced by the engine (`goal_enforcement_installed`) | free | yes |
| Opponent / own life at the end | terminal, graded | the log; the basis of idea 10's hindsight items | free | yes, not yet extracted |
| Winnable after decision k (`W(k)`) | process | `replay:` the first k answers, then `PuzzleSearch` (`exhausted` marks a proof) | see below | only when the search exhausts |
| Step regret and the floor | process, offline | `eval/puzzles/step_regret.py`, unmerged (`feat/step-regret`, 80814a6) | see below | a bracket, not a step, on long boards |
| Life difference as a forecast | dense proxy | a feature of the log | free | no: Brier about 0.21 on Luna turns against 0.245 for the base rate ([forecast-floors](2026-11-06-forecast-floors.md)) |

**Exact-search cost** (unmerged, from commit 80814a6 on `feat/step-regret` (draft PR #379), note
`eval/research/2026-11-06-step-regret-search.md` there; the main-branch estimate is
[step-regret-feasibility](2026-11-05-step-regret-feasibility.md)):
- A rollout takes a median 0.70 s; a proven search averages 75 s and a `not_found` one 211 s (166 searches in
  `runs/puzzles`, from the main-branch note).
- Four failed splice attempts (14 to 17 model decisions, budget 600 rollouts): 72 searches in 364 minutes
  (the source says "about 6 CPU-hours"; it is wall time with 2 JVMs), roughly 5 minutes a search. Counted once
  each (`step_regret.py --summary`): 1 proven, 30 unwinnable (all exhausted) and 41 not found (41/72 = 57%).
  Positions more than about 7 decisions from the end were all `not_found`. No attempt got an exact regret step; one
  got a bracket (K 4 to 10).

So the **process reward is a label, not a signal**: minutes per position, and unknown for most early positions. It
suits offline relabelling of a stored trajectory, not an online per-step reward. Cheap dense candidates, none built:
terminal shaping from the log (opponent and own life, turns survived); a free positive label on every decision of a
solved attempt (one replay of its line, under a second); exact `W` for the last 5 to 7 decisions; hindsight items
(idea 10); and the single-step labels of the [rules-probe](2026-11-05-rules-probe.md).

## 4. Determinism and reset

- **Seed to game.** The engine is seeded from `GAME_SEED` (`LlmSpikeGui.applySeed`) and a puzzle attempt from
  `PZL_SEEDS`. A zero-model game is a fixed function of its seed: 10 of 10 same-seed AI-vs-AI games replayed with the
  same winner and turn count ([seed-control-variate](2026-11-05-seed-control-variate.md), seeds 1000 to 1009).
- **This run.** The three attempts of each puzzle ended with the same `log_sha256` (PP04 `8e4dda235c`, PP20
  `07fb5a8ab8`). The raw PP20 engine logs differ in one line only, `GAME_OUTCOME: Match Duration` (`02s` on seed 1,
  `00s` on seeds 2 and 3, which are byte-identical). `LlmSpikePuzzle.logFingerprint` drops that line (`:308-317`); a
  reward computed from the raw log would flip with the clock.
- **Puzzle reset.** `PUZZLE=<file>` plus an attempt seed; the `.pzl` is hashed (`pzl_sha256`). These two puzzles have
  no randomness, so every seed gives the same game under a fixed policy.
- **What breaks it.** (1) A sampling policy: the episode is a function of its RNG too; keep the answers
  (`TRANSCRIPTS=1`) and it replays with `replay:`. (2) The wall-clock line, `GAME_CAP_MS` and a late reply (a
  fallback option). (3) A build whose menus differ from a recording (`replay:` refuses on the first difference; a
  stale build is fine for transport and wrong for any score). (4) A search seat draws one extra `MyRandom` long at
  construction, a model seat none, so a search rollout is not the model's game.

## 5. API shapes the design uses (background from memory, not verified)

- Gymnasium: `reset(seed) -> obs, info` and `step(a) -> obs, reward, terminated, truncated, info`; the adapter keeps
  this shape, with `truncated` for a capped game.
- PettingZoo: the multi-agent form, with an `action_mask` carried in the observation; the model for two external
  seats and for a per-decision legal mask.

## Not determined

- A real policy's round trip: the stub answers in microseconds; a trainer's inference and queueing, against the
  deadline and its retries, are not measured.
- Whether `step` should be an `answer` call only; lookups as actions were not tried.
- A long solved attempt's decision count (all 6 attempts failed) and a full-game episode on the current epoch (no
  epoch-83 build exists under `runs/harness-acceptance/`; the numbers are epoch 76).
- Per-step exact reward where it matters: the search leaves the middle of 12-plus-step boards open, and its figures
  are unmerged.
- A parsed mask against the per-decision schema arm, two external seats, an in-JVM many-episode server, a structured
  observation, and the adapter's own cost (estimated, not built).
