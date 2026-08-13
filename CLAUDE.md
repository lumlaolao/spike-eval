# mtg-forge-llm-agent — LLM agents playing Magic: The Gathering on the Forge engine

Claude models pilot full Legacy games inside the real Forge rules engine. A custom
`PlayerControllerLLM` routes ~15 decision types (plays, targets, combat, discards,
card-naming, mulligans...) to persistent `claude -p` CLI bridges; the engine validates
every option, so illegal moves are impossible by construction.

## CURRENT FOCUS (2026-07-26) — the eval IS the deliverable

**The goal is a general MTG-play benchmark, not a stronger student.** Training work
serves the eval, not the other way round. A change that does not make the benchmark
more trustworthy or broader is out of scope right now.

Three headline scores, each reported with its confidence interval AND the trivial
baseline it beats (see "the 88% lesson" below — a score without its floor is not a
score):

1. **STR-AI** — win rate vs Forge's built-in heuristic AI, on a **fixed, published
   seed set**, across **many deck archetypes**. Breadth is the point: burn-vs-delver
   alone measures one matchup's quirks, not play skill. Target is thousands of games
   over the full 16-deck pool, reported per-matchup and pooled.
2. **STR-HELDOUT** — the same measurement on a seed band mechanically blocked from
   every training corpus (`eval/seed_registry.json` + `eval/seed_policy.py`,
   enforced by `run_batch.py`). The gap between STR-AI and STR-HELDOUT is
   the memorization estimate.
3. **PUZ** — the golden puzzle suite: per-puzzle *solved / not solved*, *count
   solved*, and *time-to-solve*. Teacher-independent, so it survives the teacher
   being weak (see EVAL-DESIGN.md §3).
4. **PZL** — Forge's own 300 hand-authored `.pzl` positions (`eval/suites/pzl-v1`).
   Distinct from PUZ in what adjudicates: PUZ scores against a mined answer key over
   an option menu, PZL has **no key at all**. `Puzzle.addGoalEnforcement` installs a
   hidden "Puzzle Goal" card that makes the solver LOSE at their cleanup step on the
   turn limit unless the goal is met, so the rules decide and nothing of ours is in
   the loop. Run one with `PUZZLE=<id> bash run-llm-headless.sh`, a whole suite with
   `python3 eval/run_pzl.py --arm <floor|random|ai-log|llm> --out runs/pzl/<arm>`,
   then fold the results in with `python3 eval/pzl_floors.py`.
   **Floors are MEASURED (2026-08-12, executed over all 300):** do-nothing 0.0070,
   random 0.0110, Forge AI 0.0287. Quote **0.0287** as the bar — the strongest arm that
   does no LLM reasoning. Near-zero trivial baseline with ~3 points of headroom is the
   shape this axis was chosen for. No model has been scored yet.
   **9 of 300 rows are unscoreable at the pin** (`scoreability` block in the manifest):
   3 crash Forge's own attachment handling on load, and 6 have a goal trigger whose
   `TurnCount$` can never equal the turn counter, because a `.pzl` [state] may set a
   STARTING turn that `addGoalEnforcement` never offsets its limit by — the goal card
   installs, looks right, and never fires, so the solver gets unlimited turns. Both are
   upstream Forge behaviours, and both were found by RUNNING the suite after a review
   pass had cleared the turn arithmetic on three puzzles that all start at turn 1.
   **Deck-out taint is the axis's known confound:** puzzle libraries are authored
   near-empty (275 of 300 rows give a player fewer cards than the turn limit needs;
   8 multi-turn rows give the opponent zero), so a player can lose to `Milled` for
   reasons unrelated to the line — handing a do-nothing solver a free win, or denying
   a correct one. The runner records each side's `GameLossReason`; tainted rows are
   excluded from the solve rate's numerator AND denominator, never silently pooled.

`eval/EVAL-DESIGN.md` is the full design (five axes; these three are the headline).
`eval/suites/*.json` is how a suite gets frozen; `eval/build_report.py` emits the
scorecard.

### The 88% lesson (why every metric now reports its floor)

Replay decision-match against the Forge-AI teacher read **88.1%** while the same
student won **10%** of games. Both numbers were correct. The teacher declines to act
on ~83% of priority windows (`choice=-1`), `canonical_target` encodes that decline as
the last menu option (`PASS (do nothing)`), so **78.2% of scoreable rows have PASS as
the answer**. A model that only ever says PASS scores 78.2%. The student's 88.1% was
**+9.9 points over doing nothing** — and its errors concentrated in the minority class
(all 20 recorded mismatches were rows where the teacher acted), which is exactly where
games are decided.

