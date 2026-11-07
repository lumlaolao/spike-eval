# Golden checks: every decision type the seat owns, through the live controller

docs/STANDARDS.md section 8 says every ask of every type keeps the contract. That check
(`AskContract`, run after every test in `PromptFidelityTest` and `GoldenChecksTest` on every prompt
the seat asked) only sees the asks that some test makes. This page lists every decision type the LLM
seat can ask, which tests drove it through the live `PlayerControllerLLM` before this work, and which
`GoldenChecksTest` test drives it now. `GoldenChecksTest.everyOwnedDecisionTypeIsExercisedHere` fails
when a type in `GoldenChecksTest.OWNED` has no test that a model answered, or a test asks a type the
list does not know, so the list and this page cannot fall behind the controller unnoticed.

## Sources

- The ledger: `docs/harness/controller-ownership.tsv`, rows with `impl` LLM.
- The decision `type` of every `t:"decision"` row in `runs/compare/*/*.jsonl` and
  `runs/frontier/*/*.jsonl` (2,274 rows, counted 2026-10-15).
- The type strings the controller passes to `evalDec` / `decideOne` / `decideMany` / `decideCost`.

"Before" is a test that drove the live controller under the faithful profile with the per-ask
contract running (`PromptFidelityTest`). Older unit tests in `LlmSpikeTest` call some of these hooks
under the legacy profile, where the contract does not run; they are not counted.

## Inventory

