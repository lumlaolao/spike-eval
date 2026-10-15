# Designing a benchmark you can hill-climb on without fooling yourself — principles from hill-climbing guides, Mechanize, Prime Intellect and game-benchmark prior art, mapped to the MTG/Forge benchmark

Research brief, 2026-10-15. Labels used throughout:
- **[S]** sourced: the claim is stated by the cited source.
- **[I]** inference: my reasoning or recommendation, not stated by any source.
- **[S-2]** sourced only from secondary coverage (aggregators, reviews, news). Treat with caution.

Context read first: `eval/EVAL-DESIGN.md` §0–3 and `CLAUDE.md` "CURRENT FOCUS". The design already has an anchored STR ladder, paired seat-swap, pentanomial scoring, engine-verified puzzles with a 70/30 open/held-out split, and locked seed governance. The recommendations below are written as **additions or corrections to that design**, not a replacement for it.

## Which "Hillclimb" I used

Three readings turned up:
1. Anthropic's `/claude-api build-eval` + `/claude-api hillclimb` workflow (Lance Martin, "Automating eval design and hillclimbing with Claude", Sept 2026). I could not reach the primary post. Arize and other write-ups summarise it **[S-2]**.
2. LangChain's "Better Harness: a recipe for harness hill-climbing with evals" (primary, fetched) **[S]**.
3. A Maven course called "The Hillclimb". It is a course page with no extractable method.

I found **no company called Hillclimb** with relevant writing. I used (1) and (2), because both describe the same loop as this repo's `improve_loop.py`: pick a real task, use a reliable grader, record a baseline, make one change, keep it or revert it, and check a held-out set. Hamel Husain and Shreya Shankar's guides did not come up in these searches, so I do not cite them.

## Source findings (condensed)

**Hill-climbing guides**
- Record a baseline on **both** the optimisation set and the holdout set before any edit. Change one thing at a time. Keep a set of must-pass cases whose failure triggers an investigation even when the net score rises **[S, LangChain]**.
- "Any learning system is prone to reward hacking." Prompting a system not to overfit is not reliable, so pair the metric with human review of transcripts **[S, LangChain]**.
- Split cases at random into train and test. If train rises while test stays flat, the change is overfitting and is reverted **[S-2, Anthropic via Arize]**.
- The judge model must not be the model under test. Calibrate judges against human labels **[S-2]**.
- The system under test must find the answers *structurally* hard to reach **[S-2]**.
- A gain counts only if it exceeds evaluation noise **[S-2]**.
- Curate continuously: tag cases by behaviour, prune saturated cases, prefer a small well-tagged set ("quality > quantity") **[S, LangChain]**.

**Mechanize**
- Mechanize builds environments and evals for frontier coding agents, where "a grader scores the model's performance" and the score serves "as signals during reinforcement learning and evaluations". The same grader feeds both uses **[S, mechanize.work]**.
- Task quality and compute are complements ("cheap tires on a Ferrari"). Cheap, procedurally generated tasks are "lower quality, less informative". Their estimate is about $500 of compute per task per training run, so cutting corners on the task wastes the compute **[S, "Cheap RL tasks will waste compute"]**.
- Replication tasks are graded objectively and in binary: "either the generated implementation behaves identically to the reference, or it doesn't". The bottleneck is writing comprehensive tests. Environments must stay automatically scoreable as they scale to "thousands of diverse environments" **[S, "The upcoming GPT-3 moment for RL"]**.
- Narrow environments produce "narrowly overfitting" models that generalise poorly. Diversity of environments drives generalisation **[S, "How to fully automate software engineering"]**.
- None of the Mechanize essays I read discusses reward hacking by name **[S, absence]**.

