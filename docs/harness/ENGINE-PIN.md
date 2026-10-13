# Engine pin: what it is, how it was bumped, how to bump it again

The harness runs on a **pinned** Forge checkout. `FORGE_COMMIT` (repo root) holds the exact commit;
the acceptance `preflight` stage **fails** if `$FORGE_HOME` (default `~/code/forge`) is not on it,
because results built on different engines are different measurements and must never be pooled.

## Current pin

| | |
|---|---|
| Commit | `6c1810dd0b42213f9ad768d1b5f251753e1e298d` |
| Date | 2026-10-13 |
| Previous pin | `c1c64a53586459cd0d4c70f27abd1aa8bbc92891` (2026-07-09) |
| Upstream | `Card-Forge/forge` `master` (the old pin is an ancestor; this was a clean fast-forward) |

## What changed and what it cost

~3 months of upstream work: 295 changed Java files in `forge-ai`/`forge-game`/`forge-core`, plus the
usual card/script growth in `forge-gui/res`.

Against the overlay it was **inert**:

- **The overlay compiles cleanly** against the new engine (65 classes, 0 errors) — none of the ~90
  Forge classes it imports lost an API it uses.
- **227/228 Java tests passed on the first run.** The single failure was the ownership ledger, which
  every engine bump is *supposed* to trip.

The only API-visible changes that touched the overlay's surface:

| Change | Effect |
|---|---|
| `PlayerController` gained 3 abstract methods: `chooseSticker`, `chooseStickerNamePosition`, `chooseCardToKeepStickers` (the Unfinity sticker mechanic) | 3 new hooks, all resolved to `PlayerControllerAi`. The ledger and the inventory were regenerated **deliberately** (130 → 133; classification `mechanics` / `ai-inherited` / `none`), and `ControllerOwnershipTest`'s pinned count moved 130 → 133 with the reason in the message. Not strategic, so the `3 silent core strategic/payment hooks` pin is unaffected. |
| `PlayerControllerAi.setUseSimulation(boolean)` removed | The overlay never called it. |
| `AiCostDecision`, `ComputerUtilCombat`, `CombatUtil`, `ComputerUtilMana` internals | No signature change to anything the overlay calls. |
| Engine behaviour generally (card scripts, AI, rules handling) | The two zero-model canaries moved — floor kept winner **and** turns (A/108) but decisions `1053 → 1064`; random re-rolled the whole game (`B/17/75 → A/29/167`). Re-pinned **deliberately** in `eval/harness_acceptance.expected`, each gated on the analyzer's `tier=ai`=0 and `silent drops=0`. |

## How to bump it again

1. `git -C $FORGE_HOME fetch origin && git merge --ff-only origin/master` (or check out the target
   commit). Prefer a **worktree** first (`git worktree add --detach /path <sha>`) if you want to
   validate before moving the checkout the harness uses.
2. Build the engine: `cd $FORGE_HOME && mvn -pl forge-gui-desktop -am install -DskipTests`, then
   `FORGE_HOME=$FORGE_HOME bash install.sh` to refresh the installed overlay.
3. Compile the overlay against it **before** trusting anything:
   `FORGE_HOME=$FORGE_HOME STAGES="classpath compile" bash eval/harness_acceptance.sh`.
4. Run the tests and read the failures: `STAGES="classpath compile javatests" ...`. Expect the
   ownership ledger to be the one that breaks; regenerate it and the inventory **deliberately** if the
   engine added or removed hooks (keep the files sorted; update `ControllerOwnershipTest`'s count and
   the counts in `OWNERSHIP.md`).
4b. **Re-diff the copied play routine.** `PlayerControllerLLM.playChosenSpellAbility` repeats the
   steps of `ComputerUtil.handlePlayingSpellAbility` so it can pay with `LlmCostDecision` (the engine
   hard-codes `AiCostDecision` there). If upstream changed that method, port the change.
5. Update `FORGE_COMMIT`, then run the **full** acceptance. If the canaries moved, that is a real
   behaviour change: re-pin `eval/harness_acceptance.expected` **deliberately and with a note**, never
   by relaxing the assertion.
6. Record the bump: a row in `ACCEPTANCE.md` with the evidence path, and this file's table.

A pin change is a **provenance event**. It changes `engine_sha` on every artifact, so runs across a
bump are not comparable — exactly like a context-profile or checkpoint change.
