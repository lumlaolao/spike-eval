# Invalid-answer taxonomy and puzzle informativeness bands (2026-11-08)

Two free experiments from the 2026-11-08 research report (experiments 3 and 4), run over files already on disk: no
model call, no JVM, no `OPENROUTER_ALLOW_EGRESS`. Both are subcommands of `eval/research_stats.py`
(`invalid-answers`, `puzzle-bands`), tested in `eval/tests/test_research_stats.py`. Nothing in the harness changed.

## Bottom line

- **Invalid answers.** On the benchmark's own condition (menu arm, API models) the seat lost **2 of 18,019**
  tool-asked decisions (Wilson 0.00% to 0.04%), both in one GLM 5.3 game, both a shape error. Read literally, the
  decision rule ("shape errors above half of the lost decisions") fires on 2 of 2, but 2 decisions in one game is not
  evidence: the interval on the share is 34% to 100%. Shape errors are 77 of the 98 first-call failures (79%, 69% to
  86%), and every one but those 2 was corrected in the same conversation. **Recommendation: do not build a
  per-decision JSON Schema as its own harness version on this evidence.** The cost it would remove is one extra round
  on 0.42% of decisions (75 corrected, 0.33% to 0.52%), and the harness's fixed schema was chosen to keep the prompt
  cache (`docs/harness/PROMPT-CRAFT.md`, rule 19). Reopen if a new block of menu games loses 20 or more decisions.
- **Puzzle bands.** The three hard suites (gen, gen2, gen3: 420 boards) are 100% unreachable by random (99.1% to
  100%) and 0% solved by the first, greedy or Forge-AI arms (0% to 0.9%), but that is the gate's admission rule, not a
  measurement of the generators. They are **PUZ-informative and give an RL start from random no reward**. Forge's
  built-in puzzles and the rules sample are **training-only**: 14 of 26 and 13 of 29 active puzzles are solved by a
  zero-model arm (54% and 45%), and 57% and 69% sit in the live band.

## Sources

Why these two were run: the 2026-11-08 research report (session scratchpad; not kept in the repository), which read
the public pages below through a summariser, so their numbers are as that summary reported them and were not
re-fetched here.

| Source | Date | Used for |
|---|---|---|
| Prime Intellect, "Learnings from Auditing Public Evaluations", https://www.primeintellect.ai/blog/auditing-public-evaluations | 2026-11-03 | tool parameters described in prose instead of JSON Schema: the idea behind part A |
| Prime Intellect, "Systematic Reward Hacking and Prime Sprints", https://www.primeintellect.ai/blog/reward-hacking | 2026-05-20 | an unreachable or saturated visible reward speeds hacking, a live gradient slows it: why the bands matter |
| Mechanize, "Cheap RL tasks will waste compute", https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute | 2025-08-18 | a cheap task wastes the compute spent on it |
| Anthropic, "Demystifying evals for AI agents", https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents | 2026-01-09 | a 0% pass rate over many trials usually means a broken task |

Repository sources: `forge-overlay/forge-gui-desktop/src/main/java/forge/spike/AnswerTool.java` (`validate`,
`converse`: the messages and the loss rule), `docs/harness/PROMPT-CRAFT.md` (rule 19 and the open question on a
per-decision enum), `docs/eval/PUZZLES.md` and `docs/harness/HARD-PUZZLES.md` (the 20% bar and the hard gate).

## A. Invalid-answer taxonomy

### Method

Population: all 13,687 `game-*.jsonl` under `/home/user/mtg-forge-llm-agent/runs/` (git-ignored; the 23 top-level
entries, mostly `puzzles/`, `propose/`, `harness-acceptance/`, `demo/`). Of 289,884 decision rows, 58,666 carry
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
`unrecorded` and never guessed: there were none. Model and epoch come from the game line, else from the folder's
`puzzle-results.jsonl`, else its `batch-manifest.json` (epoch `unstamped` when none names it: the frontier5 puzzle
runs). Decisions are tallied per **population** and never pooled (`.claude/rules/eval.md`, "Conditions never pool"):

| Population | Games with answer-tool rows | What it is |
|---|---|---|
| menu | 1,027 | API models, the play menu shown: the benchmark's condition, and the only one the rule is applied to |
| propose | 99 | the PROPOSE_ACTIONS arm (menu hidden; owner: exploratory, never a score) |
| dev-lane | 72 | the Claude CLI lane (owner, 2026-10-24: never a score) |
| fixtures | 1,139 | loopback or scripted stand-ins, among them the acceptance canary that answers garbage on purpose |

