# Rules engine as type system: prior work (external research)

Compiled 2026-11-03. Scope: prior work on letting a model propose actions without a legal-action menu, having a
checker reject them, and scoring repairs. Every claim has a URL. **Shows** means the source reports it directly.
**Inference** means my reading or extrapolation. Where I could check only a secondary summary or abstract, I say so.

---

## 1. Text games and interactive fiction: with and without a valid-action list

### Jericho (Hausknecht et al., AAAI 2020; arXiv 1909.05398)
- **Shows:** Jericho offers five optional "handicaps": a fixed seed, save/load, game-specific templates and
  vocabulary, the world object tree, and world-change detection that identifies valid actions. Valid actions are found
  by saving the state, trying each template-plus-object candidate, keeping the ones that change the world, and
  reloading. Because changes held only in global variables are missed, it sometimes produces false negatives.
  https://ar5iv.labs.arxiv.org/html/1909.05398
- **Shows:** Normalized progress averaged over games: random 1.8%, NAIL (no handicaps) 4.9%, TDQN (generates from
  templates) 6.1%, DRRN (chooses from the valid-action list) 10.7%. The authors attribute DRRN's lead to its estimating
  values only over valid actions. Same URL.
- **Shows (secondary summary):** Zork1 has up to about 50 valid commands per state, against about 200 billion
  template-generable commands without the handicap. https://www.emergentmind.com/topics/jericho-benchmark
- **Shows:** CALM generates candidate actions with a language model instead of using the handicap. On 8 games it beat
  methods that use the handicap. https://arxiv.org/pdf/2010.02903.pdf
- **Inference:** For RL agents, the valid-action list roughly doubled the score. Jericho's validity oracle also has
  false negatives. An oracle used as a checker can wrongly reject legal moves, so its accuracy has to be audited.

### TextWorld, ScienceWorld, ALFWorld
- **Shows:** TextWorld exposes `admissible_commands` ("All commands relevant to the current state") as an optional
  `EnvInfos` field. https://textworld.readthedocs.io/en/stable/textworld.html
- **Shows:** ScienceWorld can supply valid-action detection, as Jericho does, for agents such as DRRN that choose from a
  list. Its parser asks for clarification when an input matches more than one valid action.
  https://arxiv.org/pdf/2203.07540
- **Shows:** SwiftSage detects invalid actions from environment error phrases (for example "No known action can
  match"). After two consecutive invalid actions it falls back to its fast module. https://arxiv.org/pdf/2305.17390
- **Shows (secondary):** ALFWorld exposes `info['admissible_commands']`; typical scenes have more than 50 valid
  commands per step. https://www.emergentmind.com/topics/alfworld-benchmark . I found **no** published ablation of
  ALFWorld success with versus without the admissible list.

### TALES (Cui, Yuan, Xiao, Ammanabrolu, Côté; arXiv 2504.14128, Apr 2025; NeurIPS 2026 poster)
- **Shows:** TALES runs Jericho, ALFWorld, ScienceWorld, TextWorld and TextWorldExpress in their canonical forms. It
  evaluates 34 models zero-shot on 122 games, with a 100-step cap. The prompt asks for a short phrase and suggests
  "help" when stuck. The paper does not give a per-state valid-action list. Parsers differ in their feedback: ALFWorld
  answers every failure, parse errors included, with "Nothing happens." The best agents score below 25% on games
  written for humans. https://arxiv.org/html/2504.14128v4 , https://neurips.cc/virtual/2026/poster/139524
- **Shows:** The revised version uses canonical game forms "to avoid leaking privileged information" (NeurIPS
  listing). Same NeurIPS URL.
- **Inference:** TALES treats removing the menu as the default, more honest condition. Its uninformative "Nothing
  happens" feedback means a wasted step costs budget but teaches nothing.

### TextQuests (Phan, Mazeika, Zou, Hendrycks; arXiv 2507.23701, Jul 2025)
- **Shows:** 25 Infocom games played with no external tools. The full history is kept in context, there is an
  autosave/restore mechanism, and a new progress metric. https://arxiv.org/abs/2507.23701v3 . I did not confirm how it
  handles invalid commands.
- **Shows:** A January 2026 follow-up reports that every tested model completes less than 10% of Zork-class games on
  average, the best (Claude Opus 4.5) scoring about 75 of 350 points. https://arxiv.org/abs/2602.15867 (seen in a
  search snippet only).
- **Shows:** Tsai et al. (2023) relayed ChatGPT's commands to Zork through a human. ChatGPT was competitive with
  existing systems but could not build a world model. https://arxiv.org/abs/2304.02868

### BALROG (Paglieri et al., ICLR 2025; arXiv 2411.13543)
- **Shows:** On an invalid action, BALROG "provides feedback to the agent indicating the action's invalidity,"
  "executes a default fallback action (such as a 'do-nothing' action or a standard move like 'north')," and "logs the
  occurrence for trajectory statistics." The initial prompt lists the available actions with descriptions.
  https://arxiv.org/html/2411.13543v1
- **Inference:** BALROG lists the action vocabulary but not per-state legality. Its fallback-plus-log design is the
  middle ground between "forfeit" and "unlimited retries."

