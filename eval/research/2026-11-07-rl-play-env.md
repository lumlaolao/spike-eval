# The harness as an RL environment: a first working version, measured (2026-11-07)

Implements the "adapter" section of [rlvr-env-feasibility](2026-11-06-rlvr-env-feasibility.md): `eval/rl/mtg_env.py`
(`MtgEnv`), `eval/rl/rollout.py`, `eval/rl/README.md`, tests `eval/tests/test_mtg_env.py` and
`eval/tests/test_rollout.py`. Free: no model call, no `OPENROUTER_ALLOW_EGRESS`, only a loopback port, one JVM at a
time under the shared lock. Nothing in `forge-overlay/` or the Forge checkout was changed. Engine: the build at
`/root/forge` (commit 34a8662b79), harness-epoch-82 (the epoch the puzzle rows record); no new build was made.

Runs: each is one `rollout.py` command. The trajectories were kept in a session scratchpad and were **not kept**:
the directory is gone, so every number below stands on this table and the prose, and none can be recomputed from
files. A re-run needs a JVM under the lock (`eval/rl/README.md`).

| Run | Command (abridged) | Episodes | Attempt seeds | Policy seed | Solved |
|---|---|---|---|---|---|
| **P1** | `--policy first --puzzle hard/gen/drain_08bba3eb8d53.pzl --group-size 1` | 1 | 1 | none (`first`) | not recorded; the suite's `first` arm is 0/4 on it |
| **R1** | `--policy random --group-size 4` on `drain_08bba3eb8d53`, `drain_0a395c810ae5`, `drain_2d15c4dd0e32` (hard suite, 10-action reference lines) | 12 | 1 to 4 per puzzle | 0 (the default) | 0/12 |
| **R2** | `--policy random --group-size 8` on Forge's `MTGP_08.pzl` | 8 | 1 to 8 | 0 | 0/8 |
| **R3** | `--policy random --policy-seed 1 --group-size 40` on `MTGP_08.pzl` | 40 | 1 to 40 | 1 | 4/40 |
| **G1** | `--policy random --game-seed 42 --deck eval/decks/burn.txt --max-steps 300` (game against Forge's AI) | 1 | game seed 42 | 0 | lost (winner B, 15 turns, 38 decisions) |

Seed 42 is a harness canary seed, not a reserved block. `MTGP_08` is `heldout` in its suite and was played for the
floor only (the three drain puzzles are `open`). Since this review the env refuses eval-only data unless
`allow_eval_only`, which marks the episodes so the readers never train on them (`eval/rl/README.md`, "Eval-only
data").

## What works

- **A full loop, on the real engine.** `reset(puzzle=..., seed=...)` starts a JVM and returns the first decision;
  `step(action)` returns the next, and on the last one `(None, reward, True, info)` with the reward from the engine's
  puzzle result row. All 62 episodes (P1, R1, R2, R3, G1) ended with a reward and `valid=True` under the rules of that
  commit (a verdict row, no deck-out, no bridge-down count); none needed a retry, a correction round or a step
  timeout (`deduped=0`, 0 `correction` steps in every run). The later rules (a lost decision, a game's bridge-down
  count, `jvm_rc`) were not applied to them: the game rows of G1 carried no `answer_via` count that was read.
- **The reward is the engine's.** R3 solved 4 of 40 (reward 1, `status=solved`); the rest `failed`. The group has
  advantage +3.0 on a solved episode and -0.33 on the others (`rollout.grpo_groups`); `sft_examples` keeps the 4
  solved episodes' 25 decisions.
- **Every request is a decision.** With `MEMORY_TOOLS=0 RULES_TOOL=0 LOOKAHEAD=0` and the env replying only with
  `answer`, the 61 puzzle episodes' requests equal their rows' `model_calls` (and `requests == decisions == n_steps` in all 62).
- **Games run through the same code.** G1 (burn deck, seed 42, random policy vs Forge's AI) played 15 turns in 38
  decisions and lost (winner B, `AllOpponentsLost`): reward 0, `valid=True`.
- **Unit tests (stub JVM, no engine):** at the first commit 19 in `test_mtg_env.py` and 4 in `test_rollout.py`; now 42,
  26 and 14 (`test_eval_only.py`), each new one broken once and seen red.
  A test that would have failed otherwise: the retry test (a request re-sent with the same messages must not become a
  second decision; it counts `deduped=1`, `decisions=2`, `requests=3`).

Conditions, reward and validity rules: `eval/rl/README.md` (the one home; this note records only what was measured).

### One deviation from the brief: `NATIVE_TOOLS=1`

The brief said native tools off; on the text lane the seat never reads the env's tool-call reply, so the env pins
`NATIVE_TOOLS=1` as `puzzle_solve.py` does (`eval/rl/README.md`, "How it works").

## Measured latency and episode length

All from the rows' `info` (`step_ms_*` is the wall time from the env writing the answer to the next observation or
the end, so it holds the loopback round trip, the engine's work and the JVM exit on the last step).

| Run | Episodes | Decisions per episode (mean) | Step ms (median of episode medians) | Slowest step | JVM start to first decision (mean) | Episode wall (mean) |
|---|---|---|---|---|---|---|
| R1 (drain, hard) | 12 | 20.1 (7 to 36) | 27 | 793 ms | 7.0 s | 8.3 s |
| R2 (MTGP_08) | 8 | 6.9 (4 to 10) | 47 | 850 ms | 7.0 s | 8.1 s |
| R3 (MTGP_08) | 40 | 6.2 (4 to 10) | 68 | 908 ms | 6.7 s | 7.8 s |
| G1 (game) | 1 | 38 | 44 | 836 ms | 7.1 s | 11.2 s |

- An episode is dominated by the JVM start (about 7 s of 8): the engine, not the loopback, is the cost, as the
  feasibility note's stub run said (10 s there was an upper bound; 7 s here).
- 20 episodes of R1 plus R2 took 170 s wall (8.5 s each); R3's 40 took about 5 min.
- Decisions per episode are small because these puzzles are one-turn positions and the random policy often passes
  early (R1's drain puzzles have a 10-action line; random episodes ran 7 to 36 decisions).
- The prompt is resent in full at every step: a drain step holds about 10 KB of messages, so R1 + R2 wrote 3.4 MB
  of trajectory and R3 3.0 MB for 40 episodes. A trainer that stores one prompt per episode and the diffs saves
  most of it (not done).

## The floor: the random policy

Beside any trained policy's solve rate, the same puzzles under the random policy (`.claude/rules/eval.md`):

- `MTGP_08` (Forge's own, tier 1): R3 solved **4/40 = 10%** (Wilson 95%: 4% to 23%); R2 0/8 (Wilson 95%: 0% to 32%). The suite
  (`puzzles-forge-builtin-v1`) records the engine's `random` arm at 7/40 = 17.5% for this puzzle, in the same interval.
  The policies are different random streams, so these are two samples of one rate, not a check of one by the other.
- The three drain puzzles: **0/12** (Wilson 0% to 24%); the suite records `random` at 0/20 for each hard puzzle of the
  hard-gen suite. A GRPO group over them has equal rewards and no gradient: pick puzzles where this floor is strictly
  between 0 and 1, or start from a policy that is not random.
- G1: one game, one loss; no rate.

## What is left

1. **Process rewards.** Only the terminal reward exists. A free label from this env is the positive one on every
   decision of a solved episode (`sft_examples`); per-step winnability is minutes a position
   ([feasibility note](2026-11-06-rlvr-env-feasibility.md), section 3) and is an offline relabel.
2. **Batching many JVMs.** One episode = one JVM and one port, serial. The 7 s start is the cost, so the next step is
   `PZL_SEEDS=1..G` in one JVM with `done` = a new result row (the G attempts of a group share the start) and a pool of
   up to 6 to 10 JVMs (RAM-bound, `docs/eval/CONCURRENCY.md`). The server already keys on messages, but the request
   carries no episode id, so concurrent episodes need one port each.
3. **Determinism per seed.** Not tested here. The feasibility note's argument holds (the engine is a function of the
   seed and the answers), and the stub-free check would be: the same seed and the same answers give the same
   `log_sha256` twice. R1 and R2 rows carry `log_sha256`, so the check needs two runs of one policy stream.
4. **Games.** G1 is one game; there is no game-mode test against the engine beyond it, no seat swap, and the reward
   is win/loss only (a capped game is `valid=False`, never a draw).
5. **Mask.** The legal set is parsed from the prompt (the fake server's `decision_props`). `keep`, `blocks` and
   `card` have no closed list: `blocks` and `card` were not seen in R1 to R3 and G1 (G1 saw `keep` 3 times,
   `option` 33, `options` 2), so their parse and the engine's refusal path are only unit-tested.
6. **A late reply** (above `call_timeout_ms`, 15 min here) costs a retry and then a fixed legal option; the row then
   carries `bridge_down_decisions` and the env marks the episode invalid.

## How this connects to fine-tuning

GRPO groups and rejection-sampling SFT over these episodes, and the random floor to report beside a trained policy:
`eval/rl/README.md` ("Trajectory format"). The random-policy rates above are that floor for these puzzles.
