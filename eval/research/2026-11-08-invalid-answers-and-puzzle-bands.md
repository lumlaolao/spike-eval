# Invalid-answer taxonomy and puzzle informativeness bands (2026-11-08)

Two free experiments from the 2026-11-08 research report (experiments 3 and 4), run over files already on disk: no
model call, no JVM, no `OPENROUTER_ALLOW_EGRESS`. Both are subcommands of `eval/research_stats.py`
(`invalid-answers`, `puzzle-bands`), tested in `eval/tests/test_research_stats.py`. Nothing in the harness changed.

## Bottom line

- **Invalid answers: the stamped data predates `answer-tool-v12` (harness-epoch 73), so it does not measure the
  harness as it is.** The current `HARNESS_EPOCH` is 97 and there is no tool-asked menu row at a stamped epoch of 73 or
  later on this machine; the current harness is **unmeasured here**. (That claim covers the stamped rows only; the
  rows with no stamp cannot be placed against v12.) Per epoch (menu arm, API models; conditions do not pool):
  epoch 72, the newest stamped: **79 failed of 6,297 tool-asked decisions (1.25%, Wilson 1.01% to 1.56%), 0 lost
  (0.00% to 0.06%)**; epoch 70: 9 of 8,776, 0 lost; epoch 71: 15 asked, none failed (too few to say anything); **epoch
  unknown** (no stamp anywhere; a pool of 627 games from `puzzles/bench-v1`, `bench-v1-e67`, `bench-pilot`,
  `frontier5*`, `demo/luna-*` and `jev-router-pilot`, listed in the tally; the files are dated 2026-10-20 to
  2026-10-29, which does not place them against v12): 10 of 2,931, **2 lost** (0.02% to 0.25%). The 2 lost decisions
  are in one GLM 5.3 game, and **one of them is a harness defect fixed in PR #330** (the seat read only the first
  `answer` call; `docs/harness/FRONTIER5.md`, `docs/harness/ACCEPTANCE.md`, 2026-10-26); the other was the model
  sending only `lines`, twice. **The epoch 70 and 72 denominators are decisions on hard puzzle boards (`runs/puzzles/`
  folders), not games against Forge's AI.** An all-epoch total (2 of 18,019) is printed by the tool as a pooled row
  for reference only; it is not a rate of any one condition and is not quoted as a headline.
- Where the failures were, as they stood at epochs 70 to 72: shape errors (a per-decision schema could have forbidden
  them) are 77 of the 98 first-call failures (79%, 69% to 86%), and every one but the 2 lost was corrected in the same
  conversation; 75 corrected, the cost is one extra round on 0.42% of decisions. The author's decision rule ("shape
  errors above half of the lost decisions") is read per epoch and needs at least 10 lost decisions (the author's
  threshold, labelled); the tool returns no answer below that. It has no lost decision at epochs 70 to 72, and the
  epoch-unknown row has 2 (too few, and pooled), 1 of the 2 a since-fixed bug, so it is not evidence.
- **Recommendation: do not build a per-decision schema on this evidence. The choice that is open is whether to run the
  existing per-decision arm (`ANSWER_SCHEMA=per-decision`) once as an A/B on the Claude CLI development lane (`claude
  -p`, never a score)** (see Decision). Its cache cost on the API models has not been measured.
- **Puzzle bands.** The three hard suites (gen, gen2, gen3: 420 boards) are 100% unreachable by random (99.1% to
  100%) and 0% solved by the first, greedy or Forge-AI arms (0% to 0.9%) in the stored baselines. That is the gate's
  admission rule, not a measurement of the generators, and the Forge AI 0 of 420 is observed, not enforced (the gate
  admits an AI solve of up to 1 in 4). They are **not saturated by zero-model arms, which rests only on those floors
  (no model was run); no board in the stored baselines showed a random solve in 20 to 40 rollouts**. Forge's built-in puzzles and the rules sample are
  saturated by zero-model arms: 14 of 26 and 13 of 29 active puzzles are solved by one (54% and 45%), and 57% and 69%
  sit in the live band.