**Summary for section 1.** The main with-versus-without result is from RL agents (Jericho: 10.7% versus 6.1% or 4.9%).
For LLMs, no clean menu ablation exists in the text-game literature. The newer LLM suites (TALES, TextQuests)
simply drop the menu.

---

## 2. Chess and board games where legality is part of the test

### Kaggle Game Arena (Google DeepMind and Kaggle; launched Aug 2025; paper arXiv 2609.31473, 2026)
- **Shows:** No legal-move list is given. The model gets the FEN and PGN and must reply in SAN. An illegal move
  triggers a "rethink" that says the move was invalid and asks for another. The prompt deliberately does **not** say why
  the move is invalid or what alternatives exist; the model must recover "solely based on reasoning." After three
  retries (four attempts), the game is lost. https://arxiv.org/html/2609.31473
- **Shows:** Rethinks per game: best Gemini below 0.2; GPT-5.2 "well below 1"; Grok 4, Grok 4.1 Fast and o3 about
  0.5 to 1.7; DeepSeek V3.2 and Claude Haiku 4.5 "more than five, and often more than ten." For weak models only about
  10 to 20% of rethinks fall early in the game. A common failure is a move that does not get out of check. In poker, a
  second consecutive illegal action defaults to check or fold. In Werewolf, running out of retries forfeits the turn.
  Same URL.
- **Shows:** The open harness offers three rethink prompts: resend unchanged; an illegal-move notice
  (**recommended**); and the notice plus a rule-based reason. It also offers majority voting: sample several times,
  play the most frequent move if it is legal, otherwise the game is lost. Prompts *may* include legal moves (supplied by
  OpenSpiel). https://github.com/google-deepmind/game_arena
- **Shows:** In Game Arena chess, 78% of Gemini 2.5 Flash's losses came from illegal moves, not strategic blunders
  (AutoHarness, DeepMind, Feb 2026). https://arxiv.org/html/2603.03329v1
- **Inference:** Game Arena is the closest precedent to a hidden-menu condition. It chose a binary verdict with no
  reason, a small fixed retry budget and forfeit on exhaustion, so the retry channel cannot serve as a menu. It reports
  rethinks per game as a separate metric. The harness has a reason-giving variant, but no published comparison of it.

### LLM Chess (Saplin et al.; arXiv 2512.01992, Dec 2025)
- **Shows:** Actions are tool calls: `get_current_board`, `get_legal_moves`, `make_move` (UCI). Each ply allows up to
  10 dialogue turns and 3 attempts; exceeding either is an "instruction-following" loss. After an illegal move the model
  gets a message such as "Failed to make move: illegal uci" plus the FEN. https://arxiv.org/html/2512.01992v1
- **Shows:** Removing the legal-moves tool changed Win/Loss **in both directions**: Grok 3 Mini (low) 61.7 to 36.7,
  o4-mini (low) 73.3 to 86.7. Instruction-following failures account for 71.9% of losses for non-reasoning models and
  24.4% for reasoning models. Same URL.
- **Inference:** An optional legality lookup tool is a third condition, between full menu and hidden menu. If the
  tool is offered, how often the model calls it has to be reported, or it is a menu by another name.

### ChessArena (arXiv 2509.24239, v4 Apr 2026)
- **Shows:** The cleanest with-versus-without table. Legal Rate (LR) with / without the legal-move list: GPT-4.1 Blitz
  97.5 / 71.6; Claude-3.7-Sonnet 99.6 / 68.4; Qwen3-8B 96.2 / 9.8; DeepSeek-R1 Standard 100.0 / 82.5; o3 Standard
  99.6 / 98.0; Blindfold DeepSeek-R1 94.7 / 44.6; Blindfold o3 98.4 / 86.9. Most models' move-advantage rate turns
  negative without the list. Ratings: Gemini-2.5-Pro Standard 1819 with the list, 1616 without. Legal moves are given to
  most models on the leaderboard because "they cannot effectively play without them." Up to 5 retries, with an
  instruction after each failure, then forfeit. https://arxiv.org/html/2509.24239
- **Shows:** For one position the paper notes that models with the list show "diminished analytical engagement." For
  the same FEN without the list, they sometimes chose better moves (search-snippet summary of the paper).
  https://arxiv.org/pdf/2509.24239
- **Inference:** Removing the menu separates models far more than the menu condition does: 2 points between o3 and
  GPT-4.1 with the list, about 26 without. That spread is the measurement this design wants. It also shows that
  outcome scores fall when the menu is removed, so the two conditions must be scored separately and never pooled.

### TextArena, AutoHarness, GameBench, GTBench, SPIN-Bench
- **Shows:** The TextArena paper (arXiv 2504.11442, May 2025) does not document its invalid-move rule.
  https://arxiv.org/html/2504.11442
- **Shows:** AutoHarness (DeepMind, Feb 2026) has the LLM write `is_legal_action()` and re-prompts with an
  "illegal action" warning until the action passes. The result is 100% legal actions in 145 TextArena games, and Flash
  with the harness beats Gemini-2.5-Pro. https://arxiv.org/html/2603.03329v1
