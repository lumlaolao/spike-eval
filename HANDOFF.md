# HANDOFF — Milestone D: LlmSpikeFork (replay-and-rollout harness)

Status: **core replay path (check + enumerate) DONE and validated. Rollout cleanly STUBBED,
not implemented.** Everything compiles (`install.sh` + offline `mvn ... compile`, checkstyle
clean). Baseline canary (seed 42, floor/floor, identity A wins after 108 turns) is unchanged.

## What shipped

- `forge-overlay/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeFork.java` (new, ~480
  lines) — all fork-mode logic: `check`, `enumerate`, `rollout` (stub).
- `forge-overlay/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeHeadless.java` — +9 lines:
  a `FORK_MODE` env check at the top of `main()` that delegates to `LlmSpikeFork.run(mode)` and
  returns. Absent `FORK_MODE`, behavior is byte-identical to before (verified via the seed-42
  canary).
- `forge-overlay/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java` — +24 lines,
  three small additive pieces, all package-private, all no-ops unless `LlmSpikeFork` sets them:
  1. `EvalLog.currentFile()` — returns the `File` the writer just opened (was previously
     write-only), so `LlmSpikeFork` can read its own game's jsonl back for the `check`-mode diff.
  2. `LlmSpikeGui.ForkHook` interface + `static volatile ForkHook FORK_HOOK` + a package-private
     `AtomicInteger FORK_DECISION_SEQ` counter. `emitDecisionRow` — the single funnel both the
     LLM/baseline-arm seat (`evalDec`) and the ai-teacher seat (`PlayerControllerAiLogging.emit`)
     already write through — now increments the counter and, if `FORK_HOOK` is non-null, invokes
     it synchronously right after the row is written, with the 0-based **emission-order** index.
     This is the entire interception mechanism; no other decision-path code was touched.
  3. Nothing else changed. `PlayerControllerLLM.buildSnapshot(...)` was **already**
     package-private static (used by the pre-existing ai-teacher seat) — `LlmSpikeFork` reuses it
     as-is for the enumerate-mode state snapshot, no visibility change needed.

## Integration mechanism (why this shape)

`run-llm-headless.sh` hardcodes `forge.spike.LlmSpikeHeadless` as the main class, so the least
invasive integration is exactly what CLAUDE.md suggested: a tiny env-var branch in
`LlmSpikeHeadless.main` delegating to a new class, keeping the launcher/classpath untouched. All
fork-mode logic lives in the new file; the only edits to existing files are the 9-line delegation
branch and the 24-line hook plumbing described above (both are pure extension points — dead code
paths for every existing run).

The `ForkHook` callback approach was chosen over alternatives considered:
- **Re-deriving decision boundaries externally** (e.g. counting log lines with a wrapper process)
  — rejected: can't distinguish decision types/seats/options without re-parsing jsonl mid-game,
  and can't answer "how do I cleanly stop the game AT this decision."
- **A second full copy of the decision-loop logic in `LlmSpikeFork`** — rejected: `floor`/
  `random`/`ai` controller behavior already lives in `PlayerControllerLLM`; duplicating it would
  both bloat the diff and risk the two copies drifting (exactly the kind of defect
  `defect_analyzer.py` exists to catch, applied here at the source level instead of the log level).
- **The chosen hook**: `emitDecisionRow` is the one place both decision-logging paths already
  converge, decisions there are already fully resolved (deterministic controllers pick `choice`
  *before* calling it), so the hook can observe without altering game behavior — verified by the
  fact `check` mode (which installs no hook) reproduces byte-identical decision streams.

## Validation ladder — results

All steps were engine-only (floor/random/ai controllers, zero model calls), per the milestone's
constraint. Live-JVM check (`pgrep -x java`) was run and clean before every compile/game.

1. **Compile.** `bash install.sh && cd ~/code/forge && mvn -q -pl forge-gui-desktop -am -o
   -DskipTests compile`. First attempt failed checkstyle (`UnusedImportsCheck` on an unused
   `forge.game.player.Player` import in the new file — removed, since the lambda parameter type
   is inferred from `ForkHook` and never needs the explicit import); second attempt succeeded
   cleanly. **What would have failed this:** any syntax/type error, or a checkstyle violation
   (proven — it did fail once, for exactly this reason).