Baseline: the trivial reading of the rule is "no failure is a shape error" (a schema removes nothing); the threshold is
50% of lost decisions.

### Results

| Population | Tool-asked | Failed | Corrected | Nudged | Lost | Failed rate (Wilson) | Lost rate (Wilson) |
|---|---|---|---|---|---|---|---|
| menu | 18,019 | 98 | 75 | 21 | 2 | 0.45% to 0.66% | 0.00% to 0.04% |
| propose | 641 | 53 | 53 | 0 | 0 | 6.38% to 10.66% | 0.00% to 0.60% |
| dev-lane | 645 | 6 | 6 | 0 | 0 | 0.43% to 2.01% | 0.00% to 0.59% |
| fixtures | 39,361 | 283 | 25 | 0 | 258 | 0.64% to 0.81% | 0.58% to 0.74% |

Causes of the failed decisions, by population:

| Population | index | type | count | schema | engine | no_call | other |
|---|---|---|---|---|---|---|---|
| menu | 1 | 1 | 13 | 62 | 0 | 21 | 0 |
| propose | 0 | 0 | 0 | 45 | 7 | 0 | 1 |
| dev-lane | 0 | 0 | 0 | 6 | 0 | 0 | 0 |
| fixtures | 16 | 0 | 2 | 0 | 9 | 256 | 0 |

- **The two lost menu decisions** are both `z-ai/glm-5.3` on `hard-elf2-2753b3ca0d06`
  (`runs/puzzles/frontier5-fill/z-ai_glm-5.3__hard-elf2-2753b3ca0d06/.../game-1-1793005709709.jsonl`), play
  decisions answered twice with only the note fields `lines` and `why` where `option` was required. Both lost
  decisions are one game of one model: shape share 2 of 2, Wilson 34% to 100%.
- **Menu first-call failures are 79% shape** (77 of 98; 69% to 86%): 62 schema (mostly `option` where `options` is
  asked, on the multi-pick `sacrifice` decision), 13 count, 1 index, 1 type. The other 21 (21%; 14% to 31%) are
  replies that called nothing, all recovered by the reminder. No engine-refused answer occurred outside the propose arm.
- The fixtures' 258 lost decisions are 256 no-call losses from the acceptance garbage canary (16 per acceptance run)
  and 2 count errors. They are excluded from every model table by design.
- The 231,218 decision rows without `answer_via` are outside this analysis (no tool ask, or a run before the field).