**Prime Intellect**
- An environment is an installable module with four parts: a dataset (rows holding task plus scoring metadata), a rollout method, a Rubric of one or more reward functions, and a loader. Environments are "packaged as standalone Python modules with pinnable dependencies", so they "can be versioned, shared, tested, and iterated on outside of the context of training code" **[S, INTELLECT-3 report §2.2]**.
- "The same rollout and Rubric entrypoints can be used for either training or evaluation." Eval requests are interleaved with training requests on the same inference pool **[S, INTELLECT-3]**.
- Difficulty is managed online. Problems are sorted into easy/normal/hard pools by observed solve rate. Prompts the model always fails or always solves are discarded, because they carry no gradient **[S, INTELLECT-3]**.
- Their logic environment includes Sudoku and Minesweeper. Games sit alongside code and dialogue on the Hub **[S]**.
- Verifiers v1 restructures this into tasksets (data plus rewards), harnesses (the program driving the model) and traces (the message graph plus rewards, metrics, errors and per-call records). Tools are installed into harnesses as MCP servers **[S, verifiers v1 docs]**.
- When the Hub reimplements an established benchmark, it counts as complete only after "full reproduction of known evaluation scores for a relevant subset of models" **[S, scaling-environments blog]**.
- PI's RL glossary entry on reward hacking is the most useful single source here **[S]**. It lists four hack families: editing the check, special-casing tested inputs, reading leaked answers, and parser edge cases where empty or malformed answers pass. Its mitigations: score "in a runtime the agent never controlled"; "check that null and adversarial attempts score zero"; hide the grader; give the agent an allowed exit; demote a gamed shaping term to a logged metric. Its central diagnostic: "divergence between training reward and an independent measure of the intended behavior". It also warns that penalising one hack tends to shift the policy to a neighbouring one.

**Game-benchmark prior art**
- **Kaggle Game Arena** (Google DeepMind): games have "a clear definition for winning", and resist saturation and memorisation. The harness and environments are open source. Ratings come from all-play-all with a hundred or more matches per pair, and the Elo scale is fit by Bradley–Terry rather than updated online, because online Elo depends on game order **[S-2 for the BT detail]**.
- **TextArena**: TrueSkill (μ=25, σ=25/3) across 57+ games. The authors report it converged faster than Elo **[S]**.
- **LLM Chess (Saplin et al.)**: ranked 50+ models against a *random* opponent. Reasoning models saturated that floor in 2025, which forced the addition of a strong engine anchor (Dragon) for Elo **[S]**.
- **chessbenchllm**: Glicko-2. The random mover is pinned at 400 and Maia/Eubos engine anchors are "fixed and never updated", which calibrates the pool to Lichess. An illegal move gets one retry with feedback, and a second illegal move forfeits. First-try legal% is reported separately **[S]**.
- **Lichess puzzles**: positions are mined from about 600M games and re-verified by Stockfish NNUE at 40M nodes, with a single "crystal-clear" solution as the goal. Each solve attempt is a Glicko-2 game between player and puzzle, so puzzles carry ratings. Users still report puzzles with several solutions **[S]**.
- **Kagi chess puzzles**: 1,000 random Lichess puzzles, solved %, and an "adjusted Elo" that penalises illegal moves ("take it with a grain of salt") **[S]**.
- **BALROG** reports "% progress" on graded game milestones **[S]**. **lmgame-Bench** adds perception and memory scaffolds explicitly to cut prompt variance and contamination **[S, abstract]**.
- **MTG specifically**: MageBench (XMage engine, MCP tools, LLM-vs-LLM composite rating plus a "blunder index"). A critic argued the Legacy play was too weak to be meaningful **[S-2]**. MTG Bench (no engine; models track zones themselves and fail at it) **[S-2]**. ManaBench (pick the 60th card) **[S-2]**. I found no Forge-based LLM benchmark, so a Forge-engine-validated benchmark appears to be a gap **[I]**.

---

## Principles mapped to recommendations

### 1. Scores where higher reliably means better play and cannot be gamed

**P1.1 Score outcomes in a runtime the agent never controls.** **[S, PI glossary]** Forge already gives this: the engine validates every option, so illegal moves are impossible. The residual attack surface is the **harness**, not the rules **[I]**.
→ **Rec:** Treat every harness fallback path as a potential reward channel.
- A model that times out, returns garbage or exhausts its budget must never receive a *better* deterministic default than a competent answer would get. Today, failure branches "take deterministic legal options". Audit which option is taken: if it is ever "attack with everything" or "keep the hand", a degenerate policy could exploit it.
- Add a **null-policy test**, following PI's "null and adversarial attempts score zero" **[S]**. Run an always-empty-reply arm and an always-malformed-reply arm through STR and PUZ. Their scores must equal the floor arm's, or lower. If they beat `floor`, the fallback is leaking strength.

