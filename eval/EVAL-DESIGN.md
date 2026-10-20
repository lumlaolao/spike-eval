# EVAL-DESIGN — a five-axis evaluation system for student vs. frontier MTG play

> 2026-10-15: [BENCHMARK-RESEARCH.md](BENCHMARK-RESEARCH.md) revisits the scoring (strict score,
> anchors, Bradley–Terry, puzzle rating, harness-gaming arms) from a second round of research.

> Design document, 2026-07-25. Status: **design only — nothing here is built unless
> marked EXISTS.** Each section is written to be implementable directly. Citations
> "(Brief A §n)" / "(Brief B §n)" refer to the two commissioned research briefs
> (Anthropic eval methodology; Mechanize + game-eval craft) reproduced in the
> project journal.

The question this system answers: **is the local Qwen3-8B student getting better,
and how does it compare — legitimately — to frontier models, on five axes:
strength, decision quality, tactics, plan adherence, and not-punting?**

The two traps it is designed around, both already observed in this repo:

1. **Decision accuracy is not strength.** Student v0 hit 88.1% decision-match vs
   its Forge-AI teacher (`runs/stage2-eval/replay-vs-forgeai.txt`, replay over
   `d1-selfplay` states) yet is recorded at a 10.0% win rate in the
   Delver-vs-Forge-AI-Burn anchor matchup, where the *same teacher piloting the
   same deck* wins 28.6% (ceiling control; both figures from the 2026-07-24
   stage-2 tracking notes — the win-rate runs live in the main checkout, not
   this worktree, so re-measure before re-quoting). PokerBench found the same
   dissociation — Brief B via `docs/training-feasibility.md` §2 (removed 2026-10-20; in git history). Agreement
   metrics are diagnostics, never headline results.
2. **The current eval cannot detect a student surpassing its teacher.** Every
   per-decision reference today (shadow oracle, replay-match, SFT labels) is the
   Forge heuristic AI. A student that finds a line the AI misses is scored as
   *wrong*. This design adds two teacher-independent axes: engine-verified
   puzzles (§3) and rollout win-probability scoring (§4).

---

## 0. Ground rules (inherited, non-negotiable)

- `forge-overlay/` is the source of truth; `FORGE_COMMIT` is pinned; results are
  comparable only within one engine version. Every artifact below stamps
  `engine_sha` + `overlay_sha` (run_batch already does this — EXISTS).
- `eval/defect_analyzer.py` is the integrity oracle (`ai_tier_count=0`,
  `silent_drop_total=0`). It gates every run this design produces. Never weakened.
- The jsonl schema is **additive-only**; consumers read via `.get()`. All new
  fields below are optional additions; new artifact types get their own files
  (schemas in §10 / `eval/schemas/`).
- The game-line `winner` field is identity-keyed (A = deck-identity A regardless
  of seat swap). All statistics below inherit that convention.
- **Egress consent:** any component that sends game state to a non-localhost
  endpoint requires `OPENROUTER_ALLOW_EGRESS=1`, which is set only by the user,
  never by tooling or agents. Every such component is flagged **[PAID/EGRESS]**
  below and appears in the consent table (§8.4).
- Cited-mode guards (mirror deck, confounds off, `--jobs 1` for CLI seats,
  provenance stamping) apply to any number that gets quoted (EXISTS in
  `run_batch.py`).

### Verified asset inventory (what the design builds on)

| Asset | Status | Notes |
|---|---|---|
| `eval/run_batch.py` seeded paired games, resumable, provenance | EXISTS | `--pairs` seat-swap; identity-keyed winner |
| `eval/aggregate.py` Wilson CIs, seed-level paired bootstrap, seat-bias check | EXISTS | bootstrap resamples at seed level (Brief A §1 clustering) |
| `eval/defect_analyzer.py` hard invariants | EXISTS | merge gate |
| `eval/replay_match.py` offline decision replay vs any OpenAI-compatible endpoint | EXISTS | reconstructs system preamble; excludes fallback/forced rows; localhost free, else egress-gated |
| `eval/process_metrics.py` paired-state teacher match, durdle/PASS-rate, Delver plan-adherence-lite | EXISTS | state-hash matching, not turn alignment |
| SHADOW_AI per-decision oracle (`shadow_choice` on decision rows) | EXISTS | validated by `eval/shadow_canary.sh` two-gate protocol |
| Self-play corpus: 5,394 games / ~978k decision rows (`runs/d1-selfplay`) | EXISTS (main checkout only — gitignored, not in worktrees) | seeded, deterministic ai-vs-ai; the SFT source |
| Frontier logs: `runs/tournament-1`, 8 models, 575 decision rows / 8 game files | EXISTS | `discipline=exploratory` (plans on, non-mirror) — reuse is constrained, see §7 Phase 0 |
| Frozen baseline seeds 501–525 (`runs/stage0-calibration`): deepseek (stage-1 teacher) piloting UR Delver vs Forge-AI Burn | EXISTS | pentanomial {2-0: 2, 1-1: 9, 0-2: 11}, 3 pairs draw/invalid; **deepseek-pilot** pair-share 29.5% (identity A; the Forge-AI anchor wins ~70.5%). Caveat: `discipline=exploratory` — plans on, mulligan heuristic on (see §6.3) |
| 16 Legacy decks with `.plan` files (`eval/decks/*.plan`) | EXISTS | plan-adherence ground truth (§5) |
| `ModelCfg` per-model budgets/timeouts (`OPENROUTER_MODEL_CFG`) | EXISTS | fairness basis (§7) |
| Decision-row fields: full `prompt`, `options`, `choice`, `tier`, `fallback`, `latency_ms`, `attempts`, `retry_ms`, `finish_reason`, `prompt_tokens`, `completion_tokens` | EXISTS | verified against `runs/stage0-calibration` |

**Contamination status of existing seeds (Brief A §6):** seeds 501–525 and the
`d1-selfplay` seed range have been used for development and training data. They
remain valid as *baseline anchors* (frozen, comparable across student versions)
but are **burned for held-out measurement**. §3.4 and §6.5 reserve fresh seed
blocks.

---

## 1. The five axes, as named metrics

Summary table; each axis gets a full section below. Grader hierarchy per
Brief A §2: programmatic first; model-graded only with a stronger judge and
only where flagged; human grading only for calibration samples.

