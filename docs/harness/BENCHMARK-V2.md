# MTG-Bench v2: harder puzzles, a leak-proof seat, a measurable score (proposal, 2026-10-20)

**Status: proposed, for the owner's review before any work starts.** Nothing below is built. It amends
[eval/BENCHMARK-V1.md](../../eval/BENCHMARK-V1.md) (approved 2026-10-18); what v1 decided stays decided unless a row below
says otherwise. The evidence is in
[history/2026-10-20-benchmark-v2-research.md](history/2026-10-20-benchmark-v2-research.md) (today's reading:
Anthropic's eval guides and skills, Hillclimb's DeepRSI, Prime Intellect's `verifiers`, the Hugging Face
guidebook, AISI on test-time compute, ChessArena, METR, BALROG, lmgame-Bench, the Reward Hacking Benchmark) and
in our own sample v1 numbers.

## 1. Why v2

Sample v1 (29 rules puzzles, k=5, GPT-6 Luna) measured 130/145 solved with tools off and 130/145 with them
on, PUZ 0.81. That number cannot move, so it cannot rank models or show a harness change. The reasons are in
the data, not in the model:

| Finding | Evidence | Consequence |
|---|---|---|
| The puzzles are short | 453 of the 526 converted puzzles are decided by one or two actions; the model made a median 3 decisions with a median 2 options each | there is almost nothing to search; a wrong answer is a wrong rule, never a wrong plan |
| No opponent choice | 470 of 526 proofs beat no opponent branch (the opponent had no choice) | the "beat every reply" property v1 required is untested on 90% of the suite |
| Labels do not predict | tools off: Easy 97%, Medium 91%, Hard 85% | the author's label is a prior, not a tier |
| The tools do nothing here | 0 `rules_lookup` calls in 145 attempts; every visible card's text is already in the prompt | the on/off comparison v1 wanted cannot be read from this suite |
| The brief leaks | until epoch 67 `Name` and `Difficulty` went to the seat; a `Description` that explains the card text (19 of 36) still does | part of the solve rate is reading the author's hint |
| The score is saturated | 26 of 29 puzzles solved; Anthropic: a capability eval "should start at a low pass rate" | v1's suite is a regression suite; v2 needs a capability tier |

Where the suite does separate, it is what the research says to expect: the seven puzzles Luna failed are
rules subtleties (a mana ability resolving before a trigger, bolster order, devoid, layers 7a/7b, haunt,
copies and counters). Those stay. What is missing is everything else a player does: lines of five to
fifteen actions, combat with several blockers, an opponent who can respond, more than one turn.

## 2. Owner decisions asked for

| # | Decision | Proposed answer | Why |
|---|---|---|---|
| A | Headline condition | **C0 Bare (closed book)**, reported beside C1 Reference (tools) and C2 Full (`simulate`, fixed budget) as separate numbers; v1 chose C1 | Luna never opens the book; HF 2025: tools move evaluation "from closed book to open book", so the two are reported apart; a C1 headline would be a C0 number with an unmeasured tool arm attached |
| B | Difficulty tiers replace labels | **Three measured tiers** (section 4): T1 regression (expected near 100%), T2 capability, T3 hard; the headline is the mean over T2 and T3 only, T1 reported as a floor check | Anthropic: regression near 100%, capability starts low; ChessArena's bands are what let one number say where a model stops |
| C | Mix | about 20% T1, 40% T2, 40% T3, each tier at least 50 puzzles on the open split | Anthropic skill: 50 cases x 2 reps is about ±10 points; HF: 100 items minimum for a suite |
| D | Held-out split handling | the held-out `.pzl` files **leave the public repository** (a private location the owner names; the suite file keeps only their hashes) and the public files carry a canary string | HF: assume anything public is contaminated; DeepRSI: only the private verifier counts; Anthropic BrowseComp: answers found online |
| E | A model's solve as a proof line | **no**, the proof stays the prover's or a Forge-AI/random replay; a model solve is a candidate's reason to spend prover budget, never its proof | ground truth must be structurally unreachable from the model under test (Anthropic skill); a model-proved puzzle would be proved by the thing it measures |
| F | Second model | needed for discrimination and IRT; the owner names it (v1 decision 4 is still open) | one model cannot show the suite separates strong from weak |
| G | Attempt budget | k=5 attempts a puzzle, pass@1 = c/k the headline, pass^k reported; **no pass@k headline** (that rewards enumeration) | Miller: K beyond a few only shrinks within-item noise; tau-bench pass^k for reliability |
| H | Test-time compute | record tokens, tool calls and model seconds per attempt; the score is always printed with its budget line; one extra budget point (thinking cap halved) on T3 once | AISI: capability is a curve; a score at a cap is a floor |

