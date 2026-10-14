# Root interfaces: where the seat meets the Forge engine

The harness is one implementation of one engine interface. Every decision the seat makes is the
engine calling a method on `forge.game.player.PlayerController`; every decision the seat does *not*
override is the engine's own `PlayerControllerAi` answering in its place. So a defect class is worth
attacking at that surface — the interface method and the engine subsystem behind it — rather than as
a one-off patch, and the backlog is what happens when a partial override drifts from the protocol it
shadows.

This file is the map. It names the interface the engine calls, the engine implementation of it, what
the seat does, and the interface gap that produces the filed defects. It is a companion to
`OWNERSHIP.md` (which hook is `model` vs `ai-inherited`) and `controller-ownership.tsv` (the pinned
ledger): this one is about the *shape* of each override, ownership is about *whether* there is one.

## The one root surface

```
forge.game.player.PlayerController            (abstract: the engine's decision interface)
        └── forge.ai.PlayerControllerAi       (the engine's implementation: every hook has an answer)
                ├── LlmSpikeGui.PlayerControllerLLM        (the seat; LlmSpikeGui.java:590)
                └── LlmSpikeGui.PlayerControllerAiLogging  (the ai-teacher seat; :5040)
```

The engine only ever calls `PlayerController`. Two consequences drive the whole defect backlog:

1. **Anything the seat does not override is the Forge AI's strategy.** Not an error — but if the
   seat overrides a *part* of a method's job (one target kind, one cost kind) and delegates the rest
   to `super`, the delegate is a silent AI decision inside the seat's own move. `OWNERSHIP.md` and
   `controller-ownership.tsv` pin the count; the argument here is that a partial override is its own
   hazard even where the hook is nominally `model`.
2. **A subsystem the seat re-implements can drift from the engine's protocol**, because the engine
   keeps two copies of the same rule (the seat's and `ComputerUtil*`'s) and only one is validated by
   the engine each time. The filed defects cluster exactly there.

## The subsystems the seat interacts with