Rules this earned, and they apply to every axis:
- **Report the majority-class baseline next to any accuracy.** `replay_match.py` now
  emits `majority_class_baseline` and `margin_over_baseline`; quoting a match rate
  without them is not allowed.
- **Prefer metrics whose trivial baseline is near zero** (win rate, puzzles solved)
  over accuracy on an unbalanced label set.
- **Class-balance the training corpus deliberately.** `datasets/stage2-r2-teacher-v0`
  is 78.3% PASS targets; trained as-is it teaches inaction. Use `--cap-per-source` /
  stratified caps, and check `target_synth` shares before training.

## The one rule that matters

**`forge-overlay/` is the source of truth.** Never edit files inside `~/code/forge`
directly — they get overwritten. Workflow for every change:

```bash
# 1. edit forge-overlay/.../forge/spike/LlmSpikeGui.java (or LlmSpikeHeadless.java)
bash install.sh                                   # 2. copy overlay into $FORGE_HOME
cd ~/code/forge && mvn -q -pl forge-gui-desktop -am compile -o   # 3. compile (offline is fine)
```

The Forge checkout is pinned: `FORGE_COMMIT` records the engine version everything was
validated against (`install.sh` warns on drift). Don't bump it casually — eval results
are only comparable within one engine version.

## Key files

- `forge-overlay/.../spike/LlmSpikeGui.java` — everything: `PlayerControllerLLM` (the
  router), `LLMBridge` interface + `ClaudeBridge` / `OpenRouterBridge` backends,
  `WindowRecorder` (in-JVM mp4 capture), `EvalLog` (JSONL), seat construction, deck plans.
  Model specs are `backend:model` strings, e.g. `haiku` (Claude CLI) or
  `openrouter:anthropic/claude-3.5-sonnet` (OpenRouter API).
  **Egress boundary:** any `openrouter:` spec sends the full game snapshot (board,
  hands, deck plan) to openrouter.ai on every call. Holding an API key is not
  consent — the factory refuses to start unless `OPENROUTER_ALLOW_EGRESS=1` is
  set explicitly. Never set that flag on the user's behalf.
- `forge-overlay/.../spike/LlmSpikeHeadless.java` — same match, no GUI; emits the
  eval game-summary JSONL line. Use for all eval/batch work.
- `run-llm-spike.sh` / `run-llm-headless.sh` — launchers (installed into the forge repo root).
- `eval/` — batch driver + statistics (`run_batch.py`, `aggregate.py`; stdlib-only,
  Python 3.9 — no match statements, no `X | Y` runtime unions, no pip).
