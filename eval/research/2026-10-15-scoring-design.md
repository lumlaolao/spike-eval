# Scoring proposal: one benchmark where higher is always better

*Research note, 2026-10-15. Builds on `docs/eval/EVAL-DESIGN.md` (§2, §3, §6), `eval/aggregate.py`
(Wilson CIs, seed-level paired bootstrap), `eval/seed_policy.py` / `eval/seed_registry.json`,
`eval/suites/stage3-v1.json`, `eval/match_review.py` and `runs/compare/REPORT.md`. This is a
proposal only. Nothing here is built unless the text says it exists.*

## 0. What "higher is always better" commits us to

Each headline number has to have the same properties:

1. **Monotone in skill.** A better policy cannot score lower in expectation. That rules out
   scoring by agreement with the teacher (the 88% lesson: a model that always passes scores
   78.2%), and it rules out any metric where losing slowly or stalling can raise the score.
2. **Anchored.** Each score is printed next to the trivial policies (floor = always pass,
   random = a uniformly random legal option) and next to the Forge AI. The number then means
   "this far above doing nothing" and not just "this many percent".
3. **Bounded in what one game can contribute.** One invalid game, timeout or crash moves the
   score by at most one game's worth, and always in the direction that penalises the seat that
   caused it.
4. **Comparable only inside one condition.** Same `harness_epoch`, `FORGE_COMMIT`, deck SHAs,
   `native_tools`, context profile and seed bank. `aggregate.py` already groups games by their
   full recorded condition, and the proposal keeps that rule.

There are three axes: STR-AI (vs Forge AI), PvP (rating between models) and PUZ (puzzles). The
composite question is in §4.

---

## 1. STR-AI: play against the Forge AI across 16 decks

### 1.1 The matchup grid

The pool has 16 decks: ant-storm, burn, cephalid-breakfast, death-and-taxes, doomsday, elves,
food-chain, goblins, grixis-reanimator, infect, lands, mardu-energy, miracles, painter,
sneak-and-show and ur-delver. The `hook-*` lists are mechanics probes, not archetypes, and stay
out of the pool.

There are two panels:

- **Mirror panel (headline, 16 matchups).** The model pilots deck *d*, and the Forge AI pilots
  the same deck *d*. Deck strength cancels exactly, the Forge-AI-vs-Forge-AI mirror is 50% by
  symmetry, and `run_batch.py`'s cited discipline already enforces it. One score per archetype
  answers "can it pilot this kind of deck as well as the heuristic AI can?"
- **Cross panel (breadth, 16 matchups, needs `cited-nomirror`).** Use a cyclic Latin square. The
  model pilots deck *d* against Forge AI on deck *d+k* (mod 16), with a fixed published *k*
  (for example 5, so that combo meets aggro meets control). Each deck appears once on each
  side. Every cross cell also gets an **AI-vs-AI control** on the same seeds. It costs nothing
  in model calls, only CPU. The model is scored by its **skill margin**:

  `margin(d, d') = WR(model on d vs AI on d') − WR(AI on d vs AI on d')`

  This removes matchup asymmetry. Burn vs Doomsday may be 70:30 for burn, but the margin asks
  only whether the model got more out of burn than the AI did.

### 1.2 Pooling

**Weight every matchup equally** (a stratified mean over cells), and report the per-cell numbers
next to the pool. In a balanced Latin square, equal weight per matchup is also equal weight per
deck, so the choice between the two goes away. If games are not balanced across cells (for
example after an early SPRT stop), the pooled score is still the cell-weighted mean and not the
game-weighted mean. Otherwise a model that happened to play extra games in its best matchup
would gain score.

The pooled CI comes from the stratified variance, `Var = (1/M²) Σ_m p_m(1−p_m)/n_m`. It can also
come from a cluster bootstrap that resamples seed-pairs within each cell. That bootstrap is
`aggregate.paired_bootstrap` run per cell, with the results combined.

The report must also give the **spread**: the worst cell, the best cell and the number of cells
whose CI lies entirely below 50%. EVAL-DESIGN §1.2 already records a 19.5-point spread in replay
match across decks. A pooled 50% that hides a 90/10 split is a worse artifact than no number.

