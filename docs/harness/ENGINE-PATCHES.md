# Engine patches

The Forge checkout is never edited by hand (`CLAUDE.md`, "The one rule"). Where a decision the seat should
own has no `PlayerController` hook, or an engine bug distorts play, the harness carries a patched copy of
the engine file in `forge-overlay/`, at the file's own path. Owner approval: 2026-10-15.

How a patch reaches the game:
- `install.sh` copies `forge-overlay/` over the checkout, so the Maven build compiles the patched file;
- the isolated acceptance build (`eval/harness_acceptance.sh`, stage `compile`) compiles every overlay file
  under `*/src/main/java/` and puts those classes before the checkout's, so the patched class wins there too.

The rules:
- only where the seat has no hook, or the engine is wrong; never to make a run pass;
- the whole upstream file at `FORGE_COMMIT` is copied, and every change is marked `HARNESS PATCH`;
- the table below records the sha256 of the upstream original; `eval/test_engine_patches.py` fails when the
  pinned checkout's original differs (a re-pin forces a re-diff) or a changed hunk carries no marker;
- each patch has a golden check, and the change goes through acceptance and the canaries like any other.

| Overlay file | Upstream sha256 at FORGE_COMMIT | What it changes | Golden check |
|---|---|---|---|
| `forge-overlay/forge-ai/src/main/java/forge/ai/ComputerUtilMana.java` | `dd768c381ef7caf53852f2b3c21d3c0958d73f41188393a47443fde6fd0fbb36` | For a generic shard paid by an any-colour source (Lotus Petal, LED, Birds of Paradise), a model seat's colour is left open, so `ManaEffect` asks its controller; Forge's AI payment used to fix the first of WUBRG. `HARNESS_SEAT_CHOOSES_ANY_COLOR` is false unless `PlayerControllerLLM` sets it, for model seats only, so the opponent AI and the zero-model arms are unchanged. Second change (2026-10-18): the mana sorter skipped a source whose tap damage would be lethal (City of Brass at 1 life), a preference of Forge's AI, so Angel's Grace was never offered; `HARNESS_SEAT_MAY_TAKE_LETHAL_TAP_DAMAGE` lets every harness seat pay with it (rules pilot RP_017). | `GoldenChecksTest.theColourAnAnyColourSourceMakesForGenericIsTheSeats`, `GoldenChecksTest.aSourceWhoseTapDamageIsLethalStillPaysForTheSeat` |
| `forge-overlay/forge-game/src/main/java/forge/game/player/Player.java` | `40417be8c8d0c5c524fe912b0734d34b428518007961085af4a1146d9b5cda39` | `assignCompanion` asks for a companion as an optional choice, then moved the answer to the command zone unchecked: a seat that declined (no rule makes a player reveal a companion) crashed the game at its start with a NullPointerException. A declined companion is now no companion. Found by four Vintage top-8 decks with a companion in the sideboard (2026-10-16). | `GoldenChecksTest.aCompanionTheSeatDeclinesIsNoCompanionNotACrash` |
| `forge-overlay/forge-game/src/main/java/forge/game/zone/MagicStack.java` | `e814c6eecea39df8b7e02be907d37c33688f6c8198c30ac9e6e621f301d5679b` | The resolved event's log line was built from the spell's stack description AFTER the effect applied, so a state-dependent pump (+X where X is the target's power, e.g. Berserk) re-read the pumped creature and the GAME HISTORY said twice the applied amount: g06 seed 4106 T7 showed the stack preview "+5/+0" and the next prompt's history line "+10/+0" while the effect applied +5 (damage 5+5+4=14). MagicStack now captures the description before the spell applies. (2026-10-17) | `GoldenChecksTest.aPumpResolveLineSaysWhatTheStackPreviewSays` |
| `forge-overlay/forge-ai/src/main/java/forge/ai/simulation/GameCopier.java` | `8f6c52be5909b1a3a4a9cd1e6785e2fb65dfcda83f8e359ec09ad2ac3db2df9a` | The copier threw "Couldn't map" on a card that has a zone but that no copied zone holds: Carnivorous Cultivator's prepared spell (Enroot), remembered by its effect and affected by a static effect. Every `simulate` preview in a game with a prepared card failed (runs/loop/g16, g23), including the simulator's own internal copy (GameStateEvaluator.simulateUpcomingCombatThisTurn), which the harness cannot reach. A remembered card the copy does not hold is skipped, as a zoneless one already was, and a static effect's affected card the copy does not hold is left out. (2026-10-18) | `LookaheadProbeTest.aPreparedCardsRememberedSpellDoesNotBreakThePreview` |

On an engine re-pin (`docs/harness/ENGINE-PIN.md`): for each row, diff the new upstream file against the old
one, port the change into the patched copy, and record the new sha256.