| # | Axis | Metric name | Definition (one line) | Grader | Data source | Statistical protocol | Cost tier |
|---|---|---|---|---|---|---|---|
| 1 | Raw strength | **STR-anchor** | win rate vs frozen anchor opponents (Forge AI; optionally one frontier ref) on reserved seed blocks, paired seat-swap | programmatic (game `winner`) | new seeded games | pentanomial pairs + SPRT (§6) | free-local (student) / [PAID/EGRESS] (frontier) |
| 1 | Raw strength | **STR-h2h** | head-to-head mirror match student-vN vs student-vM (or vs a frontier pilot) | programmatic | new seeded games | pentanomial + GSPRT | free-local / [PAID/EGRESS] |
| 2 | Decision quality | **DQ-wpl** | mean win-probability loss per scoreable decision vs rollout reference (centipawn-loss analogue, Brief B §4) | programmatic (engine rollouts) | flagged decisions from logged games (§4) | clustered-by-game bootstrap CI | free-local (CPU-heavy) |
| 2 | Decision quality | **DQ-shadow** | disagreement rate with SHADOW_AI oracle, by decision type / turn bucket | programmatic | live games with `SHADOW_AI=1` | clustered CI; diagnostic only, never headline | free-local |
| 2 | Decision quality | **DQ-process** | durdle/PASS-on-castable rate, format-failure rate, retry rate (EXISTS: `process_metrics.py`) | programmatic | any logged games incl. tournament-1 | clustered CI | free-local |
| 2 | Decision quality | **DQ-judge** (optional) | rubric-graded rationale quality on a stratified decision sample | model-graded, stronger judge only (§4.4) | logged decisions | paired, position-swapped, length-controlled (Brief A §2) | [PAID/EGRESS] |
| 3 | Tactics | **PUZ** | score on the engine-verified golden suite, STS-style partial credit 0–10/position (Brief B §3) | programmatic (engine-verified answer key) | mined + verified positions (§3) | resampled k=4/position, clustered by source game | free-local to run for student; [PAID/EGRESS] per frontier model |
| 4 | Plan adherence | **PLAN** | milestone-attainment profile per archetype from declarative `.milestones.json` specs (§5) | programmatic (log extractors) | any logged games | milestone rates w/ clustered CIs; segment comparison at shared milestones (Brief B §5) | free-local |
| 5 | Not punting | **PUNT-rate** | blunders per 100 scoreable decisions (ΔWP ≥ 0.25 vs best option, rollout-adjudicated) — frequency, reported separately from DQ-wpl mean (Brief B §4) | programmatic | flagged decisions (§4) | clustered CI; capability-vs-reliability split (Brief A §4) | free-local (CPU-heavy) |
| 5 | Not punting | **PUNT-pass^k** | fraction of puzzle positions solved on ALL k=8 resamples (pass^k, Brief A §3) | programmatic | golden suite | exact binomial per position, aggregate CI | free-local (student) |
| 5 | Not punting | **PUNT-worst** | per-game worst decision (max ΔWP) distribution; p95 across games | programmatic | flagged decisions | per-game maxima, bootstrap CI | free-local |

Reporting standard for **every** number (Brief A §1): point estimate + SEM +
95% CI, n and cluster count stated; decision-level metrics are always clustered
by game (naive SEs understate up to 3×); game-level metrics cluster by seed-pair.

**Plus, as of 2026-07-26 — every accuracy-shaped metric MUST report the trivial
baseline it beats, and the margin over it.** See §1.1.

### 1.1 The 88% lesson — why a match rate without its floor is not a result

Measured, this repo: replay decision-match against the Forge-AI teacher read
**88.1%** (Wilson [86.3, 89.6], n=1500) while the same student won **10%** of
games (5/50). Both numbers were correct, and the reconciliation is structural:

- The Forge AI declines to act on ~83% of priority windows, logging `choice=-1`.
- `canonical_target()` encodes that decline as the last menu option, which is
  always `PASS (do nothing)` — verified 22614/22614 sampled `play` menus.
- So **78.2% of scoreable rows have PASS as the answer** (72639 sampled;
  `play` alone is 94.9% of rows and 82.4% PASS).
- **A model that only ever emits PASS scores 78.2% on this metric.** The
  student's 88.1% is **+9.9 points over doing nothing.**
- The errors are not spread evenly: all 20 recorded mismatches were rows where
  the teacher *acted*. Agreement is near-total on the trivial majority class and
  poor on the minority class — which is precisely the set of decisions that
  decide games. Hence 88% agreement alongside a 10% win rate.

Consequences now binding on this design:

1. `replay_match.py` emits `majority_class_baseline` and `margin_over_baseline`
   in both JSON and text. **DQ-shadow and any future agreement metric inherit
   this requirement.** Quoting a raw agreement rate is a reporting error.
2. **Headline axes must have a near-zero trivial baseline.** STR (win rate vs a
   real opponent) and PUZ (positions actually solved) qualify; agreement rates
   do not, which is why DQ-shadow was already marked "diagnostic only, never
   headline" and stays there.
3. **Training corpora must be class-balanced deliberately.**
   `datasets/stage2-r2-teacher-v0` is 78.3% PASS targets; trained as-is it
   teaches inaction, which is the failure mode the win rate then measures.
   Check `target_synth` shares before every training run.
4. `DQ-process`'s durdle / PASS-on-castable rate is promoted from a nice-to-have
   to the **primary diagnostic** for this pathology: it measures the failure
   directly rather than inferring it from an agreement gap.

### 1.2 Archetype breadth is a first-class requirement

A win rate measured on one matchup is a fact about that matchup. `burn` vs
`ur-delver` was the entire basis of every pre-2026-07-26 number, and it cannot
distinguish "plays Magic well" from "has memorized one race."

**STR-AI and STR-HELDOUT must therefore be reported across the full deck pool
(16 archetypes in `eval/decks/`), per-matchup AND pooled**, with per-matchup n
stated. A pooled number that hides a 90%-vs-10% split across archetypes is a
worse artifact than no number. Mirror-deck pairing still applies within each
matchup (`run_batch.py` cited discipline); asymmetric student-deck-vs-AI-deck
matchups need the `cited-nomirror` mode noted in `eval/README.md` and are not
in v1.

Evidence this matters, already in hand: replay match rate by deck ranges from
**73.4% (Elves)** to **92.9% (Grixis Reanimator)** — a 19.5-point spread across
archetypes on the same model, larger than the entire margin over the trivial
baseline.

---