## Sources

Why these two were run: the 2026-11-08 research report (session scratchpad; not kept in the repository), which read
the public pages below through a summariser, so their numbers are as that summary reported them and were not
re-fetched here.

| Source | Date | Used for |
|---|---|---|
| Prime Intellect, "Learnings from Auditing Public Evaluations", https://www.primeintellect.ai/blog/auditing-public-evaluations | 2026-11-03 | tool parameters described in prose instead of JSON Schema: the idea behind part A |
| Prime Intellect, "Systematic Reward Hacking and Prime Sprints", https://www.primeintellect.ai/blog/reward-hacking | 2026-05-20 | background, no number here rests on it: an unreachable or saturated visible reward speeds hacking, a live gradient slows it: why the bands matter |
| Mechanize, "Cheap RL tasks will waste compute", https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute | 2025-08-18 | background, no number here rests on it: a cheap task wastes the compute spent on it |
| Anthropic, "Demystifying evals for AI agents", https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents | 2026-01-09 | background, no number here rests on it: a 0% pass rate over many trials usually means a broken task |

Repository sources: `forge-overlay/forge-gui-desktop/src/main/java/forge/spike/AnswerTool.java` (`validate`,
`converse`: the messages and the loss rule), `docs/harness/PROMPT-CRAFT.md` (rule 19 and the open question on a
per-decision enum), `docs/eval/PUZZLES.md` and `docs/harness/HARD-PUZZLES.md` (the 20% bar and the hard gate).

## A. Invalid-answer taxonomy

### Method

Population: all 13,687 `game-*.jsonl` under `/home/user/mtg-forge-llm-agent/runs/` (git-ignored; the 23 top-level
entries, mostly `puzzles/`, `propose/`, `harness-acceptance/`, `demo/`). The outputs are saved in
[the data folder](../../docs/harness/evidence/2026-11-08-invalid-answers-and-puzzle-bands/README.md) with the file count and a hash of the
file list. Of 289,884 decision rows, 58,666 carry
`answer_via` (the ask offered the answer tool); the rest are auto-answered, older, or text-lane rows and are not in any
denominator. `answer_via` takes the values `tool`, `nudged` and `lost` on disk (`text` and `reask` never occur);
`answer_invalid` counts invalid calls. A failed decision is one that is `lost`, has `answer_invalid > 0`
(**corrected**: an invalid call, then a valid one), or is `nudged` (a reply with no call, then a call).

The kind of failure is not on the decision row. It is the harness's own message in the `context_tool` rows written
since the previous decision row (`ERROR: ...` on an `answer` call, `NUDGE:`, `LOST:`), sorted by fragments of the
`AnswerTool.validate` source:

| Class | Messages | Could a per-decision schema forbid it |
|---|---|---|
| index | option not on the list, out of range, step not a CAST/ACTIVATE option | yes (enum, bounds) |
| type | not a JSON object, value must be an integer / list / boolean, `yield` value | yes |
| count | pick exactly N, give exactly N amounts, amounts must sum, each target at least 1, cancel must be alone | yes |
| schema | "this decision is answered with `X`; you gave Y", unknown field, FORMAT: this play targets | yes (required field) |
| engine | `ILLEGAL (...)`, blocks not legal together, blocker cannot block attacker | no (the rules decide) |
| no_call | `NUDGE` / `LOST`: the reply called nothing | no (a schema does not force a call) |
| other | everything else (a stale `@handle`, an unmatched text) | no |

