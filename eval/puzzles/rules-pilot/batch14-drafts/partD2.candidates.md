# partD2 screen: batch 14, questions-d2.jsonl (35 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in every question exists in Forge (`grep -rlx "Name:<card>"
/root/forge/forge-gui/res/cardsfolder`; Spite // Malice as `s/spite_malice.txt`), and every card in a draft had its
script read. Drafts are `d2_K.pzl` (K = the question's line, 0-based) with `Name:Rules pilot NNN - ...` (number them
RP_2xx when kept). Each needs the usual gate: search arm proven and accepted, pass arm rejected, uniqueness unique.
None of the 35 question ids appears anywhere under `eval/puzzles/`.

Board conventions used here: mana is a floating pool (`p0manapool=`) wherever land taps do not matter; tokens use
`T:<tokenscript>` (MTGP puzzles); a manifested card is `<card>|FaceDown:Manifested` (`GameState` sets
`setManifested` when the option ends in `Manifested`; the turn-face-up ability is built lazily by
`CardState.getManifestUp`); poison is `pNcounters=POISON=9` (as in two committed puzzles); combat starts at
`COMBAT_DECLARE_ATTACKERS` with `|Attacking|Tapped` (RP_007).

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-b34da09fc6bf15e3 | board | good | `d2_0.pzl` (Uphill Battle does not tap a Sneak Attack creature: it was not played; equivalent board, Sneak Attack for Gather Specimens) |
| 1 | rulesguru-ff30a7a5c6261126 | board | good | `d2_1.pzl` (Harm's Way on your own Sulfurous Springs sends its pain to the opponent) |
| 2 | rulesguru-6b9e749577b46268 | board | maybe | `d2_2.pzl` (order undying above Unhallowed Pact: Flayer returns 5/3; ordering-only, see weakness) |
| 3 | rulesguru-2aa64a548ec6da1b | board | maybe | `d2_3.pzl` (Mystic Subdual makes Fiend Artisan -1/1, Shock kills it; the misread line is inaction) |
| 4 | rulesguru-b80d34edd169bbd6 | legality | no | splicing with the mana the spell will make; Forge offers only payable splices |
| 5 | rulesguru-54de41f4f8163583 | board | no | the order of the magecraft and copy triggers changes nothing observable: magecraft triggers once per cast and once per copy either way |
| 6 | rulesguru-59bc11fc3371062d | board | no | duplicate of RP_119 (Blood Operative triggers on the surveil that put it there; partA #4 made the same call) |
| 7 | rulesguru-bf58c7d51c1eaf63 | board | good | `d2_7.pzl` (blinking a Pacified Bear token loses it; Holy Day survives; equivalent board with a reason to blink the token) |
| 8 | rulesguru-1d9a2d0fd1407703 | board | maybe | `d2_8.pzl` (Trilobite X=4 has mana value 8 and triggers Sanctum; X=5 leaves no Bolt; weak misread) |
| 9 | rulesguru-35bea6f594e90b73 | board | good | `d2_9.pzl` (a manifested Woodripper turned face up has no fade counters: Shatter instead; board starts manifested, no Killer's Mask) |
| 10 | rulesguru-50bf9fb1eaf374ca | multiplayer | no | three players; in two players the opponent who is already monarch cannot become monarch again, so nothing returns either way |
| 11 | rulesguru-43309417703ccdb8 | board | maybe | not drafted: Thing in the Ice's trigger always goes above Summary Dismissal and resolves first; every solver line that casts Dismissal ends the same, and a line casting a cheaper instant first also works, so no misread line differs (RP_243 already uses Thing in the Ice) |
| 12 | rulesguru-c3f68401c7eadcee | board | no | Word of Command makes the solver play the opponent's cast and mana payment, and "has cast Channel previously this turn" cannot be set up; a special action taken for another player is beyond the menus |
| 13 | rulesguru-30afb212c82646ba | legality | no | no rule redirects player damage to a planeswalker; Forge offers no such choice, and Bolt can target Vivien directly |
| 14 | rulesguru-f305b4938b36b97e | board | maybe | `d2_14.pzl` (Inkmoth Nexus animated under Humility flies and infects: tenth poison counter; misread line is inaction) |
| 15 | rulesguru-cb6c48403012934d | legality | no | ninjutsu is an activated ability, so Forge never offers it under Disruptor Flute |
| 16 | rulesguru-8571a604b60ff704 | board | good | `d2_16.pzl` (Solar Blaze kills Enemy of the Guildpact, so Shock goes face) |
| 17 | rulesguru-ce83b63180497032 | board | maybe | not drafted: the choice is a subset of four permanents to sacrifice (a large search), and a solver who wants the Garrison has no line that gets it (Golden Guardian 4/4 survives a fight with a 3/3 Dralnu otherwise), so the right line has no goal the misread line also aims at |
| 18 | rulesguru-8a4b9f0c75421565 | board | good | `d2_18.pzl` (Angel's Grace ends in cleanup and the tenth poison counter kills; Shock the Agent; equivalent board, the solver is the poisoned player) |
| 19 | rulesguru-c0af4e5a0c2dbd61 | board | maybe | `d2_19.pzl` (Break of Day does not pump the later Knight token; Dictate of Heliod does; weak lure, complement of RP_054) |
| 20 | rulesguru-1ebb819125b182c7 | legality | no | suspend works only from hand; Forge never offers it from Ragavan's exile |
| 21 | rulesguru-72f407b1e0e097b7 | board | no | Aether Rift discards at random and any player may pay 5 life (an opponent choice) |
| 22 | rulesguru-5ffe544be39b6e05 | board | good | `d2_22.pzl` (Allay on Leyline of the Void goes to the graveyard; Snapcaster flashes it back for the Anthem) |
| 23 | rulesguru-cfd023067bdb8f9e | legality | no | madness and morph are both alternative costs; Forge never offers the face-down madness cast |
| 24 | rulesguru-2a7eeeef555f2c8a | board | maybe | not drafted: the opponent's Fumiko can block (the prover branches over it), the result rests on Forge's `hadToAttackThisCombat` (`CardProperty`) with an optional Propaganda cost, and the misread line (not paying) is inaction |
| 25 | rulesguru-4ff836d03fd29a24 | board | no | needs the opponent's two declared blockers; puzzle files cannot pre-set blockers |
| 26 | rulesguru-37115be9bbc4fb43 | board | maybe | not drafted: Finale of Promise at X=10 needs 12+ mana, two copies and exiled library cards to replay, a large search; worth a direct engine check later (whether Forge's copy of a flashbacked Ignite keeps "cast from graveyard") |
| 27 | rulesguru-387419288c6f4a05 | legality | no | an MDFC with a creature front is not an instant in hand; Forge's imprint choice list excludes it |
| 28 | rulesguru-1ea2aec7b23e8b67 | board | maybe | not drafted: needs the opponent's Lightning Strike already on the stack (a precast with a chosen target), and redirecting to Spellskite is the obvious play anyway (it saves 3 life), so a solver who misreads the trigger plays the same line |
| 29 | rulesguru-c361039b26fe8435 | legality | no | the opponent's escape cast; whether Familiar's Ruse can target it is a targeting legality Forge decides |
| 30 | rulesguru-cd6260c68db87eab | board | good | `d2_30.pzl` (Mishra's Factory blocks, then taps to pump itself and survives) |
| 31 | rulesguru-fa20fd8fad3a42df | legality | no | Utopia Sprawl's mana is not snow; Forge never lets it pay {S}, so the second Astrolabe is never on the menu |
| 32 | rulesguru-62fe52a02e81448a | legality | no | Mogis on the stack is a creature spell; Spite cannot target it, which Forge enforces (and it is the opponent's cast) |
| 33 | rulesguru-126fa98ea79a4b97 | legality | no | Jace in hand is a creature card; Forge never offers it to Planebound Accomplice |
| 34 | rulesguru-32775457a0d4300a | board | good | `d2_34.pzl` (Arcane Denial on your own Abrupt Decay: Decay resolves, three cards next upkeep) |

Drafted: 14 (9 good, 5 maybe). Not drafted: 21 (6 maybe, 15 no).

---

## 1. #0 - Sneaked in, not played (d2_0)

- question_id: `rulesguru-b34da09fc6bf15e3`
- Equivalent board: the question's Gather Specimens needs the opponent to cast a creature. The same rule (CR 701.18b,
  "play" means cast or play as a land) is tested by Sneak Attack, which puts a creature onto the battlefield without
  playing it.
- Forge check: Uphill Battle `R:Event$ Moved | ValidCard$ Creature | ValidCause$ LandAbility.OppCtrl,
  Spell.wasCast+Permanent+CastSa Spell.OppCtrl | Destination$ Battlefield | ReplaceWith$ ETBTapped`: only a cast
  spell or a land play by an opponent. Sneak Attack is `AB$ ChangeZone` (an activated ability, not a cast) plus
  `Animate Keywords$ Haste`. Ball Lightning {R}{R}{R} 6/1 trample haste.

```
p0 MAIN1; Sneak Attack; hand Ball Lightning; pool R R R. p1 life 6, Uphill Battle.
[acceptance] life p1 <= 0
```

- Solution: activate Sneak Attack ({R}), put Ball Lightning onto the battlefield (untapped, haste), attack for 6.
- Misread lines: "Uphill Battle taps anything that enters under my control" -> no lethal line, so do nothing; or
  ignore Uphill Battle and hard-cast Ball Lightning for R R R: it enters tapped and cannot attack.
- Uniqueness: one creature; the hard cast spends the whole pool, so the only damage is the Sneak Attack line.
- Pass arm: nothing happens, opponent at 6: rejected.

## 2. #1 - Redirect your own land (d2_1)

- question_id: `rulesguru-ff30a7a5c6261126`
- Forge check: Harm's Way `SP$ ChooseSource | Choices$ Card,Emblem` (battlefield, stack and command-zone sources),
  then `DB$ Effect | ValidTgts$ Any` with `ReDamage: Event$ DamageDone | ValidTarget$ You,Permanent.YouCtrl |
  ValidSource$ Card.ChosenCardStrict` (no controller restriction on the source), 2 damage shield. Sulfurous Springs
  `Produced$ Combo B R | SubAbility$ DBPain` (`DealDamage NumDmg$ 1 | Defined$ You`, the land is the source).
  Shock {R}, 2 damage.

```
p0 MAIN2; Plains; Sulfurous Springs; hand Harm's Way, Shock. p1 life 3.
[acceptance] life p1 <= 0; life p0 = 20
```

- Solution: Harm's Way (Plains), choosing Sulfurous Springs as the source and the opponent as the target; then Shock
  the opponent, paying {R} from the Springs: its 1 damage is redirected (3 -> 2), Shock deals 2 (0).
- Misread lines: "you can't pick your own source" -> Shock alone (opponent at 1, you at 19). Shock first, then Harm's
  Way: the pain was already dealt to you.
- Uniqueness: two lands, two spells; Harm's Way needs the Plains and Shock needs the Springs' {R}, so the only
  freedom is order, and only Harm's Way first reaches 0.
- Pass arm: opponent at 3: rejected.
- Weakness: the source list includes every battlefield and stack card plus the puzzle goal card; small, but the
  search sees each as a branch.

## 3. #2 - Undying goes first (d2_2)

- question_id: `rulesguru-6b9e749577b46268`
- Forge check: undying is `Mode$ ChangesZone ... ValidCard$ Card.Self+counters_EQ0_P1P1`, effect `ChangeZone |
  Defined$ TriggeredNewCardLKICopy | Origin$ Graveyard | WithCountersType$ P1P1` (`CardFactoryUtil`). Unhallowed
  Pact `ChangeZone | Origin$ Graveyard | Destination$ Battlefield | GainControl$ True | Defined$
  TriggeredNewCardLKICopy`: both read the card from the graveyard, so whichever resolves second finds nothing. Both
  are the solver's, so Forge asks the solver to order them (RP_002 is also a trigger-order puzzle). Flayer of the
  Hatebound 4/2, `ReanimateDmg ... NumDmg$ TriggeredCard$CardPower` on entering from your graveyard. Fling sacrifices
  as a cost, so both dies triggers go above Fling and resolve before it; Fling uses the sacrificed Flayer's power, 4.

```
p0 MAIN2; Flayer of the Hatebound|Id:1; Unhallowed Pact|AttachedTo:1; hand Fling; pool R R. p1 life 9.
[acceptance] life p1 <= 0; counters p0 Battlefield "Flayer of the Hatebound" P1P1 = 1
```

- Solution: cast Fling sacrificing Flayer (target the opponent); order undying to resolve first: Flayer returns 5/3,
  its trigger deals 5 (9 -> 4); Fling deals 4 (0).
- Misread line: Pact first ("it doesn't matter" or "both return it"): Flayer returns 4/2 with no counter, deals 4,
  Fling 4: opponent at 1.
- Uniqueness: one spell, one creature; the only real choice is the order.
- Pass arm: opponent at 9: rejected.
- Weakness: ordering-only. The misread line plays the same cards; the gate proves the right order wins but its
  uniqueness fingerprint may not show that the wrong order fails. Play the Pact-first order once as a forced line
  before keeping it.

## 4. #3 - Subdued below the bonus (d2_3)

- question_id: `rulesguru-2aa64a548ec6da1b`
- Forge check: Fiend Artisan `S:Mode$ Continuous | AffectedDefined$ Self | AddPower$ Y | AddToughness$ Y`
  (`Count$ValidGraveyard Creature.YouOwn`), a static ability, not a characteristic-defining one, so Mystic Subdual's
  `RemoveAllAbilities$ True` (layer 6) removes it; `AddPower$ -2` remains: -1/1. Four Grizzly Bears in the opponent's
  graveyard make it 5/5 at the start.

```
p0 MAIN2; hand Mystic Subdual, Shock; pool U U R. p1 Fiend Artisan; graveyard 4 Grizzly Bears.
[acceptance] count p1 Graveyard "Fiend Artisan" = 1
```

- Solution: Mystic Subdual on Fiend Artisan (-1/1), Shock it. (Shock first also works: the 2 damage stays marked and
  Subdual drops the toughness to 1; same set of plays.)
- Misread line: "the graveyard bonus is in layer 7c and still applies, so it's a 3/5" -> no kill is possible, so the
  misreader casts nothing or Shocks the opponent.
- Uniqueness: two spells, one target. Pass arm: 5/5 Artisan stays: rejected.
- Weakness: the misread line is inaction, not a competing play. Pool payment: if Subdual's generic is paid with the
  R, Shock is stranded; Shock-first avoids it.

## 5. #7 - The token does not come back (d2_7)

- question_id: `rulesguru-bf58c7d51c1eaf63`
- Equivalent board: the question asks what happens to a blinked token. Here blinking the token looks like the best
  play (it would shed Pacifism, block and kill the attacker, and draw a card), so the rule decides the line.
- Forge check: Acrobatic Maneuver `ChangeZone ... Destination$ Exile | RememberTargets$ True`, then
  `DBReturn: ChangeZone | Defined$ Remembered | Origin$ All | Destination$ Battlefield`, then draw. A token in exile
  ceases to exist as a state-based action after resolution, and Forge does not return a token from exile; if it does,
  the misread line also survives and the gate reports a second solution (a Forge disagreement). Token
  `T:g_4_4_bear` (4/4 Bear). Pacifism `CantAttack,CantBlock`. Holy Day `SP$ Fog`.

```
p1 COMBAT_DECLARE_ATTACKERS; p0 life 3, T:g_4_4_bear|Id:1, 3 Plains, hand Acrobatic Maneuver, Holy Day.
p1 Hill Giant|Attacking|Tapped; Pacifism|AttachedTo:1.
[acceptance] life p0 >= 1; winner p0
```

- Solution: Holy Day (one Plains) before blocks: no combat damage.
- Misread line: Acrobatic Maneuver on the token "to reset it": the token is exiled for good, you draw a card, the
  Giant deals 3: you lose.
- Uniqueness: three Plains pay for one of the two spells, not both; the token cannot block. Pass arm: 3 damage, you
  lose: rejected.
- Check at the gate: the cross-player `AttachedTo` on a `T:` token (ids are global in `GameState`).

## 6. #8 - X counts twice on the stack (d2_8)

- question_id: `rulesguru-1d9a2d0fd1407703`
- Forge check: Sanctum of Ugin `T:Mode$ SpellCast | ValidCard$ Card.Colorless+cmcGE7`, `TrigSearch: Cost$
  Sac<1/CARDNAME> ... ChangeType$ Creature.Colorless` (a tapped Sanctum can still be sacrificed). Cryptic Trilobite
  `ManaCost:X X` (on the stack its mana value is 2X). Wurmcoil Engine is the only colorless creature in the library.

```
p0 MAIN2; Sanctum of Ugin; Mountain; pool C x7; hand Cryptic Trilobite, Lightning Bolt; library Wurmcoil + 3 Dreadmaw.
[acceptance] count p0 Hand "Wurmcoil Engine" = 1; life p1 = 17
```

- Solution: cast Trilobite with X=4 (pool 7 + Sanctum's {C}), sacrifice Sanctum to its trigger, fetch Wurmcoil;
  Lightning Bolt the opponent with the Mountain.
- Misread lines: "X is 0 on the stack" or "mana value counts X once" -> Sanctum looks dead, so X=3 + Bolt (mana value
  6, no trigger). Greedy X=5 (all nine mana) triggers but leaves no Bolt.
- Uniqueness: nine mana in all; only X=4 + Bolt meets both lines. Pass arm: nothing: rejected.
- Weakness: the misread line is mostly "give up on the Sanctum". Search risk: if the auto-payer spends the Mountain's
  {R} on Trilobite's X, Bolt is stranded; the line needs the Sanctum tap for the eighth mana.

## 7. #9 - Unmanifested without fade (d2_9)

- question_id: `rulesguru-35bea6f594e90b73`
- Equivalent board: the board starts with the manifested Woodripper (Killer's Mask's manifest dread is a random-ish
  two-card look; not needed for the rule).
- Forge check: Fading is `etbCounter:FADE:3` (`CardFactoryUtil` line ~2239), an enters replacement; turning a card
  face up is not entering, so no counters. Woodripper `AB$ Destroy | Cost$ 1 SubCounter<1/FADE> | ValidTgts$ Artifact`
  (unpayable with no counters). Turning face up costs its mana cost {3}{G}{G} (`CardState.getManifestUp`). Shatter
  {1}{R}.

```
p0 MAIN2; Woodripper|FaceDown:Manifested; hand Shatter; pool G G G G G R. p1 Ornithopter.
[acceptance] count p1 Graveyard "Ornithopter" = 1
```

- Solution: Shatter the Ornithopter.
- Misread line: turn Woodripper face up (5 mana) expecting three fade counters, then {1} to destroy the Ornithopter
  (and keep a 4/6): the ability cannot be paid, and only one mana is left, so Shatter is out of reach.
- Uniqueness: face up (5) + Shatter (2) is 7 > 6; only Shatter destroys it. Pass arm: Ornithopter stays: rejected.

## 8. #14 - The newer animation keeps its keywords (d2_14)

- question_id: `rulesguru-f305b4938b36b97e`
- Forge check: Humility `Affected$ Creature | SetPower$ 1 | SetToughness$ 1 | RemoveAllAbilities$ True`. Inkmoth
  Nexus `AB$ Animate | Cost$ 1 | ... Keywords$ Flying & Infect` (a later timestamp, so in layer 6 its keywords apply
  after Humility's removal). Grizzly Bears cannot block a flier. Goal `Win`, so the pass arm loses at cleanup.

```
p0 MAIN1; Inkmoth Nexus; Wastes. p1 Humility; Grizzly Bears; POISON=9.
[acceptance] winner p0
```

- Solution: Wastes pays {1}, animate Inkmoth Nexus, attack: flying over the Bears, infect: tenth poison counter.
- Misread line: "Humility strips flying and infect" -> no win exists, so the misreader does nothing (or animates by
  tapping Inkmoth itself, which cannot attack).
- Uniqueness: one attacker, one payment that leaves it untapped. Pass arm: no attack, loses at cleanup: rejected.
- Weakness: the misread line is inaction.

## 9. #16 - Protection does not stop self-damage (d2_16)

- question_id: `rulesguru-8571a604b60ff704`
- Forge check: Solar Blaze `SP$ EachDamage | ValidCards$ Creature | EachToItself$ True` (`DamageEachEffect`: each
  creature is the source of its own damage). Enemy of the Guildpact 4/2, `Protection:Card.MultiColor` (the black
  creature is not multicolored). Hill Giant 3/3 dies to its own 3.

```
p0 MAIN2; hand Solar Blaze, Shock; pool R R R W W. p1 Enemy of the Guildpact; Hill Giant.
[acceptance] both in p1 graveyard; life p1 = 18
```

- Solution: Solar Blaze (both die) and Shock the opponent.
- Misread line: "protection prevents it" -> Shock the Enemy, Solar Blaze for the Giant: opponent at 20.
- Uniqueness: the Shock target is the only freedom once Blaze is cast. Pass arm: both stay: rejected.
- Pool note: if Blaze's generic takes two R, Shock is stranded; Shock first always works (same set).

## 10. #18 - Grace ends before the turn does (d2_18)

- question_id: `rulesguru-8a4b9f0c75421565`
- Equivalent board: the solver is the poisoned player and chooses between Angel's Grace and removing the Agent.
- Forge check: Angel's Grace `SP$ Effect` with `GameLoss ... CantHappen` (an end-of-turn effect). `PhaseHandler`
  CLEANUP: `game.getEndOfTurn().executeUntil()` ends it, then "CR 514.3a" `checkStateEffects(true)`: the tenth poison
  counter loses. The `Survive` goal wins only at the next upkeep (`Puzzle.java`, turn limit + 1), after that check.
  Blighted Agent 1/1 infect, `CantBlockBy`. Sacred Foundry taps for W or R.

```
p1 COMBAT_DECLARE_ATTACKERS; p0 life 7, POISON=9, Sacred Foundry, hand Angel's Grace, Shock.
p1 Blighted Agent|Attacking|Tapped; Colossal Dreadmaw|Attacking|Tapped.
[acceptance] winner p0
```

- Solution: Shock Blighted Agent; take 6 from the Dreadmaw (7 -> 1).
- Misread line: Angel's Grace ("I can't lose, and it even floors my life"): poison 10, survive combat, lose in
  cleanup.
- Uniqueness: one land, one spell; Shock on the Dreadmaw or a player leaves the poison. Pass arm: poison 10, lose:
  rejected.

## 11. #19 - Pumped after it arrived (d2_19)

- question_id: `rulesguru-c0af4e5a0c2dbd61`
- Forge check: Break of Day `PumpAll | ValidCards$ Creature.YouCtrl` (locked to the creatures there at resolution).
  Errand of Duty makes `w_1_1_knight_banding`. Garruk's Packleader `ValidCard$ Creature.powerGE3+Other+YouCtrl`,
  optional draw; RP_054 shows a static boost counts as the creature enters. Dictate of Heliod {3}{W}{W} flash,
  static +2/+2.

```
p0 MAIN2; Garruk's Packleader; pool W x7; hand Break of Day x2, Errand of Duty, Dictate of Heliod; library 6.
[acceptance] count p0 Library * = 5
```

- Solution: Dictate of Heliod, then Errand of Duty: the Knight enters 3/3, draw one.
- Misread line (the question's): Break of Day twice, then Errand of Duty (6 mana): the Knight enters 1/1, no draw.
- Uniqueness: Dictate + Errand is exactly 7; Errand before Dictate fails (same set, order-only, and it fails).
  Pass arm: library 6: rejected.
- Weakness: under the misread both lines look equal, so the lure is only that the question's line is cheaper.
  Complement of RP_054 (same card, opposite rule).

## 12. #22 - Allay lands before the Leyline leaves (d2_22)

- question_id: `rulesguru-5ffe544be39b6e05`
- Forge check: Allay `SP$ Destroy | ValidTgts$ Enchantment`, `K:Buyback:3`. Leyline of the Void `R:Event$ Moved |
  ActiveZones$ Battlefield | Destination$ Graveyard | ValidCard$ Card.!token+OppOwn | ReplaceWith$ Exile`: once it
  is destroyed during Allay's resolution it no longer applies when Allay moves at the end. Snapcaster Mage flash,
  ETB `Pump ... KW$ Flashback` on an instant in your graveyard. Glorious Anthem a plain enchantment.

```
p0 MAIN2; hand Allay, Snapcaster Mage; pool W W W W U U. p1 Leyline of the Void; Glorious Anthem.
[acceptance] both enchantments in p1 graveyard
```

- Solution: Allay the Leyline (Allay goes to your graveyard); Snapcaster Mage targeting Allay; flash Allay back at
  Glorious Anthem. 6 mana.
- Misread lines: "Allay is exiled either way" -> Allay the Anthem first (Allay really is exiled, the Leyline stays);
  buyback Allay (5 mana) then only 1 left.
- Uniqueness: the first Allay target decides everything. Pool 4W 2U pays the three costs in any assignment. Pass
  arm: both stay: rejected.

## 13. #30 - A tapped blocker still blocks (d2_30)

- question_id: `rulesguru-cd6260c68db87eab`
- Forge check: Mishra's Factory `Animate Cost$ 1` (2/2 Assembly-Worker), `Pump Cost$ T | ValidTgts$
  Creature.Assembly-Worker`. Forge's combat damage has no tapped check for blockers (`combat/` checks `isTapped`
  only for attacking and for declaring blocks). Ainok Bond-Kin 2/1, no counters (no first strike).

```
p1 COMBAT_DECLARE_ATTACKERS; p0 Mishra's Factory; Wastes. p1 Ainok Bond-Kin|Attacking|Tapped.
[acceptance] Ainok in p1 graveyard; Mishra's Factory on p0 battlefield
```

- Solution: animate the Factory with the Wastes, block Ainok, then tap the Factory to give itself +1/+1: 3/3 takes
  2 and kills Ainok.
- Misread lines: "a tapped blocker is out of combat / deals no damage" -> block without the pump (the Factory dies);
  pump before blocks (tapped, cannot block); pay the animation with the Factory's own {C} (tapped).
- Uniqueness: one Factory, one land. Pass arm: no block, Ainok lives: rejected.

## 14. #34 - Deny your own Decay (d2_34)

- question_id: `rulesguru-32775457a0d4300a`
- Forge check: Abrupt Decay `R:Event$ Counter ... Layer$ CantHappen`. Arcane Denial `SP$ Counter | RememberTargets$
  True | SubAbility$ DelTrigSlowtrip`; `CounterEffect` skips an uncounterable spell (`continue`) and the
  sub-abilities still run: two `NextTurn$ True` upkeep delayed triggers, draw 1 (Denial's controller) and up to 2
  (`RememberedController` of Decay, also the solver).

```
p0 MAIN2, Turns:2; hand Abrupt Decay, Arcane Denial; pool B G U U; library 6. p1 Grizzly Bears.
[acceptance] count p1 Graveyard "Grizzly Bears" = 1; count p0 Library * = 3
```

- Solution: Abrupt Decay on the Bears; holding priority, Arcane Denial on Decay. Denial resolves without countering;
  Decay destroys the Bears. At the opponent's upkeep draw 1 and choose to draw 2.
- Misread line: "Denial would counter my Decay" -> Decay alone: library 6.
- Uniqueness: Denial's only target is Decay (no other spell); "up to two" must be 2. The opponent's turn has only a
  land drop. Pass arm: library 6, Bears alive: rejected.