| type | hook (ledger row) | recorded rows (model / auto) | before | GoldenChecksTest |
|---|---|---|---|---|
| play | chooseSpellAbilityToPlay | 560 / 923 | yes | aCastIsAskedAsPlayThenTargetThenWhichSourceStaysUp, theHandCallsCastableOnlyWhatTheMenuCanCast |
| target | chooseSpellAbilityToPlay, playTrigger, orderAndPlaySimultaneousSa (ensureTargetsSet) | 53 / 78 | yes | aCastIsAskedAs..., theOrderOfSimultaneousTriggersIsTheModels |
| targets | chooseTargetsFor (multi, divided) | 0 / 0 | no | dividedTargetsAndTheirSplitAreTheModels |
| mana-sources | payManaCost | 40 / 55 | yes | aCastIsAskedAs... |
| phyrexian | payManaCost | 0 / 0 | yes | howAPhyrexianSymbolIsPaidIsTheModels |
| number (incl. X) | chooseNumber, announceRequirements | 0 / 0 | yes | xAndModesAreTheModels, numbersAreTheModels |
| number-value | chooseNumber (range wider than 12) | 0 / 0 | no | numbersAreTheModels |
| modes | chooseModeForAbility | 3 / 0 | yes | xAndModesAreTheModels, spreeModesAreOfferedOnlyWhenPayable, escalateModesAreOfferedAsFarAsTheyCanBePaid, aPawprintCharmsModesStayWithinItsBudget, twoOverBudgetAnswersTakeNoMode, theModesSpecCarriesAPawprintBudget, aPawprintCharmsOtherDefaultsTakeNoMode, aRepeatableCharmWithNoBudgetIsTheModels, twoOverBudgetAnswersAreLoggedAsOverBudget |
| ability-to-play | getAbilityToPlay | 0 / 0 | no | whichWayToPlayACardIsTheModels |
| play-from-effect | playSaFromPlayEffect | 0 / 0 | no | castingFromAnEffectIsTheModels |
| mulligan | mulliganKeepHand | 14 / 12 | no | theMulliganAndTheBottomAreTheModels |
| mulligan-bottom | tuckCardsViaMulligan | 0 / 0 | no | theMulliganAndTheBottomAreTheModels |
| starting-player | chooseStartingPlayer | 6 / 6 | no | playOrDrawIsTheModels |
| attackers | declareAttackers | 66 / 15 | yes | attackersAndWhereTheyAttackAreTheModels |
| attackers-refused | declareAttackers (a set the engine refused; asked again with the reason) | 0 / 0 | no | anAttackTheEngineRefusesIsAskedAgainThenMadeLegal |
| attack-target | declareAttackers | 0 / 0 | yes | attackersAndWhereTheyAttackAreTheModels |
| blockers | declareBlockers | 1 / 2 | yes | blocksAreTheModels |
| damage-assign | assignCombatDamage | 2 / 31 | no | combatDamageIsAssignedTheModelsWay |
| vote | vote | 0 / 0 | yes | aVoteIsTheModels |
| entity | chooseSingleEntityForEffect (and chooseEntitiesForEffect, a wrapper) | 1 / 9 (as choose-entity) | no | anEntityPickIsTheModels |
| zone-change | chooseSingleCardForZoneChange | 35 / 9 | no | aTutorsPickIsTheModels |
| discard | chooseCardsToDiscardFrom | 0 / 0 | no | whatTheOpponentDiscardsIsTheModels, whatTheSeatDiscardsIsTheModels |
| cleanup-discard | chooseCardsToDiscardToMaximumHandSize | 17 / 11 | no | theCleanupDiscardIsTheModels |
| delve | chooseCardsToDelve | 0 / 0 | no | whatDelveExilesIsTheModels |
| sacrifice | choosePermanentsToSacrifice, getCostDecisionMaker | 0 / 0 | yes (cost) | anEdictsSacrificeIsTheModels |
| cards-for-effect | chooseCardsForEffect, getCostDecisionMaker | 0 / 0 | yes (cost) | cardsChosenForAnEffectAreTheModels, aCancelledTapCostTapsNothing |
| prevent-effect | payCostToPreventEffect | 11 / 33 | no | payingForAnUnlessCostIsTheModels |
| optional-costs | chooseOptionalCosts | 0 / 0 | no | aKickerIsTheModels |
| trigger-order | orderSimultaneousSa | 29 / 8 | no | theOrderOfSimultaneousTriggersIsTheModels |
| reveal | chooseCardsToRevealFromHand | 0 / 0 | no | whatIsRevealedFromHandIsTheModels |
| scry | arrangeForScry | 0 / 0 | no | scryAndSurveilAreTheModels |
| surveil | arrangeForSurveil | 0 / 0 | no | scryAndSurveilAreTheModels |
| order | orderMoveToZoneList | 3 / 1 | no | aPutBackOrderIsTheModels |
| confirm | confirmAction | 12 / 51 | no | aYesNoIsTheModels |
| confirm-trigger | confirmTrigger | 64 / 47 | no | anOptionalTriggerIsTheModels |
| confirm-payment | confirmPayment | 0 / 0 | no | anOptionalPaymentIsTheModels |
| confirm-replacement | confirmReplacementEffect | 0 / 0 | no | anOptionalReplacementIsTheModels |
| binary | chooseBinary | 0 / 0 | no | aBinaryChoiceIsTheModels |
| color | chooseColor | 0 / 30 | no | coloursAreTheModels |
| color-colorless | chooseColorAllowColorless | 0 / 0 | no | coloursAreTheModels |
| some-type | chooseSomeType | 12 / 8 | no | aTypeIsTheModels |
| protection | chooseProtectionType | 1 / 6 | no | aProtectionIsTheModels |
| card-name | chooseCardName (list and free name) | 6 / 3 | no | aNamedCardIsTheModels |
| harmonize | chooseNumber (harmonize's power to tap; only payable powers) | 0 / 0 | no | harmonizeTapsACreatureToReduceItsCost |
| attack-bands | declareAttackers (which attackers join a banding attacker's band) | 0 / 0 | no | aBandingAttackerFormsABand |
| exert, enlist | exertAttackers, enlistAttackers (Forge AI until 2026-10-17) | 0 / 0 | no | exertAndEnlistAreTheSeats |
| splice | chooseCardsForSplice (payable splices only; the picked set checked together, asked once more, then none) | 0 / 0 | no | spliceOntoArcaneIsTheSeats, aSpliceSetThePoolCannotPayTogetherIsAskedAgain, LineFindingProbeTest.aSplicedCastThatFailsToPayLeavesThePlainCastOfferedAndSaysSo |
| flip, shield | chooseFlipResult, divideShield | 0 / 0 | no | aFlipCallAndAShieldSplitAreTheSeats |
| roll-ignore, roll-reroll, roll-modify, roll-swap | chooseRollToIgnore, chooseDiceToReroll, chooseRollToModify, chooseRollToSwap, chooseRollSwapValue, choosePDRollToIgnore | 0 / 0 | no | diceChoicesAreTheSeats, unSetAndVariantChoicesAreTheSeats |
| sticker, crank, sprocket, sector, starting-hand, assist, assist-pay | the sticker, contraption, attraction, multiple-hands and assist hooks | 0 / 0 | no | unSetAndVariantChoicesAreTheSeats |
| damage-order | orderByModel (a damage assignment order under the legacy rules) | - | no | damageOrderIsTheModels, aDeclaredDamageOrderIsFollowedNotAskedAgain |
| pile | chooseCardsPile | - | no | choicesForgesAiUsedToMakeAreTheModels |
| clash | willPutCardOnTop | - | no | choicesForgesAiUsedToMakeAreTheModels |
| colors | chooseColors (two colours) | - | no | choicesForgesAiUsedToMakeAreTheModels |
| counter-type | chooseCounterType | - | no | choicesForgesAiUsedToMakeAreTheModels |
| card-face | chooseSingleCardFace | - | no | choicesForgesAiUsedToMakeAreTheModels |
| confirm-static | confirmStaticApplication | - | no | choicesForgesAiUsedToMakeAreTheModels |
| keyword | chooseKeywordForPump | - | no | choicesForgesAiUsedToMakeAreTheModels |
| opening-hand | chooseSaToActivateFromOpeningHand | - | no | choicesForgesAiUsedToMakeAreTheModels |
| cost-reduction | chooseNumberForCostReduction | - | no | choicesForgesAiUsedToMakeAreTheModels, anUpToCostReductionIsAskedWhenPayingNotWhilePricing |
| keyword-cost | chooseNumberForKeywordCost, chooseWideKeywordCost | - | no | aKeywordCostIsOfferedOnlyWhenItCanBePaid |
| destroy, spell-for-effect, discard-unless | choosePermanentsToDestroy, chooseSingleSpellForEffect / chooseSpellAbilitiesForEffect, chooseCardsToDiscardUnlessType | - | no | destroyAndSpellAndDiscardUnlessAreTheModels |
| convoke, new-targets | the convoke payment, chooseNewTargetsFor | - | no | convokeAndANewTargetAreTheModels |
| mana-from-pool | chooseManaFromPool (floating mana of different kinds) | 0 / 0 | yes (SurfaceResolutionChoicesTest) | delveMustExileWhatTheManaCannotPay |

`GoldenChecksTest.OWNED` holds 78 types, each driven by a test there; before 2026-10-15, 12 of the 42 types
then listed had a contract-checked live test. `-`: no recorded-row count.

Ledger rows with impl LLM that are not a model ask of their own: `isGuiPlayer` (a marker),
`reveal` and `notifyOfValue` (information shown in the seat stream), `playChosenSpellAbility`,
`playTrigger`, `orderAndPlaySimultaneousSa` and `getCostDecisionMaker` (they route into target,
modes, trigger-order, sacrifice and cards-for-effect above), and `orderAttackers` / `orderBlocker` /
`orderBlockers` (ownership `ai-observed`: the override logs a choice Forge's AI made; the seat is
not asked, as the ledger says).

## What the contract checks now (AskContract)

Every prompt the seat asks, of any type:

1. a `DECISION:` line, the current observation header, no option cut short, battlefield options
   that say whose, the carried play before the question (the rules before this work);
2. the question itself is not cut short (`...` from `truncateWords`), names no engine card id
   (`Name (93)`), carries no engine placeholder (`CARDNAME`), and coaches no deck's line;
3. options are numbered 1..n in order, no label repeats, no option shows an engine card id;
4. the answer format matches the menu: a `CHOICE:` ask has at least two options, a `PICKS:` ask
   says `Choose between A and B` with 0 <= A <= B <= n, a `NUMBER:` ask states its legal range;
5. on the play menu, a hand card tagged `(castable...)` has a CAST option and a card the menu casts
   is not tagged otherwise;
6. per test, where the decision has a source card: every ask names it with its rules text
   (`AskContract.namesSource`).

## Harness defects the checks found, fixed (harness-epoch-7, observation-faithful-v24)

- The mulligan ask had no observation and no `DECISION:` line, and its retry was a bare correction.
- A number ask over a range wider than 12 (`NUMBER:`) had no `DECISION:` line.
- The free card-name ask's retry was a bare correction with no state and no question.
- `chooseTargetsFor` read a divided spell's total before `clearTargets()` computes it, so a divided
  spell reached through the engine's target hook (Forked Bolt) was never asked its split and its
  targets had no allocation.
- Mode options showed the engine placeholder `CARDNAME` ("CARDNAME deals 2 damage to any target").
- Simultaneous-trigger options used the legacy label: engine card id (`[Zone Changer: Palace
  Jailer (8)]`), no handle, cut at 160 characters.
- The unless-cost question quoted the stack description with engine ids ("Spell Pierce (93) -
  Counter Umezawa's Jitte (40)"); ids now map to handles as in the narration.
- Questions were cut at 90 characters (colour, card name, sacrifice, unless cost, optional cost
  labels, mode labels) and an optional trigger's text at 320.
- The hand said `(castable)` for a card the play menu could not cast (a creature on the opponent's
  turn); the tag now follows the menu's own test (`offerable`), says `(payable, not castable now)`
  when only the mana is there, and `(castable by an alternative cost)` when only that is offered.
- The colourless-allowed colour ask and the edict sacrifice ask named no rules text of their source;
  optional payments and replacements now carry the source card's rules text beside the ability label.
- The discard ask coached a reanimator line ("discarding a huge creature you can later reanimate")
  and the delve ask coached graveyard decks, under the faithful profile.

Legacy profiles keep their bytes for every change above.