`shape` = index + type + count + schema. A decision's **cause** is `no_call` if it was nudged or its loss message was
`LOST`, else the class of the last refused call (lost) or the first (corrected). A failure with no message on disk is
`unrecorded` and never guessed: there were none. A decision that was nudged and then sent an invalid call counts as corrected, with the invalid call's class, so
`no_call` is a floor. Model and epoch come from the game line, else from the folder's
`puzzle-results.jsonl`, else its `batch-manifest.json` (epoch `unknown` when none names it: a pool of
unrelated runs, listed below). Decisions are tallied per **population** and per **harness epoch**, never pooled across either
(`.claude/rules/eval.md`, "Conditions never pool"); the tool prints one row per epoch and marks any row that holds
more than one as `pooled`:

| Population | Games with answer-tool rows | What it is |
|---|---|---|
| menu | 1,027 | API models, the play menu shown, as the condition stood at epochs 70 to 72 and epoch unknown; the only population the rule is applied to |
| propose | 99 | the PROPOSE_ACTIONS arm (menu hidden; owner: exploratory, never a score) |
| dev-lane | 72 | the Claude CLI lane (owner, 2026-10-24: never a score) |
| fixtures | 1,136 | loopback or scripted stand-ins, among them the acceptance canary that answers garbage on purpose |
| unattributed | 3 | a log whose model is `mixed` or `unknown` (21 asks, no failure): neither a fixture nor a model score |

Baseline: the trivial reading of the rule is "no failure is a shape error" (a schema removes nothing). The 50% threshold
and the rule itself are the author's choice for this note, not a repository or owner policy.

### Results

By population, each pooled over the epochs it holds (so these rows are context, not a rate of one condition):

| Population | Epochs held | Tool-asked | Failed | Corrected | Nudged | Lost | Failed rate (Wilson) | Lost rate (Wilson) |
|---|---|---|---|---|---|---|---|---|
| menu (pooled) | 70, 71, 72, unknown | 18,019 | 98 | 75 | 21 | 2 | 0.45% to 0.66% | 0.00% to 0.04% |
| propose | 74, 75 | 641 | 53 | 53 | 0 | 0 | 6.38% to 10.66% | 0.00% to 0.60% |
| dev-lane | 71, 74, 75 | 645 | 6 | 6 | 0 | 0 | 0.43% to 2.01% | 0.00% to 0.59% |
| fixtures | 65, 70, 73 to 76, unknown | 39,340 | 283 | 25 | 0 | 258 | 0.64% to 0.81% | 0.58% to 0.74% |
| unattributed | unknown | 21 | 0 | 0 | 0 | 0 | 0.00% to 15.5% | 0.00% to 15.5% |

**The menu population by harness epoch** (the headline rows; the menu games of epoch 73 and later have no tool-asked
decision on this machine):

| Epoch | Tool-asked | Failed | Corrected | Nudged | Lost | Failed rate (Wilson) | Lost rate (Wilson) | Causes |
|---|---|---|---|---|---|---|---|---|
| 70 | 8,776 | 9 | 9 | 0 | 0 | 0.05% to 0.19% | 0.00% to 0.04% | count 9 |
| 71 | 15 | 0 | 0 | 0 | 0 | 0.00% to 20.4% | 0.00% to 20.4% | |
| **72** | **6,297** | **79** | 64 | 15 | **0** | **1.01% to 1.56%** | **0.00% to 0.06%** | schema 59, no_call 15, count 4, type 1 |
| unknown (pooled) | 2,931 | 10 | 2 | 6 | 2 | 0.19% to 0.63% | 0.02% to 0.25% | no_call 6, schema 3, index 1 |

The epochs run different models, so this is descriptive, not a trend. **These denominators are decisions on
hard-puzzle boards, not games against Forge's AI:** 1,023 of the 1,027 menu games are under `runs/puzzles/` and the
other 4 are `demo/luna-*` games with no epoch stamp; epochs 70 and 72 are all puzzle folders. Epoch 72 is the newest
stamped row; the rows at epoch 73 and later (65, 73 to 76) hold only fixtures, propose or dev-lane games or games with
no tool-asked decision. **The harness since `answer-tool-v12` (73) to the current epoch 97 has no
menu row here: this note says nothing about its rate of failed or lost decisions.**

