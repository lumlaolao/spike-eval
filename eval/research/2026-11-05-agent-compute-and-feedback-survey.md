# Agent compute and feedback: a survey for spike-eval (2026-11-05)

**Verification (2026-11-05).** 33 sources were checked with WebFetch and WebSearch: 32 were read and 1 could not be read
(the Moonlight PTCG-Bench review, HTTP 429). 25 were corrected (wrong author lists, titles, ids, numbers or
attributions, or claims reworded to what the page says), and 1 was removed (the Moonlight review, unreadable). Two
claims that rested only on sources that do not state them were removed: the Kaggle Game Arena retry-then-forfeit rule
(chess.com and Decrypt state no illegal-move rule). PTCG-Bench's masking result is read from the paper's full HTML
text. Every other arXiv claim is abstract-only: no other full paper was read, so no other ablation, table or figure is
quoted.
Venue claims that the arXiv pages do not show are marked unverified. The ExpeL project page, the kagisearch README, the
PokéChamp PMLR page and the Hugging Face card were read in full; the other non-arXiv pages were read as fetched.

Scope: five questions for the propose / terse / lookahead experiments on hard puzzles and full games. Sources are
primary where I could reach them. Each source is tagged by how I saw it:

- **[abstract]** the arXiv or publisher abstract page was fetched with WebFetch.
- **[page]** a repository, project, dataset or publisher page was fetched with WebFetch.
- **[snippet]** only a WebSearch result (title, link and short extract) was seen. Treat the claim as unverified until
  the paper is read.
- **[secondary]** a blog, news item, aggregator or review page. Used only to point at something, never as a number.

No claim below is taken from memory. Where a number comes from a snippet or a secondary source, the text says so.

---

## Q1. Learning from verifier or compiler feedback across episodes

**Question:** Does in-context memory of past errors (reflection, an error notebook, a skill library) reduce repeated
errors across episodes or decisions, beyond what in-decision feedback already gives?

**Sources**

1. **Reflexion: Language Agents with Verbal Reinforcement Learning.** Noah Shinn, Federico Cassano, Edward Berman,
   Ashwin Gopinath, Karthik Narasimhan, Shunyu Yao. arXiv:2303.11366 (first submitted 20 Mar 2023).
   https://arxiv.org/abs/2303.11366 [abstract]. Agents verbally reflect on task feedback signals and keep the reflective
   text in an episodic memory buffer that conditions later trials, with no weight updates. The abstract reports 91%
   pass@1 on HumanEval, against 80% for GPT-4. (unverified: NeurIPS 2023 venue; the arXiv page names no venue.) The
   abstract does not give the ablation separating episodic memory from reflection; do not quote one without the PDF.

2. **Mistake Notebook Learning: Batch-Clustered Failures for Training-Free Agent Adaptation.** Xuanbo Su, Yingfang
   Zhang, Hao Luo, Xiaoteng Liu, Leo Huang. arXiv:2512.11485 (first submitted 12 Dec 2025; latest version 16 Jan 2026).
   https://arxiv.org/abs/2512.11485 [abstract]. The abstract states the failure mode directly: agents without systematic
   learning "repeat identical errors in similar contexts." Their fix clusters failures by batch, writes shared guidance
   into a notebook, and updates the external memory "only when batch performance improves." The abstract reports only
   qualitative competitiveness on mathematical reasoning, Text-to-SQL and interactive-agent tasks, with no figures.
   (unverified: Findings of ACL 2026; the arXiv page names only the cs.CL subject area.) This is the closest match to
   the "same refused play recurs across decisions" observation.

3. **ExpeL: LLM Agents Are Experiential Learners.** Andrew Zhao, Daniel Huang, Quentin Xu, Matthieu Lin, Yong-Jin Liu,
   Gao Huang. arXiv:2308.10144 (first submitted 20 Aug 2023); AAAI 2024 per the arXiv comments field.
   https://arxiv.org/abs/2308.10144 [abstract]. Three stages, as the abstract states them: gather experiences from
   training tasks; extract knowledge from them in natural language; at inference, recall the insights and past
   experiences to make decisions. The project page [page] (https://andrewzh112.github.io/expel) reports insights on
   HotpotQA (36%/31%) and trajectory recall on ALFWorld (50%/55%); the page does not label which number in each pair
   belongs to which arm. (unverified: the detail that insights compare successes and failures on the same task; not in
   the abstract or project page.) Use it as the design template for a cross-decision insight store.

4. **Teaching Large Language Models to Self-Debug.** Xinyun Chen, Maxwell Lin, Nathanael Schärli, Denny Zhou.
   arXiv:2304.05128 (first submitted 11 Apr 2023). https://arxiv.org/abs/2304.05128 [abstract]. The model runs its code,
   explains it in natural language (rubber-duck debugging) and fixes it, using execution results as feedback. The
   abstract states gains of up to 12% on TransCoder and MBPP, and 2-3% on Spider (9% on the hardest problems).
   (unverified: ICLR 2024 venue; the arXiv page names none.) Relevant as evidence that the feedback's content (an
   explanation of why it failed) may matter, not only that it exists; that reading is an inference from the
   code-explanation ablation, not a stated finding.