2. **Baseline canary, `FORK_MODE` unset.** `GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor
   bash run-llm-headless.sh` → `outcome: A after 108 turns (AllOpponentsLost) in 19s`. Unchanged
   from the CLAUDE.md-pinned number. **What would have failed this:** any change to
   `emitDecisionRow`'s existing 18 fields/behavior, or the `FORK_HOOK` check adding overhead/side
   effects on the normal path (it's a null-check + no-op when unset, and this run *is* that path).

3. **Replay determinism.** Generated a source game (`GAME_SEED=4242 CONTROLLER_A=random
   CONTROLLER_B=random`, `EVAL_OUT=...`) → 85 decision rows, winner B after 16 turns. Re-ran with
   `FORK_MODE=check FORK_SOURCE=<that file>`, identical seed/controllers → `REPLAY-OK n=85`, exit
   0. **What would have failed this:** any nondeterminism in the floor/random arms (RNG seeding
   drift, engine version drift, a decision path that isn't actually replay-stable) — the
   comparison is a real byte-level diff of two independently-run games, not a tautology.

3b. **ai-vs-ai check.** `CONTROLLER_A=ai CONTROLLER_B=ai FORK_MODE=check` (no `FORK_SOURCE`) →
   the game's jsonl contains **zero** decision rows (only the `t:"game"` summary line) —
   confirmed by direct inspection, so `check` reports `REPLAY-OK n=0`. NOTE (post-verifier fix):
   `n` is now the REAL emission count — `FORK_DECISION_SEQ` increments unconditionally in
   `emitDecisionRow`, and a crashed game exits 5 (`REPLAY-FAILED`), never `REPLAY-OK` — so
   `n=0` is *specific to plain-`ai` seats*, and a floor/random no-source check reports a
   nonzero `n` (validated: seed-4242 random/random → `REPLAY-OK n=85` without a source). The
   original claim that no-source check "is vacuously true" was itself flagged by the verifier
   as a gate that could not fail; that is fixed. The remaining, real scope limit:
   `CONTROLLER_A=ai` maps to plain, untouched `LobbyPlayerAi` (see
   `LlmSpikeGui.seatFor`), which never routes through `PlayerControllerLLM.evalDec` or
   `PlayerControllerAiLogging.emit` — the only two callers of `emitDecisionRow`. **Check mode
   currently validates only the floor/random arms.** `ai`-arm decision-level replay checking
   would require either switching the ai-vs-ai case to `CONTROLLER_A=ai-log` (which *does* log,
   as `ai-teacher` rows — untested here, but should work unmodified since it goes through the
   same `emitDecisionRow` funnel and hook) or accepting that `ai` proper is summary-only.
   Recommended follow-up: update the eval docs/README's `CONTROLLER_A/B` value table to note that
   `ai-log` is the decision-loggable ai arm if this gap needs closing.

4. **Enumerate.** `FORK_MODE=enumerate FORK_DECISION_INDEX=29` on the same seed-4242/random game
   → wrote one `t:"fork_enumerate"` row at index 29 (`type=play seat=Grixis Reanimator
   n_options=3`, options `["PLAY LAND Bloodstained Mire","PLAY LAND Polluted Delta","PASS (do
   nothing)"]`) — **byte-identical** type/seat/n_options/options to the source file's row at the
   same index — plus a full state snapshot (life totals, hand, board, lands). The JVM exited on
   its own (`exit=0`, no watchdog needed) within the same `bash run-llm-headless.sh` invocation
   (well under a minute). Game summary recorded `winner=invalid` (correctly — the game was
   deliberately truncated, not a real outcome). **What would have failed this:** a decision-index
   mismatch (off-by-one, or seat interleaving handled wrong) would show up as different
   type/seat/n_options at index 29 vs. the known-good source row; a broken termination would
   either hang (no exit) or trip the 999-iteration AI-loop guard.

5. **Negative check — refuse.** `FORK_MODE=check` with `GAME_SEED=9999` (deliberately different
   from the source's `4242`) and the same `FORK_SOURCE` → refused *before* running the game:
   `[FORK] REFUSE: ... seed: source=4242 env=9999`, exit **2**. **What would have failed this:**
   if the provenance comparison were missing or broken (e.g. comparing the wrong fields, or
   comparing after the run instead of before), this would have silently proceeded to
   `REPLAY-DRIFT` or even a false `REPLAY-OK` — it didn't.

5a. **Provenance sha dimensions — warn, don't refuse (post-verifier fix).** The six-field
   provenance gate is strict on seed/swap/controllers/decks, but `engine_sha`/`overlay_sha`
   compare env vars (`ENGINE_SHA`/`OVERLAY_SHA`) that only `run_batch.py` stamps — a bare
   `run-llm-headless.sh` launch never sets them. Originally both sides defaulted to
   `"unknown"`, which made those two dimensions a silent no-op (and would have spuriously
   REFUSED a legitimate bare-launch replay of a run_batch-recorded source: real sha vs
   "unknown"). Now (`evaluateProvenance`, unit-tested in `LlmSpikeForkTest`): an
   unset/blank/"unknown" CURRENT sha prints a loud `[FORK] WARNING: ... drift cannot be
   verified` and does NOT refuse; both sides present and differing still refuses (exit 2).
   Follow-up option if full sha coverage is wanted: export `ENGINE_SHA`/`OVERLAY_SHA` in the
   fork launch environment (mirroring run_batch's stamping) so the strict path applies.

5b. **Negative check — drift (extra, not in the original ladder but worth having).** Took the
   seed-4242 source file, corrupted decision row 29's `n_options` from 3 to 999, re-ran
   `check` with the *correct* seed/controllers (so provenance passes) against the corrupted file
   → `REPLAY-DRIFT at index 29: type=play seat=Grixis Reanimator n_options=999 vs type=play
   seat=Grixis Reanimator n_options=3`, exit **4**. This proves the row-by-row diff logic itself
   fires on a real (synthetic) defect, not just the cheap provenance pre-check — closing the
   "a gate that can't fail is not evidence" loop for both refusal paths, not just one.

5c. **Rollout stub.** `FORK_MODE=rollout FORK_DECISION_INDEX=10` with valid deterministic
   controllers → `FORK-ROLLOUT NOT-IMPLEMENTED (see HANDOFF)`, exit **3**. Same command with no
   `CONTROLLER_A/B` set (defaults to `llm`) → refused before printing the not-implemented message,
   exit **2**, listing both controllers as invalid. Confirms rollout validates args before hitting
   the stub, and that the llm-controller refusal applies uniformly across all three modes.

## What remains: rollout (Phase 1.1 continuation)

`LlmSpikeFork.applyOptionAndRolloutSTUB()` (bottom of `LlmSpikeFork.java`) is the marked stub —
unreachable in v1 (`runRollout()` always exits before calling it), carrying the full design
writeup as its doc comment. Summary (see the method's Javadoc for the complete version):

1. **Extend `ForkHook`** to expose engine-level option objects (SpellAbility / blocker
   assignment / etc.), not just the string labels it has today — needed to force a specific
   option rather than just observe the deterministic pick.
2. **Add a "forced" controller mode** to `PlayerControllerLLM` (or an index-injection hook) so a
   targeted decision can be steered to option *i* instead of floor/random's own pick.
3. **Seed function** `rollout_seed = f(base_seed, option_i, rollout_j)` — proposed
   `mix64(base_seed ^ (option_i * C1) ^ (j * C2))` (SplitMix64-style), applied via
   `MyRandom.setRandom(...)` at the moment of the forced decision, not at process start (the
   pre-decision prefix must stay bit-identical across every rollout of the same position). **Not
   yet verified against `MyRandom`'s actual contract** — whether reseeding mid-stream needs to
   preserve already-consumed state for future draws, or whether a fresh `Random` is correct, needs
   checking before implementation, not assuming.
4. **Replay-from-index, not mid-stack `GameCopier`.** Per `eval/EVAL-DESIGN.md` §3.1, v1 must
   restrict rollout targets to **empty-stack decisions only** (play/attackers/blockers/mulligan —
   not targets/discard/sacrifice/modes/entity/zone-change, which can fire mid-stack). Each rollout
   re-plays the full deterministic prefix from turn 1 (cheap: floor/random games run in
   single-digit seconds) then diverges at `FORK_DECISION_INDEX`. `GameCopier`-based mid-stack
   forking is explicitly plan B only, same restriction, per the design doc's own fallback note.
5. **Win-count emission**: one jsonl line per (position, option), schema
   `eval/schemas/puzzle-position.v1.schema.json` per EVAL-DESIGN.md §10.1 — **that schema file did
   not exist in this worktree as of this milestone**; confirm it has landed before writing the
   emitter, or write the schema first.

**Estimated effort: 3–5 days.** The replay-to-index plumbing this depends on
(`playDeterministicGame`, `LlmSpikeGui.ForkHook`) is done and validated above; what remains is
items 1–5.

## Known scope limits (not bugs, worth carrying forward)

- `check` mode only validates decision-level replay for `floor`/`random` controllers; `ai`
  (plain, untouched Forge AI) emits no decision rows at all (see step 3b above). `ai-log` should
  work but was not exercised in this validation pass.
- `enumerate` mode's state snapshot uses empty intel / no turn-plan (the same convention the
  pre-existing `PlayerControllerAiLogging.snap()` uses) — accurate for floor/random arms, which
  never accumulate either, so this is not a fidelity loss for the modes fork actually supports.