## 2. Axis 1 — raw strength (STR)

### 2.1 Design

- **Anchored ladder, not round-robin.** Every model (student versions, frontier
  models) plays the *same* frozen anchor under the *same* protocol: Forge AI
  piloting Burn, plus the ceiling control (Forge AI piloting the test deck vs the
  same anchor) rerun once per engine version. Anchoring makes results comparable
  across time and models without O(n²) games. A single cheap frontier reference
  (deepseek, $0.06/game) may be added as a second anchor rung once, under consent.
- **Paired seat-swap on frozen seed blocks** (EXISTS: `--pairs`). Same seed = same
  shuffles/coin flip; each identity plays both seats of the same deal (Brief B §2
  "identical openings + swapped seats"; Brief B §6 paired deals as control variates).
- **Pentanomial pair scoring** (Brief B §2): a seed-pair yields one observation in
  {2-0, 1-1, 0-2} (pair score 1, ½, 0). Pairs with a draw/invalid game are dropped
  from the paired analysis, counted separately (matches `aggregate.py` semantics).
- **Honest pairing math:** measured within-pair correlation on the real
  stage0-calibration baseline is ρ ≈ 0.02 (pair-score variance 0.106 vs 0.104
  under independence, n=22 pairs). In this *non-mirror* matchup pairing buys
  almost no variance reduction — its value here is seat-bias cancellation and the
  pentanomial unit of analysis. Do not quote the "(1−ρ) fewer games" claim
  without measuring ρ for the specific matchup; `aggregate.py`'s realized-CI
  approach already does this correctly.
