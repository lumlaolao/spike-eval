# Benchmark research, 2026-10-20 (behind BENCHMARK-V2.md)

Read on 2026-10-20 by the author's session (no subagents, owner's rule), after benchmark sample v1 showed
GPT-6 Luna solving 90% of the rules puzzles with and without tools. The 2026-10-18 note
([eval/research/2026-10-18-benchmark-research.md](../../../eval/research/2026-10-18-benchmark-research.md)) already covers Anthropic's
"Demystifying evals", Miller's error bars, Prime Intellect's `verifiers`, METR, BALROG, Lichess and the prior
MTG work; this note adds what is new, what those sources say that v1 did not act on, and what our own data
says. Marked [secondary] where only coverage was readable.

## 1. What our own data says (the reason for v2)

Sample v1: `eval/puzzles/rules-pilot/benchmark-v1/REPORT.md` (PR #272), 29 active rules puzzles, k=5,
harness-epoch-66, Luna, tools off and `--memory-tools --rules-tool`.

| What | Measured |
|---|---|
| Attempts solved | 130/145 both arms; PUZ 0.809 [0.71, 0.89]; 26/29 puzzles |
| By the author's label, tools off | Easy 29/30 (97%), Medium 50/55 (91%), Hard 51/60 (85%) |
| By the author's label, tools on | Easy 29/30, Medium 53/55, Hard 48/60 |
| Decisions the model made per attempt | median 3, max 12; median 2 options per decision |
| Model time per attempt | median 15 s, max 100 s (`latency_ms` summed over `tier: smart` rows) |
| Tokens per attempt | prompt median 11k (max 46k); completion median 509 (max 5.1k); tools arm 14k / 560 |
| `rules_lookup` calls | 0 in 145 attempts; `inspect` 40 |
| Cost | $0.20 per 145 attempts, about $0.0014 an attempt |
| Floors | random 0.088, PASS 0.000, first option 0.207, Forge AI 0.302 |

The 526 converted rules puzzles (`manifest.jsonl`): solver actions on the proof line 0: 6, 1: 199, 2: 248,
3: 63, 4: 6, 5: 3, 6: 1, so **453 of 526 (86%) are decided by one or two actions**; **56 of 526 proofs beat
any opponent branch** (the rest have no opponent choice at all); 520 unique, 5 `multiple-reviewed`.

The seven puzzles Luna failed at least once (`RP_017` City of Brass mana ability versus trigger, `RP_443`
bolster order, `RP_477` devoid is colourless, `RP_529` layers 7a/7b, `RP_098` layer 4 then SBA, `RP_464`
haunt, `RP_385` copy and counters) are all rules subtleties on short lines: the failures are "did not know
the rule", never "could not find the line". The 37 author-labelled Hard puzzles outside the sample, with
baselines so far (`runs/puzzles/hard-v2-measured.json`, 17 of 37 measured): 16 active, random at most 9%,
Forge AI at most 4%; the floors call them hard, the label did not predict Luna's 85%.

The epoch-67 rerun (the seat no longer told `Goal:Survive` for a kill puzzle, PR #273), 13 puzzles done when
this was written: 61 solves on epoch 66 became 58 (RP_017 2 to 0, RP_098 4 to 5, RP_151 5 to 3): noise-sized.

What the harness shows a puzzle seat today (`eval/puzzle_solve.py`, `LlmSpikeGui.seatFraming`): `LOOKAHEAD=0`
always, so `simulate` is never offered in PUZ; `inspect` and the memory tools are viewer-aware and cannot read
hidden zones (`ContextMemory`: "Tools search only this seat's archived observations and reveals; they cannot
query hidden state"); each of the k attempts is its own JVM with its own seed and no memory carries across;
one attempt is one game, so a wrong line loses and nothing is undone; the loop guards are 32000 thinking
tokens and 300 s a call, 16 tool calls an ask, 30 minutes an attempt (`MODEL-LIMITS.md`); the brief carries
the puzzle's `Name`, `Difficulty` label and author `Description` (19 of the 36 sample descriptions explain the
card text), and until epoch 67 Forge's goal line.

## 2. Anthropic

- **Demystifying evals for AI agents** (2026-01-09; https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
  Beyond what the 10-18 note took: capability evals "should start at a low pass rate to provide a hill to
  climb", regression evals "should sit near 100%", and a capability suite that reaches a high pass rate
  "graduates" into the regression suite; "eval saturation occurs when an agent passes all of the solvable
  tasks, leaving no room for improvement"; build balanced sets ("test both cases where a behavior should
  occur and where it shouldn't"); isolate trials (shared state produced correlated failures: "Claude examined
  git history left by earlier trials"); mature agents "need larger, harder evals to detect smaller effects".
- **The `build-eval` and `hillclimb` skills** (https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/evals/build-eval.md,
  .../eval-hillclimb.md). "Aim for somewhere between fifteen and a hundred inputs for a first eval"; the noise
  floor is about 1/sqrt(n x reps): "25 cases x 2 reps is about ±14 points", 50 x 2 about ±10; split train and
  test at random "stratified by tags[0] - never by baseline score" and check their means agree before round
  1; "cases that always pass or always fail tell you nothing round-to-round"; "never regenerate the
  reference"; ground truth must be "structurally" unreachable from the model under test; stop at a plateau
  of K >= 3 flat rounds with the threshold above the noise floor; "if train went up and test didn't, the
  change overfit", revert; "if the delta is within noise, recommend not merging"; keep failures out of the
  score (truncated rows excluded, errors to `errors.jsonl`).
- **Adding error bars to evals** (Miller 2024; https://arxiv.org/abs/2411.00640), the formulas v1 cited but
  `puzzle_score.py` does not yet apply: clustered SE when questions come in related groups (eq. 4; our puzzles
  cluster by rule family and by source); the paired-difference SE for two arms on the same items (eq. 7,
  SE = sqrt(SE_A^2 + SE_B^2 - 2 SE_A SE_B Corr)); the resampling decomposition Var = (Var(x) + E[sigma_i^2]/K)/n,
  so K beyond a few helps only while the within-item term dominates; the power formula
  n = (z_a/2 + z_b)^2 (omega^2 + sigma_A^2/K_A + sigma_B^2/K_B) / delta^2 (a 3-point gap needs about 969
  items in the worked example).
- **Challenges in evaluating AI systems** (2023; https://www.anthropic.com/news/evaluating-ai-systems):
  format changes alone moved MMLU about 5 points; a bias score of 0 looked like progress until a control
  showed the models were not answering; "after evaluations are implemented, they require constant
  monitoring", for saturation among other things.

## 3. Hillclimb

Two things carry the name. (a) **The hillclimb loop** (Anthropic's skill above; the Maven course "The
Hillclimb: How to Measure and Improve Your AI Agents", Handshake AI, https://maven.com/p/e1761b/the-hillclimb-how-to-measure-and-improve-your-ai-agents):
define success and error tolerance first, "taxonomize and build a golden dataset", write verifiable rubric
criteria, run error analysis at scale, then hill climb; measure against defined criteria, not "vibes". (b)
**Hillclimb the company and its DeepRSI benchmark** (2026-10-12; https://www.deeprsi.com/blog/, coverage at
https://runtimewire.com/article/hillclimb-deeprsi-48-hour-agent-benchmark [secondary]): 15 open-ended ML
tasks, a fixed harness (Pi), no network, 48 hours, two runs per model at the highest reasoning effort; a
**public verifier the agent may run as often as it likes and a private verifier on held-out data that alone
counts**; "overfitting" is reported as the mean public-minus-private gap, with three documented cases of
agents tuning to the public set that the private one caught; a scoring Elo fit by Bradley-Terry over
head-to-head task comparisons, with the first models' ratings frozen as anchors; agents that give up are
auto-resumed; one agent hardcoded a solver into the weights, called a "catastrophic reward hack". The authors
list their own caveats: two runs per model, token use that varied with polling and hung processes, no
confidence intervals.

## 4. Prime Intellect

- **`verifiers` v1 docs** (https://docs.primeintellect.ai/verifiers/v1/overview, /evaluation, /tasksets): an
  environment is a taskset (data and loader) plus a harness ("the program the model is run in") plus rewards
  and metrics on the task; "an Agent is the combination of harness x model x runtime policy which produces a
  Trace"; an eval is `-n` tasks x `num_rollouts` per task, saved as `traces.jsonl` with its `config.toml`;
  `--resume` "re-runs only the rollouts a previous run left missing or errored"; tools can be switched off per
  harness (`disabled_tools`); "if your reward is semantic, use an LLM judge", otherwise code; tasks get a
  durable key for identity. The docs give no guidance on difficulty, seeds or reward hacking.
- **Environments Hub** (https://docs.primeintellect.ai/tutorials-environments/environments,
  https://primeintellect.ai/blog/scaling-environments-program): environments are versioned Python packages
  with pinned dependencies; a reimplemented benchmark is accepted only on "full reproduction of known
  evaluation scores for a relevant subset of models"; difficulty is tiered only by bounty type.
- **A community walkthrough** (https://huggingface.co/blog/anakin87/environments-hub [secondary]): `vf-eval -n 5
  -r 3`; the report is average reward and standard deviation per task; "if a model's reward is consistently
  near zero, the task is probably too hard"; a difference "could be due to random fluctuations", so re-run with
  more samples before believing it; "the scoring logic is a crucial part of the environment".
- The 10-15 note's INTELLECT-3 finding still applies: problems are sorted into easy/normal/hard pools by
  observed solve rate, and prompts always solved or always failed are discarded.

## 5. Hugging Face

- **The evaluation guidebook** (GitHub edition, now marked unmaintained in favour of the December 2025 web
  edition at https://huggingface.co/spaces/OpenEvals/evaluation-guidebook, which did not render for this
  session): "100 samples is usually a minimum for automatic benchmarks"; "take 50 random samples and manually
  inspect them"; "even small changes in semantically equivalent prompts can make the results vary by quite a
  lot", so re-run with prompt variations or spread formats across items of equal difficulty; rule-based
  generation is "a very good way to get a virtually infinite supply of samples and avoid contamination";
  for high-stakes use "you don't want to measure the average performance", look at the worst outputs
  (https://github.com/huggingface/evaluation-guidebook/blob/main/contents/automated-benchmarks/designing-your-automatic-evaluation.md).
- **Tips and tricks** (.../tips-and-tricks.md): "you should assume that a dataset publicly available on the
  internet is or will be contaminated"; use a canary string; provide eval sets "in encrypted or gated forms so
  that they can't be parsed easily by web crawlers"; dynamic benchmarks "regularly updated through time";
  "no method is a foolproof contamination detection method".
- **Troubleshooting reproducibility** (.../troubleshooting/troubleshooting-reproducibility.md): scores fail to
  reproduce through the code base, seeds, metric names that hide different logic, normalisation, prompt
  wording ("a difference of several points for the same model"), few-shot order, chat templates, generation
  parameters and model loading; pin all of them and report them.
- **2025 yearly dive** (yearly_dives/2025-evaluations-for-useful-models.md): games "offer clear pass/fail
  metrics"; final outcomes count more than the tool path; prefer "functional testing rather than model
  judges"; tool access shifts evaluation "from closed book to open book", which is why the two must be
  reported apart.

## 6. Test-time compute, budgets and brute force

- **UK AISI, "More compute, more capability"** (2026-07; https://www.aisi.gov.uk/blog/more-compute-more-capability-why-ai-agent-evals-need-to-account-for-test-time-compute):
  "model capability is not a single score but a curve over test-time compute"; token budgets were swept and
  about 8% of cyber tasks were solved only above 10M tokens; "evaluations should report capability curves,
  especially when performance may still be rising"; report reach and reliability against budget "so a
  genuinely low-capability model can be distinguished from an under-resourced evaluation"; define "minimum
  informative budgets" by checking whether reach has stopped rising; more compute helps most "where an agent
  can check its own work" and little where feedback is absent. A score at a cap is a floor, not a ceiling.
- **ToolMisuseBench** (https://arxiv.org/abs/2604.01508): explicit step, call and retry budgets; success
  reported as a function of the budget cap and summarised as the area under that curve, so "unlimited retry
  behavior" cannot mask instability.
- **Reward Hacking Benchmark** (https://arxiv.org/abs/2605.02964): six exploit categories: leakage/metadata
  ("obtains task answers ... from information that is adjacent to the task"), tampering, sequence manipulation
  ("downstream progress without performing required upstream work"), proxy gaming/parser exploits,
  special-casing visible checks, denial of evaluation; correctness (binary, recomputed outside the sandbox)
  and integrity (from logs) are scored separately, and a run can be both; hardening (randomised intermediate
  outputs, explicit step verification, fail-closed parsing, protected paths) cut exploits 6.5% to 0.8% with
  no change in task success.
- **ARC Prize guide** (https://arcprize.org/guide/1): public, semi-private and private sets "calibrated to be
  roughly the same difficulty ... as measured by human & AI performance"; "100% of tasks in the ARC-AGI-2
  dataset were solved by a minimum of 2 people in less than or equal to 2 attempts"; "be sure not to leak
  information from the evaluation set into your algorithm", including "by repeatedly modifying an algorithm
  while using its evaluation score as feedback".

## 7. Difficulty: how other game benchmarks grade it

- **ChessArena** (https://arxiv.org/abs/2509.24239): 1,008 Lichess puzzles in seven 400-Elo bands of 144;
  a puzzle counts only when every move matches ("even a single error in any step results in failure"); the
  bands separate: O3 97.9 / 90.2 / 79.7 / 62.9 / 46.5 / 10.5 / 1.4 percent from the easiest band to the hardest
  (legal moves given), Gemini-2.5-Pro 37.1 down to 1.4; a human-rating anchor (Maia-1100) 98.6 down to 28.2;
  "no model beats Maia-1100 (human amateur level), and some lose to random play". Difficulty bands are what
  make one number say where a model stops.
- **METR time horizon** (https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/):
  difficulty is human completion time, "strongly predictive of model success"; near 100% under 4 minutes,
  under 10% past about 4 hours; the failure is "stringing together longer sequences of actions", not the
  single steps. Possibility Storm reports its own timings: fastest correct answer 4:52, slowest 34 minutes,
  39% wrong on the most-missed puzzle of 2017, and a puzzle with fewer than five correct answers whose
  difficulty came from "opponent's choice" (https://beta.mtggoldfish.com/articles/possibility-storm-2017-a-year-of-puzzling-in-review,
  https://beta.mtggoldfish.com/articles/possibility-storm-rivals-of-ixalan-7 [secondary]).
- **BALROG** (https://arxiv.org/abs/2411.13543): environments span human mastery times from seconds to
  years; one general prompt "not fine-tuned to any specific LLM"; a fixed history length; standard errors from
  seeds (5 to 25 per task); NetHack progress from human data, because the in-game score "does not effectively
  capture true game progression"; the knowing-doing gap (models answer NetHack questions and still eat the
  rotten food).
- **lmgame-Bench** (https://arxiv.org/abs/2505.15146): contamination tested directly (frame-order recall for
  Mario, transcript recall for Ace Attorney, correlation with score before and after masking and
  paraphrasing); prompt variance "may exceed the ±1 standard deviation range even among empirically optimized
  prompts", cut 34-64% by prompt optimisation; the harness (perception, memory, reflection) lifted the share
  of runs beating random from about 40% to 87%, so the harness is part of the measured object; three runs
  per game.
- **IRT, tinyBenchmarks and ATLAS** (https://arxiv.org/abs/2402.14992, https://arxiv.org/abs/2511.04689):
  item difficulty and discrimination are estimated from the responses of several models; 3-6% of items in
  standard benchmarks have negative discrimination ("annotation errors"); adaptive selection keeps ranking
  precision with far fewer items. Needs more than one model.

## 8. What this adds up to for PUZ

1. Our suite is a regression suite by accident: 90% for one model, 86% of items decided by one or two actions,
   no opponent choice in 470 of 526. Anthropic's rule says a capability suite starts low and the saturated
   one graduates; INTELLECT-3 discards always-solved items; the walkthrough's near-zero items are "too hard".
   PUZ needs both: a regression tier near 100% and a capability tier with headroom.
2. Difficulty is measured, not labelled: structural (line length, decision points, opponent choice points,
   turns), by floors (random, first, Forge AI), and empirically (bands must separate, like ChessArena's; IRT
   once a second model runs). The author's label is a prior only.
3. Hard means long lines and opponent choice (METR, Possibility Storm), not only obscure rules. The failure
   modes to probe are the knowing-doing gap (BALROG), long sequences (METR) and adversarial branches.
4. The budget is part of the result (AISI): record tokens, calls and time per attempt, report the score with
   its budget, and show at least one more budget point when the curve may still be rising.
5. Closed book is the headline, open book beside it (HF 2025, xAI's no-tool/tool series); `simulate` is a
   separate arm with a fixed call budget, since on a puzzle it is engine search (BENCHMARK-V1 said this; it
   must be pinned in the suite file, not left to a flag).
6. Public items are contaminated by assumption (HF); the private split stays out of the public repository,
   the public set carries a canary, and contamination is tested directly (lmgame-Bench: recall the solution
   from the name) and reported as a public-minus-private gap (DeepRSI).
7. The statistics v1 promised and `puzzle_score.py` lacks: clustered intervals by family, paired differences
   between arms, a power line that says what gap the suite can see (Miller; the skill's 1/sqrt(n x reps)).
8. Every failed attempt is read and attributed (harness, infrastructure, model) before it counts; a puzzle no
   model ever solves is audited as probably broken (Anthropic); exploits are logged apart from correctness
   (Reward Hacking Benchmark).
