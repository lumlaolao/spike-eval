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
   enforced by `run_batch.py` and CI). The gap between STR-AI and STR-HELDOUT is
   the memorization estimate.
3. **PUZ** — the golden puzzle suite: per-puzzle *solved / not solved*, *count
   solved*, and *time-to-solve*. Teacher-independent, so it survives the teacher
   being weak (see EVAL-DESIGN.md §3).

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
  **The golden suite is started but not a benchmark yet** (2026-07-29): the
  uncapped exhaustive lane minted **24 verified keys over 204 ai-log games**
  (`runs/overnight-1/lethal-positions.jsonl`), up from 3 repo-wide. Quote it as
  **15 DISTINCT positions with a 0.39 random-guess baseline**, never as "24
  puzzles" — 11 are 2-option, so a coin flip scores 39%; `suite_composition` in
  every verify summary reports this. All 24 carry `unprobed_replay_address`
  (minted with `--driver none`, so the replay address was never engine-probed).
  DQ is unmeasured (all corpora textlane-only — never add
  `punt.rate_textlane` to the rollout punt rate); PLAN resolves a deck only on
  `runs/stage0-calibration`.
- `README.md` — full env-var table and the hard-won-lessons list. Read the lessons
  before touching the priority loop or targeting code.

## Validation ladder (cheapest first — always start at the bottom)

0. **Python eval suite** (free, seconds, no JVM): every `eval/test_*.py` must pass —
   `for f in eval/test_*.py; do python3 "$f" >/dev/null || echo "FAIL $f"; done`
   (21 files, incl. `test_prompt_contract` / `test_puzzle_run` /
   `test_verify_keys` / `test_plan_adherence` / `test_blunder_flags` /
   `test_blunder_report` / `test_build_report` / `test_bedrock`). Never weaken a
   test to pass it.
0b. **Java unit tests** (free, ~1 min, no game): `cd ~/code/forge && mvn -q -pl
   forge-gui-desktop -am -o test -Dtest=LlmSpikeForkTest
   -Dsurefire.failIfNoSpecifiedTests=false` — 32 tests, the pure logic every WP
   rests on (seed mix, forceable-type gate, forced-index mapping, tally,
   determinize parsing). **Run it: it was absent from this ladder until
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