By model (menu population; models with no failure on 100 or more asks are one row at the end; rates are failed /
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
| no failure (9 models) | 1,862 | 0 | 0 | 0 | 0.00% to 3.0% or less each | |

The nine: muse-spark-1.3, gpt-6.1-sol, muse-wonder, kimi-k3, gemini-4-argon, gpt-6.1-sol-pro, glm-5.3-prime,
mistral-medium-3-5, jev-1.14 (jev-router, 20 asks, also none). Luna's rate is the lowest of the models with a
failure and its causes are different (count errors on a pick-N decision, not wrong field names).

By epoch (menu): epoch 70, 8,776 asked, 9 failed (0.05% to 0.19%, all count); epoch 71, 15 asked, 0; epoch 72, 6,297
asked, 79 failed (1.01% to 1.56%: schema 59, no_call 15, count 4, type 1); unstamped (the frontier5 puzzle runs), 2,931
asked, 10 failed (0.19% to 0.63%) and **both lost decisions**. Epochs 73 to 76 and 65 have game lines but no tool-asked
rows. The models differ by epoch, so this is descriptive, not a trend.

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

The rule as written ("if shape errors are more than about half of lost decisions, build a per-decision schema") is
met on 2 of 2 and is not worth acting on: the 2 are one game, and the loss it is meant to cut is 0.011% of asks.
What a schema would remove is the corrected share, 75 decisions (0.42%) each costing one extra round trip, 60 of them
on one decision type (`sacrifice`, 4% of its asks) and most from three Anthropic and xAI models and the Luna count
errors. That is a cost, not a loss of decisions. Against it, a schema built per decision discards the cache prefix
every call (`AnswerTool` v2, `PROMPT-CRAFT.md` rule 19) and is a legality hint the owner rule must be checked against.
**Not built.** What would reopen it: a new block of menu games whose lost decisions reach 20 or more (an interval on
the shape share narrower than about 25 points), or a lost rate above 0.1% for a model we want to score. A cheaper
change that targets the 48 `option`/`options` mix-ups on `sacrifice` is a prompt wording change, not a schema, and is
not made here.

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

Intervals are Wilson over puzzles. Two rules fixed before reading the table: a family is **PUZ-informative** if at
most 10% of its *active* puzzles are trivially solved (a dropped puzzle is not scored); it gives **an RL start from
random a reward** if at least 25% of its measured puzzles are in the live band. `niche` (25 hand-authored boards,
`eval/puzzles/hard/niche/`) is in no frozen suite and its run records are not on this machine, so it is not recomputed:
its README states every floor (random 50 rollouts, 20 for the stack theme; PASS, first, ai, greedy 4 each) solved 0,
except `reequip` by Forge's AI in 1 of 4.

Baseline: the gate that built the hard suites admits a board only if random (0 of 20), PASS, first and greedy (0 of 4)
fail it and Forge's AI solves it at most once in 4 (`docs/eval/PUZZLES.md`, "The generated hard suite"). The hard suites'
100% and 0% are therefore the floor of the construction, not a property found.

### Results

| Family | Measured | Unreachable (random 0) | Live (0 to 20%) | Easy (dropped) | Active trivially solved | Reading |
|---|---|---|---|---|---|---|
| gen | 116 | 116 (97% to 100%) | 0 (0% to 3%) | 0 | 0 of 116 (0% to 3%) | PUZ-informative, no RL reward |
| gen2 | 236 | 236 (98% to 100%) | 0 (0% to 2%) | 0 | 0 of 236 (0% to 2%) | PUZ-informative, no RL reward |
| gen3 | 68 | 68 (95% to 100%) | 0 (0% to 5%) | 0 | 0 of 68 (0% to 5%) | PUZ-informative, no RL reward |
| hard-* (gen+gen2+gen3) | 420 | 420 (99% to 100%) | 0 (0% to 1%) | 0 | 0 of 420 (0% to 1%) | PUZ-informative, no RL reward |
| forge-builtin | 30 | 9 (17% to 48%) | 17 (39% to 73%) | 4 (5% to 30%) | 14 of 26 (35% to 71%) | training-only |
| rules | 36 | 4 (4% to 25%) | 25 (53% to 82%) | 7 (10% to 35%) | 13 of 29 (28% to 62%) | training-only |
| niche (25, README) | 25 | 25 documented | 0 | 0 | 0 of 25 documented (reequip 1 of 4 by the AI) | PUZ-informative, no RL reward; not recomputed |

Every one of the 19 sub-families inside the hard suites reads the same (all boards unreachable, none trivially
solved): gen drain 46, elf 6, fling 5, ping 24, splice 29, storm 6; gen2 atog 15, copy 35, drain2 21, elf2 2, grave 35,
hoofdrain 51, splice2 77; gen3 equip 15, infect 12, landfall 17, tokens 24. The lowest lower bound of any of them is
34% (elf2, 2 boards), so the bands cannot rank the generated families.

- **forge-builtin:** 34 puzzles, 4 unscoreable (no verified line), 4 more dropped for random above 20% (37.5% to 90%),
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

- **PUZ-informative:** the three hard suites (gen, gen2, gen3) and, from its README, the niche set. A policy that plays
  nothing, plays the first option, previews greedily or is Forge's AI scores 0 on them, so a solve is evidence of play.
  Every informative family has an empty live band: an RL run that starts from a near-random policy sees reward 0 on
  420 of 420 hard boards (upper limit 0.9% of boards with any reward) and needs a curriculum (shorter or hinted
  siblings) or a better starting policy before it can use them.
- **Training-only:** forge-builtin and rules. Over half (54%) and nearly half (45%) of their active puzzles are solved by
  a zero-model arm, so a margin over the random floor there measures much less than a solve on the hard suites, and
  `docs/eval/PUZZLES.md` should not call them hard. Their live bands (57% and 69%) are where an RL start from random
  finds a reward.
- Not changed in any suite or doc by this note. The unfiltered gate folders and the niche run records are the inputs to
  settle whether the generators are 100% selective or only look it.

## Reproduce

```bash
python3 eval/research_stats.py invalid-answers /path/to/runs        # part A, JSON
python3 eval/research_stats.py puzzle-bands eval/suites/puzzles-hard-gen-v1.json eval/suites/puzzles-hard-gen2-v1.json \
  eval/suites/puzzles-hard-gen3-v1.json eval/suites/puzzles-forge-builtin-v1.json eval/suites/puzzles-rules-sample-v1.json
python3 eval/tests/test_research_stats.py
```

Part A reads `runs/`, which is git-ignored; the counts above are from the author's clone on 2026-11-08 and change
with the folder. Part B reads committed files and reproduces exactly. Caveat on A: a lost or corrected decision is
visible only as far as `answer_via` and the `context_tool` rows were logged; games without native tools carry no
refusal text, none of which hit a failed decision here.
