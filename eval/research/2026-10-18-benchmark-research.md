# Benchmark research, 2026-10-18 (behind BENCHMARK-V1.md)

Four agents read primary sources on 2026-10-18. Marked [secondary] where only coverage was readable,
[unverified] where no source was found. Three OpenAI pages (SWE-bench Verified, its retirement, "Why
language models hallucinate") returned 403 and were read through search snippets or the arXiv paper.

## Anthropic

- **Adding error bars to evals** (Miller 2024; https://arxiv.org/abs/2411.00640). Report SE and a 95% CI;
  cluster on the real unit (clustered SEs were over 3x naive on DROP, QuAC, RACE, SQuAD); resample K
  times per item (K=4 halves the within-item variance term); pair arms on the same items
  (frontier-model correlation 0.3-0.7); power analysis before running (a 3-point gap needs ~1,000 items).
- **Demystifying evals for AI agents** (2026-01-09; https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
  Start with 20-50 tasks from real failures; two experts agree on each verdict; every task has a reference
  solution; a 0% task over many trials is usually broken; prefer code graders; grade outcomes, not paths;
  read transcripts (CORE-Bench went 42% to ~95% from grader fixes); pass@k vs pass^k; clean environment per
  trial; capability suites graduate to regression suites.
- **Infrastructure noise** (2026-02-05; https://www.anthropic.com/engineering/infrastructure-noise).
  Resource configuration alone moved Terminal-Bench 2.0 by 6 points; distrust gaps under 3 points
  between unmatched setups; log infrastructure failures apart from task failures.
- **Eval awareness in BrowseComp** (2026-03-06; https://www.anthropic.com/engineering/eval-awareness-browsecomp).
  9 of 1,266 answers leaked online; 2 solved by finding and decrypting the answer key. Do not publish
  answers; canary strings.
- **Launch posts** (https://www.anthropic.com/news/claude-opus-4-5): trials averaged (5-10), thinking
  budgets and scaffolds stated, high-compute variants labelled apart, output-token efficiency reported
  beside accuracy. No confidence intervals published.

## OpenAI and Sierra

- **SWE-bench Verified** [secondary]: 93 developers, 3 reviews per task, underspecified or unfair tasks
  dropped (1,699 to 500). Retired 2026-02-23 as contaminated with flawed hard tasks.
- **MLE-bench** (https://arxiv.org/abs/2410.07095): fixed hardware and time; human anchors (medal
  thresholds); contamination tested by familiarity and obfuscation; rule-breaking caught by a log monitor.
- **PaperBench** (https://arxiv.org/abs/2504.01848): rubric trees; the judge validated on a human-graded
  set (F1 0.83); the cheap proxy correlated only r = 0.48 with the full grade.
- **HealthBench** (https://arxiv.org/abs/2505.08775): worst-at-k; a Hard subset kept for headroom.
- **BrowseComp** (https://arxiv.org/abs/2504.12516): hard to solve, easy to verify; every item had to beat
  strong references when written.
- **SimpleQA / Kalai et al.** (https://arxiv.org/abs/2509.04664): score abstention; benchmarks that give it
  no credit reward guessing.
- **GDPval**: a public gold subset, the rest private.
- **tau-bench** (https://arxiv.org/abs/2406.12045): unbiased pass^k = E[C(c,k)/C(n,k)]; final-state grading;
  ambiguous tasks found by running a reference agent 40+ times.

## Prime Intellect, Mechanize, xAI, Hillclimb, Epoch, METR

- **Prime Intellect `verifiers`** (https://docs.primeintellect.ai/verifiers/overview.md): an environment is
  taskset + harness + rubric; the harness (tools, including a tool-less `null` harness) is swappable over
  one taskset; weighted `@vf.reward`, weight-0 `@vf.metric` diagnostics; `prime env push` versions
  environments; INTELLECT-3 reports Avg@k with rule-based grading (https://arxiv.org/abs/2512.16144).
- **Mechanize** (https://www.mechanize.work/blog/the-upcoming-gpt-3-moment-for-rl/,
  https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute/,
  https://www.mechanize.work/technical-blog/introducing-gba-eval/): verification against a reference
  implementation; deterministic replay as the grader (GBA Eval); tests must not reject correct solutions;
  keep grader and solutions out of reach; fewer audited tasks beat many cheap ones.
- **xAI** (https://x.ai/news/grok-4): reports no-tool vs tool vs test-time-compute series and Vending-Bench
  over 5 runs; publishes no sample counts or grading. The Grok 3 cons@64 dispute: label @1 vs consensus.
- **Hillclimb** (https://www.ycombinator.com/companies/hillclimb): a YC F25 math RL-environment startup;
  publishes nothing on eval quality.
- **Epoch FrontierMath** (https://epoch.ai/frontiermath): unpublished problems, automatic answer checks,
  under 1% guessable, tiers, a random holdout, ~1 in 20 problems found wrong on re-review; the Hub runs
  8-16 runs per model with ±1 SE.
- **METR time horizon** (https://arxiv.org/abs/2503.14499): logistic fit of success on log human time;
  tasks weighted 1/sqrt(family size); hierarchical bootstrap (families, tasks, runs); reward hacking
  patched, not penalised (https://metr.org/blog/2025-06-05-recent-reward-hacking).

## Game benchmarks and prior MTG work

- **Kaggle Game Arena** (https://arxiv.org/abs/2609.31473): Bradley-Terry with bootstrap CIs; duplicate
  poker hands with seats swapped; randomised openings after every model played the Sicilian.
- **BALROG** (https://arxiv.org/abs/2411.13543): procedural generation against contamination; progress
  measures from human data when the game score misleads.
- **lmgame-Bench** (https://arxiv.org/abs/2505.15146): harness modules switched on and off as ablations.
- **Vending-Bench** (https://arxiv.org/abs/2502.15840): high run-to-run variance; failures start from a wrong
  belief about the agent's own state.
- **Lichess puzzles** (https://database.lichess.org/): mined from real games, verified by Stockfish, each
  attempt a Glicko-2 game between player and puzzle. **tinyBenchmarks** (https://arxiv.org/abs/2402.14992):
  IRT recovers accuracy from few items and flags uninformative ones.
- **Harness variance** (https://www.alphaxiv.org/abs/2605.23950): harness variance 7.8x model variance on part
  of SWE-bench Verified; proposes a harness card.
- **MageBench** (https://github.com/GregorStocks/mage-bench): LLMs on XMage via MCP; few games per model; an
  LLM-judged blunder index its author says is not usable for arithmetic; harness bugs admitted.
- **MTG is Turing complete** (https://arxiv.org/abs/1904.09828): no general solver, so a puzzle verifier
  must be bounded.
- **Possibility Storm** (https://www.possibilitystorm.com/): 282 of Forge's 375 `.pzl` files. Solutions must
  beat all opponent responses; no licence stated.