## 3. The seat in a puzzle: what it may and may not do (the environment contract)

Audited today; each line is either already true (with where it is enforced) or proposed.

| Rule | State | Where |
|---|---|---|
| One attempt is one game: a committed action cannot be undone; the k attempts are independent JVMs with independent seeds and nothing carries across (no memory file, no notes) | true | `puzzle_solve.py` (`PZL_SEEDS`), `ContextMemory` is in-process only |
| No engine search in C0 and C1: `simulate` is never offered (`LOOKAHEAD=0`); C2 offers it with a pinned call budget and is its own arm | true for C0/C1; C2 not yet pinned | `puzzle_solve.py condition()`; **add** `--lookahead N` as a refused-to-mix condition with its budget stamped in the manifest |
| Lookups read nothing hidden: `inspect`, memory search and reveals are viewer-aware; a preview re-deals hidden zones | true | `ContextMemory` ("cannot query hidden state"), lookahead-preview-v6 |
| The brief is neutral: goal and turn limit, the board as the engine shows it, a description that states the objective and nothing about how; no `Name`, no `Difficulty`, no author solution text, no rule citation | **proposed** | `LlmSpikePuzzle.seatBrief` (epoch 67: a rules puzzle gets its turn limit and description only; author hint text in the description remains, and a Forge puzzle keeps Forge's header); the rule and its citation stay in the manifest for the scorer |
| Time and thinking are bounded by loop guards, not by the puzzle: 32000 thinking tokens and 300 s a call, 16 tool calls an ask, 30 minutes an attempt; an attempt that hits a guard is `invalid`, not a model failure | guards true; scoring **proposed** | `MODEL-LIMITS.md`; today `puzzle_score.py` counts an `invalid` row as a miss tagged `capped`; v2 re-runs it and counts it only if it is capped again (then the puzzle, not the model, is the suspect) |
| The budget is recorded per attempt (prompt and completion tokens, tool calls, model ms) in the result row | **proposed** (the game JSONL has it; `pzl-result-v1` does not) | additive fields in `LlmSpikePuzzle`'s result row |
| The grader is the engine: Forge's Puzzle Goal card plus `[acceptance]` on the final state; no judge, no text | true | `PuzzleAcceptance`, `Puzzle.addGoalEnforcement` |
| A solve must beat every opponent reply: the proof is a strategy tree, and the opponent arm in a scored run is the Forge AI on a fixed seed | true for proven puzzles; **extend** by preferring puzzles with `opponent_choice_points >= 1` | `PuzzleSearch.prove`, `opponent_branches_beaten` |
| Exploits are logged apart from correctness: `report_unclear_decision`, `[TEXT-PARSE]`, `LOST`, fallbacks, invalid answers each counted per attempt and shown next to the score | partly (tool-use.txt by hand) | **add** to `puzzle_score.py` as weight-0 metrics (Reward Hacking Benchmark: integrity beside correctness) |
| Reproducibility stamp on every number: engine commit, suite sha, harness epoch and prompt versions, condition, model id and provider, date, k, seeds, temperature (provider default unless set) | mostly | **add** the model's served id and the request's generation parameters to the manifest |

## 4. Difficulty, measured

Every puzzle gets four kinds of difficulty evidence; the tier is computed, never typed in.

**Structural** (from the proof line, free): `L` solver actions, `D` solver decision points with two or more
options, `O` opponent choice points, `T` turns to the acceptance check, `B` the product of menu sizes along the
line. All exist in the prover's output today except `D` and `B`, which the replay can count.

**Floors** (free, already run): random, PASS, first-option, Forge AI solve rates on 50 rollouts.

**Empirical** (paid, after a run): pass@1 per model; once two or more models have run, a two-parameter IRT
fit gives each puzzle a difficulty and a discrimination, and an item with negative discrimination is audited
as broken (ATLAS: 3-6% of items in standard benchmarks).

**Tiers** (proposed thresholds; refit after the first full run so the bands separate the way ChessArena's do):

| Tier | Rule | Role | Expected for a strong model |
|---|---|---|---|
| T1 regression | `L <= 2`, `O = 0`, Forge AI solves at least half | the floor check; a failure here is read as a harness suspect first | near 100% |
| T2 capability | `L` 3 to 5, or `O >= 1`, or `T >= 2` | the hill | 40-80% |
| T3 hard | `L >= 6`, or `O >= 2`, or `B >= 10^4` | headroom; the Possibility Storm class | under 40% |

Random must stay at or under 20% for any active puzzle (v1 said 5%; 20% is what the suite uses today and
the margin `pass@1 - random` already corrects for it; **owner decision**: tighten back to 5% for T2/T3 and
keep 20% for T1 only).

**Skill tags** (one or more a puzzle, from the rule text and the proof line; the score is also reported per
tag with its own floors): rules application (layers, replacement, triggers, SBAs); sequencing (order of casts,
activations, mana, untaps); combat arithmetic (attackers, blockers, pumps, trample, deathtouch); long lines
(storm, loops, six or more actions); adversarial (the opponent can respond or block); multi-turn (survive, then
win); restraint (the right line is to pass, not block, or not cast: Anthropic's "cases where a behavior should
not occur"); hidden information (play around an unknown card; acceptance by expectation is out of scope for v2).

## 5. Where the hard puzzles come from

**The detailed plan for puzzles of 10 to 15 actions, found online and built by us, is
[HARD-PUZZLES.md](HARD-PUZZLES.md)** (the bar and its checks, the sources, the generator families, the gate
work the prover needs first). The table below is the summary.

| Source | How | Yield expected | Cost |
|---|---|---|---|
| Forge built-ins (282 Possibility Storm, 49 PC, 29 PP, 8 MTGP) | baselines running now (`forge-mine-e67`); then the prover at budget 3000+ on the unsolved, with two prover improvements below | tens of T3 puzzles; v1's prove-v1 got 9 of 34 at budget 1500 | free, CPU-bound |
| Mined from the model's own games | `harness-audit`'s PUZZLE candidates and every failed T2/T3 attempt become a position with an acceptance state, proved by the prover | by construction what the model is bad at | free after the games |
| Authored hard (the `h_*` drafts) | lines of six or more actions built around one trick, proved and gated like the rules puzzles; the two drafts so far were not found at budget 600 and are re-run at 3000 | about 70% of drafts pass (the x/y/z batches) | author time |
| Adversarial variants | a proven puzzle plus one opponent response (an instant in hand, an untapped blocker, an activated ability); the solve must now beat the reply | one per T2 puzzle where the trick survives | free |
| Generated families | parameterised combat and mana puzzles (N attackers against M blockers with random stats, exact lethal; X spells with a mana puzzle), boards drawn at random, proved by the prover | unlimited, uncontaminated (HF: rule-based generation); used for T1/T2 calibration and the regression tier, capped at a share of the suite so one trick does not dominate | free |
| The 526 rules puzzles | kept; re-tiered by the rule above (most land in T1), with the brief made neutral | T1 and the rules tag | free |

**Prover improvements the hard sources need** (free, overlay only, tests first): (1) iterative deepening on
`L`: search lines of length 1, then 2, ... so a six-action line is found before the budget is spread over
all depths; (2) the Forge AI's own solving line, and a random rollout's solving tape, used as the UCT prefix
when one exists (today the Forge AI line is used only as a reference replay); (3) budget as rollouts and
wall-clock, both recorded, so "not found" states the budget it was not found under.

## 6. The measurement protocol

1. **Conditions** pinned in the suite file and the manifest, refused when mixed (as `run_batch.py` does):
   C0 bare, C1 reference (`--memory-tools --rules-tool`), C2 full (C1 plus `simulate` with a pinned call
   budget). The headline is C0 (decision A). Tool leverage = C1 minus C0 and C2 minus C0, paired by puzzle.
2. **k=5** attempts a puzzle on seeds 1-5; pass@1 the headline, pass^5 the reliability number, the random
   margin printed beside both. An attempt that hits a loop guard or a provider error is `invalid` and the
   puzzle is re-run, never scored as a miss (Anthropic skill: errors out of the score).
3. **Intervals**: the bootstrap over puzzles stays; add Miller's clustered SE with the family (rule area or
   source) as the cluster, the paired-difference SE for any two arms on the same puzzles, and a power line
   ("this suite at k=5 resolves a gap of about X points between two arms at 95%"). A reported difference
   under its paired interval is reported as "not resolved".
4. **Budget line**: every score prints median and maximum tokens, tool calls and model seconds per attempt,
   and the guards in force. Once per release, T3 is re-run at half the thinking cap: if the score falls, the
   curve is still rising and the report says so (AISI).
5. **Transcripts**: every failed T2/T3 attempt is read before the number is published and attributed as
   harness, infrastructure or model (the `harness-audit` skill); only model failures count; harness failures
   invalidate the run as `defect_analyzer.py` does for games. A puzzle every model fails on every attempt is
   audited as probably broken before it stays.
6. **Contamination**: public items carry a canary; the held-out split is private (decision D); for Forge
   built-ins, a name-only recall probe (the puzzle's name and date, no board) is run once per model, and a
   puzzle the model can describe the solution of is tagged `recalled` and excluded from the headline; the
   public-minus-held-out gap is reported as the contamination number (DeepRSI).
7. **Prompt sensitivity**: once, the brief is paraphrased two ways on a 30-puzzle slice; the spread is
   reported next to the interval (lmgame-Bench: prompt variance can exceed ±1 SD).
8. **Reproducibility**: suite sha, engine commit, epoch and prompt versions, model id as served, generation
   parameters, seeds, date, condition, k and budget on every number; `puzzle_baselines.py verify` after any
   engine or epoch change retires a puzzle whose line no longer replays.
9. **Saturation watch**: a tier whose pass@1 for the best model exceeds 90% on two consecutive releases
   graduates to regression and the hard sources refill the tier (Anthropic).

## 7. Phases, in order

| Phase | Work | Needs | Cost |
|---|---|---|---|
| 0 | Land PR #272 (tools guard, sample v1 data) and #273 (goal line); finish the epoch-67 rerun; this plan reviewed | running | ~$0.40 |
| 1 | **Difficulty v2 in the record**: `L`, `D`, `O`, `T`, `B` and skill tags in `puzzle.v1` records (additive), the tier rule in `puzzle_format.py`, the 526 re-tiered from their proofs; tests first | nothing paid | $0 |
| 2 | **The neutral brief and the budget fields**: `seatBrief` drops author hint text (a rules puzzle's name and label went in epoch 67); `pzl-result` gains tokens, calls, ms; epoch bump; `puzzle_score.py` refuses invalid rows, adds clustered and paired intervals, the power line, the budget line and the integrity metrics | owner decision A, G, H | $0 |
| 3 | **Hard sources**: prover improvements (1)-(3); Forge built-ins at budget 3000; the `h_*` drafts; adversarial variants of 30 T2 puzzles; one generated combat family (50 boards); mined positions from the failed attempts so far | CPU time (days) | $0 |
| 4 | **Splits and contamination**: held-out files moved out, canary added, recall probe written | owner decision D | ~$0.50 |
| 5 | **Pilot v2**: 60 puzzles (20 a tier) x k=5 x C0 and C1 on Luna; read every T2/T3 failure; refit the tier thresholds so the bands separate | phases 1-4 | ~$1 |
| 6 | **Freeze v2 and the full run**: about 300 open puzzles x 5 x C0, C1, C2 for Luna, then the second model (decision F) | phase 5 | ~$5 a model at today's $0.0014 an attempt; wall clock is the constraint, about 50 puzzle-attempts an hour at `-j 2` |
| 7 | Games panel as the validity check (v1 phase 7, unchanged) | owner | $50-100 |

Paid phases state their cost first and run with `--budget-usd` (`CLAUDE.md`). Every phase lands through a PR with
the three review skills; the skills are run by the author's session and say so until the owner allows subagents.

## 8. What v2 does not do

- It does not grade with a model judge, give partial credit, or score the model's text: the engine decides.
- It does not add heuristics, hints or search to the seat to lift the score: the seat is measured as it is.
- It does not score wall-clock time (v1 decision 3) or let cost cap thinking (`MODEL-LIMITS.md`).
- It does not pool conditions, epochs or engine versions into one number.
- It does not publish Possibility Storm puzzles (v1 decision 5).
