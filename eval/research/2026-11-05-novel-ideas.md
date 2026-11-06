# Novel ML, agent, eval and RL ideas for spike-eval (research note, 2026-11-05)

Read-only research against `origin/main` at 06a4eac. Nothing was built, run or committed. The ideas are kept only if
they fit the owner rules: no heuristic decides for the model, Forge's AI never falls back, no legal action is hidden
outside a measurement arm, every number carries its floor and interval, GPT-6 Luna is the game model, `claude-cli:` is
a dev lane only, and cost is stated up front.

**Already in the repo or already proposed, so not proposed again:** the propose arm and its terse/detailed refusal
arms; the cross-decision error notebook (survey E1); compute-matched lookahead vs best-of-N vs LATS (E3); the
model-predicted vs engine-computed world-model fidelity score (E4); engine-labelled contrast sets and the built
labeller (E5, rules-probe section 9); state-tracking drift (E6); counterfactual flip tests (E7); refusal policy as
reward design (E8); thinking vs sampling (pass@k vs effort); 2PL IRT once two paid models have run (BENCHMARK-V2
section 4); tier rules, skill tags and the recall probe for Forge built-ins (BENCHMARK-V2 section 6); Bradley-Terry
ratings (`rating.py`); SPRT and seed-paired panels (EVAL-DESIGN section 6); generated hard families
(`puzzle_gen.py`); and the harness-building environment.

**How sources are tagged:** **[abstract]** means the arXiv abstract page was fetched and read. **[snippet]** means
only search results or a secondary summary were seen. Treat any number from a snippet as unverified until the paper
itself is read.

---

## Ranked summary

Value is the value to the benchmark and feasibility is feasibility here, each scored 1 to 5. The rank is their
product.

| # | Idea | Area | Value | Feas. | First experiment, cost |
|---|---|---|---|---|---|
| 1 | Variance-reduced STR: a same-seed Forge-AI control variate, AIVAT chance corrections, anytime-valid stopping | Eval | 5 | 5 | zero-model, **free** |
| 2 | Step regret on puzzle attempts: the first decision that loses a won position, labelled by the engine and the family solvers | Eval / process reward | 5 | 4 | **free** (CPU over existing runs) |
| 3 | Twin and invented cards: contamination-proof puzzles and a recall-vs-reading delta | Eval | 5 | 4 | free build, then about $0.10 of Luna |
| 4 | IRT calibrated by a free population of respondents, adaptive testing, and a rules-vs-play factor | Eval | 4 | 4 | **free** (FRONTIER5 rows plus zero-model arms) |
| 5 | Self-forecast calibration: shadow P(win) and P(solve) probes scored with Brier/AUROC against an engine-rollout reference | Eval / metacognition | 4 | 4 | free floors, then about $0.10 |
| 6 | A playbook the model curates across episodes, scored as a learning-curve axis | Learning / harness | 4 | 3 | about $0.10 to $0.15 (dev lane first) |
| 7 | Budget-visible lookahead: a budget tracker for `simulate` and the game clock, and the resulting compute-scaling curve | Test-time compute | 3 | 4 | about $0.45 |
| 8 | Opponent-panel robustness and adaptation across Forge AI profiles and best-of-three matches | Eval | 3 | 4 | zero-model profile floors free, then about $0.65 |
| 9 | Search with an LLM prior: MCTS over model-proposed actions, engine transitions and model-valued leaves, as a measured arm | Test-time compute | 3 | 2 | about $1 to $3 |
| 10 | An exportable RLVR environment with dense engine process rewards and hindsight-relabelled puzzle items | Learning | 3 | 3 | free to build; training is not ours |