**P1.2 Every number needs its trivial baseline, and the floor must not saturate.** LLM Chess's random floor was saturated by 2025 **[S]**.
→ **Rec:** Publish a **three-rung floor** for every STR matchup on the frozen seed set: `floor`, `random`, and Forge AI piloting the same deck (the ceiling control the design already has). Report the model's score as a position on that ladder.
- For PUZ, the trivial baselines are: uniform-random option choice (expected solve rate = mean over puzzles of |verified-good options| / |menu|), always-PASS, and "first option". The "first option" baseline matters: the 88% lesson showed menu position bias is real.

**P1.3 Detect hacking by divergence from an independent measure.** **[S, PI]** The design's teacher-independent axes are exactly that.
→ **Rec:** Make "STR rises while PUZ-heldout or STR-HELDOUT does not" an automatic red flag in `build_report.py` for any RL-trained checkpoint. Also flag a falling first-try legal/parse rate or a rising `tool_calls`-per-decision count. PI warns that shaping terms such as per-tool-call bonuses get gamed **[S]**.

**P1.4 Penalising one hack shifts the policy to a neighbour.** **[S, PI]**
→ **Rec [I]:** Fix hacks in the environment (harness, engine options) rather than with reward penalties. Never add a "don't do X" reward term to the headline score. Log it as a weight-0 metric instead, matching PI's rubric pattern of logged-only metrics **[S]**.

**P1.5 Breadth defeats matchup-specific shortcuts.** Mechanize's diversity argument **[S]**.
→ **Rec:** Report pooled STR only as the mean of per-matchup scores, with equal weight per matchup (a macro-average). Never pool raw games, which would let one easy matchup dominate. Publish per-matchup cells so a deck-specific trick is visible.

### 2. Rating systems: PvP and vs a fixed opponent

**P2.1 Batch maximum-likelihood fit, not online Elo.** Online Elo depends on game order. Game Arena and LMSYS fit Bradley–Terry instead **[S-2]**.
→ **Rec:** For PvP, fit **Bradley–Terry by MLE over all games in a frozen version** and bootstrap CIs by resampling seed pairs (the existing clustering unit). TrueSkill **[S, TextArena]** and Glicko-2 **[S, chessbenchllm]** are incremental and suit a live ladder. For a frozen, versioned benchmark, a batch BT fit is reproducible and order-independent **[I]**.

**P2.2 Pin anchors and never update them.** **[S, chessbenchllm]**
→ **Rec:** Put STR and PvP on **one scale** by including the fixed players as BT participants with fixed ratings: `random` = 0 and Forge-AI = a constant derived from the ceiling control.
- Forge-AI must be anchored per deck, or as a single averaged anchor. Pick one and freeze it.
- A model's STR-AI win rate then converts to a rating on the same axis as PvP. Re-derive anchor ratings only on an engine bump, with a new benchmark version number.

