# Engine patches

The Forge checkout is never edited by hand (`CLAUDE.md`, "The one rule"). Where a decision the seat should
own has no `PlayerController` hook, or an engine bug distorts play, the harness changes the engine file
itself. Owner approval: 2026-10-15.

Forge is GPL, and this repository carries only our changes to it: each patch is a unified diff against the
upstream file at `FORGE_COMMIT`, at `engine-patches/<the file's path in Forge>.patch`. Each opens with a notice
(modified from Forge, GPL-3.0-or-later, changes marked `HARNESS PATCH`, the dates of the changes, GPLv3 section
5(a)); diff tools ignore the text before the first `---`. `engine-patches/` sits outside `forge-overlay/`, so
copying the overlay never copies a patch.

How a patch reaches the game: `eval/engine_patches.py apply` reads each upstream original with
`git show <FORGE_COMMIT>:<path>` (never the working tree, which holds whatever was last installed), applies the
diff with `git apply` (exact context, no fuzz) and writes the patched file under a target root. A patch that does
not apply, or whose original is not at the pin, stops it with exit 1 and the file named, before anything is written.
- `install.sh` copies `forge-overlay/` over the checkout, then writes the patched files into it at their own
  paths, so the Maven build compiles them; a patch that does not apply stops the install;
- the isolated acceptance build (`eval/harness_acceptance.sh`, stage `compile`) writes them to
  `$OUT/patched-engine/` and compiles them with the overlay, its classes before the checkout's, so the patched
  class wins there too; a patch that does not apply fails the stage;
- `overlay_sha` (`eval/benchmark_version.py`, stamped on every game line by `eval/run_batch.py`) hashes each
  patch as the file it makes, at its Forge path, so it is the hash the whole-file copies had and moves when a
  patch does; `run_batch.py`'s drift check compares the installed file with the file the patch makes.

To change a patch: `python3 eval/engine_patches.py apply --out /tmp/eng` writes the patched files, edit them there,
then `python3 eval/engine_patches.py diff --from /tmp/eng` re-makes each changed patch (it keeps the notice and its
first date and moves the last date to today). A new patch: put the edited file at `/tmp/eng/<path>` and add
`--path <path>`, then add its row below.

The rules:
- only where the seat has no hook, or the engine is wrong; never to make a run pass;
- the repository holds the diff, never the whole Forge file, and every change is marked `HARNESS PATCH`;
- the table below records the sha256 of the upstream original; `eval/test_engine_patches.py` fails when the
  pinned checkout's original differs (a re-pin forces a re-diff), a patch does not apply to it, a hunk adds
  no marked line, a patch has no row or no notice, or `forge-overlay/` holds a whole Forge file;
- each patch has a golden check, and the change goes through acceptance and the canaries like any other.

| Patch | Upstream sha256 at FORGE_COMMIT | What it changes | Golden check |
|---|---|---|---|
| `engine-patches/forge-ai/src/main/java/forge/ai/ComputerUtilMana.java.patch` | `dd768c381ef7caf53852f2b3c21d3c0958d73f41188393a47443fde6fd0fbb36` | For a generic shard paid by an any-colour source (Lotus Petal, LED, Birds of Paradise), a model seat's colour is left open, so `ManaEffect` asks its controller; Forge's AI payment used to fix the first of WUBRG. `HARNESS_SEAT_CHOOSES_ANY_COLOR` is false unless `PlayerControllerLLM` sets it, for model seats only, so the opponent AI and the zero-model arms are unchanged. Second change (2026-10-18): the mana sorter skipped a source whose tap damage would be lethal (City of Brass at 1 life), a preference of Forge's AI, so Angel's Grace was never offered; `HARNESS_SEAT_MAY_TAKE_LETHAL_TAP_DAMAGE` lets every harness seat pay with it (rules pilot RP_017), and since epoch 65 it is sorted last, after every safe source, rather than in its usual place (at 1 life with City of Brass and Llanowar Elves, City was tapped for {1}). Third (2026-10-19): `chooseManaAbility` skipped an ability's own card as a source ("this rarely seems like a good idea") and a manland animated this turn; for a harness seat neither is skipped (Mishra's Factory tapping itself for its animate, rules pilot RP_175), unless both the ability and the mana ability tap it (Rishadan Port, runs/loop/g27, epoch 60). | `GoldenChecksTest.theColourAnAnyColourSourceMakesForGenericIsTheSeats`, `GoldenChecksTest.aSourceWhoseTapDamageIsLethalStillPaysForTheSeat`, `GoldenChecksTest.aSourceWhoseTapDamageIsLethalPaysLast`, `GoldenChecksTest.aCardMayPayForItsOwnAbility`, `GoldenChecksTest.aCardThatMustTapForItsAbilityCannotAlsoTapForItsMana` |
| `engine-patches/forge-game/src/main/java/forge/game/player/Player.java.patch` | `40417be8c8d0c5c524fe912b0734d34b428518007961085af4a1146d9b5cda39` | `assignCompanion` asks for a companion as an optional choice, then moved the answer to the command zone unchecked: a seat that declined (no rule makes a player reveal a companion) crashed the game at its start with a NullPointerException. A declined companion is now no companion. Found by four Vintage top-8 decks with a companion in the sideboard (2026-10-16). | `GoldenChecksTest.aCompanionTheSeatDeclinesIsNoCompanionNotACrash` |
| `engine-patches/forge-game/src/main/java/forge/game/zone/MagicStack.java.patch` | `e814c6eecea39df8b7e02be907d37c33688f6c8198c30ac9e6e621f301d5679b` | The resolved event's log line was built from the spell's stack description AFTER the effect applied, so a state-dependent pump (+X where X is the target's power, e.g. Berserk) re-read the pumped creature and the GAME HISTORY said twice the applied amount: g06 seed 4106 T7 showed the stack preview "+5/+0" and the next prompt's history line "+10/+0" while the effect applied +5 (damage 5+5+4=14). MagicStack now captures the description before the spell applies. (2026-10-17) | `GoldenChecksTest.aPumpResolveLineSaysWhatTheStackPreviewSays` |
| `engine-patches/forge-ai/src/main/java/forge/ai/simulation/GameCopier.java.patch` | `8f6c52be5909b1a3a4a9cd1e6785e2fb65dfcda83f8e359ec09ad2ac3db2df9a` | The copier threw "Couldn't map" on a card that has a zone but that no copied zone holds: Carnivorous Cultivator's prepared spell (Enroot), remembered by its effect and affected by a static effect. Every `simulate` preview in a game with a prepared card failed (runs/loop/g16, g23), including the simulator's own internal copy (GameStateEvaluator.simulateUpcomingCombatThisTurn), which the harness cannot reach. A remembered card the copy does not hold is skipped, as a zoneless one already was, and a static effect's affected card the copy does not hold is left out. (2026-10-18) | `LookaheadProbeTest.aPreparedCardsRememberedSpellDoesNotBreakThePreview` |

On an engine re-pin (`docs/harness/ENGINE-PIN.md`, step 4b): a patch whose context did not move applies as it is;
one that stops is ported by hand onto the new upstream file and re-made with `diff`; every row records the new
sha256.