The four free ideas (1, 2, 3's build, 4) need neither egress nor consent. Ideas 5 to 9 are paid. Each states its
cost, uses `--budget-usd`, and needs the owner's `OPENROUTER_ALLOW_EGRESS=1` consent, which an agent never sets.

---

## 1. Variance-reduced STR: a same-seed control variate, AIVAT chance corrections, anytime-valid stopping

**Idea.** Win/loss outcomes in Magic are dominated by luck: mana screw, flood and draw order. That luck makes STR-AI
expensive, because each game costs $0.02 to $0.05 and 20 to 90 minutes. The idea is to subtract the luck with
estimators that stay unbiased, then stop a block as soon as its interval is tight enough.

- **(a) Seed-level control variate.** For every panel seed, also play the free game in which Forge's AI sits in seat
  A on the same seed and deck pair, many times if the AI is stochastic. Use the AI's outcome on that seed as a control
  variate. The estimate is `Y_llm - beta * (Y_ai(seed) - mean(Y_ai))`, and it is unbiased for any beta.
- **(b) AIVAT-style chance correction.** At each chance event (a draw, a shuffle, a coin flip), add the difference
  between a value model's estimate of the realised branch and its expectation over the branches. The expectation
  comes from forked copies with the library resampled. The correction is unbiased even when the value model is bad.
- **(c) Anytime-valid stopping.** Replace fixed-size blocks with confidence sequences, so a block can stop the moment
  its interval separates from the floor or from another arm. This is valid under continuous monitoring, which SPRT on
  raw outcomes is not when its parameters are tuned after the fact.

**Prior work.**
- AIVAT (Burch, Schmid, Moravčík, Morrill, Bowling; AAAI 2018). Its standard deviation fell 85% in a hold'em
  man-machine match, so the same conclusion needed 44x fewer games. https://arxiv.org/abs/1612.06915 ,
  https://ojs.aaai.org/index.php/AAAI/article/view/11481 [snippet]
- AV-AIVAT (Li, Chen, Huang; arXiv:2608.06362, Aug 2026). AIVAT combined with confidence sequences, and an online
  value model fitted only on earlier games. Reported as a median 74x fewer hands for LLM poker agents.
  https://arxiv.org/pdf/2608.06362 [snippet]
- A GTO Wizard poker benchmark using AIVAT reports about 10x fewer hands. https://papers.cool/arxiv/2603.23660
  [snippet]
- Control Variates Evaluation (arXiv:2502.10563): synthetic judgments as a control variate, unbiased, with fewer
  labels needed. https://arxiv.org/abs/2502.10563 [snippet]

**Why it is new here.** EVAL-DESIGN section 6 has seed-paired panels, clustered errors and an SPRT. Nothing in the
repo uses a free same-seed reference policy as a control variate. No MTG or Forge evaluation does either. It fits the
rules because nothing changes in the seat: it is a pure estimator change.

**What it measures and its floor.** It does not change what STR-AI measures. It reduces the games needed for a given
interval width.
- Report the variance-reduction ratio `Var(raw) / Var(corrected)` with a bootstrap interval.
- Floor: the plain seed-paired estimator (ratio 1).
- The raw estimate is always printed beside the corrected one.

**Implementation sketch.**
- Add `eval/variance_reduction.py` (stdlib):
  - reads `aggregate.py`'s per-game rows and the reference-arm rows;
  - fits beta on a disjoint split (cross-fitting, so no game scores its own correction);
  - prints the raw and corrected win rates with their intervals, the ratio, and a confidence-sequence stop line
    (an empirical-Bernstein CS for the exact certificate, the asymptotic CS as the practical one).
- Add `run_batch.py --reference-arm ai`: the same seeds and decks with controller A set to `ai`, free and recorded as
  its own arm, never pooled.
- Teach `panel_score.py` to accept `--control-variate <ref-run>`.
- For (b), add a fork hook in `LlmSpikeFork` that resamples the draw at each draw step and evaluates a value model.
  Start with a logistic model on public features (life, cards in hand, mana, board power) fitted in Python on old
  games. It is used **only** to score; the seat never sees it, so no heuristic decides anything.

**Smallest first experiment (free, zero-model).**
- On 100 panel seeds of one matchup, play two independent Forge-AI-vs-Forge-AI games per seed and the random-vs-AI
  game per seed.
- Estimate the per-seed correlation of outcomes, which is the share of variance that is luck.
- The variance-reduction ratio is about `1 / (1 - rho^2)`. If rho is under 0.2 (EVAL-DESIGN section 6.3 already
  measured an intra-pair rho of about 0.02 for seat swaps, which is a different quantity), drop (a) and go straight to
  (b).
- Then check on any existing Luna games on panel seeds.

**Main risk.** The LLM's luck may correlate weakly with the AI's on the same seed, because different play draws
different cards once the lines diverge. Then (a) buys little, and (b) needs fork plumbing at every draw. Forge's AI
may also be deterministic on a fixed seed, which caps what (a) can estimate.

---

## 2. Step regret on puzzle attempts: the first fatal decision, labelled by the engine

**Idea.** pass@1 on the hard suites is a binary result per 14-to-34-call attempt, so its interval is wide (the tvs
study needed a 6-of-12 difference for 80% power). The idea is to label each decision of a recorded attempt as
**win-preserving** or **win-losing**: whether the position after the model's choice is still solvable. The labels give:
- a dense per-decision score;
- the first fatal decision, which shows where models fail (sequencing, mana, the trap);
- process-reward labels.

The floor comes free and exact: at each position, the share of legal options that preserve the win is precisely the
random-legal policy's survival rate at that position.

**Prior work.**
- Verifiable Process Rewards (Yuan et al., arXiv:2605.10325, May 2026, v3 Oct 2026): symbolic or algorithmic solvers
  (search for Sokoban, constraints for Sudoku, a posterior for Minesweeper) as action-level verifiers giving dense
  rewards, which beat outcome-reward GRPO. https://arxiv.org/abs/2605.10325 [abstract]
- AgentProcessBench (arXiv:2603.14465): human step labels on tool trajectories. https://arxiv.org/html/2603.14465v1
  [snippet]
- ToolPRMBench (Jan 2026). https://awesomepapers.io/papers/li2026toolprmbench [snippet]
- Step Rejection Fine-Tuning (2026): per-step critic masks. https://letsdatascience.com/news/authors-introduce-step-rejection-fine-tuning-for-trajectory-f9229971
  [snippet]
- Chess blunder metrics are the human analogue, and Kaggle Game Arena and MageBench's "Blunder Index" are model
  versions. MageBench's index is model- or engine-judged, not engine-exact.
  https://benchmarklist.com/benchmarks/mage_bench_season1/ [snippet]

**Why it is new here.**
- `PuzzleSearch` proves puzzles from the start and `PuzzleArms.Search` replays prefixes. Nobody has run either on a
  model's trajectory.
- The generated families have exact solvers in `eval/puzzles/puzzle_gen.py` (`red_search`, `drain_search`,
  `splice_best`, ...), so on those boards a "still winnable" label is exact, not "not found at budget N".
- EVAL-DESIGN section 4's DQ and PUNT axes planned reference evaluators with noisy rollouts. This gives a reference
  that is not noisy on puzzles.

**What it measures and its floor.**
- **Step survival**: the share of decisions that keep the win, macro-averaged over puzzles.
- **Excess survival**: model minus the per-position random-legal rate on the same positions.
- **First-fatal-depth** distribution.
- Floors: the random arm's own trajectories, and first-option.
- Intervals are clustered by puzzle (Miller).

**Implementation sketch.**
- Add `eval/puzzles/step_regret.py`. It reads a run's decision rows and, per attempt, builds the prefix line in
  `script:` format, the one `ScriptBridge` plays.
- **Family route (exact):** parse the state at step t from the row's recorded prompt (`SeatObservation` text: hand,
  untapped lands, pool, storm count, opponent life) into the family solver's inputs, then ask whether the best
  achievable damage is at least the opponent's life.