- **Eval v1.0 modules** (2026-07, see `eval/EVAL-V1.md` for the full walkthrough):
  `eval/prompt_contract.py` (frozen prompt/reply contract, `contract_sha`),
  `eval/puzzle_run.py` (PUZ axis; backends `offline|http|openrouter|bedrock` —
  `openrouter`/remote-`http` now send Bearer auth from `OPENROUTER_API_KEY`/`secrets.env`;
  only `offline` has end-to-end evidence, no live model has ever been scored),
  `eval/bedrock.py` (AWS Bedrock backend, hand-rolled stdlib SigV4, same
  `OPENROUTER_ALLOW_EGRESS=1` gate; **never live-called** — signing proven only
  against AWS known-answer vectors. Per-model `BEDROCK_MODEL_CFG` table mirrors
  `ModelCfg`: thinking budget is carved OUT of `max_tokens` and rows leaving
  <1024 answer tokens are rejected, so hidden-reasoning-eats-the-cap →
  empty-reply cannot recur; override via `BEDROCK_MODEL_CFG` env JSON),
  `eval/verify_keys.py` (candidate→answer-key promotion),
  `eval/plan_adherence.py` + `eval/plans/*.milestones.json` (PLAN axis),
  `eval/blunder_flags.py` + `eval/blunder_report.py` (DQ/PUNT axes),
  `eval/build_report.py` (five-axis scorecard, `scorecard.json`).
  Java rollout support: `forge-overlay/.../LlmSpikeFork.java` — **now produces real
  win probabilities** (2026-07-28). It did not before, and the reason is the
  cautionary tale below.
  **The golden suite is frozen: `eval/suites/puz-v1.jsonl` + `puz-v1.json`
  (2026-07-29) — 25 distinct positions, every trivial floor at or below
  chance.** 103 verified keys across `runs/overnight-1` + `runs/overnight-2`
  (up from 3 repo-wide) reduce to 25 quotable positions: `max_constant_index
  0.28`, `random 0.29`, `always_pass 0.20`, `majority_class 0.32`, oracle
  1.00. The manifest carries the sha256, the exact build command, and the
  known limits — read `known_limits` before quoting anything. **No live model
  has ever been scored against it**; only the offline floor policies have run.
  Rebuild it with the canonical three-step pipeline, in this order:

  ```bash
  python3 eval/shuffle_suite.py RAW.jsonl ... --out shuffled.jsonl
  python3 eval/select_suite.py shuffled.jsonl --out filtered.jsonl \
      --min-options 3 --require-single-answer --filters-only
  python3 eval/shuffle_suite.py filtered.jsonl --out suite.jsonl --balance-salts 400
  ```

  Order matters and each step fixes a floor the others cannot:
  `--min-options 3` removes 2-option positions (one movable slot — no
  permutation can move the answer, `always_first` ~0.92 forever);
  `--require-single-answer` removes positions where the CI-overlap tie rule
  marked several options 10 (no single best play to find); `--filters-only`
  stops selection from discarding for SLOT balance, because `--balance-salts`
  fixes that for free — but it deliberately still balances the PASS floor,
  which shuffling cannot touch (PASS is pinned last by `pass_option_index`).
  **Sample the rollout lane RANDOMLY across all option counts.** The 3–4-band
  guidance previously here optimized the wrong quantity. Two metrics pull
  opposite ways: the fraction of VERIFIED KEYS that survive the suite filters
  (favours n=3–4 — that is what was measured) versus the fraction of ATTEMPTED
  POSITIONS that verify at all (favours random sampling). Measured once each:
  random sampling **32/300 = 0.107** in 4h01m (`runs/overnight-2`); 3–4-band
  targeting **6/320 = 0.019** raw / 6/252 = 0.024 after excluding 68
  infrastructure failures, in 7h29m (`runs/overnight-3`). n=1 round each —
  treat as directional, not a law. Per-option-count verification rates:
  `n=2 0.139 | n=3 0.055 | n=4 0.073 | n>=5 ~0.05`. n=2 has the best
  verification rate and ~0% suite survival — if re-admitted, score it against
  an explicit 0.50 floor, never silently.
  **Mine for INFORMATIVENESS, not yield.** Measured over the 7 models in
  `runs/puz/tournament-8`, 9 of puz-v1's 25 positions solve at 1.000 and 6
  more at 0.964; only 9 fall in the informative band `0.05 < solve < 0.95`,
  and mean model pass_1 is 0.811. Paired significance
  (`puz_index.py leaderboard` runs an exact McNemar/sign test over shared
  positions): only 3 of 21 model pairs separate at p<0.05 on mean solve
  rates (1 of 21 on binary pass@1) — isolated pairs, never the ordering.
  Quote only the pairs the tool certifies. More positions from the same
  distribution arrive ~64% free — the suite's bottleneck is DIFFICULTY,
  not size.

  21 verified MULLIGAN keys are **excluded**, and "no numbered menu to
  permute" is the mechanical reason, not the real one: mulligan is BINARY, so
  its floor is 0.50 at best — above the near-zero-floor rule this suite rests
  on. Measured, it is worse: 17 KEEP / 4 MULLIGAN, so "always KEEP" scores
  **0.81**, and the mined prompt itself says *"Keeping a workable hand almost
  always beats shrinking it"* — it hands the model the prior. Never fold this
  axis into PUZ; report it separately if at all.

  **Quote `max_constant_index_baseline`, never `always_first_baseline`.**
  always_first is one constant policy of many, and balancing it alone just
  moves the skew one slot right: a shuffled 17-position suite read
  always_first 0.29 — apparently chance — while 8 of 17 answers sat in slot 2,
  so "always answer 2" scored 0.47. That is the 88% lesson recurring. Related:
  the duplicate signature is now SORTED in both `suite_composition` and
  `select_suite`; while it was order-sensitive, re-shuffling a "deduped" suite
  dropped n_distinct 23 → 21, i.e. duplicates had been counted all along.
  All keys carry `unprobed_replay_address`
  (minted with `--driver none`, so the replay address was never engine-probed).
  DQ is unmeasured (all corpora textlane-only — never add
  `punt.rate_textlane` to the rollout punt rate); PLAN resolves a deck only on
  `runs/stage0-calibration`.
