# Thinking harder or sampling more on hard puzzles? (2026-11-05)

Question: on hard MTG puzzles, is a fixed amount of test-time compute better spent on one attempt that thinks
longer, or on several independent attempts (pass@k)? This is the long-reasoning vs best-of-N question from the
test-time-scaling work, asked inside a real rules engine whose goal check is the verifier. It is a **development-lane
study** (Claude Haiku 5.5 through the Claude Code CLI, `claude-cli:`, `CLAUDE_CLI_SEAT=1`, on the owner's
subscription). Nothing here is a PUZ score, and every cost is the CLI's own notional figure.

**Shows** means a source or a run reports it directly; **Inference** is my reading.

The study was first asked as world model vs sampling (the engine lookahead, `--lookahead`, against pass@k). That arm
cannot run on this lane: `LLMBridgeFactory.claudeCli` refuses `LOOKAHEAD=1` ("Phase 1 offers it no lookups and no
preview": one `claude -p --json-schema` process per turn has no tool loop), and `puzzle_solve.py` pins `LOOKAHEAD=0`.
It was re-scoped to the lever this lane has: how hard the model thinks.

---

## 1. Prior work

- **Repeated sampling scales.** Coverage (any of k solves) grows roughly log-linearly with k over four orders of
  magnitude on coding and math. The gain holds only where a verifier picks the right sample; majority vote and reward
  models plateau (Brown et al. 2024, "Large Language Monkeys", arXiv:2407.21787). pass@k and its unbiased estimator
  1 - C(n-c,k)/C(n,k) come from Chen et al. 2021 (Codex, arXiv:2107.03374).
- **Compute-optimal test-time scaling.** The best split between sequential revision and parallel sampling depends on
  difficulty: on easy problems sequential refinement wins, on the hardest a mix of parallel search and revision does
  better, and a compute-optimal policy beats best-of-N at about 4x less compute (Snell et al. 2024,
  arXiv:2408.03314).
- **Longer thinking as the axis.** RL-trained reasoning models improve with the length of their chain of thought
  (OpenAI, "Learning to reason with LLMs", 2024; DeepSeek-R1, arXiv:2501.12948). Budget forcing (stop or extend the
  thinking) gives a controllable sequential-scaling curve (Muennighoff et al. 2025, "s1", arXiv:2501.19393).
- **Verifiers and best-of-N.** A trained verifier over N samples beats fine-tuning alone on GSM8K (Cobbe et al. 2021,
  arXiv:2110.14168). Here the verifier is exact and free (Forge's puzzle goal), which is the case most favourable to
  sampling.
- **Search with a world model**, the original arm: RAP plans with the LLM as its own world model through MCTS (Hao et
  al. 2023, arXiv:2305.14992); Tree of Thoughts searches over reasoning steps (Yao et al. 2023, arXiv:2305.10601). The
  harness's `PlayerLookahead` would be a ground-truth world model, which neither had.
- **Error bars on evals.** Cluster the standard error by item and report paired differences (Miller 2024, "Adding
  Error Bars to Evals", arXiv:2411.00640); `puzzle_score.py` follows it.

**Inference:** these results are on problems where an attempt is cheap and independent. A Magic puzzle attempt is a
whole game of 14 to 34 model calls (`context_call` rows per finished game in `runs/tvs`, section 4), and pass@k with the
engine as verifier is an oracle a real game never has: you cannot replay a lost game. So pass@k here measures how much
a solver *could* gain from retries, not what it gets in play.

---

## 2. The lever, measured before the design (probes, not runs)

`runs/tvs/probe/` on CLI 2.1.318 (`cli-version.txt`): `bash run_probe.sh` sends `prompt.txt`, a fixed 110-word
mana-counting puzzle, through `claude -p --model claude-haiku-5-5` with the bridge's flags except `--safe-mode`
(`--tools "" --strict-mcp-config --no-session-persistence --setting-sources "" --disable-slash-commands`; whether
leaving `--safe-mode` out changes the thinking was not measured), four runs a setting, six settings, and keeps each
result (`<setting>-<i>.json`); `python3 probe_table.py` prints the table below from the CLI's own count,
`usage.output_tokens_details.thinking_tokens`. All 24 runs returned a result.

**A correction (after merge).** The probe's shell exported `MAX_THINKING_TOKENS=31999`, and `run_probe.sh` does not
unset it, so every setting but the two that set it ran under that budget: the table's "no flag" row is
`MAX_THINKING_TOKENS=31999` with no flag, and the three effort rows carry it too. The bridge's seats never inherit it
(its child environment is an allow-list, `ClaudeCliBridge.childEnv`). So three settings were run again with it unset
(`runs/tvs/probe/unset/`: `run_probe.sh` runs each under `env -u MAX_THINKING_TOKENS`, `probe_table.py` prints this;
the same CLI, 2.1.318; 12 of 12 returned a result):

| Setting, `MAX_THINKING_TOKENS` unset | n | thinking tokens, each run | mean | mean wall |
|---|---|---|---|---|
| `--effort low` | 4 | 1,526 / 5,544 / 14,369 / 12,462 | 8,500 | 45 s |
| no flag (the CLI's own default) | 4 | 5,955 / 8,197 / 7,161 / 18,242 | 9,900 | 52 s |
| `--effort max` | 4 | 40,384 / 21,394 / 26,067 / 19,017 | 26,700 | 118 s |

**Shows:** unset, one max-effort run thought 40,384 tokens, past the 31,999 the exported runs had (two of those sat
at 31,299 and 31,320), so the export may have capped max effort. Max over low is 3.1x unset and 3.8x exported, four
runs a side with wide spread: about 3x to 4x, not a sharper figure. The low budget (1024) still did not bind, so the
finding that the thinking budget is not this lane's lever holds.

| Setting | n | thinking tokens, each run | mean | mean wall |
|---|---|---|---|---|
| `MAX_THINKING_TOKENS=1024` | 4 | 6,091 / 15,805 / 6,524 / 7,976 | 9,100 | 48 s |
| `MAX_THINKING_TOKENS=31999` | 4 | 9,738 / 7,229 / 8,821 / 10,488 | 9,100 | 49 s |
| `--effort low` | 4 | 3,589 / 5,697 / 13,227 / 7,833 | 7,600 | 42 s |
| `--effort high` | 4 | 12,645 / 8,265 / 9,181 / 6,633 | 9,200 | 49 s |
| no flag (`MAX_THINKING_TOKENS=31999` from the shell) | 4 | 8,305 / 6,948 / 5,879 / 8,024 | 7,300 | 40 s |
| `--effort max` | 4 | 31,299 / 31,320 / 23,432 / 28,116 | 28,500 | 128 s |

**Shows:** `MAX_THINKING_TOKENS`, which `--claude-cli-thinking-tokens` sets, does not cap Haiku 5.5's thinking through
this CLI: a budget of 1024 thought 6,091 to 15,805 tokens, and its two settings have the same mean. `--effort` separates
only at the top: max thought about 3.8x what low did (3.1x with the budget unset, the correction above); low, high and
no flag overlap run to run.

**A deviation, recorded:** the design (section 3) was written from a first probe of the same settings whose per-run
results were not kept (it estimated thinking as output tokens less the reply's characters / 4, and read max as 3.4x
low and high as 1.5x). This table is the re-run, made after the study so that every number has a file; the
pre-registration's "about 3.4x" stays as it was written.

So the thinking lever in this study is the CLI's effort (`--claude-cli-effort`), which `puzzle_solve.py` already pins
in the condition (`CLAUDE_BRIDGE_EFFORT`, both tiers), refuses on a resume under another value
(`resume_conflicts`), stamps on every row (`claude_cli_effort`) and `puzzle_score.py` refuses to pool
(`BRIDGE_CONDITIONS`). No new flag was needed. **Inference:** a run that stamps `claude_cli_thinking_tokens: 1024`
for this model records a cap that did not hold (section 7).

---

## 3. Design (pre-registered before any game; this section is committed before the runs start)

- **Puzzles:** the 12 open puzzles of `eval/suites/puzzles-hard-gen-v1.json` that `runs/terse/b2` played (fling 2,
  splice 5, ping 3, storm 2), so the per-attempt compute of a known run sizes this one. Every floor is 0 on all 12
  (random 0/20, Forge AI 0/4, PASS and first option 0). There is no drain or elf board among them.
- **Condition:** the default menu (no `--propose-actions`), `v4-faithful`, the answer tool, no lookups, as
  `puzzle_solve.py` pins it; `claude-cli:claude-haiku-5-5` on both tiers; game cap 180 minutes for every arm (recorded;
  a pair needs one cap, and a max-effort game at about 3x the b2 attempts' up to 25 model-minutes must not be cut
  short); the CLI's 300 s per-call deadline (the lane's default, not changed).
- **Arms:**
  - **A**: k=1, effort low;
  - **B**: k=1, effort max (about 3.4x A's thinking per call, section 2);
  - **C**: k=3, effort low (seeds 1..3; seed 1 is A's seed, so C's first attempt is a replicate of A).
- **Order:** puzzle by puzzle, the three arms rotated per puzzle (A B C, B C A, C A B, ...), one JVM at a time
  (`-j 1`, which the lane requires), each only when the load average is under 6.
- **Primary outcome:** solves per puzzle. The headline contrast is **B pass@1 vs C pass@3** (one long-thinking attempt
  vs three short ones, any of three solving: an oracle verifier, section 1), paired by puzzle. Secondary contrasts: B
  vs A pass@1 (does thinking help at one attempt), and C pass@1 vs A (the same condition twice: the run-to-run noise).
- **Secondary outcome:** compute per attempt and per solve: model calls, completion tokens, model time, wall time,
  from each row's budget fields. The compute match of B against C is stated from these, not assumed.
- **What it can detect:** 12 puzzles, one attempt per arm. A paired difference in solve rate with a per-puzzle
  standard deviation near 0.6 has a standard error near 0.17, so 80% power at alpha 0.05 needs a difference of about
  0.5: six of the twelve puzzles. Anything smaller is reported as not determined. `puzzle_score.py` prints the
  smallest difference each run detects (`mde80`).
- **Stop rule:** a lost decision, a refused tool, a `[TEXT-PARSE]` or a crash stops the study for a report; it is not
  worked around.

---

## 4. Results: stopped by hand at puzzle 3 of 12

**The study did not finish.** On the third puzzle, arm B (effort max) ran into the CLI seat's 300 s per-call deadline:
four calls were cut, in three decisions, and each decision was answered by the model when the bridge restarted its
session. No decision was lost, so the pre-registered stop rule (section 3) was **not** met. The run was stopped by hand
after 61 minutes, on the `[CLAUDE-CLI-NO-ANSWER]` log line, which says the seat takes its fixed legal option even when a
retry follows. That stop is a deviation from the pre-registration, recorded here as one. Run directory `runs/tvs` (kept
by its author in the worktree `tvs-wt`): `A-low-k1`, `B-max-k1`, `C-low-k3`, `driver.log`, and the drivers and
`tvs_compute.py` that wrote the tables below. Every row was played on a `harness-epoch-80` build (the scorer's header
line); main is now at epoch 82, so these rows do not pool with later runs.

### 4.1 What happened to arm B on splice-06da

`B-max-k1/hard-splice-06dad20af5b3/jvm.log`: `[CLAUDE-CLI-NO-ANSWER] no result within 300000 ms -> no answer; the
seat takes its fixed legal option`, four times, in three decisions (twice in one, `jvm.log` lines 61 and 63). Each is
followed by `[BRIDGE Solver/smart] no reply (attempt 2/3) -> restarting session` (lines 37, 62 and 71; line 64 is the
second restart, attempt 3/3, in the decision cut twice) and then the
model's own `answer` call. In the game's 14 `decision` rows no `fallback` is set except the two
`splice(none payable)->none` rows, which are the `[AUTO-LEGAL]` lines 47 and 58 (no splice cost was payable) and are
the `auto=2` of the final tally, `[TALLY] smart=12 fast=0 auto=2 ... avgSmart=306201ms`. The three cut decisions are
the three `context_call` rows over 300 s below (520, 554 and 876 s, each including its restarted session). So the
deadline did not take B's answers; it made its three hardest decisions restart from a fresh session after 300 s or
more, which is a different condition from the one designed, and the override that would lift it
(`CLAUDE_DEADLINE_MS`) is not recorded by the puzzle runner.
`puzzle_watch.py` flagged it `NO-ANSWER x4`. Per-call figures, from each game's `context_call` rows (`elapsed_ms`,
`completion_tokens`):

| arm | puzzle | calls | seconds, median / max | calls at 300 s or more | completion tokens, median / max |
|---|---|---|---|---|---|
| A low | storm-5bf9 | 33 | 6 / 16 | 0 | 751 / 3,174 |
| A low | storm-88f9 | 29 | 5 / 18 | 0 | 511 / 3,205 |
| A low | splice-06da | 16 | 14 / 47 | 0 | 2,475 / 9,843 |
| B max | storm-5bf9 | 32 | 21 / 115 | 0 | 4,568 / 27,863 |
| B max | storm-88f9 | 32 | 27 / 117 | 0 | 6,025 / 27,633 |
| **B max** | **splice-06da** | 12 | **227 / 876** | **3** | **52,530 / 64,031** |
| C low | storm-5bf9 | 95 | 5 / 22 | 0 | 624 / 4,545 |
| C low | storm-88f9 | 87 | 5 / 18 | 0 | 697 / 3,481 |
| C low | splice-06da | 46 | 16 / 50 | 0 | 2,801 / 10,652 |

**Shows:**
- At effort max, a hard splice board's median decision ran 227 s and 52K completion tokens, so the 300 s deadline
  (`ModelCfg`, which the `claude-cli:` seat takes: `ClaudeCliBridge.deadlineFor`) cuts the calls it most needs to
  think about.
- On the two storm boards the same effort stayed under 120 s.
- The prompt is the same size on every board (median 7K to 8K tokens). What grows is the thinking.

**Why the deadline was not a setting to change on this lane** (when the study ran; since fixed, section 5):
- `puzzle_solve.py` refused `--call-timeout-minutes` for a `claude-cli:` model ("... is for a openrouter: or jev: model;
  ... never reads it").
- The bridge's own override, `CLAUDE_DEADLINE_MS`, was a shell variable. `puzzle_solve.py` neither pinned it nor
  recorded it in the manifest, so setting it would have run arm B under a deadline no row records. That is the
  unrecorded condition STANDARDS section 4 forbids.

So arm B, as designed, cannot run on this lane without its hardest decisions restarting from a fresh session after
the deadline.

### 4.2 Solves on the puzzles every arm reached (development lane, not a score)

`python3 eval/puzzles/puzzle_score.py --suite eval/suites/puzzles-hard-gen-v1.json --split open --k K --ids IDS
runs/tvs/<arm>`. Every floor (random, PASS, first) is 0.000, as on all 12 puzzles; Forge's AI solved 0 of 4 on each.

| arm | puzzles | attempts | solved | pass@1 (Wilson 95%) | pass@3 (Wilson 95%) |
|---|---|---|---|---|---|
| A: k=1, low | 3 | 3 | 2 | 0.667 [0.208, 0.939] | - |
| C: k=3, low | 3 | 9 | 6 | 0.667 (bootstrap [0.000, 1.000], clustered [0.013, 1.320]) | 0.667 [0.208, 0.939] |
| B: k=1, max | 2 (storms) | 2 | 2 | 1.000 [0.342, 1.000] | - |

Per puzzle (1 solved, 0 failed; C's attempts in seed order):

| puzzle | A | B | C |
|---|---|---|---|
| storm-5bf9 | 1 | 1 | 111 |
| storm-88f9 | 1 | 1 | 111 |
| splice-06da | 0 | (stopped by hand; no result row) | 000 |

The pre-registered contrasts, on the two storm boards the arms share:
- B - A pass@1: +0.000, 95% CI [0.000, 0.000] (`--paired-with`).
- B pass@1 vs C pass@3: +0.000, CROSS-K. The scorer's line, wrapped and with its padding collapsed (`--k 1 --ids <the
  two storms> runs/tvs/B-max-k1 --paired-with runs/tvs/C-low-k3 --paired-k 3 --paired-metric pass@k`; the scorer names
  the first run A and the paired run B, so its "B pass@3" is arm C and its "A pass@1" is arm B):

  ```
  paired B pass@3 - A pass@1  +0.000  95% CI [0.000, 0.000]  clustered [0.000, 0.000]  (2 puzzles, ids sorted,
  10000 resamples, seed 7)  CROSS-K 1 vs 3 (any of each run's own k: an oracle verifier); floors from k alone:
  random +0.000, PASS +0.000, first +0.000
  ```

Both are zeros on two boards that every attempt of every arm solved. **Shows:** the storm boards are at the ceiling for
this model at either effort, so they cannot separate the arms. The only board that could (splice-06da: A 0, C 0 of 3) is
the one where B's calls ran into the deadline and the game was stopped by hand. C's seed-1 attempt matches A's on all
three boards, which is the replicate the design expected.

### 4.3 Compute: the match was not what the probe predicted

From the rows' budget fields (`tvs_compute.py`; wall time from `driver.log`'s `[end]` lines). B has no splice
row, so the last three rows (the script's "storm only" lines) compare the arms on the two storm boards alone:

| arm | attempts | per attempt: model calls | completion tokens | model minutes | per solve: completion tokens |
|---|---|---|---|---|---|
| A: k=1, low | 3 | 26.0 | 35,521 | 3.6 | 53,281 |
| C: k=3, low | 9 | 25.3 | 36,967 | 3.6 | 55,450 |
| B: k=1, max (storm boards only) | 2 | 32.0 | 264,212 | 19.3 | 264,212 |
| A: k=1, low (storm boards only) | 2 | 31.0 | 27,535 | 3.2 | 27,535 |
| C: k=3, low (storm boards only) | 6 | 30.3 | 28,794 | 3.2 | 28,794 |

**Shows:** on the storm boards one max-effort attempt spent 255K to 274K completion tokens. Three low-effort attempts
on the same board spent 84K to 89K in all. So B cost about three times C's whole budget, not the same.
- The probe's 3x to 4x (section 2) measured one short prompt. On the same storm boards, max effort spent 9 to 11
  times A's completion tokens per attempt (255K to 274K against 24K to 31K).
- Model time per call grew about 4x on the storm boards (medians 21-27 s against 5-6 s).

A compute-matched design on this lane would not use `high`, which thought about as much as low in the probe (1.2x,
inside the run-to-run spread); it would give C about k=7 (264,212 / 36,967, all boards) to k=9 (264,212 / 28,794, storm
boards).

---

## 5. What was not determined

- **The question itself.** Whether one long-thinking attempt beats several short ones at matched compute is not
  determined. The run reached 3 of 12 puzzles; two of them sit at the ceiling, and on the third arm B's game was stopped
  by hand.
- **Effort max on hard boards.** Whether max effort solves splice-06da is not known: its game was stopped by hand
  after the deadline restarted three of its decisions.
- **Compute match.** Effort max does not match k=3 at effort low in compute; it costs about 3x more. `high` thought
  about as much as low in the probe, so it is not a middle setting on this model; it was not measured in games.
- **Families.** The run covered storm and splice only; ping, fling, drain and elf were not reached.
- **`MAX_THINKING_TOKENS`.** Why it does not cap this model's thinking through the CLI (section 2) was not traced.
  The probe shows the effect only: a budget of 1024 thought up to 15,805 tokens.

What would reopen the study:
- A recorded deadline for the CLI seat: done after the study. `--call-timeout-minutes` now sets `CLAUDE_DEADLINE_MS`
  for a `claude-cli:` seat, records it as `record.call_timeout_ms` (refused for a resume, pooling and pairing), and a
  shell value no longer reaches a puzzle game. Still open: a deadline above 5 minutes needs `--k 1` (the post-cap
  grace rule, `docs/eval/PUZZLES.md`), so B can take 20 to 30 minutes (876 s was its longest call) but C at k>1
  cannot take the same deadline until that rule is lifted.
- Then rerun C at k=7 to 9 against B at max, so the compute is matched by measurement.
- Use boards below the ceiling: the storm boards are solved by every attempt.