### 1.3 Paired seeds

There are two separate pairings, and only one of them reliably helps.

- **Seat-swap pairing** (the same seed played in both seat orders; this exists as `--pairs`).
  Its main value is that it **cancels first-player and deal bias by construction**. It reduces
  variance only when the within-pair correlation ρ is negative (the luck of one deal favours
  seat 1 in both games, so the two results offset each other). The variance of a pair-mean is
  `p(1−p)(1+ρ)/2`. The measured ρ is **+0.02** (stage0-calibration, non-mirror, n=22 pairs), so
  in practice this pairing buys no variance reduction. Treat it as bias control and re-measure ρ
  in the mirror panel. Do not assume the "(1−ρ) fewer games" claim.
- **Cross-pilot pairing** (model X and model Y, or a model and the AI control, each play the
  *same* seed list against the same opponent). This is where variance reduction can come from:
  `Var(X−Y) = Var X + Var Y − 2Cov`. If good and bad deals affect both pilots alike, the
  correlation between pilots ρ_xy > 0 and the *difference* shrinks by (1−ρ_xy). Chess testing
  makes the same move with side-reversed pairs and pentanomial variance; plain trinomial
  variance overstates the error bars
  ([pentanomial tools](https://github-redirect.dependabot.com/vdbergh/pentanomial),
  [TalkChess discussion](https://talkchess.com/viewtopic.php?p=785782)). The same thread warns
  that naive pentanomial variance can *understate* the error when openings repeat. Here,
  bootstrapping over seeds is the safer CI, and `aggregate.py` already does it.

**Rule:** every model plays the **same published seed list** in every cell (from the bank in
`seed_registry.json`; stage3-holdout is 7000–7999), so all model-vs-model deltas are seed-paired.
The STR-HELDOUT number uses the 30000–30099 certification block once, at release.

### 1.4 How many games for ±5 points

The 95% half-width for a binomial proportion is `1.96·√(p(1−p)/n)`.

| target | p≈0.5 | p≈0.3 | p≈0.15 |
|---|---:|---:|---:|
| ±5 pts | 384 | 323 | 196 |
| ±7 pts | 196 | 165 | 100 |
| ±10 pts | 96 | 81 | 49 |

Inflate by ÷0.94 for the measured 6% invalid rate (EVAL-DESIGN §6.3). Stratifying over 16 cells
never makes the pooled interval worse, so **±5 on the pool takes about 400 games, 25 per cell**.
That gives a per-cell CI of about ±20, so cells can only be read as "clearly above or below
parity". For **±10 per cell** you need about 1,600 games, and for ±5 per cell about 6,400.

The margin score (§1.1) adds the variance of the AI-vs-AI control. That control is free, so run
it with 4× the seeds and the extra variance it adds becomes negligible.

For gating questions ("is v1 better than v0?"), use `eval/sprt.py` and not a fixed n, as
EVAL-DESIGN §6.2 already specifies. Never peek at a fixed-n block with SPRT.

### 1.5 First player and mulligans

- **First-player effect.** It is removed from the score by seat swap. It is *reported* per cell
  as the seat-1 win share; `aggregate.summarize_games` already computes `seat1_wins`. Expect it
  to be large for the combo decks (storm, doomsday, sneak-and-show). A cell with a missing swap
  partner is not scored.
- **Mulligans.** These are part of the skill, so `MULLIGAN_HEURISTIC=0` in cited mode (the
  model decides). Report the mulligan rate and the win rate conditional on keeping 7 as
  diagnostics. Do not adjust the score for mulligans. Doing so would reward a model that keeps
  bad hands.

### 1.6 Draws, timeouts and crashes

Today `aggregate.py` **excludes** draws and invalid games from the denominators. That is fine
for diagnostics but not for a headline score: a model that is losing and stalls into the
wall-clock cap gets its loss deleted, which breaks monotonicity. Proposal:

| outcome | headline score | why |
|---|---|---|
| rules draw (engine says draw) | 0.5 | symmetric and rare |
| wall-clock cap hit, the model seat consumed most of the clock | **loss for the model** | it ran outside its own declared `ModelCfg` regime, which EVAL-DESIGN §7 already calls "a result, not a confound" |
| cap hit, AI or engine time dominated | 0.5 and flagged | not the model's fault |
| harness crash (no game line, exception, `[FALLBACK->AI]`) | **excluded and re-run on the same seed**; if it crashes again, drop the seed from *every* model compared, so pairing holds | a harness defect is not information about the model |

The **crash rate** gets a cap: a block with more than 2% harness invalids is not scored (§5).
Report the exclude-everything number next to the headline as a sensitivity check.

### 1.7 Anchors

The anchors are part of the score itself, not just a sanity check:

- **floor** (always pass): expected about 0%. The measured result is 0/6 vs Forge AI
  (`runs/compare/REPORT.md`). It confirms the harness cannot "win by doing nothing", for
  example if the opponent decks out or a heuristic plays for the seat. A floor win is a harness
  defect.
- **random** (uniform over legal options; it reaches the full play/attack/block menu, which
  floor does not): the real "no skill" floor. Measured 0/6. If random gets above about 5% in
  any cell, that cell is too easy or the AI side is broken.
- **Forge AI mirror** (AI vs AI, same seeds): exactly 50% in the mirror panel. In the cross
  panel it gives the control for the margin score.

Normalised STR (monotone, and 0 means random):

`STR_norm = (WR_model − WR_random) / (WR_AImirror − WR_random)`

Here 1.0 means "plays like the Forge AI", and values above 1 mean better than the AI. It has
no upper ceiling, and that is deliberate. Clipping it at 1 would hide exactly the result the
benchmark exists to find. In the mirror panel, with WR_random ≈ 0, STR_norm ≈ 2·WR.

---

## 2. PvP: a Bradley-Terry rating with fixed anchors

### 2.1 Model

Fit a Bradley-Terry (logistic) model to every decisive game, including the STR-AI games, which
are just model-vs-AI edges:

`logit P(i beats j) = r_i − r_j + h·[i on the play] + m(d_i, d_j)`

- `h` is a global first-player term. `m` is a deck-matchup effect, which is 0 in mirrors and is
  estimated from the AI-vs-AI control games in the cross panel.
- **Identifiability:** fix **Forge AI = 1500** (Elo scale, 400/ln 10 per logit unit). Floor and
  random enter as players with ratings, but they win almost nothing, so their MLE goes to −∞.
  Give every rating a weak Gaussian prior (for example N(1500, 400²)) or add one virtual draw
  per player. That prior is stated in the report. The anchors then serve as **checks**: floor
  < random << Forge AI must hold in every bootstrap replicate, or the run is invalid.
- Chatbot Arena moved from online Elo to a Bradley-Terry MLE for more stable ratings and better
  CIs ([LMSYS blog](https://lmsys.org/blog/2023-12-07-leaderboard);
  [Chiang et al. 2024](https://arxiv.org/pdf/2403.04132)). Online Elo depends on the order of
  games, and a benchmark should not.

### 2.2 Scheduling and deck assignment

- **Unit of play:** a model pair plays **every mirror deck in both seat orders** on the same
  seeds. With 16 decks × 2 seats × k seeds, k=2 gives 64 games and k=4 gives 128.
- **Few models (≤6): full round robin.** This gives the most data for intransitivity checks.
- **Many models: anchored plus adaptive.** Every model plays the full STR-AI block, which links
  it to the AI anchor. It then plays PvP only against its 2–3 nearest neighbours by current
  rating, choosing the pairs with the widest CI on `r_i − r_j` (information-gain sampling, as
  Arena does). The cost then grows linearly in the number of models, not quadratically.
- **Precision:** SE in logits ≈ `1/√(n·p(1−p))`. At p=0.5, n=128 gives 0.177 logit, about
  ±31 Elo SE, so a 95% CI of about ±60 Elo. ±5 points of win rate near parity is about ±35
  Elo. Fine resolution is expensive, as in chess, where ±5 Elo takes thousands of games.

### 2.3 Confidence intervals

Use a cluster bootstrap: resample **seed-pairs** (both seat orders together) within each model
pair, refit BT, and run 1,000+ replicates. Report the percentile CI for each rating and a
**rank CI** (the 2.5–97.5% range of rank across replicates). The Arena paper found bootstrap and
sandwich intervals nearly identical
([arXiv 2403.04132](https://arxiv.org/pdf/2403.04132)). A later study showed that dropping under
1% of votes can flip the top Arena rank
([arXiv 2508.11847](https://arxiv.org/pdf/2508.11847)). So publish rank CIs and do not announce
a #1 that the CI does not support. [Evalica](https://arxiv.org/pdf/2412.11314) is a reference
implementation of BT with a bootstrap.

### 2.4 Intransitivity checks

BT assumes a single scalar of skill. Magic, with 16 archetypes, may not be like that: model A
may beat B on combo decks and lose on control. Checks:

1. **Goodness of fit:** compare the observed and BT-predicted win rate per model pair, with a
   χ² over pairs. Flag any pair whose residual is larger than 2 SE.
2. **Cycles:** count directed 3-cycles among pairs whose head-to-head CI excludes 50%.
3. **Per-deck BT:** refit per archetype family (aggro, combo, control, tempo). If the
   rankings disagree, publish the family ratings and not a single number.
4. **Nash averaging** ([Balduzzi et al., NeurIPS 2018](https://arxiv.org/pdf/1806.02643)) as a
   diagnostic. It is invariant to duplicated weak agents and easy tasks. Adding three
   near-identical deepseek variants should not shift everyone's rating, and Nash averaging
   shows when it does.

---

## 3. PUZ: puzzles

### 3.1 Two puzzle sets with different roles

- **Engine-verified play puzzles** (EVAL-DESIGN §3, mined from seeds 20000–29999). These are
  positions forked into Forge, where "solved" means **the engine reports the goal outcome**
  (win this turn, survive the turn, and so on). Any line that reaches it counts; no specific
  line is required. This is the headline PUZ.
- **Rules QA** (`eval/puzzles/modern/puzzles.jsonl`, 996 items from RulesGuru, Ask a Magic Judge and
  Cranial Insertion). These are **public web text, so assume they are in pretraining**. Report
  them as a separate rules-knowledge score, never as part of PUZ, and never as held-out.

### 3.2 Per-puzzle scoring with k attempts

Each model makes **n = 4 independent attempts** per puzzle in its native regime. With c
successes:

- **pass@1** = c/n (unbiased). The headline is the mean over puzzles, plus "puzzles solved"
  (the count with c ≥ n/2).
- **pass^k** = `C(c,k)/C(n,k)`, the chance that all k attempts succeed. This is the reliability
  number from τ-bench ([Yao et al. 2024](https://arxiv.org/pdf/2406.12045)). It is reported for
  k = 2 and 4 (k=8 for the free student). Under independence pass^k = (pass@1)^k, and the
  shortfall against that is itself a finding
  ([ReliabilityBench](https://arxiv.org/pdf/2601.06112)).
- **pass@k** (at least one success) is only a diagnostic. A game gives one attempt, so pass@k
  overstates playing strength.
- **Baselines per puzzle:** the random solve rate (estimated by about 200 random rollouts from
  the position; this is free) and the Forge AI solve rate. Headline PUZ =
  `mean_p (pass@1 − random_p)`, so a puzzle that a coin flip solves earns almost nothing.
  **Drop or down-tier puzzles with random_p > 0.2.**

### 3.3 Difficulty tiers and a puzzle rating

- **Tiers before any model is run:** tier 1 = Forge AI solves it, random does not. Tier 2 =
  Forge AI fails it, and the solution takes ≤ 3 decisions. Tier 3 = Forge AI fails it, and the
  solution takes 4 or more decisions or needs hidden-information reasoning. Report solved
  counts per tier.
- **Rating:** treat each attempt as a game between model and puzzle, so puzzles have a
  difficulty and models a skill. That is a 1PL IRT (Rasch) model, which is the same thing as BT
  between models and puzzles. Lichess rates puzzles with the same Glicko-2 system used for
  games: a correct solve is a win against the puzzle, and new puzzles start with high deviation
  ([Lichess forum](https://lichess.org/forum/general-chess-discussion/puzzle-rating-question)).
  We have few models (about 10), so fit the IRT offline, with the tier as a prior on puzzle
  difficulty, instead of updating online. Use Glicko-style deviations only to mark which puzzle
  ratings are still unreliable. The model's PUZ rating is then on a scale anchored at
  random = 0 and Forge AI = tier-1 mean.

### 3.4 Secondary measures

Among puzzles solved: median tokens, dollars and wall-clock per solved puzzle, and a cost-per-
solve curve. **Never fold these into the headline**, or a model that gives up fast would be
rewarded. Report them only on puzzles solved by every model compared, so the sets are the same.

### 3.5 Validity rules

- **Engine-verified:** each puzzle stores a reference line that the engine replays from the
  frozen state to the goal at the pinned `FORGE_COMMIT`. If that replay fails after an engine
  bump, the puzzle is retired.
- **Uniqueness is not required, but measure it.** Scoring is by outcome, so several solutions
  are fine. Record the verifier's count of winning first moves. A puzzle where most legal first
  moves win is a weak puzzle, and that is a tier question.
- **Contamination:** mined puzzles come only from 20000–29999. Hold out 30% by
  `sha256(game_id)` (EVAL-DESIGN §3.4). Exclude any position whose source game appears in
  `datasets/*/meta.jsonl`. Rotate on each engine bump. Publish the open 70% and never the
  held-out keys.

---

## 4. A single composite? Yes, but only as a derived view

**Arguments against:** the three axes measure different things. Strategic play over a whole
game, tactical precision and rating between models do not live on a common unit. Any weighting
is a value judgement, and a composite invites optimising the easiest axis.

**Arguments for:** leaderboards need one sortable column, and people will build one anyway, but
worse.

**Proposal:** publish a composite, with these four rules:

1. **Baseline-normalise each axis to the same anchors:** `S_axis = (score − random)/(ForgeAI −
   random)`. Random = 0 and Forge AI = 1 on every axis. STR uses the mirror-panel pooled win
   rate. PvP uses the BT rating, mapped to its expected score vs Forge AI. PUZ uses mean
   pass@1 over baseline on the held-out mined suite.
2. **Combine with a fixed, published weighting** (equal thirds, or the geometric mean of the
   S values clipped at 0). The geometric mean punishes a model that is strong on one axis and
   at the floor on another, which suits a "general MTG play" claim.
3. **Monotone by construction:** each S_axis is monotone in its raw score and the combiner is
   monotone, so improving any axis cannot lower the composite. Do not clip at 1 (§1.7).
4. **Never shown alone.** Always print it next to the three S values with their CIs. Propagate
   the CI by bootstrapping the composite from the per-axis bootstrap replicates.

---

## 5. Validity gates: no score counts unless all of these hold

| gate | source | threshold |
|---|---|---|
| no Forge-AI strategy | log `[TALLY] ai=0`; `defect_analyzer.py` `ai_tier_count` | **= 0** |
| no silent drops | `defect_analyzer.py` `silent_drop_total` | **= 0** |
| model was heard | `match_review.py` LOST count (answer asked for and not used) | **≤ 1% of model decisions** per block, 0 in puzzles |
| model was asked | `match_review.py` TAKEN + SKIP (harness decided an unforced choice) | **harness-decided share ≤ 2%** of unforced decisions |
| tools served | `tool_audit.py` REFUSED/FAILED | 0 REFUSED (a refusal is a harness defect, per CLAUDE.md) |
| fallback rate | `aggregate.py` fallback gate | ≤ 10% (existing); **propose 5% for headline** |
| same condition | `harness_epoch`, `engine_sha`, `overlay_sha`, `deck_sha`, `native_tools`, profile | identical across every compared arm; `aggregate.py` refuses mixed experiments |
| resolved identity | `served_*` fields | resolved for every llm seat (aggregate exit 3 otherwise) |
| seed hygiene | `seed_registry.py check`; seeds from the declared bank | clean |
| crash rate | harness invalids / games | ≤ 2% |
| anchor sanity | floor and random arms on the same seeds | floor wins = 0; random ≤ 5% per cell |
| canary | seed 42 floor mirror | identity A wins in 108 turns |

Each scorecard row also carries **cost per score**: $/game and $/puzzle-attempt from
`cost_report.py`, total $ for the row, and $ per point of CI width. That makes "the student ties
at $0" readable.

---

## 6. Worked numbers

Per-game prices: deepseek-v4.1-flash **$0.10** (two-tier, measured $0.102) to **$0.16**
(one-tier, measured $0.156), and frontier **about $5**. Zero-model arms (floor, random, AI-vs-AI)
cost $0 and are CPU-bound. Frontier games take about 35–45 minutes each.

**STR-AI (one model, the model seat only):**

| design | games | deepseek flash | frontier |
|---|---:|---:|---:|
| smoke, 6/arm (as in REPORT.md) | 6 | $0.6–1.0 | $30 |
| pooled ±10 (16 cells × 6) | 96 | $10–15 | $480 |
| pooled ±5 (16 × 25) | 400 | $40–64 | $2,000 |
| per-cell ±10 (16 × 100) | 1,600 | $160–256 | $8,000 |
| mirror + cross panels, pooled ±5 each | 800 | $80–128 | $4,000 |
| per-cell ±5 (16 × 400) | 6,400 | $640–1,024 | $32,000 |

Recommendation: cheap models get per-cell ±10 (about $200). Frontier models get pooled ±5 on the
mirror panel (about $2,000 each) and pooled ±10 on the cross panel ($480). Each starts with a
six-game block per the cost rule, then SPRT for the comparisons that matter. Run the free
controls (AI mirror ×4 seeds, floor, random) at full size every time.

**PvP:** both seats spend, so a game costs about $0.26 for deepseek vs deepseek and about $10 for
frontier vs frontier. A 5-model round robin is 10 pairs × 64 games = 640 games. That is about
$170 if all five models are cheap, and about $6,400 if all five are frontier, which is only
worth doing for an adaptive top-3 (3 pairs × 64 × $10 ≈ $1,900, giving about ±85 Elo 95% CI per
pair). In practice the STR-AI games already link every model to the anchor, so PvP should be
the adaptive extra run on the 2–3 closest pairs.

**PUZ:** the cost per attempt is about one decision, roughly 1/60 of a game: deepseek about
$0.002 and frontier about $0.08. A 300-puzzle held-out suite × 4 attempts = 1,200 attempts, about
$2.40 cheap and about $100 frontier. PUZ is the cheapest axis for each bit of information by
two orders of magnitude, which is one more reason to build the mined, engine-verified suite.

**A full scorecard for one frontier model** (STR mirror ±5 + cross ±10 + PUZ k=4 + one adaptive
PvP block) comes to about $2,000 + $480 + $100 + $640, roughly **$3,200**. For deepseek-flash it
is about $250–400. Quote the expected cost before every launch, and pass `--budget-usd`.

---

## Sources

- Chiang et al., *Chatbot Arena* (BT, bootstrap vs sandwich CIs): https://arxiv.org/pdf/2403.04132
- LMSYS, Elo to Bradley-Terry: https://lmsys.org/blog/2023-12-07-leaderboard
- Rank fragility on Arena: https://arxiv.org/pdf/2508.11847
- Evalica (BT and bootstrap implementation): https://arxiv.org/pdf/2412.11314
- Yao et al., *τ-bench* (pass^k): https://arxiv.org/pdf/2406.12045
- ReliabilityBench (pass^k vs independence): https://arxiv.org/pdf/2601.06112
- Balduzzi et al., *Re-evaluating Evaluation* (Nash averaging): https://arxiv.org/pdf/1806.02643
- Pentanomial paired-game statistics: https://github-redirect.dependabot.com/vdbergh/pentanomial ; https://talkchess.com/viewtopic.php?p=785782
- Lichess puzzle Glicko-2: https://lichess.org/forum/general-chess-discussion/puzzle-rating-question