5. **Voyager: An Open-Ended Embodied Agent with Large Language Models.** Guanzhi Wang, Yuqi Xie, Yunfan Jiang, Ajay
   Mandlekar, Chaowei Xiao, Yuke Zhu, Linxi Fan, Anima Anandkumar. arXiv:2305.16291 (first submitted 25 May 2023).
   https://arxiv.org/abs/2305.16291 [abstract]. A skill library of executable code for storing and retrieving complex
   behaviours, reused later. The abstract reports 3.3x more unique items than the prior state of the art. (unverified:
   the NeurIPS 2023 and TMLR 2024 venues; the author affiliations NVIDIA, Caltech, UT Austin, Stanford and UW Madison;
   the claim that skills are built from environment feedback, execution errors and self-verification.)

**Finding for this harness.** The evidence supports storing failure causes across episodes, not only retrying within a
decision. The mechanism that matters is a retrieved, specific note ("this play class is refused because X") that the
model reads before choosing. None of the sources tests whether the generic word "illegal" in a notebook helps. That is
the open question for the terse arm.

---

## Q2. Test-time compute: world-model lookahead vs best-of-N vs tree search

**Question:** In games and interactive environments, does spending inference compute on simulation (a model or engine
predicting the result of a move) beat sampling more candidates, or searching a tree?

**Sources**

1. **Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters.** Charlie Snell,
   Jaehoon Lee, Kelvin Xu, Aviral Kumar. arXiv:2408.03314 (first submitted 6 Aug 2024). https://arxiv.org/abs/2408.03314
   [abstract]. The compute-optimal strategy improves test-time efficiency "by more than 4x compared to a best-of-N
   baseline" (the page does not name the benchmark in the text read; unverified: that it is math). A smaller base model
   with test-time compute can outperform a 14x larger model, on problems where the smaller model already has "somewhat
   non-trivial success rates". The abstract says effectiveness "critically varies depending on the difficulty of the
   prompt"; it does not say the advantage shrinks on hard problems, which is my inference. (unverified: ICLR 2025 oral;
   the arXiv page names no venue.)

2. **Language Agent Tree Search Unifies Reasoning, Acting and Planning in Language Models.** Andy Zhou, Kai Yan, Michal
   Shlapentokh-Rothman, Haohan Wang, Yu-Xiong Wang. arXiv:2310.04406 (first submitted 6 Oct 2023).
   https://arxiv.org/abs/2310.04406 [abstract]. Monte Carlo tree search in which the LM acts as the agent, with
   LM-powered value functions and self-reflection, and with "incorporation of an environment for external feedback". The
   abstract does not say the LM is the optimizer. This is the direct template for "search with the engine's preview as
   the transition function." (unverified: ICML 2024 venue and the UIUC and CMU affiliations; the arXiv page names
   neither.)

3. **Reasoning with Language Model is Planning with World Model (RAP).** Shibo Hao, Yi Gu, Haodi Ma, Joshua Jiahua Hong,
   Zhen Wang, Daisy Zhe Wang, Zhiting Hu. EMNLP 2023 (per the arXiv comments); arXiv:2305.14992 (first submitted 24 May
   2023). https://arxiv.org/abs/2305.14992 [abstract]. One LLM acts as a world model that predicts the next state and as
   a reasoner that searches with MCTS. The abstract says "RAP on LLaMA-33B surpasses CoT on GPT-4 with 33% relative
   improvement in a plan generation setting." The comparison is RAP on LLaMA-33B against chain-of-thought on GPT-4, not
   LLaMA-33B against LLaMA-33B. The contrast with the engine is the point: RAP's world model is the LLM itself, so its
   errors are not checked (my reading of the design).