**The epoch-unknown row is a pool, not a condition.** Nothing in its game files, results files or manifests stamps a
harness epoch. The games it holds (the tool prints this as `sources`, games per folder under `runs/`):

| Folder | Games | Epoch evidence |
|---|---|---|
| puzzles/bench-v1, puzzles/bench-v1-e67 | 290 + 290 | none on the game files; files dated 2026-10-20 |
| puzzles/bench-pilot | 20 | none; dated 2026-10-20 |
| puzzles/frontier5, frontier5-fill, frontier5-long, frontier5-kimi-k2.6-int4 | 7 + 7 + 7 + 1 | none on these games; dated 2026-10-26 |
| demo/luna-mirror-s777, luna-mirror-dt-s4242, luna-elves-goblins-s9191, luna-doomsday-painter-s3131 | 1 each (332 asks) | none; dated 2026-10-29, possibly after the v12 fix |
| puzzles/jev-router-pilot | 1 | none; dated 2026-10-22 |

File dates are modification times (a copy changes them), so they bound nothing. The row's rate is shown for reference
and is tagged `pooled` in the JSON; it is not "the frontier5 puzzle runs" (22 of its 627 games).

Causes of the failed decisions, by population (menu pooled over the four epoch rows above):

| Population | index | type | count | schema | engine | no_call | other |
|---|---|---|---|---|---|---|---|
| menu | 1 | 1 | 13 | 62 | 0 | 21 | 0 |
| propose | 0 | 0 | 0 | 45 | 7 | 0 | 1 |
| dev-lane | 0 | 0 | 0 | 6 | 0 | 0 | 0 |
| fixtures | 16 | 0 | 2 | 0 | 9 | 256 | 0 |
| unattributed | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

