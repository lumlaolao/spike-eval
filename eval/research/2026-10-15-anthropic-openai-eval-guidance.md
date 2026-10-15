# Designing a trustworthy MTG-play benchmark: what Anthropic's and OpenAI's public eval guidance says, applied to STR-AI / STR-HELDOUT / PUZ / PvP

Researched 2026-10-15. Labels used throughout: **[Source]** means the cited page says it. **[Inference]** means it is my application or extrapolation. All sample-size numbers were computed here with standard normal-approximation formulas (alpha = 0.05 two-sided, power = 80%, z = 1.96 + 0.84).

---

## 1. A score where higher always means better play

### What the sources say
- **[Source: Anthropic, "Demystifying evals"]** Grade the outcome, meaning the end state of the environment, not the path the agent took: "it's often better to grade what the agent produced, not the path it took." Graders should resist bypasses and hacks, so that passing requires actually solving the problem. In one internal eval, Claude got an unfair advantage by reading git history that earlier trials had left behind.
- **[Source: Demystifying]** "One-sided evals create one-sided optimization." Test both cases: when a behavior should occur and when it should not.
- **[Source: Demystifying]** Capability evals "should start at a low pass rate." Saturation happens when every solvable task is passed, and a near-saturated suite hides real gains, so harder tasks have to be added as scores climb.
- **[Source: Anthropic, "Challenges in evaluating AI systems"]** "All evaluations are subject to the failure mode where you overinterpret the quantitative score." The BBQ bias score looked unbiased until a control showed the models were barely answering. This is the same failure the repo calls "the 88% lesson".
- **[Source: Anthropic "Define success criteria" docs]** Targets should be specific, measurable, achievable and relevant, and evaluation should usually cover several dimensions.
- **[Source: MLE-bench]** MLE-bench anchors scores to a human distribution (Kaggle private-leaderboard medal thresholds) instead of reporting raw metrics. **[Source: PaperBench]** PaperBench reports a random-judge F1 of 0.49 next to its chosen judge's 0.83, so the judge is shown against its trivial floor.