- **Engine route (any puzzle):** add a `PuzzleSearch` entry that takes a forced prefix (`script:relaxed:<prefix>` then
  search). A Java test in `forge-overlay/forge-gui-desktop/src/test/java/forge/spike/` pins that a known-good prefix
  stays winnable and a known blunder does not. Record the budget, so "not found" states the budget it was not found
  under.
- Output `step-regret.jsonl` and a `puzzle_score.py --step-regret` table.

**Smallest first experiment (free).**
- Run the family route on the 12 gen-v1 open puzzles already played in `runs/terse/b2`, `runs/tvs` and the FRONTIER5
  record (the storm, splice and ping families).
- Report step survival against the random floor, and the first-fatal-step histogram.
- Read 10 fatal steps by hand to audit the labels.

**Main risk.**
- Reconstructing the solver inputs from the prompt text is per-family work and can be wrong, so audit it by hand.
- Engine-route search on 10-to-15-step lines may be too weak to label "lost" exactly, so report those as "unknown at
  budget N".
- Hidden information is absent in these puzzles, which is what makes the labels exact. That does not carry over to
  full games.

---

## 3. Twin and invented cards: contamination-proof puzzles and a recall-vs-reading delta

**Idea.** Forge cards are plain text scripts in `res/cardsfolder`, so new cards can be written that exist in no
training corpus. Build twins of active puzzles in three kinds:
- **(a) rename-only**: Lightning Bolt becomes "Vexing Spark" with the same Oracle text;
- **(b) parameter shift**: the same mechanics with changed numbers or costs, with the answer key recomputed by the
  family solver;
- **(c) novel compositions**: existing keyword primitives combined in ways no printed card has.

The paired difference `pass@1(original) - pass@1(twin)` measures how much a model leans on recalled card knowledge.
The twins also give a refreshable, contamination-resistant suite for STR-HELDOUT and PUZ.

**Prior work.**
- Atomic Chess as a counterfactual benchmark with a tunable "variant divergence" (ICML 2026).
  https://icml.cc/virtual/2026/68348 [snippet]