- `README.md` — full env-var table and the hard-won-lessons list. Read the lessons
  before touching the priority loop or targeting code.

## Tooling entry points (2026-07-31)

The repo-root **`Makefile` is the single implementation** of the ladder and
pipelines — `make help` lists targets (`test`, `jtest`, `canary`, `ladder`,
`preflight`, `mine`, `str-floors`, `scorecard`, `leaderboard`). Slash commands
(`/ladder`, `/mine-round`, `/handoff`, `/verify-diff` in `.claude/commands/`)
and docs must WRAP make targets, never re-implement them — two copies of one
procedure is how prose drift happens. Subagent roles live in
`.claude/agents/` (`run-analyst`, `floor-checker`, `suite-builder`).
Recurring run scripts are VERSIONED in `eval/pipelines/` + `cloud/bench/` —
never leave a load-bearing script in a job tmp dir again.
`eval/preflight.py` gates every game-launching pipeline (deck names,
classpath, engine pin, disk); `eval/test_docs_claims.py` mechanically checks
doc claims that have drifted before. `secrets.env` lives at the MAIN repo
root and worktrees cannot see it — source it by absolute path:
`source /Users/lumlumlam/code/mtg-forge-llm-agent/secrets.env`.

## Validation ladder (cheapest first — always start at the bottom)

0. **Python eval suite** (free, seconds, no JVM): every `eval/test_*.py` must pass —
   `for f in eval/test_*.py; do python3 "$f" >/dev/null || echo "FAIL $f"; done`
   (30 files, incl. `test_prompt_contract` / `test_puzzle_run` /
   `test_pzl_corpus` / `test_pzl_verdicts` / `test_run_pzl` / `test_pzl_floors` /
   `test_verify_keys` / `test_plan_adherence` / `test_blunder_flags` /
   `test_blunder_report` / `test_build_report` / `test_bedrock`). Never weaken a
   test to pass it.
0b. **Java unit tests** (free, ~1 min, no game): `make jtest` (canonical; it
   runs `-Dtest='LlmSpikeForkTest+LlmSpikeTest+LlmSpikePuzzleTest'`) — 116+ tests: the pure logic
   every WP rests on (seed mix, forceable-type gate, forced-index mapping,
   tally, determinize parsing) plus the menu/label pins (numberedPlayMenu
   byte-for-byte, saLabel truncation, disambiguate). **Run it: LlmSpikeForkTest
   was absent from this ladder until
   2026-07-28 and had been failing the whole time** on an assertion that was
   arithmetically impossible (`mix64(0) != 0`; zero is a genuine fixed point of
   the SplitMix64 finalizer). A test nobody runs is not a test.
