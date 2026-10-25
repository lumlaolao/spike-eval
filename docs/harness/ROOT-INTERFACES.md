# Root interfaces: where the seat meets the Forge engine

The harness is one implementation of one engine interface. Every decision the seat makes is the engine calling a
method on `forge.game.player.PlayerController`; every decision the seat does *not* override is the engine's own
`PlayerControllerAi` answering in its place. So a defect class is worth attacking at that surface, the interface
method and the engine subsystem behind it, rather than as a one-off patch: the defects cluster where a partial
override drifts from the protocol it shadows.

This file is the map: the interface the engine calls, the engine implementation of it, what the seat does, and where
the seat still copies engine machinery. It is a companion to [OWNERSHIP.md](OWNERSHIP.md) and
`controller-ownership.tsv` (the pinned ledger of which hook is whose): this one is about the *shape* of each
override, ownership is about *whether* there is one. The contract that keeps it is `docs/STANDARDS.md` section 8.

## The one root surface

```
forge.game.player.PlayerController            (abstract: the engine's decision interface)
        └── forge.ai.PlayerControllerAi       (the engine's implementation: every hook has an answer)
                ├── LlmSpikeGui.PlayerControllerLLM        (the seat)
                └── LlmSpikeGui.PlayerControllerAiLogging  (the ai-teacher seat)
```

The engine only ever calls `PlayerController`. Two consequences drive the defect classes:

1. **Anything the seat does not override is Forge AI's strategy.** If the seat overrides a *part* of a method's job
   (one target kind, one cost kind) and delegates the rest to `super`, the delegate is a silent AI decision inside
   the seat's own move. The ledger pins the count; a partial override is its own hazard even where the hook is
   nominally `model`.
2. **A subsystem the seat re-implements can drift from the engine's protocol**, because there are then two copies
   of the same rule (the seat's and `ComputerUtil*`'s) and the engine validates only one of them each time.

## The subsystems the seat interacts with

Engine citations are the pinned checkout (`$FORGE_HOME`, [ENGINE-PIN.md](ENGINE-PIN.md)); the seat is cited by
method name.