- **Shows:** GameBench (9 environments, 2024) found GPT-4 sometimes below a random player. https://arxiv.org/abs/2406.06613
  GTBench's code caps matches to stop on low completion rates when agents keep making illegal moves.
  https://github.com/jinhaoduan/GTBench
- **Shows (snippet):** SPIN-Bench provides the legal-move list and still sees more illegal moves as games get more
  complex (Connect Four, chess). https://arxiv.org/pdf/2503.12349
- **Shows:** A NeurIPS 2025 statistical framework for game evaluation models premature endings (timeouts, repeated
  invalid moves) **separately** from the conditional win/draw/loss outcome. https://neurips.cc/virtual/2025/122510
- **Shows (blog, unverified):** giving Claude Opus the legal-move list raised its legal-move rate from 8% to 73% on a
  chess benchmark. https://jso.eecs.yorku.ca/2026/07/

### Feedback that backfires (Gumaan; arXiv 2610.30205, Oct 2026)
- **Shows:** On Sokoban, Connect Four and Othello, with 135M to 1.7B models, adding the failed move and its failure
  message to the transcript made repeating it **more** likely. Greedy repeat rates for illegal moves rose from 18% to
  35% (Sokoban), 7% to 20% (Connect Four) and 1% to 18% (Othello). A "do not repeat" instruction had no effect
  (+0.03 nats). A clean retry was worst: 94% of failed moves exactly repeated an earlier failure. A runtime description
  of the failure that leaves out the move string removed about 76% of the effect. A decoder ban cut exact repeats to
  about 0 to 3% but left the illegal share almost unchanged (81% versus 79%): models swapped one illegal move for
  another. https://arxiv.org/html/2610.30205
- **Inference:** The study uses small models only, so it may not carry over to frontier seats. It still shows the
  error *message format* is a variable that must be fixed and reported. Quoting the failed proposal back verbatim may
  hurt.

---

## 3. Card games and MTG

### PTCG-Bench (Pokémon TCG; Hua et al.; arXiv 2605.29653, May 2026, v2 Sep 2026)
- **Shows:** The closest card-game precedent for the proposed ablation. Each decision is a structured tool call: an
  action type plus parameters (`source_card`, `target_card`, `attack_name`, `position`); names and indices must be
  copied exactly. With "legal-action masking," `available_actions` is authoritative. Without it, the agent must infer
  valid actions and parameters from the raw state; invalid, unparsable or unavailable actions are rejected and counted
  "before retry or fallback." Full harness: Glicko-2 1726, invalid rate 3.3%, 78.8 tool calls. Without masking: 1608
  (−118, about 2.1× the median gap between adjacent backbones), invalid rate 15.9%, 86.7 tool calls.
  https://arxiv.org/html/2605.29653
- **Inference:** This is the same design as the proposal, and it shows the effect is large. The paper does not
  describe its error-message content or retry count, so those are open for us to specify and publish.

### mage-bench (XMage fork; Show HN Feb 2026)
- **Shows:** A harness that "lets LLM agents pilot decks through structured tools"; models "choose legal actions, and
  the engine resolves the consequences." Elo per format, and a blunder-analysis pass the authors call "pretty
  unreliable." https://mage-bench.pages.dev/docs/about , https://github.com/GregorStocks/mage-bench ,
  https://hn.svelte.dev/item/47049227
- **Shows (search summary of the HN thread):** a commenter argued mage-bench ended up testing tool calling more than
  rules knowledge. https://news.ycombinator.com/item?id=48492177 (I did not open the comment itself).
- **Inference:** mage-bench uses a menu, like ours today; rules knowledge is not measured.

### MTG Bench (MTG Auto Deck; 11 Jun 2026)
- **Shows:** **No rules engine.** The model gets an MCP server with library primitives (draw, put on bottom,
  shuffle) and manages everything else. Legality and scoring are judged by an LLM (gpt-5.5 medium); the author found
  LLMs better at judging whether a turn was legal than at playing one. Scores range from 95.4 (gpt-5.5 medium) to 12.8.
  No per-violation counts are published. https://mtgautodeck.com/articles/mtg-bench/
- **Inference:** This is the opposite extreme, free generation with an LLM judge. An engine checker would replace the
  noisy judge with an exact one. That is the main argument that the proposed condition is new for MTG.

### MTG rules knowledge datasets
- **Shows:** MTG-Eval (Hugging Face) has 80,032 synthetic QA pairs: 27,104 from official rulings, 26,702 card
  descriptions and 26,226 combo interactions; 4,001 test examples.
  https://huggingface.co/datasets/jakeboggs/MTG-Eval/raw/main/README.md . A companion fine-tuning project scored
  answers with GPT-4 on a 1 to 5 scale (1.62 to 1.79 after tuning).
  https://github.com/JakeBoggs/Large-Language-Models-for-Magic-the-Gathering