- **The two lost menu decisions** are both `z-ai/glm-5.3` on `hard-elf2-2753b3ca0d06`
  (`runs/puzzles/frontier5-fill/z-ai_glm-5.3__hard-elf2-2753b3ca0d06/.../game-1-1793005709709.jsonl`), play
  decisions answered twice with only the note fields `lines` and `why` where `option` was required, in the epoch-unknown
  row. **The first is a harness defect, fixed in PR #330** (`answer-tool-v12`, epoch 73): the reply held two `answer`
  calls, the analysis and then `option` 14, and the seat read only the first (`docs/harness/FRONTIER5.md`, "Decisions the
  harness lost"; `docs/harness/ACCEPTANCE.md`, 2026-10-26). The second is the model's own miss (`lines` twice). So at
  most 1 of the 2 is a loss the current harness could still produce, and both are one game of one model: shape share
  2 of 2, Wilson 34% to 100%. This tool cannot tell the two apart; the attribution is from FRONTIER5.md.
- **Menu first-call failures are 79% shape** (77 of 98; 69% to 86%): 62 schema (mostly `option` where `options` is
  asked, on the multi-pick `sacrifice` decision), 13 count, 1 index, 1 type. The other 21 (21%; 14% to 31%) are
  replies that called nothing, all recovered by the reminder. No engine-refused answer occurred outside the propose arm.
- The fixtures' 258 lost decisions are 256 no-call losses from the acceptance garbage canary (16 per acceptance run)
  and 2 count errors. They are excluded from every model table by design.
- The 231,218 decision rows without `answer_via` are outside this analysis (no tool ask, or a run before the field).

By model (menu population, pooled over epochs 70, 71, 72 and unknown, so descriptive only; models with no failure are one row at the end; rates are failed /
tool-asked, Wilson):

| Model | Asked | Corrected | Nudged | Lost | Failed rate | Cause |
|---|---|---|---|---|---|---|
| gpt-6-luna | 13,261 | 15 | 0 | 0 | 0.07% to 0.19% | count 13, index 1, type 1 |
| claude-sonnet-5.5 | 402 | 21 | 0 | 0 | 3.44% to 7.85% | schema 21 |
| grok-4.7 | 308 | 0 | 11 | 0 | 2.01% to 6.28% | no_call 11 |
| grok-4.6 | 274 | 0 | 4 | 0 | 0.57% to 3.69% | no_call 4 |
| claude-haiku-5.5 | 268 | 12 | 0 | 0 | 2.58% to 7.66% | schema 12 |
| deepseek-v4.1-flash | 268 | 2 | 0 | 0 | 0.20% to 2.68% | schema 2 |
| grok-build-0.1 | 243 | 3 | 0 | 0 | 0.42% to 3.57% | schema 3 |
| gemini-3.8-flash | 242 | 7 | 0 | 0 | 1.41% to 5.85% | schema 7 |
| grok-4.3 | 229 | 14 | 0 | 0 | 3.68% to 10.00% | schema 14 |
| deepseek-v4-pro | 212 | 0 | 2 | 0 | 0.26% to 3.37% | no_call 2 |
| kimi-k2.6 | 211 | 1 | 3 | 0 | 0.74% to 4.77% | no_call 3, schema 1 |
| glm-5.3 | 183 | 0 | 0 | 2 | 0.30% to 3.90% | schema 2 |
| mistral-large-4-0 | 170 | 0 | 1 | 0 | 0.10% to 3.26% | no_call 1 |
| no failure (10 models) | 1,748 | 0 | 0 | 0 | upper limits 1.5% to 3.0% each (124 to 250 asks); jev-router 16% (20 asks) | |

The ten: muse-spark-1.3, gpt-6.1-sol, muse-wonder, kimi-k3, gemini-4-argon, gpt-6.1-sol-pro, glm-5.3-prime,
mistral-medium-3-5, jev-1.14 (124 to 250 asks each) and jev-router (20 asks). Luna's rate is the lowest of the models with a
failure and its causes are different (count errors on a pick-N decision, not wrong field names).

By decision type (menu; types with a failure; 3,330 asks over 30 other types had none):

| Type | Asked | Corrected | Nudged | Lost | Failed rate | Cause |
|---|---|---|---|---|---|---|
| play | 10,726 | 8 | 18 | 2 | 0.18% to 0.38% | no_call 18, schema 9, type 1 |
| sacrifice | 1,502 | 60 | 0 | 0 | 3.12% to 5.11% | schema 48, count 12 |
| confirm-trigger | 929 | 1 | 0 | 0 | 0.02% to 0.61% | schema 1 |
| trigger-order | 610 | 0 | 1 | 0 | 0.03% to 0.92% | no_call 1 |
| scry | 537 | 1 | 0 | 0 | 0.03% to 1.05% | schema 1 |
| cards-for-effect | 180 | 2 | 2 | 0 | 0.87% to 5.57% | no_call 2, schema 2 |
| splice | 157 | 1 | 0 | 0 | 0.11% to 3.52% | schema 1 |
| modes | 34 | 1 | 0 | 0 | 0.52% to 14.92% | count 1 |
| entity | 14 | 1 | 0 | 0 | 1.27% to 31.47% | index 1 |

### Decision

Nothing in the harness changes. The rule as written (the author's: "if shape errors are more than about half of lost
decisions, build a per-decision schema") has no lost decision to apply to at epochs 70 to 72, and its only input (2
lost, one game, in the pooled epoch-unknown row) includes a harness bug that is fixed; the tool now returns no answer
below 10 lost decisions. What a schema would remove is the corrected share at epoch 72,
64 of 6,297 (1.0%), and 75 of 18,019 over all rows (0.42%), each costing one extra round trip; 60 of the 75 are on one
decision type (`sacrifice`, 4% of its asks, measured on hard-puzzle boards, not in games against Forge's AI) and most come from Anthropic and xAI models, plus Luna's count errors. That
is a cost, not a loss of decisions.

**The per-decision schema already exists.** `AnswerTool` keeps the v1 arm: `ANSWER_SCHEMA=per-decision` builds the
`answer` schema from each decision's legal values, off by default and kept for a side-by-side measurement
(`AnswerTool.java`, `perDecision()`; `docs/harness/PLAYER-HARNESS.md`, "Measure": a direct JVM launch only, every launcher
pins the default). **Its cache cost is unmeasured.** The claim that it breaks the prompt cache rests on the design
argument in `docs/harness/PROMPT-CRAFT.md` rule 19 (tool definitions open the cache prefix, so a schema that changes
every decision discards it), and rule 19's open question says the two have not been measured against each other. No
cached-token comparison of the two arms is in the repository or in `runs/` on this machine.

So the choice is not "build a schema" but **whether to run the existing arm once as an A/B on the Claude CLI
development lane** (`claude -p`, `CLAUDE_CLI_SEAT=1`, Haiku or Sonnet; the owner allows model experiments only there
for now; never part of a score). `run_batch.py` cannot run it: `ANSWER_SCHEMA` is in `CONDITION_ENV_KEYS`, so `--env
ANSWER_SCHEMA=...` is refused in any mode, an ambient value is scrubbed and there is no flag. It is a direct launch of
`run-llm-headless.sh` from `$FORGE_HOME` (after `install.sh`), which does not pin it; the `claude-cli:` seat answers
through the same `answer` call, takes no lookups (`MEMORY_TOOLS=0`, no lookahead) and needs no
`OPENROUTER_ALLOW_EGRESS` (no `openrouter:` spec; the state goes to Anthropic through the operator's own login). Not
run here; the variable names are those of `docs/harness/SETTINGS.md`, and the game line stamps
`+per-decision` in the answer-tool version, so the arms cannot be confused:

```bash
cd "$FORGE_HOME"      # one block of six seeds per arm, one game at a time (the game-loop form)
for arm in "" per-decision; do          # "" is the default fixed schema
  for seed in 1 2 3 4 5 6; do
    CLAUDE_CLI_SEAT=1 MEMORY_TOOLS=0 RULES_TOOL=0 ANSWER_SCHEMA="$arm" GAME_SEED=$seed CONTROLLER_B=ai \
      SMART_MODEL_A=claude-cli:haiku FAST_MODEL_A=claude-cli:haiku bash run-llm-headless.sh
  done
done
```

The cost is subscription time and quota, not dollars: every decision is a fresh `claude -p` process, so a game takes
longer than an API game (not measured here). What the block reads is the corrected and lost rate (`answer_via`,
`answer_invalid` on the decision rows). At the rates above six games per arm are too few to resolve a 1% corrected rate
(six games hold on the order of a hundred asks; the 18,019 asks are over 1,027 games), and a CLI seat says little
about the prompt cache that rule 19 of `docs/harness/PROMPT-CRAFT.md` is about (not checked whether its game line
carries cached tokens). A version on the scored model (Luna) would answer the cache question but is paid, needs the
owner's `OPENROUTER_ALLOW_EGRESS` consent and a go-ahead for a model experiment, and is not proposed here. This note
recommends neither run: not running costs nothing and leaves the failure cost at about 1% extra rounds on the data we
have.

**Thresholds in this section are the author's choice for this note, not repository or owner policy.** A "reopen"
trigger the author would use, to be replaced by the owner's: a new block of menu games at the current epoch with 20 or
more lost decisions (if all 20 were shape errors, the Wilson interval on the share is 84% to 100%, about 16 points wide
at this size), or a lost rate for a model we want to score above 0.1% measured over at least 4,000 tool-asked decisions
(at zero losses 4,000 asks bound the rate at 0.10%; with fewer asks the bound cannot resolve it). A cheaper change that
targets the 48 `option`/`options` mix-ups on `sacrifice` is a prompt wording change, not a schema, and is not made here.

## B. Puzzle informativeness bands

### Method

Input: the zero-model arms stored in each puzzle of the frozen suites, `eval/suites/puzzles-hard-gen-v1.json`
(`gen`, 116), `puzzles-hard-gen2-v1.json` (`gen2`, 236), `puzzles-hard-gen3-v1.json` (`gen3`, 68),
`puzzles-forge-builtin-v1.json` (34), `puzzles-rules-sample-v1.json` (`rules`, 36). Each stores `baselines` for
random (20 to 69 rollouts), pass, first, floor, greedy (hard suites only) and ai (4 seeds each). Per puzzle:

- **band** by the random solve rate: *unreachable* (0), *live* (above 0 to 20%, the bar in `docs/eval/PUZZLES.md`),
  *easy* (above 20%: dropped as `random_above_threshold`). A puzzle flagged `unscoreable` (4 in forge-builtin) is
  counted apart.
- **trivially solved** = at least one of first, greedy, ai solved it in at least 1 of its seeds.

Intervals are Wilson over puzzles. Two thresholds, **both the author's choice for this note and fixed before reading the
table, not repository or owner policy**: a family is **not saturated by zero-model arms** if at most 10% of its *active*
puzzles are solved by one (a dropped puzzle is not scored); it gives **an RL start from random a reward** if at least 25%
of its measured puzzles are in the live band. The label rests on the stored floors only: no model was run, so it does
not say a family is informative about a model. The `niche` set (25 hand-authored boards, `eval/puzzles/hard/niche/`) is
in no frozen suite and its run records are not on this machine; its README states floors, but with no run records
behind them this note gives it no label and does not recompute it (README: every floor, random 50 rollouts (20 for the
stack theme) and PASS, first, ai, greedy 4 each, solved 0, except `reequip` by Forge's AI in 1 of 4).

Baseline: the gate that built the hard suites admits a board only if random (0 of 20), PASS, first and greedy (0 of 4)
fail it and Forge's AI solves it at most once in 4 (`docs/eval/PUZZLES.md`, "The generated hard suite"). The hard suites'
100% and 0% for random, first and greedy are therefore the floor of the construction, not a property found. The AI's 0
of 420 is different: the gate would admit up to 1 in 4, so 0 is **observed in the stored baselines, not enforced by the
gate**, and a regenerated suite could contain boards the AI solves once.

### Results

| Family | Measured | Unreachable (random 0) | Live (0 to 20%) | Easy (dropped) | Active trivially solved | Reading |
|---|---|---|---|---|---|---|
| gen | 116 | 116 (97% to 100%) | 0 (0% to 3%) | 0 | 0 of 116 (0% to 3%) | not saturated, no stored random solve |
| gen2 | 236 | 236 (98% to 100%) | 0 (0% to 2%) | 0 | 0 of 236 (0% to 2%) | not saturated, no stored random solve |
| gen3 | 68 | 68 (95% to 100%) | 0 (0% to 5%) | 0 | 0 of 68 (0% to 5%) | not saturated, no stored random solve |
| hard-* (gen+gen2+gen3) | 420 | 420 (99% to 100%) | 0 (0% to 1%) | 0 | 0 of 420 (0% to 1%) | not saturated, no stored random solve |
| forge-builtin | 30 | 9 (17% to 48%) | 17 (39% to 73%) | 4 (5% to 30%) | 14 of 26 (35% to 71%) | saturated by zero-model arms |
| rules | 36 | 4 (4% to 25%) | 25 (53% to 82%) | 7 (10% to 35%) | 13 of 29 (28% to 62%) | saturated by zero-model arms |
| niche (25, README) | 25 | 25 documented | 0 | 0 | 0 of 25 documented (reequip 1 of 4 by the AI) | no label (README only, no run records) |

Every one of the 17 sub-families inside the hard suites reads the same (all boards unreachable, none trivially
solved): gen drain 46, elf 6, fling 5, ping 24, splice 29, storm 6; gen2 atog 15, copy 35, drain2 21, elf2 2, grave 35,
hoofdrain 51, splice2 77; gen3 equip 15, infect 12, landfall 17, tokens 24. The lowest lower bound of any of them is
34% (elf2, 2 boards), so the bands cannot rank the generated families.

- **forge-builtin:** 34 puzzles, 4 unscoreable (no verified line), 4 more dropped for random above 20% (32.5% to 90%),
  26 active. Of the active, 14 are solved by Forge's AI (the first-option arm solves one of those 14). Every
  active puzzle that random never solves (9) is solved by Forge's AI: its random-unreachable band is the AI's.
- **rules:** 36 measured, 29 active, 7 dropped (random 30% to 56%). Of the 29 active, 9 are solved by Forge's AI and 6 by
  the first-option arm (13 distinct). Only 3 active puzzles are both unreachable by random and not trivially solved.
  This matches [irt-pilot](2026-11-05-irt-pilot.md): 18 of 29 are solved by Luna on every attempt.
- **Random rate is itself an estimate:** the hard suites' "0" is 0 of 20 (one gen2 group 40); the upper limit on the true
  random rate is about 16% at 20 rollouts. The niche bar used 50 for "at most 2%" and noted 20 is too few to resolve it.
- The frozen hard files hold only active boards (all 420), although `docs/eval/PUZZLES.md` says the gate's other boards
  stay in the file as dropped; the generators' unfiltered yield is in the gate folders (`runs/puzzles/hard-gen-gate`,
  `hard-gen2-gate`, `hard-gen3-gate`), which this note did not read.

### Decision

- **Not saturated by zero-model arms** (on the stored floors only): the three hard suites (gen, gen2, gen3). A policy that
  plays nothing, plays the first option, previews greedily or is Forge's AI scored 0 on them in the stored baselines
  (the AI figure is observed, not gate-enforced), so a solve there is more likely evidence of play than a solve on the
  other suites; no model was run to confirm it. Their live band is empty: no board in the stored baselines
  showed a random solve in 20 to 40 rollouts. A board's true random rate can still be nonzero (a board that random
  solves 5% of the time shows no solve in 20 rollouts with probability 0.95^20 = 0.36), so this does not say an RL start
  would see no reward; it says the stored rollouts found none. `niche` has no label.
- **Saturated by zero-model arms:** forge-builtin and rules. Over half (54%) and nearly half (45%) of their active
  puzzles are solved by a zero-model arm, so a margin over the random floor there measures much less than a solve on
  the hard suites, and `docs/eval/PUZZLES.md` should not call them hard. Their live bands (57% and 69%) are where an RL
  start from random finds a reward. (Earlier wording called these "training-only"; that was not a rule found here.)
- Not changed in any suite or doc by this note. The unfiltered gate folders and the niche run records are the inputs to
  settle whether the generators are 100% selective or only look it.

## Reproduce

```bash
python3 eval/research_stats.py invalid-answers /path/to/runs        # part A, JSON: per-epoch rows, pooled rows marked
python3 eval/research_stats.py puzzle-bands eval/suites/puzzles-hard-gen-v1.json eval/suites/puzzles-hard-gen2-v1.json \
  eval/suites/puzzles-hard-gen3-v1.json eval/suites/puzzles-forge-builtin-v1.json eval/suites/puzzles-rules-sample-v1.json
python3 eval/tests/test_research_stats.py
```

Both outputs as run on 2026-11-08 are saved in `docs/harness/evidence/2026-11-08-invalid-answers-and-puzzle-bands/`
(`invalid-answers.json`, `puzzle-bands.json`, and its README with the file count, 13,687, and the sha256 of the sorted
file list). Part A reads `runs/`, which is git-ignored and not saved; the counts are from the author's clone on
2026-11-08 and change with the folder. Part B reads committed files and reproduces exactly. Caveat on A: a lost or
corrected decision is visible only as far as `answer_via` and the `context_tool` rows were logged; games without native
tools carry no refusal text, none of which hit a failed decision here. **Not determined:** the failed and lost rates
of the harness at epoch 73 to 97 (no tool-asked menu rows here), and the cache cost of the per-decision arm.