1. **Harness mechanics** (seeding, JSONL, arms, engine plumbing):
   `GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor bash run-llm-headless.sh`
   — zero model calls, ~12s per full game, bit-reproducible. Seed 42 floor mirror
   is the regression canary: identity A wins after 108 turns, every time.
   (Was 106 when first recorded; the harness commits since moved it to 108. If
   you change an auto/floor path, re-pin this number — a stale canary is a
   canary that can't fail.)
   **Floor proves less than it looks like.** Floor short-circuits every play at
   `play(no-budget)->PASS` before the play menu is built, so it never exercises
   the play/attack/block decision paths. For anything touching those, use the
   **random** arm (`CONTROLLER_A=random CONTROLLER_B=random`) — also zero-model
   and deterministic, but it has budget (`smartOk()` is false only for
   `mode=="floor"`) and reaches the full menu.
1b. **D4 shadow labeler** (`SHADOW_AI`): `bash eval/shadow_canary.sh` — two gates,
   both required. Gate 1 (on/off) catches shadow perturbing the game; gate 2
   (no-op vs committed HEAD) catches defects that fire on BOTH arms, which gate 1
   is structurally blind to. A real BLOCKER hid there: an SVar restore placed
   before the `!SHADOW` guard mutated SpellAbilities in every config, and gate 1
   stayed green because both arms were equally broken. The script asserts the
   intended build was actually installed (SHADOW ref count 0 vs 16) — without
   that, an unnoticed build-swap failure compares a build against itself and
   reports PASS. **General rule this cycle earned: before trusting a green gate,
   say what would have made it fail. If you can't, it isn't evidence.**
2. **LLM code paths** (routing, parsing, prompts): `SMART_MODEL=haiku FAST_MODEL=haiku`
   — never test with opus. For OpenRouter-backed validation, run one exploratory
   game with a cheap model first, e.g.
   `GAME_SEED=42 SMART_MODEL=openrouter:meta-llama/llama-3.1-8b-instruct FAST_MODEL=openrouter:meta-llama/llama-3.1-8b-instruct OPENROUTER_ALLOW_EGRESS=1 bash run-llm-headless.sh`
   (requires `OPENROUTER_API_KEY`; the `OPENROUTER_ALLOW_EGRESS=1` opt-in is
   mandatory — see the egress boundary note above).
3. **Opus / frontier API models** only for user-requested showcases/recordings
   (a full game ≈ 35-45 min).

Watchdog every GUI validation run — `( sleep N; pkill -INT -f LlmSpikeGui ) &` — and
`pkill -f 'sleep [0-9]'` before each relaunch (stale watchdogs kill fresh games).
SIGINT (not SIGKILL) so the recorder's shutdown hook finalizes the mp4.

## Engine gotchas (violating these cost us days)

- **`ThreadUtil.isGameThread()` is a STRING PREFIX ON THE THREAD NAME** —
  literally `Thread.currentThread().getName().startsWith("Game")`
  (`forge-core/.../ThreadUtil.java:45`). `GameAction.invoke()` runs its runnable
  inline on a "game thread" but posts it to a background pool otherwise, and
  `GameState.applyToGame` (how a puzzle's board is installed) goes through it. A
  worker named `forge-puzzle` therefore applied the entire puzzle position
  ASYNCHRONOUSLY: the start-game hook returned before any of it existed, the goal
  card check read an empty command zone, and turn 1 began on a blank table — the
  do-nothing arm "won" PS_THB1b because the opponent decked out of an empty
  library. Name any thread that drives a game `Game...`. Note `LlmSpikeHeadless`'s
  `forge-game` thread does NOT match either; it gets away with it only because a
  normal match never calls `invoke()` during setup.
- **`Game.getPlayers()` SHRINKS when a player loses.** It returns `ingamePlayers`
  ("the players who are still fighting to win", `Game.java:400`), and
  `Game.onPlayerLost` calls `ingamePlayers.remove(p)` (`Game.java:998`) from
  `checkGameOverCondition`, *before* `setGameOver`. So after a 2-player game ends,
  `getPlayers()` holds ONE element: index 0 is the winner and index 1 is out of
  bounds. Reading per-seat outcomes off it reported "no deck-outs anywhere" on runs
  whose own log said otherwise. Post-game, index `getRegisteredPlayers()`
  (`allPlayers`, append-only, registration order, `Game.java:347`).
- **A mid-game reseed does not re-deal the library — rollouts need explicit
  determinization.** `LlmSpikeFork` reseeded `MyRandom` at the fork point on the
  documented assumption that "j-variance enters through direct MyRandom consumers
  (Forge-AI evaluators via `percentTrue`)". Measured, that is false: across 6
  positions and 18 option-arms, at every `n`, every arm returned exactly `0/n` or
  `n/n`. All `n` replays played the identical line, so `n=16` bought `n=1` of
  information and no answer key could ever be minted. The library order was fixed
  at game start and no amount of reseeding moves it. Fix:
  `FORK_ROLLOUT_DETERMINIZE=library` (the default) re-deals every library at the
  fork via `Zone.shuffle()` — trigger-free, so no ability observes it, and only a
  hidden zone's ORDER changes. **Generalisable lesson:** a rollout that never
  returns an intermediate value is not a lopsided position, it is a broken
  continuation. Check the *distribution*, not just that rows appeared.
- **Pass priority with `null`, never an empty list** from `chooseSpellAbilityToPlay` —
  empty list spins the 999-iteration "AI looped too much" loop.
- **The AI cast path does no targeting** for ordinary spells: set engine-validated
  targets on the chosen SpellAbility *before* returning it (`ensureTargetsSet`), or
  the cast fizzles ("Couldn't add to stack, failed to target").
- **Never fall back to Forge's AI for strategy.** Failure branches take deterministic
  legal options. `[TALLY] ... ai=0` in the log is the invariant; every `[FALLBACK->AI]`
  is a bug to investigate.
- GUI matches are best-of-3 — a log with multiple mulligan clusters is one *match*,
  several *games*.
- **Games launch with `cwd=$FORGE_HOME`**, so a repo-relative deck path
  (`eval/decks/x.txt`) resolves against `~/code/forge` inside the JVM →
  `NoSuchFileException` in `loadDeck` kills every game in the batch, surfacing as
  one macOS crash-report dialog PER PARALLEL JOB with the cwd mismatch nowhere in
  the message. `run_batch.py resolve_deck_paths` now absolutises + existence-checks
  deck paths in BOTH cited and exploratory modes; always pass absolute paths to
  anything that execs from FORGE_HOME.

## The improvement loop (added 2026-07-21, branch harness-fixes)

`runs/tournament-1/HARNESS-ANALYSIS.md` is the forensic baseline: 13 confirmed defect
clusters, all fixed on this branch. The loop that keeps them fixed:

```bash
python3 eval/improve_loop.py --controller llm --games 2   # play (floor mode = free)
# defect_analyzer.py runs automatically; read metrics.json
python3 eval/model_sweep.py                               # per-model certification
```

- **`eval/defect_analyzer.py` is the merge gate.** Hard invariants: `ai_tier_count=0`
  and `silent_drop_total=0` (log-vs-jsonl reconciliation). It exits non-zero on
  violation. Never weaken the analyzer to make a run pass — it is the oracle.
- **Per-model config lives in `ModelCfg`** (LlmSpikeGui.java, `OPENROUTER_MODEL_CFG`
  JSON env override): max_tokens / timeouts / fast-tier caps per model. Reasoning
  models (gpt, deepseek) need big fast-tier budgets — hidden reasoning tokens burn
  the cap and surface as empty replies, not truncated text.
- **jsonl schema is additive-only.** Newer fields: `attempts`, `retry_ms`
  (dead-attempt time; `latency_ms` is the successful attempt only), `finish_reason`,
  `first_failed_reply`. Existing consumers read via `.get()` — keep it that way.
- **Parallel games:** cheap `openrouter:` LLM games **6–10** (RAM-bound, ~1GB
  RSS/JVM; count JVMs across ALL concurrent batches — 7 proven healthy
  2026-07-21), floor **4–8** (CPU-burst-bound), mixed **4–6**, **1** for bare
  Claude-CLI specs; see `eval/CONCURRENCY.md` §7. **Never rebuild while games run** —
  a mid-batch `mvn compile` swaps `target/classes` under live JVMs and regenerating
  `/tmp/forge_cp.txt` has no lock. No containers; a build lock + worktree is enough.
- Wall-clock-capped games grant in-flight asks a grace window sized to the slowest
  configured model timeout before JVM exit (else the ask's jsonl row is orphaned —
  that was a real silent-drop). `run_batch.py`'s backstop must stay wider than
  cap + that grace.
- `secrets.env` (git-ignored, repo root; template `secrets.env.example`) holds
  `OPENROUTER_API_KEY` + the egress opt-in; eval drivers auto-load it.

## Seed governance (locked 2026-07-25 — user-approved)

Reserved eval seed blocks, never usable as SFT/training-data sources:
**501–525** (frozen legacy baselines), **20000–29999** (puzzle-suite mining),
**30000–30099** (certification holdout). `eval/seed_policy.py` is the single
source of truth; `make_sft_data.py` refuses reserved-seed sources (override
`--allow-reserved-seeds` prints a loud banner and is stamped into provenance);
`run_batch.py` warns when requested seeds are eval-reserved. Pick training-data
generation seeds outside these blocks. Full policy: `eval/EVAL-DESIGN.md` §6.5.

## Eval mode

Seeded, baseline-anchored A/B comparisons (see README "Eval mode" + `eval/README.md`):
`GAME_SEED`, `SWAP_SEATS`, `SMART_MODEL_A/B`, `CONTROLLER_A/B` (llm|random|floor|ai),
`EVAL_OUT` (JSONL), confound switches `MULLIGAN_HEURISTIC=0` / `SKIP_HEURISTICS=0` /
`PLAN_A=""`. The JSONL winner field is **identity-keyed** (A = deck-identity A
regardless of seat swap) — don't "fix" that in either language.

## Recording

`RECORD=1` captures the Forge window in-JVM (no macOS screen-recording permission
needed) → H.264 mp4. `RECORD_OUT`, `RECORD_FPS` tune it. Bigger window = crisper video.
30-second social cut: `ffmpeg -i in.mp4 -vf "setpts=PTS*(30/DUR),fps=30" -an out.mp4`.

## Before committing

Non-trivial changes: stage, then dispatch the `verifier` agent from the claude-obsidian
plugin against `git diff --cached` — it has caught a BLOCKER (baseline contamination)
and multiple HIGHs that validation runs missed. Then commit and push to `main`.
