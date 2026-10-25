# Player lookahead: the `simulate` preview and the combat outlook

The capability the owner asked for: *the model can ask the Forge engine what a move does, a step into the future,
and reason on the result*, without seeing anything a player could not. This is the G8 gate ("safe simulation",
[history/COMPLETION-GATES.md](history/COMPLETION-GATES.md)). How the seat uses it is in
[ARCHITECTURE.md](ARCHITECTURE.md) section 7 (the `simulate` row); this page holds the design, the safety contract
and what is not built. The current version is `PlayerLookahead.VERSION` ([VERSIONS.md](VERSIONS.md)).

## What is built

Everything below is on only with `LOOKAHEAD=1` (`run_batch.py --lookahead`); without it the prompt bytes are
unchanged and `simulate` is not declared.

- **`simulate`, a native tool** (`NativeTools.SIMULATE`, mirrored in `eval/tooluse/tools.json`). `option` is a
  CAST, ACTIVATE, PLAY LAND or PASS number of the priority menu, or a target's number while the seat aims a play it is
  making; `target` aims the first option at a named target; `then` previews a line of further options, in order, on
  the same copy (land drops and mana abilities are steps like any other). It may be called several times an ask,
  within the tool budget (`ContextMemory.TOOLS_PER_ASK`, [MODEL-LIMITS.md](MODEL-LIMITS.md)).
- **What a preview is:** one immediate resolution on a copy of the game (`PlayerLookahead.previewCast` and
  `previewLine`): the option is cast on the copy, the stack resolves, and the report is the public delta (which
  permanents left or arrived; each player's life, hand, library, graveyard and exile counts; the mana pool), with the
  targets it assumed disclosed (`[assuming target: ...]`). PASS resolves nothing and makes no copy. It is not a
  multi-turn rollout and not a win probability.
- **The combat outlook:** the attackers question carries `OUTLOOK (engine, all unblocked): N damage`, the attackers
  nothing can block, and for each other attacker which of the defender's creatures could block it
  (`attackersCeiling`); the blockers prompt carries `INCOMING (engine, if nothing
  blocks): N damage` (`blockersCeiling`). Both come from the engine's own combat arithmetic
  (`ComputerUtilCombat.sumDamageIfUnblocked`, `damageIfUnblocked`, `CombatUtil.canBeBlocked`, `canBlock`).
- **The greedy floor** (`greedy:` model spec, `GreedyBridge`) uses the same preview to take the play that drops the
  opponent's life most ([ARCHITECTURE.md](ARCHITECTURE.md) section 11).

## The safety contract (PLAYER-HARNESS.md section 7)

A preview may use only the seat's information set and must say what it assumed. It must not read the real hidden
hand or library order, consume the live game's randomness, change the live game, or use Forge's strategic policy
without saying so. A rules preview, a sampled imperfect-information rollout and an omniscient offline analysis are
three different things and are labelled as such.

How the code meets it (`PlayerLookahead.java`):

- **No live-state aliasing.** `GameCopier.makeCopy()` yields a distinct object graph; the copy's players are not the
  live players.
- **No live randomness.** Forge draws from a process-wide `MyRandom`. `copyForPreview` installs a fresh `Random`
  for the copy and restores the live reference in a `finally`, so the live stream is bit-identical afterwards.
- **No hidden card read.** The copy shuffles every library and `determinize` re-deals each opponent's hand from its
  hand and library before anything resolves (lookahead-preview-v6, review H2).
- **No model inside a copy.** While a preview runs (`PlayerLookahead.inPreview()`), every player the engine creates
  is driven by the engine's AI, never by the seat's model (found in runs/loop/g01: the engine's scoring simulated a
  combat on a second copy and asked the real model about it).
- **A failure is not an answer.** A preview that cannot be built or paid reports that, and the live game goes on.

## Acceptance criteria and their tests

| Criterion | Shown by |
|---|---|
| Hidden-world invariance: two worlds differing only in hidden cards give the same report | `LookaheadProbeTest.hiddenCardsDoNotChangeThePreview` |
| No live side effects: live RNG stream, library order and board unchanged | `LookaheadProbeTest.copyForPreviewDoesNotTouchLiveRngOrState`, `previewCastResolvesANonTargetingSpellWithoutTouchingTheLiveGame` |
| The option is left as it was (targets restored on every ability of the chain) | `LookaheadProbeTest.aPreviewLeavesNoTargetOnAnyAbilityOfTheChain` |
| The seat asks, previews and is asked again in a real decision | `LlmSpikeTest.g8LookaheadPreviewsASpellThenReasks`; acceptance stages `lookahead` and `nativetool` |
| No model is asked inside a copy | `LookaheadProbeTest.everyPlayerCreatedDuringAPreviewIsCreatedInsideThePreview` |
| A call that does not apply gets an error with the reason, never an action | the `NativeTools.NO_PREVIEW` error |

## Not built

- **A multi-turn or sampled rollout.** `LlmSpikeFork.runRollout()` (`FORK_MODE=rollout`, the offline answer-key
  generator, `FORK_ROLLOUT_DETERMINIZE=library`) replays a deterministic prefix from turn 1 in a fresh match, so it
  cannot serve a live seat whose prefix holds model decisions. A live rollout would have to run future turns on the
  copy, with the determinization caveat applying to every turn it plays.
- **Many sampled worlds (ISMCTS).** A preview samples one world from a fixed seed.
- **A per-attacker breakdown at the block decision** (what each block saves).
- **Omniscient analysis with the real hidden order:** never a player tool.
