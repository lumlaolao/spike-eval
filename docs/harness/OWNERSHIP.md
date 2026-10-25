# Controller ownership ledger (gate G7)

`controller-ownership.tsv` classifies all 133 public overridable `PlayerController` hooks by who decides them on
the LLM seat. `ControllerOwnershipTest` regenerates the inventory by reflection on every run and fails when the
ledger and the compiled controller disagree in either direction, so an override cannot appear or vanish without a
reviewed ledger edit. The test also pins the inherited AI cheat-shuffle hook inert on both launch paths (engine
default, preference, and every shipped AI profile). `controller-inventory.tsv` is the raw reflection inventory.

Columns: `impl` (LLM / AI / BASE, from reflection), `kind` (strategic, payment, information, mechanics, ui, match,
exotic), `ownership`, `legacy_reach` (core / rare / none for the 16 eval decks), `notes` (evidence, call sites, and
the golden check that proves each model row: "proven by ...").

Ownership values:

- `model`: model-routed with deterministic fallbacks, never Forge AI.
- `model+ai-fallback`: model-routed, but a failure branch calls `super` (recorded as a `tier=ai` row, which the
  defect analyzer counts).
- `ai-observed`: overridden only to log; Forge AI decides (`tier=ai` row).
- `ai-inherited`: not overridden; Forge AI decides silently with no game-line row. **The analyzer's `ai=0`
  invariant cannot see these.**
- `deterministic`, `base-inherited`, `wrapper`: no model and no strategic AI content.

## Current state

Counted from `controller-ownership.tsv`:

| impl | rows | ownership |
| --- | --- | --- |
| LLM | 102 | 89 model, 8 model+ai-fallback, 4 deterministic, 1 ai-observed (`cheatShuffle`, kept inert) |
| AI | 18 | 17 ai-inherited, 1 wrapper |
| BASE | 13 | 11 base-inherited, 2 wrapper |

**Known ledger drift:** six `model+ai-fallback` rows (`declareAttackers`, `declareBlockers`, `chooseCardsForEffect`,
`chooseCardsToDiscardFrom`, `chooseSingleCardForZoneChange`, `chooseSingleEntityForEffect`) say a failure branch
calls `super`. `PlayerControllerLLM` no longer does: a failure or a null defender declares nothing (an auto row,
#126), blocks take `fallbackBlocks`, an empty list returns nothing, and a forced pick is an auto row. The `super`
calls in `LlmSpikeGui.java` for these hooks are in `PlayerControllerAiLogging`, the ai-teacher seat. The rows are to
be corrected with a code change that `ControllerOwnershipTest` checks, not by a hand edit here.

The **silent count** (a strategic or payment hook reachable in Legacy play that Forge's AI decides with no row,
or one whose notes say `PARTLY OWNED`) is **0**. `ControllerOwnershipTest` prints it as `[OWNERSHIP] ...` and fails
if it grows. The 17 `ai-inherited` hooks are no-ops, engine placeholders the AI path never calls, ante, the
deck-sanity report, `isAI`, end-of-turn memory reset, `orderCosts` (returns the list unchanged), `specifyManaCombo`
(which loops the seat's `chooseColor`) and between-games `sideboard` (only with `MATCH_GAMES>1`).

Every decision hook the seat answers is proven by a golden check that reaches it with the model asked (acceptance
stage `surfaces`, [GOLDEN-CHECKS.md](GOLDEN-CHECKS.md)).

## Hooks that needed more than an override

- **`payManaCost`** (`model+ai-fallback`). Which sources pay is `ComputerUtilMana.payManaCost(...)`, the whole
  payment mechanism with no seam. The seat owns the parts that change the game: a mana ability with a tap-type cost
  (Heritage Druid) is activated by the seat before the engine pays; which untapped sources stay untapped
  (`holdManaSources`); how Phyrexian symbols are paid (`phyrexianByLife`); and the colour an any-colour source
  (Lotus Petal, Lion's Eye Diamond, City of Brass) makes for a generic shard, through the engine patch to
  `ComputerUtilMana.java` ([ENGINE-PATCHES.md](ENGINE-PATCHES.md)). A Lotus Petal activated from the menu, outside
  a payment, asks its colour through the seat.
- **`orderAndPlaySimultaneousSa`, `playTrigger`, `playSpellAbilityNoStack`** (`model`): the seat orders the
  triggers (`orderSimultaneousSa`), chooses modes and targets (`prepareTrigger`), and a stack-less effect is paid
  with the seat's cost decision (`playNoStack`, a copy of `ComputerUtil.playNoStack` that pays with
  `LlmCostDecision`). A copy that may take new targets (storm, Twincast) is retargeted by the seat.
- **`chooseEntitiesForEffect`** (`wrapper`): an AI loop that dispatches each pick to the seat's
  `chooseSingleEntityForEffect`, so its strategic content goes through the model.
- **`isAI()`** returns true for the LLM seat, and 14 engine call sites branch on it.

## Correction: sacrifice-as-cost on the play path

The ledger's `getCostDecisionMaker` row is model-owned through `LlmCostDecision`, but the path a cast or activation
takes (the inherited `playChosenSpellAbility`, then `ComputerUtil.handlePlayingSpellAbility`) pays with a hard-coded
`new AiCostDecision(...)` and never calls the hook. Reproduced against the engine (2026-10-13): Goblin Bombardment
sacrificed Memnite with zero model asks, and a Cabal Therapy flashback with Ajani, Nacatl Pariah and another Cat in
play sacrificed Ajani.

`PlayerControllerLLM.playChosenSpellAbility` therefore runs the same steps as the engine routine for every cast and
activation and pays with `LlmCostDecision`, which routes a choosable sacrifice, exile, return or tap to the model and
hands every other cost part to the engine's own decision. The cost is read after optional keyword costs are added,
so a Casualty sacrifice is covered. If the model route fails and the engine chooses, it is counted as a
`[FALLBACK->AI]` delegation. Tests: `LineFindingProbeTest`. Still on the inherited path: an ability with a
`TargetingPlayer` parameter (another player chooses its targets). The override must be re-diffed against
`handlePlayingSpellAbility` on every engine re-pin ([ENGINE-PIN.md](ENGINE-PIN.md)).

How the ledger reached this state (the 18 silent hooks of 2026-09-07, routed one at a time) is in git history and
[history/G7-REMAINING-WORK-ORDER.md](history/G7-REMAINING-WORK-ORDER.md).