- Wu et al., "Reasoning or Reciting?" (counterfactual chess openings). https://arxiv.org/html/2307.02477v3
  [via the repo's rules-probe note]
- Evaluating Counterfactual Strategic Reasoning (arXiv:2603.19167): label- and payoff-perturbed games.
  https://arxiv.org/html/2603.19167v1 [snippet]
- HardcoreLogic (arXiv:2510.12563): long-tail puzzle variants. https://arxiv.org/pdf/2510.12563v3 [snippet]
- gg-bench (arXiv:2505.07215): LLM-invented games, where o1 and R1 win 31-36%. https://arxiv.org/pdf/2505.07215v1
  [snippet]
- Hearthstone LLM+RL with up to 50% unseen cards (AAMAS 2025).
  https://aamas.csc.liv.ac.uk/Proceedings/aamas2025/pdfs/p2795.pdf [snippet]
- Cardiverse (EMNLP 2025): LLM-designed card mechanics.
  https://aclanthology.org/anthology-files/pdf/emnlp/2025.emnlp-main.1511.pdf [snippet]

**Why it is new here.**
- The generated suites are fresh boards built from **real** cards, so the model can still recall each card's
  behaviour and its known combos.
- BENCHMARK-V2's recall probe covers only the published solutions of Forge's built-in puzzles.
- No MTG benchmark found uses engine-executed invented cards.
- Twins also solve the RulesGuru licence problem for a released rules-probe set (rules-probe note, section 8).

**What it measures and its floor.**
- The twin delta, with a paired interval from `puzzle_score.py --paired-with`.
- Each twin's own floors (random, PASS, first, Forge AI), recomputed, because a rename must not change them. A floor
  that moves flags a broken twin.
- Note that Forge's AI reads card scripts, not names, so it is a built-in control: its solve rate should not move
  under a rename.

**Implementation sketch.**
- Add card scripts under `forge-overlay/forge-gui/res/cardsfolder/` in a dedicated subfolder. `install.sh` already
  copies the overlay with `cp -R`. Add an edition file if Forge needs a set code.
- Treat these as engine data like a patch: list them in `docs/harness/ENGINE-PATCHES.md` or a sibling and cover them
  with a version stamp.
- Add `puzzle_gen.py --twin rename|shift`, which emits the twin `.pzl` with a `twin_of` field in `puzzle.v1`.
- Add a `twins` split to the suite JSON.
- Add a test in `RulesProbeLabelTest` style that loads each custom card and casts it on a built board.

**Smallest first experiment.**
- Free first: rename-only twins of the 12 gen-v1 open puzzles. Run `puzzle_baselines.py` floors and replay the
  written line to confirm every twin replays identically. The labeller export on twin boards confirms the legality
  labels are unchanged.
- Then a dev-lane Haiku pilot on the 12 pairs (notional CLI cost, on the subscription). Then Luna, 12 pairs at k=3,
  about 72 attempts at about $0.0014 each: **about $0.10**.

**Main risk.**
- Forge's card database may refuse or warn on cards with no set, needing an edition file, and the overlay rule forbids
  editing the checkout.
- A rename can shift tokenisation and prompt length, a small confound.
- The delta may be near zero for strong models on the gen families, which use simple cards. Kind (c) carries the
  real test, and authoring it costs real time.

---

## 4. IRT calibrated by a free population, adaptive testing, and a rules-vs-play factor

**Idea.** BENCHMARK-V2 plans a 2PL fit "once two or more models have run". This idea calibrates item difficulty and
discrimination **before** any paid run, then spends paid attempts only where they carry information.
- **Free respondents:** the random arm at many seeds, `greedy:`, Forge's AI under each shipped profile (`Default`,
  `Cautious`, `Reckless`, `Experimental` in `res/ai/`), the prover at several budgets, and dev-lane Haiku at effort
  low and max.
- **FRONTIER5 data:** 20 models on 100 boards, already recorded.
- **Uses of the fit:**
  - computerised adaptive testing (Fisher-information item selection) for cheap screening of new models and
    conditions;
  - a two-factor model (MIRT) over PUZ items plus rules-probe items, to separate a rules-knowledge factor from a
    play-skill factor;
  - Latency-Response Theory, which adds the model's thinking time as a second observed variable.

**Prior work.**
- ATLAS (arXiv:2511.04689, ICML 2026 poster): 3PL plus Fisher-information CAT matches HellaSwag with 42 items (MAE
  0.154), and 3-6% of items are flawed. https://arxiv.org/abs/2511.04689v1 , https://icml.cc/virtual/2026/poster/64880
  [snippet]
- PSN-IRT, "Lost in Benchmarks?" (arXiv:2505.15055, AAAI). https://arxiv.org/html/2505.15055v3 [snippet]
- Latency-Response Theory (arXiv:2512.07019): CoT length as a second variable, which changes rankings.
  https://arxiv.org/abs/2512.07019v1 [snippet]
- Lichess rates each puzzle like a Glicko-2 player, and a puzzle is said to stabilise in about 20-30 attempts.
  https://lichess.org/@/Toadofsky/blog/lichess-ratings-are-not-glicko-2/x1bpwn38 [snippet]

**Why it is new here.**
- The repo has the 2PL plan and Bradley-Terry for games. It does not have zero-model respondents as a calibration
  population, CAT, latency as a variable, or a factor model joining the rules probe with PUZ.
- The rules-probe note says outright that "whether the probe predicts play" is not determined. A two-factor fit is
  the instrument that answers it.

**What it measures and its floor.**
- Item parameters with bootstrap intervals, and the flagged items (negative discrimination are audited as broken).
- For CAT: the ability-estimate error at n items against the full-suite estimate. Floor: a **random subset of the
  same size** (tinyBenchmarks-style). CAT must beat it.
