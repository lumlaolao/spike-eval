# mtg-forge-llm-agent — LLM agents playing Magic: The Gathering on the Forge engine

Claude models pilot full Legacy games inside the real Forge rules engine. A custom
`PlayerControllerLLM` routes ~15 decision types (plays, targets, combat, discards,
card-naming, mulligans...) to persistent `claude -p` CLI bridges; the engine validates
every option, so illegal moves are impossible by construction.

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
  `python3 eval/test_aggregate.py` must pass).
- `README.md` — full env-var table and the hard-won-lessons list. Read the lessons
  before touching the priority loop or targeting code.

## Validation ladder (cheapest first — always start at the bottom)

1. **Harness mechanics** (seeding, JSONL, arms, engine plumbing):
   `GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor bash run-llm-headless.sh`
   — zero model calls, ~12s per full game, bit-reproducible. Seed 42 floor mirror
   is the regression canary: identity A wins after 106 turns, every time.
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
- **Parallel games:** safe at `--jobs 4-6` for cheap/floor games, **1** for bare
  Claude-CLI specs; see `eval/CONCURRENCY.md`. **Never rebuild while games run** —
  a mid-batch `mvn compile` swaps `target/classes` under live JVMs and regenerating
  `/tmp/forge_cp.txt` has no lock. No containers; a build lock + worktree is enough.
- Wall-clock-capped games grant in-flight asks a grace window sized to the slowest
  configured model timeout before JVM exit (else the ask's jsonl row is orphaned —
  that was a real silent-drop). `run_batch.py`'s backstop must stay wider than
  cap + that grace.
- `secrets.env` (git-ignored, repo root; template `secrets.env.example`) holds
  `OPENROUTER_API_KEY` + the egress opt-in; eval drivers auto-load it.

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
