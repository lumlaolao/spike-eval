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
whole game of 20 to 46 model calls (section 4), and pass@k with the engine as verifier is an oracle a real game never
has: you cannot replay a lost game. So pass@k here measures how much a solver *could* gain from retries, not what it
gets in play.

---

## 2. The lever, measured before the design (probes, not runs)

Three probes in `<scratchpad>/probe/` on CLI 2.1.318. Each sends a fixed 140-word mana-counting puzzle through `claude
-p --model claude-haiku-5-5` with the bridge's flags (`--tools "" --strict-mcp-config --no-session-persistence
--setting-sources "" --disable-slash-commands`). Thinking is approximated as output tokens minus the result's
characters / 4.

| Setting | n | thinking tokens (approx.), each run | mean |
|---|---|---|---|
| `MAX_THINKING_TOKENS=1024` | 4 | 18,208 / 10,801 / 8,230 / 18,085 | 13,800 |
| `MAX_THINKING_TOKENS=31999` | 4 | 4,161 / 9,747 / 13,575 / 7,944 | 8,900 |
| `--effort low` | 4 | 4,234 / 8,023 / 15,061 / 11,354 | 9,700 |
| `--effort high` | 4 | 18,871 / 10,136 / 17,794 / 10,943 | 14,400 |
| CLI default (no flag) | 3 | 8,895 / 10,222 / 10,262 | 9,800 |
| `--effort max` | 3 | 29,049 / 35,871 / 35,798 | 33,600 |

**Shows:** `MAX_THINKING_TOKENS`, which `--claude-cli-thinking-tokens` sets, does not cap Haiku 5.5's thinking through
this CLI: a budget of 1024 thought 18,208 tokens. Its two settings do not separate. `--effort` does, at the top:
max thought about 3.4x what low did, and low is about the CLI default.

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

## 4. Results

(pending: the runs)

---

## 5. What was not determined

(pending)