4. **Mastering Board Games by External and Internal Planning with Language Models.** John Schultz, Jakub Adamek, Matej
   Jusup, Marc Lanctot, Michael Kaisers, Sarah Perrin, Daniel Hennes, Jeremy Shar, Cannada Lewis, Anian Ruoss, Tom
   Zahavy, Petar Veličković, Laurel Prince, Satinder Singh, Eric Malmi, Nenad Tomašev (Google DeepMind).
   arXiv:2412.12119 (first submitted 2 Dec 2024). https://arxiv.org/abs/2412.12119 [abstract]. A pretrained LM captures
   the environment's transition and value functions. External search runs MCTS guided by the LM, with no engine calls;
   internal search has the model write a linearised search tree in its context. The abstract reports substantial gains
   over the base model and comparisons against game-specific state-of-the-art engines, with Grandmaster-level chess, in
   chess, Chess960, Connect Four and Hex. (unverified: ICML 2025 venue; the abstract does not name a policy role or the
   engines beaten.) The third-party figure of 2673 internal Elo against 2568 for the base model [secondary] is not
   confirmed by me.

5. **Emergent World Representations: Exploring a Sequence Model Trained on a Synthetic Task (Othello-GPT).** Kenneth Li,
   Aspen K. Hopkins, David Bau, Fernanda Viégas, Hanspeter Pfister, Martin Wattenberg. ICLR 2023 (per the arXiv
   comments, as an oral); arXiv:2210.13382 (first submitted 24 Oct 2022). https://arxiv.org/abs/2210.13382 [abstract]. A
   GPT-style model predicts legal Othello moves with no prior knowledge of the game, and the abstract reports an
   emergent nonlinear internal representation of the board state. Interventions on that representation can control the
   model's output. It is about internal state, not search, but it is the standard reference for checking whether a
   planner's world model is actually tracked. (unverified: the linear-versus-nonlinear probe comparison in earlier
   drafts; the abstract does not give it.)