| Engine subsystem | Interface method(s) the engine calls | Engine implementation | What the seat does | Interface gap (root cause) | Filed defects it explains |
|---|---|---|---|---|---|
| Play loop / priority | `chooseSpellAbilityToPlay()` (`PlayerController.java:287`) | `ComputerUtilAbility` builds the legal castable list; the AI loops it | overrides fully (`LlmSpikeGui.java:2033`): builds its own menu and picks | the seat re-derives the menu; legality stays the engine's, but *description* and *ordering* are the seat's | menu misdescription (ARCHITECTURE-REVIEW #2), the 12-spell cap (fixed #116), a Dig pile's order (#115) |
| **Targeting** | `chooseTargetsFor(sa)` (`PlayerController.java:120`, self-labelled "this is bad a function for it assigns targets to sa inside its body") | **`PlayerControllerAi.chooseTargetsFor` = `brains.doTrigger(sa, true)`** (`PlayerControllerAi.java:1416-1418`) — the AI core sets targets *and* modes through the engine's own `TargetChoices` | **partial override** (`LlmSpikeGui.java:2480`): single-target only (`getMaxTargets() != 1` -> `super`), bails on any targeting sub-ability, then manually `sa.resetTargets()` + `TargetChoices.add(...)` from `TargetRestrictions.getAllCandidates`. A second hand-rolled copy sits in `ensureTargetsSet` (`:2543`) for the AI cast path | the seat shadows one slice of `doTrigger`'s job and delegates the rest; **multi-target, split, modal and sub-ability targeting are AI or engine, and target *modes* are never the seat's** | the whole "targeting is messed up" class; filed as **#124** |
| Mana payment | `payManaCost(...)` (`PlayerController.java:354`) | **one call**: `ComputerUtilMana.payManaCost(new Cost(toPay, effect), player, sa, effect)` (`PlayerControllerAi.java:1258-1260`) | partial override (`LlmSpikeGui.java:2825`): prepays tap-type mana abilities the engine's *test* payment plans to use, and keeps its own reserve set | the seat decides *which sources are spoken for* with its own copy of `ComputerUtilMana`'s test payment plus `AiCardMemory.PAYS_TAP_COST` (`LlmCostDecision.manaSources`, `LlmCostDecision.java:214`) instead of by asking the engine what its real payment will tap — so a **raised** cost and a **prepay filter** both reserve nothing | **#110** (raised cost ignores the reserve), **#111** (filter-land prepay taps an unreserved source) |
| Non-mana cost decisions | `getCostDecisionMaker(...)` (`PlayerController.java:361`) | `new AiCostDecision(player, ability, effect)` (`PlayerControllerAi.java:1285-1287`) | `LlmCostDecision` subclass (`LlmSpikeGui.java:2808`) delegating the card-choosing `visit(...)`s to model-owned hooks | the candidate filter was **copied verbatim from `ComputerUtil.chooseSacrificeType`** (the drift risk #95 matched but did not remove); the wording prints raw Java (`LlmCostDecision.java:125-126`) | **#109** (multi-zone exile wording) |
| Triggers | `playTrigger`, `confirmTrigger`, `orderSimultaneousSa`, `orderAndPlaySimultaneousSa` (`PlayerController.java:103-105,152`) | `prepareSingleSa(...) && ComputerUtil.playNoStack(...)`; `prepareSingleSa` sets targets/modes via `brains.doTrigger` | owns the yes/no (`confirmTrigger`, `:3192`) and the strategic order (`orderSimultaneousSa`, `:2926`); leaves play/targeting to the engine | trigger **targeting** rides `doTrigger` (same seam as targeting); `playTrigger`'s yes/no is already the seat's | G7 `playTrigger`/`orderAndPlaySimultaneousSa` (intentionally left AI, `G7-REMAINING-WORK-ORDER.md`) |
| Combat | `declareAttackers`, `declareBlockers`, `assignCombatDamage`, `orderBlockers/orderBlocker/orderAttackers`, `exertAttackers`, `enlistAttackers` (`PlayerController.java:111,154-170`) | `ComputerUtilCombat` | overrides declarations and damage assignment (`:1869,:1960,:3763`); blocker/attacker *ordering* partly AI | a declaration and its ordering are the same decision split across two methods; the seat owns one, the engine the other | the ordering half of a declaration (ledger `controller-ownership.tsv:109-111` marks `orderAttackers/orderBlocker/orderBlockers` `ai-observed`: "damage order chosen by AiBlockController"); not yet filed |
| Zone changes / card movement | `chooseSingleCardForZoneChange`, `orderMoveToZoneList`, `choosePermanentsToSacrifice`, `chooseCardsForEffect`, `chooseCardsToDiscard*`, `arrangeForScry/Surveil` (`PlayerController.java:115,129,193-204,229-243,367`) | `ComputerUtil` / the AI brains (`PlayerControllerAi.java:278` `ComputerUtil`, `:288` `brains.chooseCardsForEffect`, `:1590` `brains.chooseCardToHiddenOriginChangeZone`) | overrides the picks (`:2446,:2950,:3083,:3912`); Forge's `AiCardMemory` is consulted in `LlmCostDecision` | the seat picks, but the *set it picks from* is re-derived in the seat instead of read from the engine's own candidate computation | same reserve shape as Mana payment above |
| Information / reveal | `reveal(...)`, `notifyOfValue`, `confirmReplacementEffect`, `confirmStaticApplication`, `chooseCardsPile` (`PlayerController.java:148-152,179-191,318`) | AI is largely a no-op (`revealAnte` "Ai won't understand that anyway", `PlayerControllerAi.java:1448`) | seat overrides `reveal` (`:1003`) and rebuilds state in `SeatObservation` | the seat's *observation* is a rendering of engine state, versioned separately from the interface | observation profile work (`SeatObservation.VERSION`) |
| Cross-tier context | (no engine method; a harness concern) | the engine hands each decision to the current tier | two independent `claude`/API conversations (smart, fast) | **the decision context is per-call, not per priority window**: an ask answered by one tier does not carry the play the other tier chose | **#108** (a cost/shuffle ask does not say which play caused it), GAME-LOOP-LOG g08/g10 |

## Root fixes, in dependency order

Each is an interface-level change that removes a class of defects, not one symptom.

- **R1 — targeting: own the choice, not the machinery.** Give the seat a hook that makes the model's
  choice *where the engine computes it*, so `chooseTargetsFor` never re-implements target assignment:
  ask the engine for the ability's `TargetChoices` (the whole chain: multi-target, split, modal) and
  have the model pick among them, then let the engine apply them. This removes the `getMaxTargets()==1`
  restriction, the `resetTargets()/add()` duplication in both `chooseTargetsFor` and `ensureTargetsSet`,
  and the sub-ability bail to `brains.doTrigger`. Closes the "targeting is messed up" class and the
  silent multi-target AI fallback. *(Found by code reading; filed as #124.)*
- **R2 — mana: stop duplicating `ComputerUtilMana`'s plan.** Either (a) leave `payManaCost` to the
  engine entirely and expose only the choice among *equivalent* payment plans (the G7 finding), or
  (b) ask the engine, dry-run, which permanents its real payment will tap and use that as the reserve
  set, instead of `manaSources()`'s own computation. Closes **#110** and **#111** together, because
  both are "the reserve is not what the payment will use". *(Root of two filed defects.)*
- **R3 — cost decisions: delegate, never copy.** `LlmCostDecision`'s tap/sacrifice filters must be
  *borrowed from* `ComputerUtil` (call it) rather than copied beside it, so the seat's candidate set
  is the engine's by construction; and the prompt must render zones in words, not Java
  (`LlmCostDecision.java:125-126`). Closes **#109** and the class it belongs to. *(Half-done in #95.)*
- **R4 — priority-window context.** A per-window "pending play" on the controller, set when a cast/
  activation is chosen, read by every follow-up ask (cost, target, shuffle, trigger) whichever tier
  answers. Closes **#108** at the root instead of per-case patching (the #95 shuffle-note was a
  single-site patch of exactly this). *(Root of one filed defect.)*
- **R5 — combat: keep a declaration and its ordering together.** Own `orderAttackers/orderBlockers`
  wherever the seat owns `declareAttackers/declareBlockers`, so one decision is not split between the
  seat and the AI. *(Same "partial override" shape as R1.)*

## Keeping this map honest

- A root change moves its `controller-ownership.tsv` entry and its `ControllerOwnershipTest` expectation
  in the same PR (the G7 method, `G7-REMAINING-WORK-ORDER.md`).
- A claim here cites the engine line it was read from (`~/code/forge`), never memory of the rules.
- A row's "interface gap" that gains a *new* partial-override fallback should be filed as a
  `harness-defect` issue in the hunter's format, so the map stays a plan and not a wish list.

## What this does not claim

- It does not claim the seat should own everything: own the method or leave it, and say which.
- It does not re-open the G7 hooks already routed (10 of 13; `G7-REMAINING-WORK-ORDER.md`). It is
  about the three subsystems (targeting, mana, cost) where the seat still shadows engine machinery,
  plus the cross-tier context and combat-ordering seams.
- It does not determine which of R1-R5 is highest value; that needs a game read per class
  (`game-loop`), which is the next step.
