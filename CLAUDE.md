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
  router), `ClaudeBridge` (persistent CLI sessions), `WindowRecorder` (in-JVM mp4
  capture), `EvalLog` (JSONL), seat construction, deck plans.
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
   — never test with opus.
3. **Opus** only for user-requested showcases/recordings (a full game ≈ 35-45 min).

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