| Engine subsystem | Interface method(s) | Engine implementation | What the seat does | Where it still shadows the engine |
|---|---|---|---|---|
| Play loop / priority | `chooseSpellAbilityToPlay` | `ComputerUtilAbility` builds the castable list; the AI loops it | builds its own menu from the engine's legal list and asks; every window is a `t:"window"` row | the menu's description and order are the seat's; legality stays the engine's |
| Targeting | `chooseTargetsFor` | `PlayerControllerAi.chooseTargetsFor` = `brains.doTrigger(sa, true)`, which sets targets and modes through `TargetChoices` | routes single, multi, chained and divided targets; modes are chosen before targets; a target ask can cancel the play; `ensureTargetsSet` targets the AI cast path | the seat assigns targets itself from `TargetRestrictions.getAllCandidates` rather than through `doTrigger` |
| Mana payment | `payManaCost` | one call: `ComputerUtilMana.payManaCost(...)` | activates tap-type mana abilities first, asks which sources stay untapped and how Phyrexian symbols are paid; an engine patch gives it a generic shard's colour | which sources pay is still the engine's plan; the seat's reserve comes from a test payment of the same ability (`LlmCostDecision.manaSources`) |
| Non-mana costs | `getCostDecisionMaker`, and the play path `playChosenSpellAbility` | `new AiCostDecision(...)`; `ComputerUtil.handlePlayingSpellAbility` hard-codes it | `LlmCostDecision` routes choosable sacrifice, exile, return and tap costs; `playChosenSpellAbility` and `playNoStack` are copies of the engine routines that pay with it | see [OWNERSHIP.md](OWNERSHIP.md), "Correction": the candidate filters are copied from `ComputerUtil`, and the play routines must be re-diffed on every re-pin |
| Triggers | `playTrigger`, `confirmTrigger`, `orderSimultaneousSa`, `orderAndPlaySimultaneousSa` | `prepareSingleSa(...)` with `brains.doTrigger`, then `ComputerUtil.playNoStack` | owns the yes/no, the order, the modes and targets (`prepareTrigger`) and the payment (`playNoStack`); retargets a copy that may take new targets | a copied spell that may not take new targets keeps the engine path |
| Combat | `declareAttackers`, `declareBlockers`, `assignCombatDamage`, `orderAttackers`, `orderBlocker`, `orderBlockers`, `exertAttackers`, `enlistAttackers` | `ComputerUtilCombat`, `AiBlockController` | owns declarations, damage assignment, damage order, exert and enlist | none: a null defender or an exception declares nothing (an auto row, #126); blocks take `fallbackBlocks` |
| Zone changes | `chooseSingleCardForZoneChange`, `orderMoveToZoneList`, `choosePermanentsToSacrifice`, `chooseCardsForEffect`, `chooseCardsToDiscard*`, `arrangeForScry`, `arrangeForSurveil` | `ComputerUtil` and the AI brains | owns the picks and orders over the engine's list as passed; a `delayedReveal` pick reveals first, then asks | none: an empty list returns nothing; a forced pick is an auto row |
| Information | `reveal`, `notifyOfValue`, `confirmReplacementEffect`, `confirmStaticApplication`, `chooseCardsPile` | `reveal` records to `AiCardMemory.REVEALED_CARDS`; `notifyOfValue` is a no-op | records every reveal and notice in the seat stream (`SeatObservation`); the confirmations and piles are asked | the observation is a rendering of engine state, versioned separately (`SeatObservation.VERSION`) |
| Context across asks | no engine method | the engine hands each decision to the controller | every ask after a play carries the play and the model's reason for it | none known |

## Rare hooks and the decks that reach them

Every strategic hook reachable in play is the seat's ([OWNERSHIP.md](OWNERSHIP.md)), but a hook the eval pool
never calls is proven only by its golden check. The engine effect that calls each is the reachability key (grep the
effect class under `forge-game/.../ability/effects/`); the `hook-*` probe decks ([HOOK-DECKS.md](HOOK-DECKS.md))
reach them on purpose.

| Hook | Engine effect that calls it | Probe deck |
|---|---|---|
| `chooseCardsPile` | `TwoPilesEffect` | `hook-piles` |
| `choosePermanentsToDestroy` | `SacrificeEffect` (`Destroy$ True`) | `hook-destroy-choice` |
| `chooseCardsToDiscardUnlessType` | `DiscardEffect` (`UnlessType`) | `hook-discard-unless` |
| `chooseSingleSpellForEffect`, `chooseNewTargetsFor`, `chooseTarget` | `CopySpellAbilityEffect`, `ChangeTargetsEffect` | `hook-redirect-copy` |
| `chooseSpellAbilitiesForEffect` | `VillainousChoiceEffect` | `hook-villainous-choice` |
| `chooseCardsForEffectMultiple` | `DigMultipleEffect` | `hook-dig-multiple` |
| `chooseColors` | `ChooseColorEffect` (`TwoColors$ True`) | `hook-two-colors` |
| `chooseCounterType` | `CountersPutOrRemoveEffect`, `CountersMoveEffect` | `hook-counter-type` |
| `chooseSingleCardFace` | `VentureEffect` | `hook-venture` |
| `chooseFlipResult` | `FlipCoinEffect` | `hook-flip` |
| `willPutCardOnTop` | `ClashEffect` | `hook-clash` |
| `chooseSaToActivateFromOpeningHand` | `GameAction` | `hook-opening-hand` |
| `playSaFromPlayEffect` | `PlayEffect` (`DB$ Play`) | `hook-play-exile` |
| `exertAttackers`, `enlistAttackers` | `PhaseHandler` | `hook-exert` |
| `chooseCardsForSplice` | `AbilityUtils` (splice) | `hook-splice` |
| `chooseModeForAbility` with `allowRepeat` | a charm with `CanRepeatModes$ True` | `hook-repeat-modes` |
| `chooseKeywordForPump`, `chooseSingleCardState`, `chooseSingleReplacementEffect`, `chooseSingleStaticAbility`, `confirmStaticApplication`, `chooseStartingHand` | `PumpEffect` (`KWChoice`), `UnlockDoorEffect`, `ReplacementHandler`, `CostAdjustment`, `Combat`, `BackupPlanService` | none: proven by the `surfaces` stage; the ledger names a test for four of them and none for `chooseKeywordForPump` or `confirmStaticApplication` |

Re-run this table when a deck joins the pool: a new card can make a dormant hook live.

## What would remove a class of defects

- **Targeting: own the choice, not the machinery.** Ask the engine for the ability's `TargetChoices` and have the
  model pick among them, so the seat no longer assigns targets beside `doTrigger`.
- **Mana: ask the engine what its real payment will tap**, instead of reading the reserve from a test payment.
- **Cost decisions: delegate, never copy.** Borrow the candidate filters from `ComputerUtil` (call it) rather than
  copy them, so the seat's candidate set is the engine's by construction.

Which of these is worth its risk is not determined.

## Keeping this map honest

- A root change moves its `controller-ownership.tsv` entry and its `ControllerOwnershipTest` expectation in the same
  PR (the G7 method, [history/G7-REMAINING-WORK-ORDER.md](history/G7-REMAINING-WORK-ORDER.md)).
- Cite the engine by file and method in the pinned checkout, the seat by method name, never by line: the seat's line
  numbers move with every edit. Never cite the checkout's copy of an overlay file: it is the last `install.sh`
  snapshot, not this source.
- A new partial-override fallback is filed as a `harness-defect` issue (the `defect-hunt` skill's format), so the
  map stays a plan and not a wish list.
