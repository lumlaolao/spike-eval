---
paths:
  - "forge-overlay/**"
  - "engine-patches/**"
  - "install.sh"
  - "run-llm-*.sh"
---

# Working on the overlay (the harness's Java)

`forge-overlay/` is the source of truth; `install.sh` copies it over the pinned Forge checkout (`FORGE_COMMIT`).
Never edit the checkout by hand. Build and check:

```bash
FORGE_HOME=/root/forge bash install.sh
(cd /root/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests)   # check the exit code: checkstyle runs here
FORGE_HOME=/root/forge bash eval/harness_acceptance.sh                         # isolated build, never touches the checkout
```

Never build the checkout while a game runs (`pgrep -f LlmSpikeHeadless`): it swaps `target/classes` under the live JVM.
Acceptance and the gauntlet use their own build directory and are safe alongside a game.

## Engine patches

Where the seat has no `PlayerController` hook, change the engine file by a diff in `engine-patches/<its path>.patch`,
never a whole copy in `forge-overlay/`; every change marked `HARNESS PATCH` and listed in
`docs/harness/ENGINE-PATCHES.md`, which says how to make, change and re-diff one. Card scripts are never patched.

## Gotchas that cost days

- Pass priority with `null`, never an empty list, from `chooseSpellAbilityToPlay` (an empty list spins the engine's
  999-iteration loop).
- The AI cast path does no targeting: set engine-validated targets on the chosen SpellAbility before returning it
  (`ensureTargetsSet`), or the cast fizzles.
- Never fall back to Forge's AI for a choice. A failure takes a fixed legal option; `[TALLY] ... ai=0` is the invariant
  and every `[FALLBACK->AI]` is a bug.
- A legal action is never hidden (owner): what is offered is what the engine allows (`legallyPayable`), and every count,
  mode or target offered must be payable, so no answer fails the cast.
- No model ask while pricing. A hook reached from a snapshot build or an affordability test returns a fixed answer
  (`PRICING_FOR_SNAPSHOT`); set it by saving and restoring, never by clearing.
- Fields read after `evalDec` are cleared by it (`lastPicks`): return locals.
- Golden checks run without the game log; `GoldenChecksToolTest` runs them with it. A path that behaves differently
  with logging on needs that suite.

## When a change alters what the seat sees or how its answer is carried out

- Bump the stamp that covers it (`eval/versions.py` lists them; `docs/harness/VERSIONS.md` is generated) and
  `HARNESS_EPOCH`, with a dated line in the constant's comment. Move the pins in `eval/harness_acceptance.sh` with it.
- A canary that moves because an automatic path changed on purpose is re-pinned in `eval/harness_acceptance.expected`
  with the reason in `docs/harness/ACCEPTANCE.md`; never to make a run pass.
- Update `docs/harness/ARCHITECTURE.md` in the same change.
- Write the test first, from the recorded row, and watch it fail: `GoldenChecksTest` (one per decision surface),
  `KeywordSurfaceSweepTest`, `LineFindingProbeTest`, `NativeToolsTest`, `SeatObservationTest`.