### Recommendations for this benchmark
1. **[Inference] Publish every headline score as a floor, the score and a ceiling.** For STR-AI the floors are the `random` and `floor` controllers' win rates against Forge AI on the same seeds and matchups. The ceiling, or reference point, is Forge AI piloting the same deck against the same anchor (§2.1 of EVAL-DESIGN already lists this as the ceiling control). Optionally also report a normalized score, (p - p_random) / (p_ForgeAImirror - p_random). It is linear, so it stays monotone, but report the raw win rate too, because the normalization explodes when the gap between floor and ceiling is small in some matchups.
2. **[Inference] Close the "non-completion" shortcut.** EVAL-DESIGN §2.1 drops pairs that contain a draw or invalid game. If a model's own behavior can produce an invalid game (a timeout, a wall-clock cap, a stalled turn loop), dropping it turns a likely loss into "no data", and a slow model gains score. Rule: a non-completion the model caused counts as a loss. A non-completion the harness caused is rerun with a logged reason. Both counts are published next to the score. Anthropic's isolation and grader-robustness guidance is the basis for this.
3. **[Inference] Make PUZ two-sided.** Include positions where the correct answer is to hold: don't attack into a trick, don't fire removal yet, PASS is correct. An "always act" policy then scores near its baseline, just as "always PASS" does. Report per puzzle category both the exact random-legal-option solve rate (computable from the engine's option list) and the always-PASS solve rate.
4. **[Inference] Plan for STR-AI saturation now.** Forge AI is a fixed and fairly weak anchor. Once frontier models win more than about 85% against it, STR-AI stops separating them (Demystifying's saturation warning). Remedy: a ladder of anchors (Forge AI, then a fixed cheap LLM pilot, then a fixed strong LLM pilot), all rated on one Bradley-Terry/Elo scale using PvP games, with Forge AI pinned at 0. For scale: 55% is about +35 Elo, 75% about +191 and 90% about +382, so differences near the top of a single anchor become very expensive to resolve.
5. **[Inference] Time-to-solve in PUZ is not monotone unless it is defined carefully.** Faster but wrong must never beat slower but right. Make "solved" the primary score. Report time-to-solve only over solved positions, or as a censored curve with unsolved positions at the cap. Measure time in decisions or tool calls rather than wall-clock seconds, which mostly measure provider latency.

---

## 2. Statistics

### What the sources say
- **[Source: Miller, "Adding error bars to evals" (arXiv 2411.00640), and Anthropic's summary post]**
  - Report the SEM, n and a 95% CI (mean ± 1.96·SE) for every score. Use the CLT rather than the bootstrap unless the estimator is complex.
  - **Cluster** standard errors on the unit of randomization. "Clustered standard errors on popular evals can be over three times as large as naive standard errors." Report the cluster count.
  - **Resample** each item K times and average. Var(mean) = (Var(x) + E[σᵢ²]/K)/n. Pick K so that E[σᵢ²]/K ≪ Var(x). In the paper's binary worked example, the maximum reduction is 2/3.
  - **Do not lower the temperature** to cut variance. It can move variance into the conditional means or add bias.
  - **Paired differences** on the same items: SE²_paired = SE²_A + SE²_B − 2·SE_A·SE_B·Corr. Frontier models' item-score correlations typically run 0.3 to 0.7.
  - **Power** (Eq. 9): n = (z_{α/2}+z_β)²·(ω² + σ²_A/K_A + σ²_B/K_B)/δ². Worked example: n ≈ 969 for δ = 3 points. The paper suggests new evals should have about 1,000 or more items.
- **[Source: Demystifying]** Run multiple trials, because results vary between runs. pass@k is the chance of at least one success in k tries, and pass^k is the chance that all k tries succeed (75% per trial gives about 42% pass^3). Trials that hit the same resource limit "are not independent".
- **[Source: MLE-bench]** Most experiments use 3 seeds, reported as mean ± SE. Headline models get 16 to 36 seeds.

### Worked numbers for this benchmark (computed here)

**Games needed at 80% power, alpha = 0.05 two-sided**

| Question | Design | Games |
|---|---|---:|
| Is model X better than a fixed 50%, e.g. a PvP head-to-head or a known anchor rate? Detect 55% vs 50% | one-sample | **783** |
| Same, 60% vs 50% | one-sample | 194 |
| Same, 52.5% vs 50% | one-sample | 3,137 |
| Model A vs model B, each playing its own games against Forge AI. Detect 55% vs 50% | two independent arms | **1,565 per arm** |
| Same, but both pilots play identical seeds, so the design is paired. Pair correlation 0.2 / 0.4 / 0.6 | paired | 1,252 / 939 / 626 per arm |
| Low-skill regime: 10% → 20% | two arms | 199 per arm |
| Low-skill regime: 10% → 15% | two arms | 686 per arm |
| Mirror pentanomial pairs (σ² = 0.125). Detect a pair-score change of 0.05 / 0.10 | one-sample on pairs | 392 / 98 pairs |
| Same with the measured σ² = 0.106 | | 333 / 83 pairs |
| SPRT: 0.50 vs 0.55, alpha = beta = 0.05 (Wald approximation) | sequential | about 530 expected |

Inflate every row by ÷0.94 for the 6% draw/invalid rate measured in the repo. That rate should fall if recommendation 1.2 is adopted.

**CI half-widths (95%):** at p = 0.5, ±13.9 points with n = 50, ±9.8 with 100, ±4.9 with 400, ±3.1 with 1,000 and ±1.5 with 4,000. At p = 0.1, ±5.9 with n = 100.

**Cost check:** 783 games at about $0.04 per game (the repo's deepseek-flash figure) is about $31 per model for a 5-point resolution against a fixed rate. A two-arm comparison is about $63 per arm before pairing.

### Recommendations
1. **[Inference] Possible inconsistency in EVAL-DESIGN §6.4.** The row "near-parity mirrors p≈0.5, Δ0.10 → ~390" uses the two-arm formula. A direct head-to-head mirror is a one-sample test against 0.5, so it needs about **194** games for Δ = 0.10 and **783** for Δ = 0.05. The pentanomial row in the same table already uses the one-sample form. One of the two rows needs relabeling.
2. **[Inference] Decide what population "pooled STR-AI" generalizes to, and cluster to match.**
   - If the claim is "on these 16 decks", treat matchups as fixed strata. Weight them equally, and the SE comes only from games.
   - If the claim is "general MTG play", decks and matchups are a sample, and between-matchup variance does not shrink with more games. With a matchup-level SD of true win rates of 0.15 (plausible given the repo's observed 12–70% archetype spread), the generalization CI never gets narrower than **±7.3 points with 16 matchups**, ±3.7 with 64 and ±2.7 with 120, however many games are played.
   - Breadth in matchups buys more than depth in any one matchup. This is Miller's clustering point applied to decks, and it matches the repo's own "breadth is the point".
3. **[Source → applied]** Use paired designs everywhere: the same seed set, both seats, the same matchup grid for every model. Report the pair correlation that was actually realized. EVAL-DESIGN §2.1 correctly notes ρ ≈ 0.02 in the asymmetric Delver-vs-Burn matchup. For *model-vs-model comparisons against the same anchor*, the relevant correlation is across pilots on the same deal. Measure it; if it lands in Miller's 0.3–0.7 range, it can halve the cost.
4. **[Source → applied] PUZ resampling.**
   - Use the operating temperature, with K = 4–8 samples per position and the mean as the position score. Report pass^k separately as the reliability number; EVAL-DESIGN §3.3 already does this.
   - Cluster SEs by source game. A paired comparison of two models on 200 positions with item correlation 0.5 detects a 10-point solve-rate gap (n ≈ 196). A 5-point gap needs about 785 positions, which is near Miller's "about 1,000 items" guidance.
   - At p ≈ 0.5 a 200-position suite has SE ≈ 3.5 points before clustering.
5. **[Inference]** Pre-register n or the SPRT bounds; EVAL-DESIGN §6.1 already requires this. Never peek at a fixed-n block, and run headline numbers at fixed n. SPRT stays a gating tool, not a reporting tool, because sequentially stopped estimates are biased.

---

## 3. Grading agent trajectories

### What the sources say
- **[Source: Demystifying]** There are three grader types. Code graders are fast, cheap and reproducible but brittle. Model graders are flexible but non-deterministic. Human graders are the gold standard. Use "deterministic graders where possible, LLM graders where necessary, and human graders for validation." Judges "should be closely calibrated with human experts". Give the judge an "Unknown" exit. Use a separate, isolated judge per dimension. "You won't know if your graders are working well unless you read the transcripts."
- **[Source: OpenAI evaluation best practices]** Pairwise, classification and pass/fail judgments are more reliable than open-ended scores. Control position bias and verbosity bias. "Validate before scaling": use the judge at scale only once it agrees with human labels. Avoid "vibe-based evals".
- **[Source: Anthropic docs]** Use a different model as the grader than the one that produced the output. Constrain the grader's output format. Define the endpoints of any scale.
- **[Source: PaperBench]** Hierarchical rubric whose leaves are binary and weighted. The judge (o3-mini) was validated on JudgeEval against expert labels: F1 0.83, against 0.49 for a random judge. Accuracy differs by node type (0.72 on code development, 0.94 on result match). The process-only variant Code-Dev correlated weakly with the full outcome score (r = 0.48).
- **[Source: GDPval]** Blind pairwise grading by three experts per comparison. Human-human agreement was 70.8%, and the automated grader agreed with humans 65.7% of the time. The grader agreed less on outputs from the most capable models, and blinding leaked through style.

### Recommendations
1. **[Inference] Win and loss, and puzzle solved or not, are the only primary grades.** Both are code-graded by the engine. Decision-quality (DQ) and plan-adherence axes stay secondary and are never blended into a headline. PaperBench's r = 0.48 between process and outcome scores is direct evidence that process proxies can diverge from outcomes.
2. **[Inference] Engine-verified puzzle keys are a code grader; keep it that way.**
   - Accept every option the verifier proves reaches the goal (EVAL-DESIGN §3.3 already does). This is Demystifying's "don't penalize valid unanticipated paths".
   - Require a reference solution for every position. Demystifying requires this so that each task is known to be solvable.
3. **[Inference] If the DQ-judge lane is used, calibrate it like GDPval and PaperBench.**
   - Have 2–3 experienced MTG players label a set of 100–200 decisions blind.
   - Report human-human agreement as the ceiling, judge-human agreement, and the judge's majority-class baseline (the 88% lesson applied to the judge itself).
   - Present options pairwise in randomized order to control position bias.
   - The judge must not be the model under test or from its family. Give it an "Unknown" exit, and use one judge per dimension.
   - Recheck calibration on outputs from each new, stronger model, since GDPval saw agreement fall on the most capable outputs.
4. **[Source → applied]** Read transcripts on a schedule. The repo's `harness-audit` skill is exactly Demystifying's advice: before blaming the model, check whether a failure was unfair (harness, ambiguity, a tool refusing a request).

---

## 4. Contamination, held-out sets, versioning and freezing

### What the sources say
- **[Source: OpenAI, SWE-bench Verified (2024)]** 93 developers screened 1,699 samples, each labeled by 3 annotators for underspecified tasks and for tests that reject valid solutions. About 68% were filtered out (exact sub-percentages come from secondary sources). GPT-4o went from 16% to 33.2%: broken tasks had *understated* ability.
- **[Source: OpenAI, "Why we no longer evaluate SWE-bench Verified" (Feb 2026), as reported]** An audit of 138 tasks that o3 did not solve reliably found 59.4% with significant test or description flaws. Models that had seen the problems in training did better, because memorized fixes passed underspecified tests. Some frontier models reproduced original fixes from memory. I could not fetch the page directly (HTTP 403); these details come from the search summary and secondary coverage.
- **[Source: MLE-bench]** Contamination check: per-token familiarity with competition pages showed no positive correlation with score. Obfuscated task descriptions scored the same, 8.5% against 8.4%. Plagiarism detection (Dolos) compared submissions with public notebooks.
- **[Source: Anthropic "Challenges"]** Widely used benchmarks leak into training data ("students seeing the exam"). Small formatting changes move MMLU by about 5%. Implementation details (prompt format) change scores across labs.
- **[Source: Demystifying]** Suspect the task first when pass rates are near 0%. Have two experts independently agree on each verdict. Graduate saturated capability tasks into regression suites.

### Recommendations
1. **[Inference] Seeds protect against *student* memorization, not *frontier* contamination.** STR-HELDOUT's seed band is right for the trained student. For frontier models the risk is the **published puzzle suite** itself. Keep held-out PUZ positions and answer keys out of any public repo, add a canary string to open suites, and rotate on schedule (§3.4 already does).
2. **[Inference] Use MLE-bench-style contamination probes.**
   - Report the gap between open and held-out positions per model.
   - Run a perturbation check: re-pose puzzles with irrelevant details changed (life totals that don't matter, irrelevant permanents, reordered zones) and confirm the solve rate holds.
   - Report STR-AI minus STR-HELDOUT for every model, including frontier models, where it should be about 0 and acts as a sanity check.
3. **[Source → applied] Audit the failures, not only the passes.** OpenAI's 2026 audit found most flaws among the *unsolved* hard tasks. Before freezing a PUZ version, have two MTG-literate reviewers independently check every position that no model solves and every position all models fail at K = 8. Drop or fix positions where the reviewers disagree on the answer.
4. **[Inference] Version the full condition tuple:** engine commit (`FORGE_COMMIT`), harness/prompt version, `native_tools`, observation profile, suite hash, seed-set hash, anchor deck lists, and controller settings (MULLIGAN_HEURISTIC and similar). Anthropic's point that formatting moves scores by about 5% means prompt or harness changes are condition changes. Never pool across tuples; the repo already applies this rule to native vs text tools.
5. **[Source → applied]** Split the suite by purpose. Capability PUZ positions should start at low solve rates. Positions every model solves graduate into a **regression** set (target about 100%) that gates harness changes.

---

## 5. Game and puzzle evals specifically

- **[Source: Claude Plays Pokémon coverage]** Harness help changes results. The community write-up argues that runs with more harness assistance are not comparable to minimal-harness runs. **[Source: Building effective agents]** Agents need "ground truth from the environment", and agent-computer interfaces deserve as much design effort as prompts ("poka-yoke"). **[Inference]** The harness is part of what is measured. Freeze the harness per eval version, publish it, and report scores per harness configuration. Forge already makes illegal moves impossible, which removes one large confound: score differences are differences in strategy, not in rules compliance.
- **[Inference] PvP.**
  - Fit Bradley-Terry/Elo over all pairings with both seats and identical deals, with Forge AI pinned as the anchor.
  - Use pentanomial pairs as the unit and a GSPRT for gating.
  - Give both seats the same information profile and the same per-decision tool budget. Since `openrouter:` specs send snapshots off-machine, a PvP game between two paid models sends data twice; budget and consent for it accordingly.
  - Check for non-transitivity (A > B > C > A), which is common in card-game matchups. Report the win-rate matrix along with Elo, not Elo alone.
- **[Inference] Per-matchup reporting is required, and the pooled number is secondary.** This matches the repo's rule. Use equal weights across a published matchup grid so that a model cannot gain by being strong in a high-n matchup.
- **[Inference] Puzzle-suite size.** Sources favor volume (Anthropic docs: "more questions with slightly lower signal automated grading is better than fewer questions with high-quality human hand-graded evals") and Miller suggests about 1,000 items. Engine-verified mining makes this cheap. Target 300+ positions for a 10-point gap and about 800+ for a 5-point gap, spread over many source games so the clusters aren't dominated by a few games.

---

## Sources
- Anthropic, "Demystifying evals for AI agents": https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic, "A statistical approach to model evaluations": https://www.anthropic.com/research/statistical-approach-to-model-evals
- E. Miller, "Adding Error Bars to Evals", arXiv 2411.00640: https://arxiv.org/abs/2411.00640 (formulas from https://arxiv.org/html/2411.00640v1)
- Anthropic, "Challenges in evaluating AI systems": https://www.anthropic.com/research/evaluating-ai-systems
- Anthropic, "Building effective agents": https://www.anthropic.com/engineering/building-effective-agents
- Anthropic docs, "Define success criteria" / "Create strong empirical evaluations": https://platform.claude.com/docs/en/docs/test-and-evaluate/define-success
- OpenAI, "Evaluation best practices": https://developers.openai.com/api/docs/guides/evaluation-best-practices
- OpenAI, "Introducing SWE-bench Verified": https://openai.com/index/introducing-swe-bench-verified/ (page returned 403; figures confirmed via search results, sub-percentages are secondary)
- OpenAI, "Why we no longer evaluate SWE-bench Verified" (Feb 2026): https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ (403; details via search summary and secondary coverage, e.g. https://gigazine.net/gsc_news/en/20260429-swe-bench-verified)
- OpenAI, MLE-bench, arXiv 2410.07095: https://arxiv.org/abs/2410.07095 (details from https://arxiv.org/html/2410.07095v6)
- OpenAI, PaperBench, arXiv 2504.01848: https://arxiv.org/html/2504.01848v3
- OpenAI, GDPval, arXiv 2510.04374: https://arxiv.org/html/2510.04374
- Claude Plays Pokémon follow-up (community, harness-comparability argument): https://www.greaterwrong.com/posts/gogZyeistdaDFuhbG/claude-plays-pokemon-opus-4-5-follow-up