- **Shows:** Small projects only: Priority, a local rules assistant with frozen scenario suites and AI-assisted reviews
  that no human has validated (https://github.com/D-Couch/Priority), and a Medium post on a multi-agent rules
  assistant judged by an LLM (https://medium.com/@fkrempl/evaluating-a-multi-agent-system-for-magic-the-gathering-rules-questions-d206044deef1).
- **Not found:** a peer-reviewed, judge-validated MTG comprehensive-rules QA benchmark, or any LLM work on Forge
  itself. A Forge-based legality-repair benchmark would be new as far as these searches show.

### Other card and battle games
- **Shows:** PokéLLMon (Pokémon battles, 2024) feeds battle text back so the agent stops repeating ineffective
  actions; without that feedback it "might stick to the same error action." https://arxiv.org/html/2402.01118v2
  (strategic errors, not illegal ones).
- **Shows:** Argentum Engine (a Kotlin MTG engine) has an LLM opponent that falls back to heuristics on unparseable
  replies. https://github.com/rwaldron/argentum-engine . No 2025–2026 Hearthstone LLM legality study was found.

---

## 4. Verifier feedback and repair: the "type checker" pattern

### Self-correction needs an external, reliable signal
- **Shows:** Huang et al. (ICLR 2024): without an oracle, *intrinsic* self-correction does not improve reasoning and
  sometimes degrades it. Earlier positive results relied on ground-truth labels deciding when to stop.
  https://arxiv.org/abs/2310.01798
- **Shows:** Kamoi et al. (TACL 2024): self-correction works with reliable external feedback; the bottleneck is
  feedback generation. They also give a checklist against unfair setups. https://arxiv.org/abs/2406.01297
- **Inference:** A rules engine *is* reliable external feedback, so repair should work. By Huang's argument, though,
  each "illegal" verdict is an oracle bit. Scores after repair overstate what the model knew, so first-try legality has
  to be the headline rules-knowledge number.

### Code repair from compiler and test feedback
- **Shows:** Self-Debugging (Chen et al., 2023): code explanation adds 2 to 3% on Spider (9% on the hardest
  problems); unit-test feedback adds up to 12% on TransCoder and MBPP. It matches baselines that sample more than 10x
  as many programs. https://arxiv.org/abs/2304.05128
- **Shows:** Olausson et al. (ICLR 2024): once its cost is counted, self-repair's gains are "often modest," sometimes
  zero. Better feedback, from a stronger model or a human, helps substantially. https://arxiv.org/abs/2306.09896
- **Shows:** Reflexion stores verbal self-reflections on failures across trials. https://arxiv.org/abs/2303.11366
  (cited from general knowledge of the paper; not re-read here).
- **Inference:** Score repair at a matched budget. pass@k with k fresh samples is the fair comparison to k repair
  rounds, otherwise repair looks better than it is.

### Proof assistants
- **Shows:** COPRA (COLM 2024) adds the ITP's error text to the prompt ("type mismatch," "unknown identifier"); it
  beats ReProver on miniF2F pass@1 with fewer queries. https://arxiv.org/html/2310.04353v5
- **Shows:** DeepSeek-Prover-V1.5 truncates at the first Lean error and resumes from the latest tactic state.
  https://arxiv.org/html/2408.08152v1
- **Shows:** Goedel-Prover-V2 (ICLR 2026) self-corrects for up to 2 rounds (5 in an extended run): MiniF2F pass@32
  rises from 88.1% to 90.4%, and 92.7% at 5 rounds. Removing compiler feedback "significantly lowers performance."
  https://arxiv.org/pdf/2508.03613
- **Inference:** Specific error messages carry real signal, and gains flatten after 2 to 5 rounds. Lean's errors
  state what is wrong without supplying the proof, which is the model for our errors.

### Tool-call errors
- **Shows:** CRITICTOOL (EMNLP 2025) tests identifying, diagnosing and recovering from tool-call errors as a capability
  separate from task success. Example: a wrong parameter name (`content` where `body` is expected).
  https://arxiv.org/abs/2506.13977v1 (the figure of 1,490 cases and the under-55% recovery rate come from third-party
  listings and are not verified).

### Constrained decoding versus free generation
- **Shows:** Type-constrained decoding (Mündler et al., PLDI 2025) more than halves compilation errors in
  TypeScript and improves functional correctness. https://arxiv.org/abs/2504.09246
- **Shows:** Let Me Speak Freely? (EMNLP 2024 Industry): strict format constraints, JSON mode the worst, reduce
  reasoning accuracy. Answering in natural language and converting afterward mostly avoids the loss.
  https://arxiv.org/html/2408.02442v3
- **Shows:** OpenAI strict structured outputs reported 100% schema conformance on its own evals; the guarantee covers
  structure, not values (secondary coverage). https://lablab.ai/blog/how-openais-structured-outputs-are-transforming-api-reliability-and-developer-control
- **Inference:** Two separate layers. **Syntax**, the shape of the proposal, can be constrained by schema so no
  attempts are lost to JSON. **Semantics**, whether the action is legal, must stay unconstrained: constraining
  semantics *is* the menu.

---

## 5. Metrics in use and how sources avoid a hidden menu

| Metric | Used by |
|---|---|
| Legal rate per move (first attempt) | ChessArena LR https://arxiv.org/html/2509.24239 |
| Retries per game ("rethinks") | Game Arena https://arxiv.org/html/2609.31473 |
| Invalid-action rate before retry | PTCG-Bench https://arxiv.org/html/2605.29653 |
| Mistakes per 1000 moves; instruction-following losses | LLM Chess https://arxiv.org/html/2512.01992v1 |
| Forfeit (premature ending) modelled separately | NeurIPS 2025 framework https://neurips.cc/virtual/2025/122510 |
| Exact-repeat share of failures | Gumaan 2026 https://arxiv.org/html/2610.30205 |
| Invalid-action count logged, fallback played | BALROG https://arxiv.org/html/2411.13543v1 |
| Cost-adjusted repair (pass@k at equal budget) | Olausson https://arxiv.org/abs/2306.09896 |
| Penalized reward (−0.4 per invalid action, −0.2 per parse failure) | arXiv 2607.20668 https://arxiv.org/pdf/2607.20668 (snippet only) |

How the sources limit leakage through error messages:
- **Binary verdict, small budget, forfeit (Game Arena).** No reason, no alternatives, 4 attempts.
  https://arxiv.org/html/2609.31473 . The repository marks the "with reason" variant as non-default.
  https://github.com/google-deepmind/game_arena
- **Fallback instead of forfeit (BALROG, Game Arena poker).** A failure cannot be brute-forced because the next attempt
  does not continue the search. https://arxiv.org/html/2411.13543v1
- **Count before retry (PTCG-Bench).** The headline invalid rate is measured on the first attempt, so later retries
  cannot inflate it. https://arxiv.org/html/2605.29653
- **Canonical, privileged-info-free environments (TALES).** https://neurips.cc/virtual/2026/poster/139524
- **Inference:** No source I found formally measures how much information an error message leaks. ChessArena's
  observation that listed options reduce "analytical engagement" points to the cost on the other side.

---

## Design implications for the hidden-menu condition

1. **Two error tiers, specified ahead of time.** *Syntax/schema errors* (unknown verb, handle not on the board,
   missing field) get full detail and do not count against legality. CRITICTOOL treats these as recoverable
   tool-use errors (https://arxiv.org/abs/2506.13977v1), and Let Me Speak Freely shows format and reasoning should be
   kept apart (https://arxiv.org/html/2408.02442v3). *Rules errors* return only a **category** ("timing," "cost,"
   "target," "zone," "restriction"), never the legal alternative and never the specific fix. Game Arena withholds both
   the reason and the alternatives (https://arxiv.org/html/2609.31473). Run a second arm with Lean-style specific
   reasons ("illegal target: hexproof"), since COPRA and Goedel-Prover-V2 show specific errors drive repair
   (https://arxiv.org/html/2310.04353v5, https://arxiv.org/pdf/2508.03613). The gap between the two arms measures how
   much the message itself contributes.
2. **Never list alternatives in an error or name the missing mana.** "Insufficient mana: need {1}{U}, have {G}" is
   fine because it restates the proposal's own cost. "Legal targets are X, Y" is the menu returning. Rule of thumb
   (inference): an error may describe the *proposal* and the *rule broken*, never the *state's legal set*.
3. **Retry budget of 3 to 4 attempts, then a fixed fallback.** Game Arena uses 4 attempts
   (https://arxiv.org/html/2609.31473), LLM Chess 3 (https://arxiv.org/html/2512.01992v1), ChessArena 5
   (https://arxiv.org/html/2509.24239). Goedel gains flatten by 2 to 5 rounds (https://arxiv.org/pdf/2508.03613). On
   exhaustion, play the same fixed legal fallback the harness uses today, as BALROG does
   (https://arxiv.org/html/2411.13543v1); don't forfeit, since a forfeit would turn a 20-to-90-minute game into a data
   point about formatting. Count the fallback as a "lost decision."
4. **Headline legality metric: first-attempt legal rate per decision,** split by error category and by decision kind
   (cast, activate, target, pay, block). This follows ChessArena LR (https://arxiv.org/html/2509.24239) and PTCG-Bench's
   pre-retry count (https://arxiv.org/html/2605.29653). Report repair success at k (share legal by attempt k),
   retries per game (https://arxiv.org/html/2609.31473) and lost decisions. Following Huang et al., post-repair
   legality is oracle-assisted and is not rules knowledge (https://arxiv.org/abs/2310.01798).
5. **Report outcome (STR-AI win rate) per condition; never pool.** ChessArena and PTCG-Bench show the hidden condition
   lowers ratings by a lot (https://arxiv.org/html/2509.24239, https://arxiv.org/html/2605.29653), and LLM Chess shows
   the sign can differ by model (https://arxiv.org/html/2512.01992v1). Model premature or fallback endings separately
   from the outcome (https://neurips.cc/virtual/2025/122510).
6. **Detect probing.** Log every proposal. Flag sequences that walk through targets or cards one by one after
   rejections (inference; there is no published detector). Report the exact-repeat rate
   (https://arxiv.org/html/2610.30205). A high share of repairs that land on the *second* candidate of a visible
   list suggests enumeration rather than knowledge.
7. **Do not quote the rejected proposal verbatim in the error, and drop failed attempts from the next decision's
   context.** Describe the failure abstractly; quoting the failed move raised repetition (+0.78 nats), clean retry was
   worst (https://arxiv.org/html/2610.30205). The evidence is from small models, so A/B it once on Luna.
8. **Constrain the schema, not the semantics.** Keep `answer`'s fixed JSON schema (and tool_choice "required") so
   proposals are always well-formed. Do not restrict decoding to legal handles: that is the menu again. Mündler et al.
   show constraint works for well-formedness (https://arxiv.org/abs/2504.09246).
9. **Offering a legality-lookup tool is a third condition, not a variant of the hidden one.** If the seat can call
   "is this legal?" or "what can I do?", that is LLM Chess's `get_legal_moves` arm
   (https://arxiv.org/html/2512.01992v1). Either forbid it in the hidden condition or count every call as a menu read.
10. **Audit the checker.** Jericho's validity oracle has false negatives (https://ar5iv.labs.arxiv.org/html/1909.05398).
    Before scoring, check that every action in Forge's enumerated menu passes the proposal checker on a replay corpus
    (false rejections must be zero), and that every rejected proposal is in fact absent from the menu.
11. **Charge repairs against budget.** Olausson shows repair gains shrink once cost is counted
    (https://arxiv.org/abs/2306.09896). Report tokens and dollars per decision in each condition, next to `cost_report.py`.
12. **Keep a rules-QA probe as a sanity check.** MTG-Eval's 27k rulings items (https://huggingface.co/datasets/jakeboggs/MTG-Eval/raw/main/README.md)
    could be used for a static check that first-try legality tracks static rules knowledge. Check for contamination
    first (inference).
13. **Novelty.** MTG Bench judges legality with an LLM and no engine (https://mtgautodeck.com/articles/mtg-bench/), and
    mage-bench uses a menu (https://mage-bench.pages.dev/docs/about). An exact-engine proposal checker for MTG appears
    to be unoccupied; PTCG-Bench is the closest analogue and should be the main comparison
    (https://arxiv.org/html/2605.29653).

### Caveats on this research
- Several items came from search snippets or abstracts only and are marked as such. Most important: the
  MTG-specific items and the CRITICTOOL numbers.
- The Game Arena paper's page shows both "February 2026" and "25 Sep 2026" dates.
- One host was unreachable from this session: commander-bench-website.pages.dev, an XMage-based Commander benchmark
  that I could not inspect.

---

## Pilot: Claude Haiku 5.5 on the rules sample, menu against propose (2026-11-03)

A development-lane pilot (`claude-cli:claude-haiku-5-5`, `CLAUDE_CLI_SEAT=1`, owner's opt-in; never a benchmark score):
the 29 active open puzzles of `eval/suites/puzzles-rules-sample-v1.json`, one attempt each, harness-epoch-74, no
lookups. Run directories (container-local, not committed): `runs/propose/haiku-rules-menu`,
`runs/propose/haiku-rules-propose` (first error text) and `runs/propose/haiku-rules-propose-v2` (the shipped error
text). Notional CLI cost $0.13, $0.17 and $0.16.

| Arm | Solved (pass@1) | 95% CI (Wilson) | Play decisions | Passed | Non-pass first-try legal | Format errors |
|---|---|---|---|---|---|---|
| menu | 23/29 (0.79) | 0.62-0.90 | 48 | 9 (19%) | n/a: the menu allows nothing else | n/a |
| propose, first text | 20/29 (0.69) | 0.51-0.83 | 48 | 20 (42%) | 28/28 | 11 |
| propose, shipped text | 21/29 (0.72) | 0.54-0.85 | 50 | 17 (34%) | 33/33 | 10 |

Floors on this suite: random 0.088, PASS 0.000, first-option 0.207 (`puzzle_score.py`).

What it shows, and what it does not:
- **The solve rates do not differ detectably.** Two runs of this size detect only a difference of about 0.33.
- **Haiku broke no rule it proposed:** every non-pass proposal it made was legal on the first try (61 of 61 over
  both propose blocks). On these single-rule positions the arm's cost is not illegal plays.
- **It passes far more without the menu:** 34-42% of its play decisions against 19% with it. All six puzzles solved
  only with the menu were lost to a pass where the menu arm cast the winning spell (RP_124, RP_443, RP_458, RP_477,
  RP_520, RP_542). The menu was doing part of the finding: it shows what can be played. Decisions without a format
  error still passed 38% of the time in the first block, so the error messages are not what caused it.
- **Format errors are a habit, not a rules error:** about one play in five was first answered with the menu's
  `option` (or `keep`) field. They are recorded apart and cost a try, not a rules error. The shipped text shows an
  example proposal; it did not lower the first-answer rate (10 against 11), as expected, since the first answer
  comes before any error.

### Replication on harness-epoch-75 (targets named in the proposal)

The same 29 puzzles, one attempt each, on harness-epoch-75 (a proposal now names its target; the checker audit found
no menu play it cannot reach). Run directories: `runs/propose/e75-haiku-rules-menu`,
`runs/propose/e75-haiku-rules-propose`. Notional CLI cost $0.14 and $0.20.

| Arm | Solved (pass@1) | 95% CI (Wilson) | Play decisions | Passed | Non-pass first-try legal | Format errors |
|---|---|---|---|---|---|---|
| menu | 26/29 (0.90) | 0.74-0.96 | 50 | 9 (18%) | n/a | n/a |
| propose | 20/29 (0.69) | 0.51-0.83 | 56 | 22 (39%) | 34/34 | 10 |

- **The direction repeats; the size is still under what the block resolves** (about 0.23 to 0.35). All six flips go
  one way: RP_055, RP_190, RP_204, RP_370, RP_520, RP_529 were solved only with the menu. The epoch-74 blocks were
  mixed: 6 to the menu and 3 the other way in the first (RP_151, RP_190, RP_529 solved only by proposal), 4 and 2 in
  the second. So the flips lean to the menu (16 against 5 over the three pairs), not all one way.
- **None of the six is a harness fault**, read row by row: four open with a pass where the menu arm cast the winning
  spell (RP_370 passes away its floating red mana; RP_529 passes with Turn to Frog castable), RP_520 equips Lightning
  Greaves before Giant Growth and so shrouds its own target, and RP_190 targets the wrong creature. RP_055 is the one
  the arm's format shaped: Haiku proposed Treachery without a target, was told the play needs one, and then passed.
- **Still no illegal play proposed:** 95 of 95 non-pass first proposals legal over the three propose blocks. The
  difference is in what Haiku looks for, not in rules it breaks: without the menu it misses plays and passes.

### Hard puzzles (harness-epoch-75)

Twelve puzzles of `eval/suites/puzzles-hard-gen-v1.json` (two from each of six families: drain, elf, fling, ping,
splice, storm), one attempt each, same lane. Run directories: `runs/propose/e75-haiku-hard-menu`,
`runs/propose/e75-haiku-hard-propose` (both stopped part way and finished with `--resume`). Notional CLI cost $1.18
and $1.10.

| Arm | Solved (pass@1) | 95% CI (Wilson) | Play decisions | Passed | Non-pass first-try legal | Format errors |
|---|---|---|---|---|---|---|
| menu | 7/12 | 0.32-0.81 | 242 | 109 (45%) | n/a | n/a |
| propose | 4/12 | 0.14-0.61 | 260 | 154 (59%) | 101/110 (0.92) | 13 |

- **The first illegal proposals:** 9 of 110 first tries, plus one later try, all refused correctly by the rules:
  sorceries proposed with a spell on the stack or in the end step (Lava Spike, Rite of Flame, Gitaxian Probe; timing
  5), and cost names the card does not have (Flashback on Desperate Ritual, Spectacle on Glacial Ray, "alternative"
  for Mutagenic Growth's Phyrexian mana; cost 5). Every one was repaired within two tries; none was lost.
- **Three flips, all to the menu** (ping-0134, storm-5bf9, storm-88f9), none a harness fault. Both storm losses
  pass out of the second main phase with a full hand on the first decision, then propose sorceries in the end step,
  where they are refused. The ping loss passes priority between its own spells where the menu arm kept casting.
  Both arms word a pass the same way ("PASS (do nothing)" in the menu; nothing more in the proposal prompt), so the
  arms differ only in the list.
- Over the 41 puzzles of this note's epoch-75 blocks, the menu solves 33 and the proposal arm 24; all nine flips go
  to the menu. On these positions the hidden list costs Haiku plays it does not look for, and on the hard set some
  rules it does not know (sorcery timing), which is the measurement the arm was built for.

Next, in order: the same pilot on GPT-6 Luna (paid; the default game model); a second arm with specific reasons
(implication 1) to measure the message's share; hiding attackers and blockers (ARCHITECTURE.md, "The propose
condition", not yet hidden). Done since the pilot: targets named in the proposal, and the checker audit
(implication 10).

### Does the refusal's reason matter? The terse arm (2026-11-05)

`PROPOSE_TERSE=1` (ARCHITECTURE.md, "The terse arm") tells a refused proposal only "ILLEGAL: that play is not legal
now." The check, its categories and the four tries are those of the detailed arm, and the row keeps the engine's
reason, so both arms count the same rule categories. Twelve puzzles of `puzzles-hard-gen-v1` open (storm 2, splice 5,
ping 3, fling 2: the families with refusals in the block above), one attempt each, Claude Haiku 5.5 on the
development lane, arms interleaved per puzzle with the order alternating. Both arms ran on one build (overlay
187b31cea8dd, the arm's commit f306f14 before it merged main and took harness-epoch-80). Run directories:
`runs/terse/b2-detailed`, `runs/terse/b2-terse`; notional CLI cost $5.61 and $5.16, summed from the per-puzzle
`[cost]` lines of `runs/terse/b2.log` ($0.43 to $0.47 a puzzle run). A smoke run on an earlier build
(`runs/terse/smoke-64dc1f33-*`) is not pooled.

| Arm | Solved (pass@1) | 95% CI (Wilson) | Play decisions | Pass first | Non-pass first-try legal | Refusal-opened decisions | Repaired to a move | Refused again later | Format errors |
|---|---|---|---|---|---|---|---|---|---|
| detailed | 5/12 | 0.19-0.68 | 259 | 48% | 0.867 [0.80, 0.91], n=135 | 18 | 10 (0.56 [0.34, 0.75]) | 5 of 11 | 8 |
| terse | 3/12 | 0.09-0.53 | 247 | 50% | 0.951 [0.90, 0.98], n=123 | 6 | 3 (0.50 [0.19, 0.81]) | 1 of 5 | 16 |

- **Floors:** random, PASS and first-option solve 0 of the 12. Repaired-to-a-move has a floor of zero; "repaired" as
  the block above counted it (a pass after a refusal included) does not: 8 of 18 and 3 of 6 refusal-opened decisions
  ended in a pass.
- **Solve rate:** paired terse minus detailed -0.167, 95% CI [-0.417, 0.000] (clustered [-0.387, 0.054]); two flips,
  both to the detailed arm (ping-1178, fling-05dc), none the other way. Not resolved: two runs of this size detect
  about 0.59.
- **The refusals:** no proposal was lost (all four tries refused) in either arm, and no proposal was resent word for
  word inside its decision (0 of 21, 0 of 6). What does recur is the same refused play in a later decision of the same
  game (`refused_again`), which is where a reason could teach something.
- **Unexpected:** the terse arm's first tries were more often legal (0.95 against 0.87), before any refusal of that
  decision was shown. The decisions are clustered by puzzle and the detailed arm played longer lines in the puzzles
  it solved, so this is not a finding; if it holds, the candidates are the preamble (the PLAYS and COMBAT promises
  and the `action` schema description differ by one clause each) and what a game's earlier refusals taught.
- **Power:** at the observed 0.5 to 1.5 refusal-opened decisions per puzzle run, a 0.25 difference in
  repaired-to-a-move needs about 53 opened decisions per arm, some 70 puzzle runs per arm (about $30 notional per arm
  at this block's rate). The next block should raise the rate of refusals (storm and other sorcery-timing puzzles,
  several attempts per puzzle) and pre-register repaired-to-a-move and refused-again as its outcomes, then measure
  GPT-6 Luna's base rate on the detailed arm first.

### Repeated refusals in every propose log on disk (2026-11-05)

`python3 eval/proposal_audit.py --json <run>` over every propose-arm run directory with a model seat (Claude Haiku 5.5,
development lane, never a score), and per puzzle directory for the split. The runs differ in build and epoch, so each
row is its own run and no rate is pooled across them.

| Run | Refusal text | Play decisions | Refusal-opened | Repaired to a move | Resent within its decision | Refused again in a later decision |
|---|---|---|---|---|---|---|
| runs/propose/haiku-rules-propose | n/a (no refusal) | 48 | 0 | - | - | - |
| runs/propose/haiku-rules-propose-v2 | n/a (no refusal) | 50 | 0 | - | - | - |
| runs/propose/e75-haiku-rules-propose | n/a (no refusal) | 56 | 0 | - | - | - |
| runs/propose/e75-haiku-hard-propose | detailed | 260 | 9 | 5 | 0 of 10 | 0 of 10 |
| runs/terse/smoke-64dc1f33-detailed | detailed | 53 | 3 | 2 | 0 of 4 | 1 of 3 |
| runs/terse/b2-detailed | detailed | 259 | 18 | 10 | 0 of 21 | 5 of 11 |
| runs/terse/smoke-64dc1f33-terse | terse | 36 | 8 | 5 | 0 of 8 | 3 of 5 |
| runs/terse/b2-terse | terse | 247 | 6 | 3 | 0 of 6 | 1 of 5 |

The two b2 rows are the b2 block above, one row per arm.

- **Within a decision** no run resent a refused proposal: 0 in each of the five runs that had one to resend (10, 4, 21,
  8 and 6 refused tries). The tries are not independent (b2-detailed's 21 come from 18 decisions in about 20 puzzle
  runs), so no interval is given for a combined rate. The repeat Gumaan 2026 reports did not appear in these runs.
- **Across decisions** the same refused play recurs in a later decision of the same game in 1 of 3, 5 of 11, 3 of 5 and
  1 of 5 distinct refused actions, and 0 of 10 in e75-haiku-hard-propose. There is no floor yet: a card that stays in
  hand can be proposed again with no feedback effect at all; the survey's E2 floor (refusals re-paired with decisions at
  random) is not computed.
- **Not the puzzle set.** All ten of e75-haiku-hard-propose's refused actions are on puzzles b2 also played (eight of
  its twelve puzzles are b2's); on those puzzles b2-detailed refused 3 of 6 again and e75 0 of 10. b2-detailed's five
  come from fling-0a55 (1), ping-1734 (2), splice-0551 (1) and splice-06da (1), none from the storm puzzles; the only
  storm repeats are in smoke-64dc1f33-terse (storm-5bf9 2 of 2, storm-88f9 1 of 3). The difference follows the build
  (e75 at harness-epoch-75, b2 on overlay 187b31cea8dd); that it is the build and not chance is a hypothesis, untested.
- A block that tests a notebook of earlier refusals (E1 of
  `eval/research/2026-11-05-agent-compute-and-feedback-survey.md`) should hold the build fixed and compare arms on the
  same puzzles, with the shuffled floor beside it.