**P2.3 Transitivity is not guaranteed.** **[I]** MTG matchups are rock-paper-scissors at the deck level, and pilot skill interacts with archetype (the design's measured 12–70% archetype spread).
→ **Rec:**
- Always run PvP as **mirror matches**, or as a fixed deck-pair schedule where both pilots play both sides with seat-swap. That separates pilot skill from deck choice.
- Fit BT per archetype class as well as pooled, and report a cycle check: the share of model triples with A>B>C>A at CI-significant margins.
- If intransitivity is significant, publish the full pairwise win-rate matrix as the primary artifact and the BT rating as a summary.

**P2.4 Draws and invalids.** Treating draws as half-wins is a modelling choice **[S-2]**.
→ **Rec:** Keep the design's current rule (pairs containing a draw or invalid game are dropped and counted separately). Report the invalid rate per model as its own number. A model that causes more timeouts or invalid games is being rated on a filtered sample **[I]**.

### 3. Puzzle suite design

**P3.1 Verification by engine, one clear answer.** Lichess re-verifies every candidate with a deep engine search and targets a single solution. Users still report multiple solutions **[S]**.
→ **Rec:** The design's verified-good option *sets* (all options that achieve the goal score full marks) handle multiple solutions correctly.
- Add a **uniqueness tier**: tag puzzles where |good set| = 1 and the best alternative is verifiably losing. Report PUZ-unique separately. These puzzles have the lowest trivial baseline and the clearest signal.
- Drop puzzles where |good set| / |menu| > 0.5, because their chance baseline is too high.

**P3.2 Rate the puzzles, not just the solvers.** Lichess treats each attempt as a Glicko game between player and puzzle **[S]**.
→ **Rec:** After each certification round, fit a joint BT/Rasch model over (model, puzzle) outcomes. That gives each puzzle a difficulty and each model an ability on one scale.
- Use the difficulties to retire saturated puzzles: solved by every model including `random`-adjacent baselines. This matches the PI/INTELLECT-3 practice of discarding always-solved and never-solved items **[S]**.
- Report PUZ as count solved *and* the Rasch ability, since count solved shifts whenever the suite mix changes **[I]**.

**P3.3 Hidden information makes "engine-verified" fragile.** **[I]** The design notes that the fixed deal can mark a correct-under-uncertainty choice as wrong (§3.5).
→ **Rec:** For graded categories, compute the answer key by **determinization**: resample the opponent's hidden hand and library consistent with public information K times (e.g. 16), roll out each, and average.
- Tactic categories should require the win or survival to hold **for every determinization** in which the visible board is the same. The key then rewards what was knowable.

**P3.4 Time-to-solve needs a defined unit.** **[I]**
→ **Rec:** Measure it in model calls, tool calls and tokens to the committed answer, not wall-clock, which depends on the provider. Report it only on solved puzzles, alongside cost. The hill-climbing guides advocate "cost while performance holds" as a target **[S-2]**.

**P3.5 Contamination.**
→ **Rec:** The 70/30 hash split and the 20000–29999 mining band are right. Add two things:
- **Never publish held-out position serialisations.** Publish only their hashes plus the generator commit, so anyone can re-derive and audit the set after it rotates out. This follows Game Arena's "open harness" stance **[S-2]** without leaking keys.
- Keep a **canary string** in every published open-split file, so later training-corpus scrapes can be detected **[I]**.

### 4. One environment for eval and RL without contaminating the eval

**P4.1 Same entrypoints, separate data.** PI uses identical rollout and rubric code for training and eval, and interleaves both **[S]**. LangChain and Anthropic insist on a holdout that the optimiser never sees **[S]/[S-2]**.
→ **Rec:** Package the Forge environment as a verifiers-style module. Dataset rows are `(deck_a, deck_b, seed, swap, controller_b)` for games and `position_address` for puzzles. The rollout is the Forge JVM. The rubric is win = 1, loss = 0 for games and verified-good-set membership for puzzles, with tool-call counts and legal% as weight-0 metrics.
- The **seed policy becomes the dataset split**. `seed_policy.py` should be called inside the loader, not only in `run_batch.py`, so an RL trainer loading the env cannot draw a reserved seed even by accident.

**P4.2 RL needs online difficulty filtering; eval needs fixed items.** **[S, INTELLECT-3]**
→ **Rec:** The training env may adapt its opponent mix: drop matchups the policy wins or loses at 100%, and add puzzle categories near 50% solve. The eval env must **never** adapt.
- Encode this as two loaders (`load_environment(mode="train")` and `load_environment(mode="eval", suite="v1-frozen")`). The eval loader refuses any adaptive option.

**P4.3 Training against Forge AI will overfit to Forge AI.** **[I]** Hill-climbing the STR-AI number with RL will learn Forge-AI's specific exploitable habits. That is the same failure mode as narrow environments **[S, Mechanize]**.
→ **Rec:** Keep at least one evaluation opponent that RL never trains against. Options: a held-out frontier model in PvP, a different Forge AI profile, or a deck pool partition with N decks never used as training opponents.
- The STR-AI-minus-PvP gap then measures opponent-exploitation, just as the STR-AI-minus-STR-HELDOUT gap measures seed memorisation.

**P4.4 Read the top-reward traces.** **[S, PI; S, LangChain]**
→ **Rec:** After every RL checkpoint, run `harness-audit` on the five highest-reward and five most surprising wins, to separate real skill from harness exploitation.

### 5. Versioning and freezing

**P5.1 Environments are pinned, versioned packages.** **[S, INTELLECT-3]** A Hub reimplementation must reproduce published scores before it counts **[S, PI]**.
→ **Rec:** One **benchmark version id** should hash `FORGE_COMMIT`, the overlay sha, the deck-pool file hashes, `seed_registry.json`, the suite files, `NativeTools`/`tools.json`, the observation profile, and the anchor ratings.
- Scores are comparable only within a version id. This generalises the existing engine-version rule.
- On a version bump, **re-run a pinned reproduction set** before the new version's scores count: floor canary, ceiling control, and two reference models. That is PI's "reproduce known scores" gate, applied to yourself.

**P5.2 Freeze, then rotate.** **[S-2, Anthropic: prune saturated cases; S, LangChain]**
→ **Rec:** Held-out seed bands and held-out puzzles are opened for a bounded number of certifications, then retired to open status. The design says three for puzzles. Apply the same rule to STR-HELDOUT seed blocks, and log every opening in the registry.

**P5.3 Conditions are part of the version.** **[I, consistent with CLAUDE.md native-vs-text rule]**
→ **Rec:** Native-tools vs text-tools, memory tools on or off, and observation profile are **conditions**, not noise. Each leaderboard row carries its condition tuple, and rows with different tuples are never pooled or ranked together.

---

## Sources

- LangChain, "Better Harness: A Recipe for Harness Hill-Climbing with Evals": https://www.langchain.com/blog/better-harness-a-recipe-for-harness-hill-climbing-with-evals
- Arize, "Claude's hillclimb loop for AI agents" (secondary on Anthropic's hillclimb): https://arize.com/?p=32658
- The Neuron explainer (secondary): https://www.theneuron.ai/explainer-articles/anthropic-built-a-better-way-to-improve-ai-agents-without-fooling-yourself/
- Maven, "The Hillclimb" course page: https://maven.com/p/e1761b/the-hillclimb-how-to-measure-and-improve-your-ai-agents
- Mechanize home: https://mechanize.work
- Mechanize, "Cheap RL tasks will waste compute": https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute/
- Mechanize, "The upcoming GPT-3 moment for RL": https://www.mechanize.work/blog/the-upcoming-gpt-3-moment-for-rl/
- Mechanize, "How to fully automate software engineering": https://www.mechanize.work/blog/how-to-fully-automate-software-engineering/
- Prime Intellect, Environments Hub blog: https://primeintellect.ai/blog/environments
- Prime Intellect, scaling environments program: https://primeintellect.ai/blog/scaling-environments-program
- Prime Intellect, RL glossary: reward hacking: https://primeintellect.ai/rl-glossary/reward-hacking
- verifiers docs (v1 overview): https://docs.primeintellect.ai/verifiers/v1/overview ; repo: https://github.com/PrimeIntellect-ai/verifiers
- INTELLECT-3 technical report: https://arxiv.org/html/2512.16144v1
- Google, Kaggle Game Arena announcement: https://blog.google/innovation-and-ai/products/kaggle-game-arena/
- InfoQ on Game Arena: https://infoq.com/news/2025/09/kaggle-game-arena
- AI Wiki on Elo vs BT in arenas (secondary): https://www.aiwiki.ai/wiki/elo_rating_system_ai/raw
- "Drawing Conclusions from Draws" (draw semantics): https://arxiv.org/pdf/2510.02306
- TextArena: https://arxiv.org/pdf/2504.11442
- LLM Chess (Saplin et al.): https://arxiv.org/html/2512.01992v1
- chessbenchllm methodology: https://chessbenchllm.onrender.com/methodology
- Kagi llm-chess-puzzles: https://github.com/kagisearch/llm-chess-puzzles
- Lichess, "New puzzles are here": https://lichess.org/blog/X-S6gRUAAGjNX4ki/new-puzzles-are-here
- BALROG: https://arxiv.org/pdf/2411.13543
- lmgame-Bench: https://arxiv.org/abs/2505.15146v1
- MageBench Season 1 (aggregator): https://benchmarklist.com/benchmarks/mage_bench_season1/ ; Show HN thread: https://hn.svelte.dev/item/47049227
- MTG Bench (secondary): https://feedbagel.com/post/mtg-bench-testing-how-well-llms-can-play-magic
- Hearthstone AI competition framework: https://arxiv.org/abs/1906.04238