- For MIRT: the variance explained by a second factor over one factor (likelihood ratio, held-out log-likelihood).
  Floor: the one-factor model.

**Implementation sketch.**
- Add `eval/puzzles/puzzle_irt.py` (stdlib: 2PL by marginal maximum likelihood with Gauss-Hermite quadrature, about
  200 lines, or numpy if allowed in eval/). It reads result rows from `puzzle_score.py`'s loader.
- Respondent ids are the solver and condition key, so conditions never pool (the scorer's existing refusals apply).
- Add `--respondent-arms random:1..50,ai:Default,ai:Reckless,greedy`. The AI-profile arm needs a `PuzzleArms` or
  launcher flag selecting the profile.
- CAT is a dry-run simulator first: `puzzle_irt.py cat --bank items.json --replay <run>` replays recorded answers in
  CAT order. A live `puzzle_solve.py --adaptive` comes later and is never a headline (the headline stays the fixed
  frozen suite).

**Smallest first experiment (free).**
- Fit 2PL on the FRONTIER5 rows plus random and Forge AI floors already on disk.
- Report the items with discrimination of 0 or less, and the CAT-replay error against the random-subset floor at 10,
  20 and 40 items.

**Main risk.**
- Zero-model respondents may measure a different ability: random and AI policies fail for reasons LLMs do not, so the
  fit becomes multidimensional by accident. Report the fits with and without them.
- 100 boards of 5 puzzle families is a narrow item bank.

---

## 5. Self-forecast calibration: does the model know when it is winning?

**Idea.** A "shadow probe" is a separate call, outside the live game, on the exact recorded prompt state. Ask the model
for **P(I win this game)** at the start of each of its turns, and **P(I solve this)** before a puzzle attempt, through
a fixed-schema `forecast` tool call (so no text parsing). Score the forecasts against the real outcome with:
- Brier score and its calibration/refinement split;
- expected calibration error;
- AUROC.

Because the probe is offline, the live seat's bytes never change. A later live arm could ask the same question in a
prompt (its own version) to test whether forecasting changes play.

**Prior work.**
- Barkan, Black, Sourbut, "Do Large Language Models Know What They Are Capable Of?" (arXiv:2512.24661, Dec 2025). All
  models tested are overconfident, though most discriminate better than chance. Several frontier models become more
  overconfident as multi-step tasks progress. In-context failures help only some models.
  https://arxiv.org/abs/2512.24661 [abstract]
- "Can an Agent Learn to Predict Its Own Failure?" (arXiv:2610.18614): step confidence AUROC rises from about chance
  to 0.68-0.85 after training. https://www.emergentmind.com/papers/2610.18614 [snippet]
- PredictaBoard (arXiv:2502.14445). https://arxiv.org/pdf/2502.14445 [snippet]
- "Are LLM Decisions Faithful to Verbal Confidence?" (arXiv:2601.07767). https://arxiv.org/pdf/2601.07767 [snippet]
- LLM-SoccerArena: binned outcome probabilities against outcomes. https://arxiv.org/pdf/2607.24573 [snippet]
- No source was found that scores an LLM's in-play win-probability forecasts in a game.

**Why it is new here.** The `simulate` preview is deliberately "not a win probability" (LOOKAHEAD-DESIGN). Nothing in
the repo asks the model what it believes its chances are. Calibration is a capability axis separate from play
strength, and it is cheap: one short call per probed state.

**What it measures and its floors.** Brier and AUROC against:
- the constant base-rate predictor (Brier = p(1-p));
- a public-feature logistic model (life difference, cards in hand, board power, turn) fitted on other games;
- an **engine-rollout reference**: from the same forked state, N Forge-AI-vs-Forge-AI continuations with the hidden
  cards resampled, as `PlayerLookahead`'s copy already does. This is not ground truth, but it is a competent reference
  that never reads hidden cards.

For puzzles the floor is the base rate on the suite.

**Implementation sketch.**
- Add `eval/forecast_probe.py`:
  - reads `game-*.jsonl` decision rows and takes each turn's first priority prompt;
  - sends it with a one-tool schema `forecast{p_win: number 0..1}` to `claude-cli:` (dev) or `openrouter:` (owner
    consent);
  - records each request the way `Transcript` does;
  - scores with Brier, ECE, AUROC and a cluster-by-game bootstrap.
- Add an `LlmSpikeFork` entry, `rollout-equity <game> <row> --n 20`, for the reference, run only with the `ai`
  controller: zero-model, free.
- Report the forecast calibration curve beside STR, never folded into it.

**Smallest first experiment.**
- Free: compute both trivial floors and the rollout reference on 30 finished Luna games already on disk.
- Then Luna on about 300 turn-start states at about $0.0003 a call: **about $0.10**.
- The puzzle variant uses the 44 gen3 boards × k=1 forecasts before attempts, about $0.02.

**Main risk.**
- Few finished games means wide intervals: 30 games is about 300 correlated states, so cluster by game.
- The reference is only as good as Forge's AI plays the continuation.
- Asking out of game context may differ from what the seat believes in play. That is a reason for a later live arm.

---

## 6. A playbook the model curates across episodes, scored as a learning-curve axis

**Idea.**
- Within one game the seat has `memory_note` and `set_plan`; across games nothing carries over.
- Give the seat a read-only **playbook** at the start of each episode (puzzle or game), and after each episode one
  reflection call. In that call the model sees its own trajectory and the engine's outcome, and edits the playbook
  with a fixed-schema `playbook_update` tool (add, replace or delete bullets, in ACE's delta style to avoid context
  collapse).
- The harness writes nothing into the playbook (no coaching).
- The benchmark quantity is the **learning slope**: solve rate as a function of episode index over an ordered
  sequence, against the same sequence with the playbook disabled.

**Prior work.**
- ACE, Agentic Context Engineering (arXiv:2510.04618, ICLR 2026): generator, reflector and curator; +10.6% on
  AppWorld; incremental deltas prevent context collapse. https://arxiv.org/html/2510.04618v1 [snippet]
- Dynamic Cheatsheet (Suzgun et al. 2025), cited by ACE [snippet].
- Mistake Notebook Learning (arXiv:2512.11485), ExpeL and Reflexion: already in the repo's survey, used there for
  cross-*decision* memory.
- PTCG-Bench: "sustained self-evolution is hard". https://arxiv.org/abs/2605.29653 [via repo survey]

**Why it is new here.**
- Survey E1 is a cross-decision notebook built from refusals inside the propose arm.
- This is cross-**episode**, model-authored, and measured as an axis (learning from experience), with ordering
  controls.
- The repo's own `GAME-LOOP` does this for the *harness* with a human in the loop. Nothing does it for the *seat*.

**What it measures and its floor.**
- The slope of pass@1 against episode index, by logistic regression with puzzle fixed effects, and the area between
  the arm and its control.
- Floors:
  - the no-playbook arm on the same order;
  - a **shuffled-playbook** arm (another sequence's final playbook), to separate generic tips from learned specifics;
  - random-legal, which is flat by construction.
- Train on the open split and test on held-out families, so the playbook cannot carry puzzle-specific answers into a
  score.

**Implementation sketch.**
- Add `PLAYBOOK=<path>` (SETTINGS.md) read by `ContextMemory` into the brief, as a "note, not a commitment" like the
  plan.
- `AnswerTool` gains `playbook_update`, offered **only** in the post-episode reflection call, which
  `LlmSpikeHeadless` or `LlmSpikePuzzle` sends after the result row.
- Add `puzzle_solve.py --playbook-sequence <order.json>` to run puzzles serially (`-j 1`), stamping
  `playbook_sha_before` on each row.
- Add a version stamp `playbook-v1`, with ARCHITECTURE section 8 updated in the same PR.
- `puzzle_score.py` refuses to pool playbook and no-playbook rows.

**Smallest first experiment.**
- Dev lane first: Haiku through `claude-cli:` on 24 gen and gen3 open puzzles in one fixed order, playbook on and off
  (notional cost).
- Then Luna: 24 × 2 arms × about $0.0014, plus 24 reflection calls: **about $0.10 to $0.15**.

**Main risk.**
- Order effects and puzzle-family clustering make the slope noisy at n=24, so it needs several orders.
- The owner may see a playbook as coaching even when the model writes it. It is a condition, never in STR without the
  owner's decision.
- Prompt-cache prefixes move as the playbook grows (cost).

---

## 7. Budget-visible lookahead: a budget tracker and the compute-scaling curve

**Idea.**
- The seat's `simulate` calls are capped (16 an ask, 1,000 a game) and puzzle games have a 30-minute wall cap, but
  the seat is told none of that while it plays.
- The tvs study and FRONTIER5 show games hitting caps and deadlines.
- Add a one-line budget tracker to each ask ("simulate: 11 of 16 left this ask; game clock 12 of 30 min"). Then
  measure the solve rate as the `simulate` budget varies, with the tracker on and off.
- This is information, not a decision, so it fits the "no heuristics" rule.

**Prior work.**
- "Budget-Aware Tool-Use Enables Effective Agent Scaling" (BATS, arXiv:2511.17006, Nov 2025): without budget awareness,
  more tool budget hits a ceiling; a budget tracker plus budget-conditioned planning scales.
  https://arxiv.org/abs/2511.17006v1 [snippet]
- Snell et al. on compute-optimal test-time scaling (in the repo's survey).

**Why it is new here.** E3 compares lookahead, best-of-N and LATS at a matched budget. It does not test whether the
model **knowing** its budget changes how it spends it, or trace a scaling curve over the `simulate` budget.

**What it measures and its floor.**
- Solve rate and calls used at budgets {2, 8, 16} × tracker {on, off}.
- Floors: the no-lookahead arm, and `greedy:` (the engine-preview floor, free).
- Integrity: capped games counted as misses (already in `puzzle_score.py`).

**Implementation sketch.**
- Add `SIMULATE_BUDGET_ASK=N` and `BUDGET_TRACKER=1` to `ContextMemory.TOOLS_PER_ASK` and the prompt renderer in
  `SeatObservation`.
- Bump the version stamps: seat tools and observation.
- `puzzle_solve.py` pins and records both settings, and the scorer refuses to pool them.

**Smallest first experiment.** 12 gen-v1 open puzzles × 6 arms × k=2 with `--lookahead`, at roughly 2-3x a plain
attempt's cost: **about $0.45**. Dev lane cannot run it: the CLI lane refuses `LOOKAHEAD=1`.

**Main risk.**
- The 12-puzzle block resolves only large effects (mde80 about 0.5).
- The tracker line itself is a prompt change, which confounds "aware" with "longer prompt". Add a placebo line of the
  same length.

---

## 8. Opponent-panel robustness and adaptation

**Idea.** STR-AI is against one opponent policy, which a model could exploit or be exploited by in idiosyncratic ways.
Two additions:
- **(a) Robustness:** score the seat against all four shipped Forge AI profiles (`Default`, `Cautious`, `Reckless`,
  `Experimental`) and report the minimum and the spread, not only the mean.
- **(b) Adaptation:** in best-of-three matches against the same deck, compare games 2 and 3 with game 1. Supply the
  opponent's revealed cards through the existing `profile` read, or through idea 6's playbook. This measures
  within-match learning about the opponent.

**Prior work.**
- SPIRAL (self-play, multi-opponent robustness, arXiv:2506.24119), TextArena's TrueSkill ladder (arXiv:2504.11442),
  and lmgame-Bench's warning about single-condition evals: all in the repo's survey.
- "Evaluating Counterfactual Strategic Reasoning" shows models failing to adapt to changed payoffs.
  https://arxiv.org/html/2603.19167v1 [snippet]

**Why it is new here.** `rating.py` and the panels use one AI condition. The profiles exist in the pinned engine and
are unused as an axis. `ControllerOwnershipTest` already reads the profile directory.

**What it measures and its floor.**
- Win rate per profile with Wilson or clustered intervals, plus min-over-profiles.
- Floors: random-vs-profile and AI(Default)-vs-profile mirrors, both free, which give each profile's strength.
- For (b): the game-1 rate is the floor of games 2 and 3. Also run a control in which the profile read is wiped
  between games.

**Implementation sketch.**
- Add `run_batch.py --ai-profile-b <name>`, passed to the engine's AI profile preference for seat B and recorded as a
  condition.
- Add a profile column to `panels-v1.json` in a new `panels-v2-profiles.json`.
- Add a `rating.py` opponent-profile term.
- For (b): `run_batch.py --match bo3` with `ONE_CHAT` off and an explicit carried `profile` field.

**Smallest first experiment.**
- Free: 50 seeds of random-vs-each-profile and AI-vs-each-profile, to show the profiles differ at all. If they do not
  separate, stop.
- Then Luna, 6 games × 3 non-default profiles at about $0.035: **about $0.65**.

**Main risk.**
- The profiles may differ little in strength, so the axis is flat.
- Best-of-three adds hours of wall clock per match.

---

## 9. Search with an LLM prior, as a measured arm

**Idea.** A PUCT/MCTS arm in which:
- the model gives the prior over legal options (from the menu, or from proposals under the propose arm);
- `PlayerLookahead` lines give the transitions on information-set-safe copies;
- the model's own `forecast` (idea 5) values the leaves.

The final pick is the visit-count argmax, so every number driving it comes from the model and the engine. It is still
a harness algorithm aggregating them. It is therefore a **named measurement arm, never STR or PUZ**, like
`PROPOSE_ACTIONS`, and needs an owner decision first. It answers how much of the gap to the puzzle ceiling (pass@k with
the oracle) search can close at a stated compute.

**Prior work.**
- MC-DML: an LLM as the PUCT prior with memory, on Jericho (arXiv:2504.16855). https://arxiv.org/html/2504.16855v1
  [snippet]
- LLM-First Search (arXiv:2506.05213): the LLM gives value and prior with no rollouts. https://arxiv.org/pdf/2506.05213
  [snippet]
- LATS, RAP, and Schultz et al.'s external and internal search: in the repo's survey.
- Soft self-consistency (ACL 2024) and arbiter voting for long-horizon agents (arXiv:2602.12276): majority votes
  scatter over many options. https://arxiv.org/abs/2402.13212 , https://www.arxiv.org/pdf/2602.12276 [snippet]

**Why it is new here.**
- E3's LATS-style arm searches lines. It does not use model-valued leaves calibrated by idea 5.
- Every source above uses a simulator on full-information tasks, or the LLM as the world model. Here the transition
  model is exact on public state, with hidden cards resampled.

**What it measures and its floor.**
- The solve rate as a function of tree size at matched tokens.
- Floors: the plain seat (k=1), the oracle pass@k ceiling, and `greedy:`.

**Implementation sketch.**
- Add `SearchBridge` beside `GreedyBridge`. It calls the model bridge for priors and values, and
  `PlayerLookahead.previewLine` for children.
- Add a `search:` model spec gated by `SEARCH_ARM=1`, stamped and refused by `panel_score.py` and the PUZ headline.

**Smallest first experiment.** 12 gen-v1 puzzles at tree size 8, k=1: tens of calls per decision, **about $1 to $3**.
Run it only after ideas 5 and 7 show that value and budget signals exist.

**Main risk.**
- The cost per decision explodes.
- The one-step preview has no opponent reply, so the tree is optimistic in adversarial puzzles.
- It edges toward a harness strategy, the owner's line.

---

## 10. An exportable RLVR environment with dense engine rewards and hindsight items

**Idea.** Package what the repo already verifies as an RL-with-verifiable-rewards environment for third parties and
for any future open-weights student:
- the puzzle goal check, as the outcome reward;
- the rules-probe labels, as exact supervised items;
- idea 2's win-preserving labels, as a dense process reward in VPR's sense.

Grow the item bank with **hindsight relabelling**:
- a failed attempt whose final state reached opponent life L becomes a verified easier item, "from this board, bring
  the opponent to L";
- the engine replays the attempt's own line to prove it;
- idea 4's IRT gives the item a difficulty, so the bank becomes a curriculum.

Luna has no weights here, so this does not train the benchmark's model. Its value is a benchmark others can train
against, and a T1/T2 refill source.

**Prior work.**
- Reasoning Gym (arXiv:2505.24760): 100+ generators and verifiers, unlimited data, adjustable difficulty.
  https://arxiv.org/abs/2505.24760 [snippet]
- Prime Intellect verifiers and Environments Hub: in the repo's survey.
- VPR (above) [abstract].
- ECHO, hindsight rewriting for LM agents (arXiv:2510.10304). https://arxiv.org/abs/2510.10304v1 [snippet]
- AgentHER (arXiv:2603.21357): relabelling failed trajectories, +7-12 pp over success-only SFT.
  https://arxiv.org/abs/2603.21357 [snippet]
- Hindsight Supervised Learning (ICLR 2026). https://iclr.cc/virtual/2026/poster/10009594 [snippet]
- LeAct (2026): expert iteration on poker. https://www.alphaxiv.org/abs/2607.21856v1 [snippet]

**Why it is new here.**
- The harness-building environment rewards building harnesses, not playing.
- No play environment is exported.
- HER for *benchmark items*, proved by the engine rather than judged, is new.

**What it measures and its floor.**
- For the environment: its own floors, which are the suite's (random, PASS, Forge AI).
- For the hindsight items: each passes the existing active bar, with random at or under 20% and the line replayed by
  `puzzle_baselines.py line`.

**Implementation sketch.**
- Add `eval/rlenv/` as a `verifiers`-style module wrapping `puzzle_solve.py` with the harness-env ABI's loopback
  OpenAI-compatible endpoint (`SeatFactory`), so any policy server can act.
- Add `puzzle_gen.py --hindsight <run>`, which writes `.pzl` items with `derived_from` and a `seed_policy.py` check.
  Items from reserved seed bands or held-out families are never exported.

**Smallest first experiment (free).** Mine hindsight items from the gen3 Luna run (`runs/puzzles/gen3-luna`). Prove
them with the written-line replay and floor them. Report how many pass the active bar.

**Main risk.**
- Hindsight items are, by construction, ones the model *partly* solved, which biases the bank toward its strengths.
  Tag them and keep them out of PUZ headline splits.
- Export is scope creep beyond "a benchmark, not a stronger student". It needs the owner's decision.

---

## Cross-cutting notes

- **Order of work:**
  - 1, 2 and 4 are free and make every later paid number cheaper or sharper: a smaller interval per dollar, a dense
    per-decision metric, and item difficulty.
  - 3 is the main contamination defence.
  - 5 is the cheapest new capability axis.
- **Owner-rule checks:**
  - Ideas 1, 2, 4 and 5 change no seat byte.
  - Ideas 6 and 7 are seat-visible conditions, each with its own version stamp and an ARCHITECTURE update.
  - Ideas 8 and 9 add arms that never pool into STR or PUZ without an owner decision.
  - No idea uses Forge's AI to decide for the seat: it appears only as an opponent, a reference arm or a
    control-variate policy.
- **Not verified:** every [snippet] number above. Read the PDFs before any of them reaches a doc in the repo.