- **Matchup suite:** primary = the training matchup (UR Delver vs Burn) for
  continuity with baselines; secondary = 2 held-out archetype matchups the
  student never trained against as the *generalization* strength number
  (training-feasibility risk #4: 12–70% win-rate spread by archetype). Report
  per-matchup, never blended.

### 2.2 Protocols by question

| Question | Protocol | Stopping rule |
|---|---|---|
| "Is student v1 better than v0?" | both versions vs the same anchor on the same reserved seed block, or direct v1-vs-v0 mirror | SPRT (§6.2): H0 p=p_v0, H1 p=p_v0+0.10, α=β=0.05 |
| "Track student progress over versions" | fixed 25-pair anchor block (frozen forever, like seeds 501–525) | fixed-n, CI plotted over versions |
| "Student vs frontier model X" | both pilot the same deck vs the same anchor on the same seeds — **paired across pilots**: per-seed-pair score difference | fixed-n from power table (§6.4); frontier games are [PAID/EGRESS] |
| "Certify a release" | held-out seed block, opened once, then retired (§6.5) | fixed-n, pre-registered |

Direct student-vs-frontier head-to-head mirrors are supported but expensive per
bit of information; the shared-anchor paired design gets most of the variance
reduction (same deals for both pilots) at half the frontier-game cost.

### 2.3 Cost & where it runs

Student games: free (local MLX serve + local JVMs) or Phase-1 AWS (vLLM student
server + parallel JVMs; `eval/CONCURRENCY.md` limits apply). Frontier games:
[PAID/EGRESS], $0.06–$0.37/game (§8.4).

---

## 3. Axis 3 — the golden puzzle suite (PUZ) — *the teacher-independent axis*

STS model (Brief B §3): a curated suite of positions, each with a known tactical
or positional point, top options scored 1–10 partial credit; measures specific
skills orthogonally to full games and correlates with strength better than pure
tactics suites. Ours differs from chess in one crucial way: **the answer key is
produced by engine simulation, never by the heuristic AI's opinion.** This is
what makes a student-surpasses-teacher signal detectable.

### 3.1 Position source and the fork harness

Positions are *states from real logged games*, re-reached by deterministic
replay. Self-play (`ai`/`floor`/`random` controllers) games are bit-deterministic
given (seed, controllers, engine_sha, overlay_sha) — the seed-42 floor canary is
the proof of concept. Therefore a position is addressed as:

```
(engine_sha, overlay_sha, deck_a, deck_b, controllers, seed, swap, decision_index)
```

**New component — `LlmSpikeFork.java`** (forge-overlay, headless): replays a
seeded game with the original deterministic controllers up to `decision_index`,
then at that decision point either
(a) **enumerate-and-verify**: enumerate the live options menu and run bounded
    exhaustive checks (e.g., all block assignments) — used for tactic
    verification; or
(b) **rollout**: apply option *i*, then play both sides to completion with the
    deterministic AI controller under `rollout_seed` = f(base_seed, i, j) for
    j=1..N; emit per-option win counts.

Implementation notes (for the implementer):
- Reuse the replay determinism the canary already relies on; do NOT attempt
  `GameCopier` mid-stack state copies in v1. **Restrict v1 puzzle points to
  empty-stack decision points** (main-phase play, attackers, blockers, mulligan)
  — cleaner semantics and sidesteps copy-fidelity risk. If replay-to-index
  proves fragile, fall back to `GameCopier` (Forge's simulation AI uses it) as
  plan B, same empty-stack restriction.
- Output: one jsonl line per (position, option) with win counts — schema
  `eval/schemas/puzzle-position.v1.schema.json` (§10.1).
- The fork harness must run `defect_analyzer`-style self-checks: replayed
  decision count must match the source log's, else abort (a drifted replay is a
  wrong answer key). Stamp both shas; refuse on mismatch with the source game
  line (canary lesson: say what would make the gate fail).

### 3.2 Candidate mining — `eval/mine_puzzles.py` (new, ~350 lines, stdlib)

Scans decision jsonl corpora (CPU text-scan only; runs now) and emits *candidate*
position addresses with a category hypothesis. Cheap text heuristics over the
`prompt` state block + `options` menu; the engine verifies, so miner precision
only affects yield, not correctness:

| Category | Candidate heuristic (text) | Engine verification (fork harness) | Target n (v1) |
|---|---|---|---|
| **lethal-now** | sum of own untapped creature power + castable burn ≥ opponent life | exhaustive: some option sequence ends the game this turn vs every legal block assignment (defenders ≤ 4 blockers, else drop candidate) | 300 |
| **save-vs-lethal** | opponent attack on the board would be lethal if unblocked/unanswered | exhaustive over own blocks/answers: ≥1 option survives the turn, ≥1 loses | 150 |
| **forced-block** | attackers declared, own life ≤ incoming unblocked damage | same as above at the blockers decision | 150 |
| **free-win** (counter/punish) | opponent's game-winning spell on stack (from prompt), answer in hand | rollout: WP gap between answer and pass ≥ 0.5 | 100 |
| **mulligan** | opening-hand decisions | rollout N=32 per branch; keep positions where |WP(keep)−WP(mull)| ≥ 0.15 and gap ≥ 2·SE | 150 |
| **graded-midgame** | none (random stratified sample of play/attack decisions, turns 3–8) | full per-option rollouts; keep positions where WP(best)−WP(worst) ≥ 0.2 | 150 |

Total v1 target: **~1,000 verified positions** (STS used 1,500 — Brief B §3);
accept fewer per category if verification yield is low, and report yield.

**Sourcing rule (contamination, Brief A §6 / Brief B §1 hidden test sets):**
the training corpus `runs/d1-selfplay` is *burned* for the student — it may have
memorized teacher answers on those exact states. Eval positions come from
**freshly generated ai-vs-ai games on reserved seed block 20000–29999** (free,
~9–20 s/game headless; a 2,000-game mining run is an overnight CPU job). The
d1-selfplay corpus may be used only to *tune the miner's heuristics*, never to
source suite positions.

### 3.3 Scoring — partial credit, resampling, pass^k

- **Tactic categories** (lethal-now, save-vs-lethal, forced-block, free-win):
  binary core — chosen option in the verified winning/surviving set = 10, else 0
  — with partial credit for options the verifier proves *also* achieve the goal
  by a different line (all verified-good options score 10; there is no "style"
  credit; grade what was achieved, not the path — Brief A §3).
- **Graded categories** (mulligan, graded-midgame): STS-style mapping from
  rollout WP: `score(o) = round(10 · (WP(o) − WP_min) / (WP_max − WP_min))`,
  answer key frozen into the suite file. Options whose WP CIs overlap WP_max get
  10 (don't punish ties the rollout can't distinguish).
- **Resampling per position** (Brief A §1): each model answers each position
  k=4 times at its documented operating regime (§7); position score = mean.
  **pass^k** with k=8 on the tactic categories is the PUNT-pass^k reliability
  number (Brief A §3–4: pass^k, worst-case behavior tracked separately from
  average). For the student this is free; for frontier models resampling
  multiplies token cost — Phase 2 may reduce to k=2/k=4 and must say so.
- **Serving**: `eval/puzzle_run.py` (new, ~250 lines) reuses `replay_match.py`'s
  request-reconstruction and parser port; localhost endpoints free, anything
  else refuses without `OPENROUTER_ALLOW_EGRESS=1` (same boundary, same wording).
- **Aggregation**: overall PUZ = mean position score; report per-category; SEs
  cluster-bootstrapped **by source game** (positions from one game correlate —
  Brief A §1).

### 3.4 Holdout and rotation (Brief A §6, Brief B §1)

- Split the verified suite by `sha256(game_id)`: **70% open** (may be looked at,
  used for error analysis and training-data ideas), **30% held-out** (answer keys
  kept out of any training pipeline input path; never used for development; run
  at certification time only).
- **Rotation**: on each engine bump, or once the held-out set has been run ≥3
  certifications, mine a fresh suite from a new seed block (dynamic regeneration
  is cheap here — that's the luxury of an engine-verified generator). Old suites
  are retired to open status.
- **Hard rule**: any position whose source game contributed a row to any SFT
  dataset (`datasets/*/meta.jsonl` records game provenance) is excluded from the
  held-out split automatically. `mine_puzzles.py` takes `--exclude-train-meta`.

### 3.5 Answer-key limits (stated on every report)

Rollout WPs are **single-deal, perfect-information continuations by the Forge
AI**: (a) the continuation policy is weak at combo, so combo-line WPs are
underestimated — combo-deck graded positions carry a `rollout_policy_caveat`
flag; (b) the deal is fixed, so "correct given hidden information you couldn't
see" can be scored as wrong — tactic categories are restricted to
public-information-dominant states (lethal on board, declared attackers) where
this bias is minimal; graded categories carry the caveat.

---

## 4. Axes 2 & 5 — decision quality (DQ) and blunder/punt (PUNT)

The chess analogy (Brief B §4): centipawn loss → **win-probability loss (WPL)**
per decision, with a classification hierarchy, and *frequency of large drops
tracked separately from the mean* (consistency vs strength; Brief A §4
capability vs reliability).

### 4.1 Reference evaluators — candidates and failure modes

| Evaluator | What it gives | Failure modes | Verdict |
|---|---|---|---|
| **Shadow oracle** (`SHADOW_AI=1`, EXISTS) | per-decision agreement with Forge AI over the same menu; free; every decision | teacher-capped (agreement ≠ quality; can't see student surpassing teacher); AI is weak at combo and some targeting; binary — no magnitude | **trigger + diagnostic**, never a quality score |
| **Rollout ΔWP** (fork harness §3.1) | teacher-independent magnitude: `WPL(d) = WP(best option) − WP(chosen)` from N rollouts per option | expensive (~option_count × N × seconds-per-rollout); continuation-policy bias (combo blind spot); single-deal hidden-info bias; only meaningful when WP gaps exceed rollout noise | **the adjudicator**, applied to a flagged subset |
| **Hybrid (chosen design)** | shadow disagreement ∪ cheap red-flags → rollout adjudication of flagged decisions only | inherits both, but cheaply; flagging recall < 1 (a blunder the AI also makes is missed — quantify via a random-sample audit lane) | **v1 design** |

**Flagging rule (per decision):** flag if any of
(a) `shadow_choice` present and ≠ `choice`;
(b) text red-flag: PASS while the lethal-now heuristic (§3.2) fires; chump-free
    lethal blocks skipped; discard of the only land / only threat (per-type rules,
    ~10 patterns, in `blunder_flags.py`);
(c) **audit lane**: a 5% uniform random sample of all scoreable decisions —
    this measures what the flaggers miss (report flagged-lane and audit-lane
    PUNT-rates side by side; if the audit lane finds blunders at a
    non-trivial rate, the flaggers are leaky and the report says so).

**Adjudication:** fork harness re-reaches the state (deterministic-controller
games) and rolls out every option, N=16, both-sides Forge-AI continuation.
`WPL = WP_max − WP_chosen`; require `WPL > 2·SE_rollout` to classify above
"good" (don't classify noise).

### 4.2 Classification hierarchy (Brief B §4)

| Class | ΔWP (win-prob points) |
|---|---|
| good | < 0.05 |
| inaccuracy | 0.05 – 0.10 |
| mistake | 0.10 – 0.25 |
| **blunder (punt)** | ≥ 0.25, or any drop crossing WP 0.5 by ≥ 0.15 |

Thresholds are v1 priors; recalibrate once against the observed rollout-noise
floor (SE at N=16 ≈ 0.125 worst-case — if that proves too noisy, blunder-class
adjudication reruns at N=64). Metrics:

- **DQ-wpl** = mean WPL over adjudicated decisions (strength-like, average).
- **PUNT-rate** = blunders per 100 scoreable decisions (reliability, tail).
- **PUNT-worst** = per-game max WPL; report the p95 across games.

### 4.3 Which decisions are scoreable (and which are not)

Scoreable: `n_options ≥ 2`, no `fallback`, type ∈ {play, attackers, blockers,
targets, discard, sacrifice, modes, entity, zone-change, mulligan}, decision
point re-reachable (v1: deterministic-controller games only ⇒ **student and
frontier LLM games are NOT rollout-scoreable in v1**, because an LLM game can't
be deterministically replayed to the decision point).

This is the key scope limit and the roadmap driver: v1 rollout adjudication
applies to (a) puzzle-suite answers by construction and (b) **shadow-flagged
decisions in LLM games become scoreable in v2** via a state-import path (fork
harness accepts a serialized state snapshot instead of a replay address —
requires emitting an additive `state_export` blob on flagged decision rows from
the live harness; bounded: only flagged rows, empty-stack only). Until v2,
in-game PUNT for LLM pilots is estimated from the **text red-flag lane verified
by bounded exhaustive checks only** (lethal-missed and forced-block-missed are
still engine-verifiable from the prompt snapshot alone in most cases) — a
narrower but honest punt count, labeled `punt_rate_textlane`.

Not scoreable, ever, in this design: hidden-information "read" quality (was
holding up Daze correct given the opponent's actual hand is not knowable from
one deal), multi-decision line intent (each decision scored locally), and any
decision the engine auto-resolved (`tier=auto`, `n_options ≤ 1`).

### 4.4 DQ-judge — the only model-graded lane (optional, Brief A §2)

Only if the user wants a "reasoning quality" number at all (open question §9):
stratified sample of ≤200 decisions, judge must be strictly stronger than every
judged model on this task (practically: judge the *student's* rationales with a
frontier model; do NOT judge frontier flagships against each other — no
credible stronger judge exists); pairwise position-swapped A/B, identity-blind,
length-penalty control (verbosity bias is the dominant judge bias, 0.76–0.92 —
Brief A §2); calibrate against a 50-decision human-labeled set before trusting;
judges fail on tasks they can't solve, so judge prompts include the engine
verdict (rollout WPs) as ground truth — the judge grades the *explanation*, not
the move. [PAID/EGRESS], Phase 2.

---

## 5. Axis 4 — plan adherence (PLAN)

PTCG-Bench's finding (Brief B §5): models fail most at maintaining strategy
*across* turns. The `.plan` files are the per-archetype ground truth for what
"the strategy" is. `process_metrics.py` already implements the UR Delver
plan-adherence-lite triple (first-threat turn, protection-while-threat,
face-vs-creature burn split) — this section generalizes it.

### 5.1 Declarative milestone specs — `eval/plans/<deck>.milestones.json` (new)

One JSON file per archetype (16 files, hand-written from the `.plan` files, each
~10–20 predicates). Predicates are **programmatic extractors over decision rows
and game lines** — card-name/regex matches on chosen `options[choice]` text,
turn windows, and simple state predicates parsed from the `prompt` snapshot
(life totals, storm count where surfaced, battlefield card names). No judge
model. Schema in §10.3. Example milestone kinds:

| Kind | Example (deck) | Extractor |
|---|---|---|
| `deploy_by_turn` | threat on board by T2 (UR Delver); Thalia by T2 (D&T) | first chosen play matching name-set, turn ≤ N |
| `sequence_order` | ritual before engine spell in the combo turn (Ant Storm); Sneak Attack before creature reveal (Sneak & Show) | ordered pair of chosen plays within one turn |
| `resource_discipline` | Wasteland activation on a dual (Delver, Lands); fetch crack timing (already in process_metrics) | chosen-option text match |
| `combo_assembly_progress` | pieces in hand/board count over turns (Painter, Food Chain, Doomsday pile started) | prompt-state name counts, monotone progress curve |
| `payoff_condition` | Tendrils only at storm ≥ 9 vs opp life (ANT); Murktide only with ≥5 cards in yard | chosen play + state predicate |
| `protection_usage` | counter held/used while threat on board (Delver, Show) | already implemented for Delver — port |

### 5.2 Metrics

- **PLAN-attain**: per milestone, fraction of games attained (with turn
  distribution); per deck, the attainment vector — reported as a profile, not
  collapsed to one number (different milestones have different win-relevance).
- **PLAN-hold**: strategy *maintenance* — for `combo_assembly_progress` and
  `protection_usage`, the fraction of turns t where the milestone predicate
  still holds given it held at t−1 (directly targets the PTCG-Bench cross-turn
  failure mode, Brief B §5).
- **Segment comparison** (Brief B §5): among games (across models) that reach
  the same milestone, compare downstream win rate and time-to-next-milestone —
  isolates local quality from downstream variance.
- **Checkpoint partial credit** (Brief A §3): a lost combo game that assembled
  the combo scores on assembly milestones — progress is credited even when the
  outcome verifier says loss.

Caveats stated in the report: milestone attainment is correlational, not causal
(attaining a milestone against a fast clock may be a punt); extractors are
text-brittle — each spec file ships with 3 hand-checked example games as
fixture tests (`test_plan_adherence.py`).

Runs on any logged corpus, including tournament-1 frontier logs and all student
runs. Free-local, Phase 0. New code: `eval/plan_adherence.py` (~300 lines) + 16
spec files + tests.

---

## 6. Statistical protocol

### 6.1 Units, clustering, CI standards (Brief A §1)

- Game-level unit: the **seed-pair** (pentanomial observation). Decision-level
  unit: the decision, **always clustered by game** (bootstrap resamples games,
  not rows). Puzzle unit: the position, clustered by source game.
- Every reported number: estimate, SEM, 95% CI, n, cluster count. Wilson CI for
  single proportions (EXISTS in aggregate.py); seed-level paired bootstrap for
  deltas (EXISTS); BCa or percentile bootstrap (≥2,000 resamples) elsewhere.
- Pre-register (in the run's README, before launching): the question, the
  protocol row from §2.2, n or SPRT bounds, and the primary metric. Everything
  else is exploratory and labeled so.

### 6.2 SPRT for "is v1 better than v0" (Brief B §2)

Anchored binomial SPRT on win rate vs the frozen anchor:

- H0: p = p_baseline (v0's measured anchor win rate, e.g. 0.10)
- H1: p = p_baseline + 0.10 (the minimum improvement worth certifying)
- α = β = 0.05 ⇒ log-bounds ±ln(19) ≈ ±2.944
- Per-game LLR increments at (0.10 → 0.20): win +ln(2)=+0.693, loss
  +ln(8/9)=−0.118. Expected games ≈ 60 (H1 true) to ≈ 72 (H0 true); plan the
  batch backstop at 4× that. Draw/invalid games add no LLR and don't count.
- For head-to-head mirrors, use **GSPRT over the pentanomial** pair scores
  (variance estimated from the observed pentanomial frequencies — Brief B §2;
  implementation `eval/sprt.py`, ~150 lines, formulas inline there): H0 pair-mean
  0.5 vs H1 0.5 + 0.05.
- LOS (likelihood of superiority) reported alongside:
  `LOS = 0.5·(1 + erf((W−L)/√(2W+2L)))` (Brief B §2).

SPRT is for *gating decisions* (is v1 better — stop as soon as certified);
frozen fixed-n blocks are for *tracking* (comparable CIs across versions). Never
SPRT-peek a fixed-n block.

### 6.3 Real variance numbers (measured, this repo)

From `runs/stage0-calibration` (25 seed-pairs, deepseek — the stage-1 teacher —
piloting UR Delver vs Forge-AI Burn, seeds 501–525). **Source caveat:** these
games are `discipline=exploratory` with confounds ON (`mulligan_heuristic=1`,
`skip_heuristics=1`, non-empty plans, non-mirror decks) — legitimate for
estimating variance structure, but the ρ/variance numbers below may not
transfer to a cited-mode (confounds-off, mirror) run; re-measure there before
leaning on them:

- Pentanomial: {2-0: 2, 1-1: 9, 0-2: 11}; 3 pairs dropped (draw/invalid game).
- Pair-score mean 0.295 = the **deepseek-pilot** baseline (identity A pair-share;
  the Forge-AI Burn anchor wins ~70.5%); decisive-game pilot win rate
  15/47 = 31.9%.
- Pair-score variance 0.106 ⇒ within-pair correlation **ρ ≈ 0.02** (vs 0.104
  expected under independence). Pairing gains ≈ nothing *in this asymmetric
  matchup*; expect (but verify) higher ρ in true mirrors.
- Draw/invalid rate: 3/50 games = 6% — power plans below inflate n by ÷0.94.
- Game length: mean 12.1 turns (7–23); ~90–100 decision rows/seat/game.

### 6.4 Power table (α=0.05 two-sided, 80% power)

All rows are **raw n** (decisive games); inflate every row by ÷0.94 for the
observed 6% draw/invalid rate. Anchored comparisons at our operating win rates
(two-proportion, unpaired across arms since each version plays its own games;
ρ≈0 measured):

| Comparison | Detectable Δ | games/arm (raw) |
|---|---:|---:|
| v1 vs v0 near p≈0.10 | +0.10 (0.10→0.20) | ~200 |
| v1 vs v0 near p≈0.10 | +0.05 (0.10→0.15) | ~690 |
| near-parity mirrors p≈0.5 | 0.10 | ~390 |
| near-parity mirrors p≈0.5 | 0.05 | ~1,560 |
| mirror pentanomial pairs (σ²=0.125) | 0.10 pair-score | ~100 pairs (200 games) |
| mirror pentanomial pairs | 0.05 | ~390 pairs |

Design consequence (Brief B §2: ±5 Elo needs thousands): **local/AWS lanes are
sized for large effects (Δ ≥ 0.10) and SPRT early stopping; fine resolution
(Δ ≈ 0.05) is only attempted where games are free and parallel** (student vs
anchor on AWS: 725 games ≈ $2–4 of spot compute at Phase-1 throughput). Frontier
fine resolution is explicitly out of budget — the design reports frontier CIs at
whatever width 25–50 consented pairs buy (≈ ±13–19 pts at n=50 games) and does
not pretend otherwise.

### 6.5 Seed governance (Brief A §6) — **LOCKED 2026-07-25, user-approved**

| Block | Purpose | Status |
|---|---|---|
| 42, 7 | canaries (floor/shadow) | burned, keep |
| 501–525 | frozen legacy anchor block (v0/teacher/ceiling baselines) | burned for holdout; keep for tracking continuity |
| 1000–8999 | development / exploratory / SPRT lanes | open |
| 20000–29999 | puzzle-suite mining generations (§3.2) | reserved, never for SFT |
| 30000–30099 | **certification holdout** (STR + PUZ held-out runs) | reserved; opened at release time, then retired and replaced |
| d1-selfplay seed range | SFT corpus | burned everywhere |

Enforcement (implemented with this lock): `eval/seed_policy.py` is the single
source of truth for the reserved ranges. `make_sft_data.py` refuses (exit 2)
any SFT source containing a game whose seed falls in a reserved block —
override only via `--allow-reserved-seeds`, which prints a loud banner and is
recorded in the dataset provenance. `run_batch.py` prints a prominent
eval-reserved warning when requested seeds intersect a reserved block (games
there are fine — feeding them to SFT is not). `mine_puzzles.py` tags candidate
seeds with their block. Tests: `eval/test_seed_policy.py`.

---

## 7. Comparison fairness: student vs frontier (Brief A §5)

- **Native-regime primary reporting.** Each model runs under its documented
  `ModelCfg` operating regime (max_tokens, timeouts, fast-tier caps — EXISTS;
  reasoning models need large fast-tier budgets or they emit empty replies).
  The report prints each model's regime next to its scores. **No uniform token
  cap is imposed** — uniform caps structurally favor non-reasoning
  architectures (Brief A §5).
- **Matched-budget secondary lane (optional):** puzzle suite only, all models
  capped to the student's completion budget — reported as a separate labeled
  column, never merged with native-regime numbers.
- **Compute/cost normalization:** every scorecard row carries $/game (measured:
  $0.06 deepseek … $0.37 gemini-flash; student $0), tokens/decision
  (prompt+completion, EXISTS in rows), median+p95 latency/decision, and
  wall-clock/game. Strength-per-dollar and strength-per-second are first-class
  columns — an 8B model at $0 and 3–8 s/decision losing 40:60 to a $0.37/game
  frontier model is a *headline result*, and the scorecard must make that
  legible rather than burying it under raw win rate.
- **Timeout policy:** per-model ModelCfg timeouts; a timeout → deterministic
  legal fallback (house rule, never Forge-AI strategy) and the row is excluded
  from decision-quality metrics but *included* in fallback-rate, which gates at
  10% (aggregate.py EXISTS). A model that can't answer inside its own declared
  regime fails the gate — that is a result, not a confound to hide.
- **Sampling regime:** student runs greedy (temp 0) as served; frontier models
  run their default API sampling. Documented per model in the scorecard;
  resampled-puzzle metrics (k=4) absorb sampling variance into the CI.
- **Identical interface:** all models see the same prompt/menu contract via the
  same bridges; the scorecard notes the residual confound (§9: menu-format
  aptitude ≠ Magic aptitude).

---

## 8. Phased roadmap

Reuse first: `run_batch`, `aggregate`, `defect_analyzer`, `replay_match`,
`process_metrics`, `shadow_canary`, ModelCfg. New components are listed with
estimates; everything stdlib-only except the Java fork harness.

### Phase 0 — free, local, now (no games, no GPU — safe alongside the live LoRA run)

| Work item | Reuses | New code | Est. |
|---|---|---|---|
| 0.1 Frontier process profile: run `process_metrics.py` + `defect_analyzer.py` over `runs/tournament-1` per model → DQ-process scorecard for all 8 frontier models from the existing 575 decision rows | EXISTS tools | report script only | hours |
| 0.2 Plan-adherence extractors: 16 milestone specs + `plan_adherence.py`; validate on tournament-1 + stage0-calibration logs | process_metrics patterns | ~300 lines + 16 specs + tests | 2–3 days |
| 0.3 `eval/sprt.py` (binomial SPRT + pentanomial GSPRT + LOS) + `seed_policy.py`; unit tests | aggregate.py | ~200 lines | 1 day |
| 0.4 `mine_puzzles.py` candidate miner, tuned on main-checkout `runs/d1-selfplay` (text scan, CPU) — candidates only, no verification yet | make_sft_data loaders | ~350 lines | 2 days |
| 0.5 Schemas committed (`eval/schemas/*.json`, §10) | — | done with this doc | — |
| 0.6 **After the LoRA run finishes** (still free/local): replay the 575 tournament-1 frontier prompts against the local student (`replay_match.py`, localhost) → student-vs-each-frontier agreement on frontier-visited states; plus student DQ-shadow via a small SHADOW_AI=1 game batch | replay_match, SHADOW_AI | config only | hours |

Phase-0 reuse validity limits (stated wherever quoted): tournament-1 is
`discipline=exploratory` (plans on, mulligan heuristic on, non-mirror decks,
n=8 games) — its numbers are *profiles*, never citable strength claims; the
575-row replay measures agreement on frontier-visited states, not strength.

### Phase 1 — AWS g5 (student-scale compute; when quota lands)

| Work item | Depends on | Est. |
|---|---|---|
| 1.1 `LlmSpikeFork.java` fork/rollout harness + replay-determinism gate | 0.4 candidates | **the big one: ~700–900 lines Java, 1–2 weeks incl. validation canary** |
| 1.2 Generate mining block (seeds 20000+, ~2,000 ai-vs-ai games) and verify → golden suite v1 (~1,000 positions, 70/30 split) | 1.1 | overnight CPU + 1–2 days integration |
| 1.3 `puzzle_run.py` + student PUZ/PUNT-pass^k (vLLM student on g5; k=8 resamples free) | 1.2 | ~250 lines, 1 day |
| 1.4 STR at scale: SPRT lanes + 725-game fine-resolution anchor runs (student versions) | sprt.py | config; ~$2–4 spot per certification |
| 1.5 `blunder_flags.py` + `blunder_report.py`: hybrid flagging + rollout adjudication over deterministic-controller corpora; text-lane punt counts for LLM games | 1.1 | ~450 lines total, 3–4 days |
| 1.6 (v2, optional) `state_export` on flagged rows + fork-from-snapshot → full PUNT for LLM/student games | 1.5 | Java + schema, ~1 week; defer until v1 metrics prove insufficient |

### Phase 2 — paid frontier calls (**every row requires explicit user consent + `OPENROUTER_ALLOW_EGRESS=1`; nothing here runs by default**)

**Decided roster (user decision, 2026-07-25):** the same 8 models as
tournament-1 — opus-4.8, claude-fable-5, gpt-5, grok-4.5, gemini-3.5-flash,
qwen3.7-max, kimi-k3, deepseek-v4-pro. Recording this decision authorizes
nothing: each Phase-2 run still requires explicit per-run user consent and the
egress opt-in at launch time.

| Work item | Est. cost (per model / total for 8) | Notes |
|---|---|---|
| 2.1 Frontier PUZ: golden suite, k=2 resamples ≈ 2,000 calls × ~1.5k tok | ~$1–6 / ~$10–50 | cheapest frontier signal per bit; egress = full reconstructed states |
| 2.2 Frontier STR: 25 pairs vs Forge-AI anchor on certification seeds, native ModelCfg regime | $3–19 / ~$30–150 (50 games × $0.06–$0.37) | CI ≈ ±13–19 pts at this n — reported as such, no fine claims |
| 2.3 DQ-judge rationale grading (student only), if wanted | ~$5–20 | §4.4 constraints; needs the 50-decision human calibration set first |

Consent/cost flag summary: **[PAID/EGRESS]** components are exactly 2.1–2.3,
any `openrouter:` seat in §2, and any non-localhost `replay_match`/`puzzle_run`
endpoint. Everything else in this design is $0 and local/AWS.

---

## 9. What this eval still cannot tell you

1. **Absolute strength.** Every strength number is anchored to Forge's heuristic
   AI, whose human-relative strength is unknown (probably weak amateur). "Beats
   the anchor 60%" does not place the model on any human ladder.
2. **Combo-line quality.** The rollout continuation policy is the same AI that
   wins 0% with Ant Storm — WP answer keys systematically undervalue combo
   lines. Combo-deck graded positions are flagged, not fixed.
3. **Hidden-information judgment.** Single-deal rollouts can't score information-
   set-correct play (bluff-Daze, playing around unseen cards). A "punt" that was
   correct against the opponent's *range* is miscounted; AIVAT-grade correction
   (Brief B §6) is future work.
4. **Cross-turn intent.** PLAN measures observable milestone pursuit, not
   whether the model *meant* it; DQ scores decisions locally, so a coherent
   losing line vs an incoherent winning one is only partially separated.
5. **Interface aptitude vs Magic aptitude.** All models play through this
   harness's menu/text contract; a model bad at `CHOICE:` discipline scores low
   regardless of underlying Magic knowledge (DQ-process makes this visible but
   can't remove it).
6. **Metagame breadth.** 16 decks, Legacy, 1v1, best-of-1 headless: nothing
   about sideboarding, unfamiliar archetypes outside the pool, or multiplayer.
7. **Frontier fine resolution.** At consented budgets, frontier CIs are wide
   (±13–19 pts); rankings between frontier models closer than that are not
   claims this system can make.
8. **Teacher-relative metrics stay teacher-capped.** DQ-shadow and replay-match
   agreement remain bounded by Forge-AI quality; only PUZ, rollout-WPL, and STR
   can show the student exceeding its teacher — if those three move while
   DQ-shadow *falls*, that is the surpassing-the-teacher signature, and the
   scorecard template says so explicitly.

---

## 10. Artifact schemas (stubs; jsonl, additive-only)

Machine-readable copies: `eval/schemas/puzzle-position.v1.schema.json`,
`eval/schemas/blunder-report.v1.schema.json`. Milestone spec schema inline below.
Note: pipe-separated strings in the examples (e.g. `"split": "open | holdout"`)
enumerate the allowed values for illustration — a real instance carries exactly
one of them, per the schema enums; do not paste the examples verbatim.

### 10.1 Puzzle position (`suite-<name>/positions.jsonl`, one line per position)

```json
{
  "schema": "puzzle-position.v1",
  "id": "sha256-of-(engine_sha,seed,swap,game_file,decision_index,category)[:16] — unique per line (game_file salted in: seeds are reused across match games, and llm games are non-deterministic, so (engine_sha,seed,swap) alone does not identify a game instance); the shared replay address of same-decision candidates is the source block",
  "category": "lethal-now | save-vs-lethal | forced-block | free-win | mulligan | graded-midgame",
  "source": {
    "engine_sha": "c1c64a5...", "overlay_sha": "6c3b0bd...",
    "deck_a": "UR Delver", "deck_b": "Burn",
    "controllers": "ai/ai", "seed": 20017, "swap": false,
    "decision_index": 41, "game_file": "game-20017-....jsonl", "turn": 6,
    "decision_type": "play", "seat": "UR Delver"
  },
  "prompt": "<verbatim user message the seat saw>",
  "options": ["...menu strings..."],
  "n_options": 6,
  "answer_key": {
    "verified_by": "exhaustive | rollout",
    "rollout": {"n_per_option": 16, "continuation": "ai/ai", "seed_fn": "f(seed,i,j)"},
    "option_scores": [10, 0, 4, 10, 0, 2],
    "option_wp": [0.81, 0.22, 0.41, 0.79, 0.20, 0.31],
    "option_wp_se": [0.09, 0.10, 0.12, 0.10, 0.10, 0.11],
    "caveats": ["rollout_policy_caveat"]
  },
  "split": "open | holdout",
  "excluded_from_sft_overlap": true
}
```

### 10.2 Blunder report (`blunder-<run>/decisions.jsonl`, one line per adjudicated decision)

```json
{
  "schema": "blunder-report.v1",
  "run_dir": "runs/...", "game_file": "game-...jsonl", "decision_index": 57,
  "seat": "UR Delver", "model": "local:qwen-mtg-stage2-v1", "turn": 8,
  "decision_type": "attackers", "n_options": 5, "choice": 2,
  "flag_lane": "shadow-disagree | text-flag | audit-sample",
  "flags": ["lethal_available_passed"],
  "shadow_choice": 0,
  "adjudication": {
    "method": "rollout | exhaustive | textlane-verified | unadjudicable",
    "n_per_option": 16, "wp_chosen": 0.31, "wp_best": 0.74, "wpl": 0.43,
    "wpl_se": 0.11, "class": "good | inaccuracy | mistake | blunder",
    "caveats": ["single_deal_hidden_info"]
  }
}
```

Aggregate block (one `t:"summary"` line per report): `dq_wpl_mean`, `punt_rate`
(+ per-lane), `punt_rate_textlane`, `per_game_worst_p95`, all with
`{est, se, ci95, n_decisions, n_games}`.

### 10.3 Milestone spec (`eval/plans/<deck>.milestones.json`)

```json
{
  "schema": "milestones.v1", "deck": "ur-delver", "plan_file": "eval/decks/ur-delver.plan",
  "milestones": [
    {"id": "threat_by_2", "kind": "deploy_by_turn", "turn_max": 2,
     "chosen_option_matches": ["Delver of Secrets", "Dragon's Rage Channeler", "Ragavan"]},
    {"id": "protect_held", "kind": "protection_usage",
     "requires_state": "own_threat_on_battlefield",
     "chosen_option_matches": ["Daze", "Force of Will", "Spell Pierce"]},
    {"id": "burn_split", "kind": "target_split", "spells": ["Lightning Bolt", "Chain Lightning"],
     "report": "face_vs_creature_ratio"}
  ],
  "fixtures": ["runs/fixtures/ur-delver-game-1.jsonl", "..."]
}
```

---

## 11. Scorecard (the single output everything feeds)

One markdown + json scorecard per certified model/version
(`runs/certification-<tag>/scorecard.{md,json}`): the five axes as rows of §1's
table with real numbers, per-regime documentation (§7), cost columns, the §9
caveats footer, and the provenance block (engine/overlay/deck/suite shas, seed
blocks, discipline). A version-over-version student chart (STR-anchor + PUZ +
PUNT-rate on the frozen tracking block) is the project's progress headline.
Generator: `eval/scorecard.py` (~200 lines, Phase 1) — reads only the json
outputs of the tools above; contains no metric logic of its own.