**Finding for this harness.** The engine's preview is an exact world model on public state; hidden cards are resampled
(the simulate copy shuffles every library and re-deals the opponent's hand: docs/harness/ARCHITECTURE.md). RAP's world
model is the LLM itself, and Schultz et al. use a trained transformer as world model. The expected gain is therefore
plausibly larger than in papers where the model simulates itself; that is a hypothesis, not a finding of any source. The
Snell abstract says test-time compute's effectiveness depends on prompt difficulty; a hard-puzzle benchmark may show the
least benefit, which would be a result to report. Best-of-N has no check on the candidates, so the comparison to
lookahead must hold the token budget fixed.

---

## Q3. Game benchmarks with legality or illegal-move metrics

**Question:** How do LLM game benchmarks count illegal moves, and what do they do after one?

**Sources**

1. **ChessArena: A Chess Testbed for Evaluating Strategic Reasoning Capabilities of Large Language Models.** Jincheng
   Liu, Sijun He, Jingjing Wu, Xiangsen Wang, Yang Chen, Zhaoqi Kuang, Siqi Bao, Yuan Yao. arXiv:2509.24239 (first
   submitted 29 Sep 2025). https://arxiv.org/abs/2509.24239 [abstract]. The abstract says that no model beats Maia-1100
   (human amateur level) and that some models lose to random play. It gives no illegal-move rates.

2. **Chess puzzle benchmark (kagisearch/llm-chess-puzzles).** GitHub repository, README dated April 2025.
   https://github.com/kagisearch/llm-chess-puzzles [page]. On 1,000 Lichess puzzles, the README reports illegal-move
   rates of 8.9% for GPT-4o, 46.4% for Claude 3 Opus and 59.0% for Claude 3 Haiku. The models are from the 2024
   generation.

3. **The Register, 4 June 2024** (corroborating for item 2).
   https://www.theregister.com/2024/06/04/chess_puzzle_benchmark_llm [secondary]. Reports 12.7% illegal moves for GPT-4o
   and 1,000 puzzles. It gives no figures for Claude 3 Opus or Claude 3 Haiku; it says only that most other LLMs made
   more illegal moves than legal ones. The 12.7% figure differs from the README's 8.9%, so cite the README.

4. **Emergent World Models and Latent Variable Estimation in Chess-Playing Language Models.** Adam Karvonen (sole
   author). arXiv:2403.15498 (first submitted 21 Mar 2024; v2 14 Jul 2024). https://arxiv.org/abs/2403.15498 [abstract].
   A model solely trained on next-character prediction of real games, with no a priori knowledge of the game, shows
   internal board-state representations under linear probes and contrastive activations. The abstract gives no
   legal-move rate. Shows that the model's internal state can be probed without any rules text in the prompt.

5. **PTCG-Bench: Can LLM Agents Master Pokémon Trading Card Game?** Dongdong Hua, Yifei Sun, Renhong Huang, Feng Gao,
   Chunping Wang, Yang Yang. arXiv:2605.29653 (first submitted 28 May 2026; v2 19 Sep 2026).
   https://arxiv.org/abs/2605.29653 [abstract]. The abstract says LLM agents reach non-trivial gameplay performance,
   that sustained self-evolution is hard, and that performance is sensitive to harness design. It names no
   invalid-action metric, masking or rating figure. The full text (https://arxiv.org/html/2605.29653 [page], Table 2)
   does: with legal-action masking Glicko-2 1726 and an invalid-action rate of 3.3%; without it 1608 and 15.9%.

6. **Game Reasoning Arena: A Framework and Benchmark for Assessing Reasoning Capabilities of LLMs via Game Play.** Lucia
   Cipolina-Kun, Marianna Nezhurina, Jenia Jitsev (LAION). arXiv:2508.03368 (first submitted 5 Aug 2025).
   https://arxiv.org/abs/2508.03368 [abstract]. A framework over Google's OpenSpiel library. The abstract does not say
   how an invalid move is handled and gives no illegal-move rate. Read the full paper before quoting anything about its
   retry rule.

7. **Kaggle Game Arena (Kaggle and Google DeepMind).** Launched 2025, chess first. Sources: [secondary] chess.com news
   (https://www.chess.com/news/view/which-ai-model-is-the-best-at-chess-kaggle-game-arena, updated 4 Aug 2025) and
   Decrypt (https://decrypt.co/333478/google-top-ai-models-against-live-chess-tournament, 4 Aug 2025). Neither article
   states an illegal-move rule. Chess.com says LLMs "have been known to both make illegal moves and absurd
   resignations"; Decrypt quotes a user's unanswered question on what happens after the allowed rethinks run out. No
   primary Kaggle or DeepMind document was found.

8. **MageBench / mage-bench.** A fork of XMage with an LLM harness that lets agents play through structured tools.
   https://mage-bench.pages.dev/docs/about [page]. The page says models "choose legal actions, and the engine resolves
   the consequences under the same rules a human game would use"; it does not describe the enforcement mechanism. A
   Season 1 leaderboard is on the benchmark list (https://benchmarklist.com/benchmarks/mage_bench_season1/ [secondary]),
   which reports rating, a Blunder Index, win rate and API cost, not illegal-move counts. The mage-bench page has no
   standings, illegal-move rate or paper. I found no paper.

9. **BalatroBench.** https://balatrobench.com/about.html [page] (the earlier coder.github.io address redirects here).
   The primary score is average round reached. Tool-call validity (valid and executable, valid but not executable, or no
   valid call) and average input and output tokens per tool call are reported alongside. (unverified: that seeds are
   fixed with three plays per seed, and the 2026 date; neither is on the page.)

10. **PokerBench: Training Large Language Models to become Professional Poker Players.** Richard Zhuang, Akshat Gupta,
    Richard Yang, Aniket Rahane, Zhengyu Li, Gopala Anumanchipalli. arXiv:2501.08328 (first submitted 14 Jan 2025); AAAI
    2025 per the arXiv comments. https://arxiv.org/abs/2501.08328 [abstract]. 11,000 pre-flop and post-flop scenarios.
    The abstract validates the score by having models with different scores compete, and says higher PokerBench scores
    lead to higher win rates in actual poker games. (unverified: GPT-4 at 53.55%; the abstract gives no accuracy figure
    and says all state-of-the-art LLMs underperform.)

**Finding for this harness.** Three ways of scoring illegality appear: (a) a per-move illegal rate, reported as a
quality signal (kagisearch, source 2); (b) the engine masks illegal moves, so the rate is zero by construction; this is
the design of the harness's engine-validated menu, and PTCG-Bench measures its effect (masking off: rating 1726 to 1608,
invalid rate 3.3% to 15.9%; source 5); (c) the harness's "propose" arm is a measurement-only design. The sources do not
score the question the harness asks: whether a refusal text changes the next decision.

---

## Q4. RL environments built on game engines for LLM agents

**Question:** What makes a good verifier or reward for an LLM agent in a game engine, and what hubs exist?

**Sources**

1. **Verifiers (Prime Intellect).** https://github.com/PrimeIntellect-ai/verifiers [page] and the Environments Hub
   https://docs.primeintellect.ai/tutorials-environments/environments.md [page]. The README calls verifiers "our library
   for creating environments to train and evaluate LLMs" and links the Environments Hub as an integration partner. The
   docs page describes an environment as "dataset + harness + scoring rules", packaged as an installable module with a
   pyproject.toml. The page does not use the word "rubric". The README gives no version number; its tests mirror a
   `verifiers/v1` layout. Whether the package is at v0.1.x or v1 is not established here; check the repository before
   building on it.

2. **SPIRAL: Self-Play on Zero-Sum Games Incentivizes Reasoning via Multi-Agent Multi-Turn Reinforcement Learning.** Bo
   Liu, Leon Guertler, Simon Yu, Zichen Liu, Penghui Qi, Daniel Balcells, Mickel Liu, Cheston Tan, Weiyan Shi, Min Lin,
   Wee Sun Lee, Natasha Jaques. arXiv:2506.24119 (first submitted 30 Jun 2025); accepted at ICLR 2026 per the arXiv
   comments. https://arxiv.org/abs/2506.24119 [abstract]. Multi-turn, zero-sum self-play against continuously improving
   versions of the model. Multi-game training on TicTacToe, Kuhn Poker and Simple Negotiation "yields the strongest
   results". The authors propose role-conditioned advantage estimation "to stabilize multi-agent training". (The earlier
   draft's Qwen3-4B gains are not in the abstract, which names Qwen and Llama families and DeepSeek-R1-Distill-Qwen-7B;
   they are removed.)

3. **TextArena.** Leon Guertler, Bobby Cheng, Simon Yu, Bo Liu, Leshem Choshen, Cheston Tan. arXiv:2504.11442 (first
   submitted 15 Apr 2025). https://arxiv.org/abs/2504.11442 [abstract]. "57+ unique environments" spanning
   single-player, two-player and multi-player setups, with an online-play system that gives real-time TrueSkill scores
   and supports matches against humans and other submitted models. (unverified: the Gym-style API; the abstract does not
   mention it.)

4. **lmgame-Bench: How Good are LLMs at Playing Games?** Lanxiang Hu, Mingjia Huo, Yuxuan Zhang, Haoyang Yu, Eric P.
   Xing, Ion Stoica, Tajana Rosing, Haojian Jin, Hao Zhang. arXiv:2505.15146 (first submitted 21 May 2025).
   https://arxiv.org/abs/2505.15146 [abstract]. Reinforcement learning on one game transfers to unseen games and to
   external planning tasks. The abstract says "directly dropping LLMs into games cannot make an effective evaluation"
   and gives three reasons: brittle vision perception, sensitivity to prompt wording, and possible data contamination.
   These apply to any harness that reads prose.

5. **Code World Models (CWM).** Meta FAIR CodeGen team, with about fifty named authors, ending with Gabriel Synnaeve.
   "CWM: An Open-Weights LLM for Research on Code Generation with World Models." arXiv:2510.02387 (first submitted 30
   Sep 2025). https://arxiv.org/abs/2510.02387 [abstract]. A 32-billion-parameter open-weights model, mid-trained on
   observation-action trajectories from Python interpreter and agentic Docker environments. It is a research testbed.
   (unverified: Meta affiliation; the page gives no affiliations.)
   Relevant as a model trained to predict an engine's state transitions, a learned counterpart to the engine preview.

6. **PokéChamp: an Expert-level Minimax Language Agent.** Seth Karten, Andy Luu Nguyen, Chi Jin. arXiv:2503.04094
   (first submitted 6 Mar 2025). PMLR vol. 267 (Proceedings of ICML 2025), pp. 29205-29222.
   https://proceedings.mlr.press/v267/karten25a.html [page]. An LLM replaces three minimax modules: player action
   sampling, opponent modelling and value estimation. Reported results are win rates (76% against the best LLM-based bot
   and 84% against the strongest rule-based bot, with GPT-4o) and a projected Elo of 1300-1500 on the Pokémon Showdown
   ladder. The abstract reports no illegal-move rates. It is a Pokémon video game agent, not a card-game one.

**Finding for this harness.** Two design points recur. First, the scoring rule for an illegal attempt (masked, penalised
or retried) is a design choice that the reward must state. Second, self-play and rating systems need variance control,
which a fixed panel against Forge's AI does not require but a training run would. The harness's engine already gives a
verifier whose labels are the engine's rulings (engine bugs, as engine-patches/ records, are its error source); what it
lacks is a reward for the decision's quality that does not depend on the opponent.

---

## Q5. Measuring rules knowledge separately from strategic skill

**Question:** How do papers separate "does the model know the rule" from "does it play well", using probes, contrast
sets or counterfactual boards?

**Sources**

1. **Emergent World Representations (Othello-GPT).** Li et al., ICLR 2023; arXiv:2210.13382.
   https://arxiv.org/abs/2210.13382 [abstract]. Interventions on the internal board representation change the model's
   output. The method is the template for a counterfactual board test, although it needs weights, which GPT-6 Luna via
   OpenRouter does not expose. (unverified: the linear-versus-nonlinear probe comparison; not in the abstract.)

2. **Evaluating Models' Local Decision Boundaries via Contrast Sets.** Matt Gardner, Yoav Artzi, Victoria Basmova,
   Jonathan Berant, Ben Bogin, Sihao Chen, Pradeep Dasigi, Dheeru Dua, Yanai Elazar, Ananth Gottumukkala, Nitish Gupta,
   Hanna Hajishirzi, Gabriel Ilharco, Daniel Khashabi, Kevin Lin, Jiangming Liu, Nelson F. Liu, Phoebe Mulcaire, Qiang
   Ning, Sameer Singh, Noah A. Smith, Sanjay Subramanian, Reut Tsarfaty, Eric Wallace, Ally Zhang, Ben Zhou.
   arXiv:2004.02709 (first submitted 6 Apr 2020). https://arxiv.org/abs/2004.02709 [abstract]. The authors manually
   perturb test instances "in small but meaningful ways that (typically) change the gold label". Model performance is
   "significantly lower" on the contrast sets, "up to 25%" in some cases (the earlier "25 points" is corrected to 25%).
   (unverified: the Findings of EMNLP 2020 venue; the consistency metric, which the abstract does not mention.)

3. **Emergent World Models and Latent Variable Estimation in Chess-Playing Language Models.** Adam Karvonen.
   arXiv:2403.15498 [abstract]. Probing of a chess model for board state, as in source 1 of Q3.

4. **PokerBench.** arXiv:2501.08328 [abstract]. Accuracy on decision spots that are separate from play, validated by
   competition between models of different scores (higher PokerBench scores lead to higher win rates in actual play).
   The same two-track design (static knowledge items, then play) is what the harness needs.

5. **MTG-Eval (Hugging Face dataset).** https://huggingface.co/datasets/jakeboggs/MTG-Eval [page]. The dataset card
   lists 80,032 instances: 27,104 rules questions from the MTG Rules Committee's official rulings, 26,702 card
   descriptions and 26,226 card-interaction items generated from combo descriptions. The data come from MTGJSON and
   Commander Spellbook. The card calls itself unofficial fan content, not endorsed by Wizards of the Coast. No
   peer-reviewed venue is listed. It answers "does the model know the rule" by question, not by board, so it is not a
   counterfactual test.

6. **BalatroBench tool-call validity** (see Q3, source 9) reports a tool-use score separately from round reached, which
   is the same separation applied to a different axis.

**Finding for this harness.** The contrast-set method transfers directly. Engine-labelled counterfactual boards are
free: the engine decides the legality of each variant, so the labels are the engine's rulings (its bugs are the error
source). I found no MTG-specific paper that does this. I found no paper that gives a legality-consistency score across a
contrast set for a game engine.

---

## Synthesis: experiment ideas for this harness

Notation: the trivial floor is the score a policy gets with no information (always PASS, a random legal action, or
the majority label). Sample sizes are rough and assume the default puzzle suite (12 hard puzzles) or a board suite
of about 100 cards.

**E1. Cross-decision error notebook in the terse arm (most directly testable).**
- Hypothesis: if the unresolved terse-vs-detailed difference on puzzles (5/12 vs 3/12; see
  `eval/research/2026-11-03-propose-condition-prior-work.md`) is real, it comes from information the model cannot
  recover across decisions, not from the refusal text itself, and a notebook of cause-level notes (built from refused
  plays, clustered by rule, in the style of Mistake Notebook Learning and ExpeL) narrows it under terse refusals.
- Arms: terse; terse + notebook; detailed; detailed + notebook.
- Primary metric: puzzles solved. Floor: terse without notebook (3/12, Wilson 0.09-0.53, `runs/terse/b2-terse`; one
  block cannot separate it from detailed's 5/12), and the random-legal floor.
- Secondary metric: repeat-refusal rate, the share of refused plays re-proposed later in the same game. Its floor is the
  no-notebook rate.
- Sample: 12 puzzles x 4 seeds = 48 per arm. Roughly 35 per arm to separate 25% from 58% solve rates at 80% power (my
  calculation, not from a source), if the runs were independent; they cluster on 12 puzzles, so the real need is larger.

**E2. Repeat-refusal rate in the existing logs (no new games): done.** See
`eval/research/2026-11-03-propose-condition-prior-work.md`, "Repeated refusals in every propose log on disk": no refused
play was resent within its decision in any run; across decisions the repeat rate differed between builds on shared
puzzles (cause untested). The shuffled refusal-to-decision floor proposed here is not yet computed.

**E3. Compute-matched lookahead vs best-of-N vs LATS-style search on puzzles.**
- Hypothesis (from Snell and LATS, extrapolated to this setting; neither source tests puzzles): at a fixed token budget,
  engine-checked lookahead beats best-of-N on hard puzzles, and the gain is smaller on hard puzzles than on easy ones.
- Arms: no lookahead; best-of-4 sampled plays with the model choosing; one-step engine preview; two-step engine-checked
  search.
- Primary metric: puzzles solved. Floor: no lookahead, and the random-legal floor.
- Sample: 12 puzzles x 4 seeds = 48 per arm. Paid cost should be priced first with `eval/cost_report.py`.

**E4. Engine-checked versus self-simulated world model (RAP-style), scored against the engine (most novel).**
- Hypothesis: a model's own prediction of a move's result (the game state after the move, in text) is accurate for
  simple effects and drifts for multi-step effects. Scoring its prediction against the engine's real state gives a
  world-model fidelity score, which is a measure separate from play.
- Arms: model-predicted state vs engine-computed state, by effect class (damage, draw, destroy, layered effects).
- Primary metric: state-diff match rate. Floor: the "no change" predictor (predicts the state is unchanged).
- Sample: about 200 decisions drawn from the logs, stratified by effect class. Needs no paid run if predictions are made
  on recorded states, or about 200 calls at pennies each.

**E5. Contrast sets for rules, with engine-labelled legality (novel for MTG).**
- Hypothesis: a model that knows a rule keeps its answer on a contrast set where one rule-relevant word or board element
  flips legality (haste vs summoning sickness; a "can't attack" clause; the same card on a tapped vs untapped board). A
  model that matches surface text fails the consistency score even when its single-item accuracy looks high.
- Arms: model with the card text only; model with the rule text; model with the engine preview.
- Primary metric: consistency (all variants of a local set answered correctly, as Gardner et al. define it; the
  definition is not in their abstract, so check the paper first). Floor: 50% per binary item, and the majority-label
  baseline on each set.
- Sample: 100 cards x 4 variants = 400 items. The labels are free because the engine decides legality.

**E6. State-tracking accuracy under the terse arm.**
- Hypothesis: the model's own zone summary (which creatures, lands, counters) drifts from the engine's state over a
  game, and the drift is the cause of repeated refusals. A per-turn state summary that the engine checks should reduce
  repeats.
- Arms: no summary; model-written zone summary, checked against the engine each turn (the summary is not sent to the
  model until it matches).
- Primary metric: fraction of turns where the summary matches the engine. Floor: copy-last-turn's summary.
- Sample: 10 full games at about $0.02 to $0.05 each, per the CLAUDE.md cost note; or 200 recorded turns for the match
  metric alone.

**E7. Counterfactual flip test on decisions.**
- Hypothesis: when a rule-relevant change is made to the board (remove the blocker, untap the land), the model's
  decision changes; when an irrelevant change is made (rename a card, change life total by 1), it does not. A model that
  tracks the rule passes both, a model that tracks surface features fails one.
- Arms: the same decisions with rule-relevant and irrelevant perturbations, under the terse and detailed refusals.
- Primary metric: flip-sensitivity, the share of rule-relevant perturbations that change the decision, combined with the
  share of irrelevant ones that do not. Floor: 50% (the coin-flip decision rate).
- Sample: 300 pairs drawn from logs, perturbed by the engine.

**E8. Refusal policy as a reward design (later, costly).**
- Hypothesis: the scoring rule for an illegal attempt (masked, retried-then-forfeit, retried-then-passed) changes win
  rate against the panel more than the refusal wording does. No source here measures this; the retry rules are design
  options, not reported results.
- Arms: masked; retry up to K then pass; retry then forfeit; the current harness rule.
- Primary metric: panel win rate with its CI (STR-AI), with illegal attempts per game as a second metric. Floor: the
  masked arm, which cannot produce an illegal attempt.
- Sample: about 6 games per arm for the first read, then a full panel. Price first with `eval/cost_report.py --plan`.

**Most novel:** E4 (world-model fidelity measured against the engine, separate from play) and E5 (engine-labelled rule
contrast sets for an MTG engine). Both are unclaimed in what I found. E1 is the most directly testable. E2 is done; its
shuffled floor is the open item.

---

## Gaps and limits of this survey

- No MTG paper on illegal-move rates was found. MageBench is the nearest; it is a fork of XMage with a leaderboard, and
  I found no paper or published illegal-move rate for it.
- No paper I could reach gives a Forge-based LLM evaluation.
- The BALROG, Mind2Web and AgentBench illegal-action rates were searched for and not found. The BALROG hits were on the
  benchmark's design only.
- None of the Q1 ablation numbers, the Q3 figures from full papers other than PTCG-Bench's Table 2, or the Q5 probe
  results were available. Read the PDFs before any claim
  goes into a paper or a results table.
- The Kaggle Game Arena rules are not documented in any source I could read.

---

## Sources (consolidated)

Q1
- Shinn et al., Reflexion, arXiv:2303.11366 (NeurIPS 2023 unverified). https://arxiv.org/abs/2303.11366 [abstract]
- Su et al., Mistake Notebook Learning, arXiv:2512.11485 (2025-26; Findings of ACL 2026 unverified).
  https://arxiv.org/abs/2512.11485 [abstract]
- Zhao et al., ExpeL, arXiv:2308.10144, AAAI 2024. https://arxiv.org/abs/2308.10144 [abstract]; project page
  https://andrewzh112.github.io/expel [page]
- Chen et al., Teaching LLMs to Self-Debug, arXiv:2304.05128 (ICLR 2024 unverified). https://arxiv.org/abs/2304.05128
  [abstract]
- Wang et al., Voyager, arXiv:2305.16291. https://arxiv.org/abs/2305.16291 [abstract]

Q2
- Snell et al., arXiv:2408.03314 (ICLR 2025 unverified). https://arxiv.org/abs/2408.03314 [abstract]
- Zhou et al., LATS, arXiv:2310.04406 (ICML 2024 unverified). https://arxiv.org/abs/2310.04406 [abstract]
- Hao et al., RAP, arXiv:2305.14992, EMNLP 2023. https://arxiv.org/abs/2305.14992 [abstract]
- Schultz et al., arXiv:2412.12119 (ICML 2025 unverified). https://arxiv.org/abs/2412.12119 [abstract]
- Li et al., Othello-GPT, arXiv:2210.13382, ICLR 2023. https://arxiv.org/abs/2210.13382 [abstract]

Q3
- ChessArena, arXiv:2509.24239. https://arxiv.org/abs/2509.24239 [abstract]
- kagisearch/llm-chess-puzzles, README April 2025. https://github.com/kagisearch/llm-chess-puzzles [page]
- The Register, 4 June 2024. https://www.theregister.com/2024/06/04/chess_puzzle_benchmark_llm [secondary]
- Karvonen, Emergent World Models in Chess-Playing LMs, arXiv:2403.15498. https://arxiv.org/abs/2403.15498 [abstract]
- Hua et al., PTCG-Bench, arXiv:2605.29653. https://arxiv.org/abs/2605.29653 [abstract]; full text
  https://arxiv.org/html/2605.29653 [page]
- Cipolina-Kun et al., Game Reasoning Arena, arXiv:2508.03368. https://arxiv.org/abs/2508.03368 [abstract]
- Kaggle Game Arena, 2025. chess.com
  https://www.chess.com/news/view/which-ai-model-is-the-best-at-chess-kaggle-game-arena [secondary]; Decrypt
  https://decrypt.co/333478/google-top-ai-models-against-live-chess-tournament [secondary]
- MageBench / mage-bench. https://mage-bench.pages.dev/docs/about [page]; leaderboard
  https://benchmarklist.com/benchmarks/mage_bench_season1/ [secondary]
- BalatroBench. https://balatrobench.com/about.html [page]
- Zhuang et al., PokerBench, arXiv:2501.08328, AAAI 2025. https://arxiv.org/abs/2501.08328 [abstract]

Q4
- Prime Intellect, Verifiers and Environments Hub. https://github.com/PrimeIntellect-ai/verifiers [page];
  https://docs.primeintellect.ai/tutorials-environments/environments.md [page]
- Liu et al., SPIRAL, arXiv:2506.24119, ICLR 2026. https://arxiv.org/abs/2506.24119 [abstract]
- Guertler et al., TextArena, arXiv:2504.11442. https://arxiv.org/abs/2504.11442 [abstract]
- Hu et al., lmgame-Bench, arXiv:2505.15146. https://arxiv.org/abs/2505.15146 [abstract]
- Meta FAIR CodeGen, CWM, arXiv:2510.02387. https://arxiv.org/abs/2510.02387 [abstract]
- Karten et al., PokéChamp, arXiv:2503.04094, ICML 2025 (PMLR 267). https://proceedings.mlr.press/v267/karten25a.html
  [page]

Q5
- Li et al., Othello-GPT, arXiv:2210.13382 (as above).
- Gardner et al., Contrast Sets, arXiv:2004.02709 (Findings of EMNLP 2020 unverified). https://arxiv.org/abs/2004.02709
  [abstract]
- Zhuang et al., PokerBench (as above).
- MTG-Eval dataset. https://huggingface.co/datasets/jakeboggs/MTG-Eval [page]
