# partA screen: batch 14, questions-a.jsonl (29 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in a draft was checked with
`grep -rlx "Name:<card>" /root/forge/forge-gui/res/cardsfolder` and its script read. Drafts are `a_K.pzl` with
`Name:Rules pilot NNN - ...` (number them RP_2xx when kept). Each needs the usual gate: search arm proven and
accepted, pass arm rejected, uniqueness unique.

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-f6872bdbe0f73d3d | board | maybe | not drafted: Dragon's control + ability loss leaves no misread line the solver would play differently; Lattice/Dragon target set gives several lines |
| 1 | rulesguru-4f88642d6e0a25e4 | board | good | `a_0.pzl` (Founding the Third Path, Fatal Push copy gets Revolt) |
| 2 | rulesguru-8a1a7ff86da79097 | board | maybe | not drafted: the defender (opponent side) chooses when Melira enters; for the attacker Melira's poison immunity hides the layer result |
| 3 | rulesguru-6627b11b70f8a544 | procedure | no | Mindslaver turn control and the choice to skip a turn; no single-turn board |
| 4 | rulesguru-1d20363aa5c0712b | board | no | duplicate of RP_119 (Blood Operative triggers on the surveil that put it there) |
| 5 | rulesguru-09aea431bcd09bac | board | no | replacement order (Forge never offers it); the count is fixed, no solver choice |
| 6 | rulesguru-7351f3023fa24a1b | legality | no | emerge sacrifice vs mana ability order; Forge offers only legal casts |
| 7 | rulesguru-9dc8633c30d3fec3 | legality | no | activation in cleanup; Forge gives no cleanup priority without a trigger |
| 8 | rulesguru-6517cfe8637a7da0 | board | maybe | `a_7.pzl` (Urza's Saga survives under Magus; three turns, weak misread line) |
| 9 | rulesguru-ba2da7f391d32917 | board | maybe | `a_6.pzl` (Rune of Flight on Magebane Armor; Forge timestamp tie, see weakness) |
| 10 | rulesguru-36a386d145200408 | board | good | `a_5.pzl` (Starfield of Nyx animates Iroas as a 4/4) |
| 11 | rulesguru-9bed756340b50e61 | board | no | +0/+2 and -0/-2 counters do not change any outcome a solver chooses; P/T same either reading |
| 12 | rulesguru-bd94082f9a2d54a7 | board | good | `a_4.pzl` (dredge the Chains replacement draw three times) |
| 13 | rulesguru-a320578e3d81f127 | board | good | `a_1.pzl` (Mystic Confluence counter x3 triggers Baral once) |
| 14 | rulesguru-ca096ee3f6809667 | procedure | no | pregame actions (Gemstone Caverns, Chancellor); not a puzzle state |
| 15 | rulesguru-fbdbdc5d382a4843 | board | maybe | not drafted: Radiate copies of a modal spell with Omniscience; heavy setup, no clear misread line the solver makes |
| 16 | rulesguru-049c905a00e915b7 | board | good | `a_2.pzl` (Eel Umbra first, Incendiary Flow's exile waits) |
| 17 | rulesguru-884582a76bceb635 | board | no | Day's Undoing ends up in exile whatever the solver does; no choice |
| 18 | rulesguru-c5748fb255f7ecba | board | no | replacement order of two Chains (Forge never offers the order); no solver choice |
| 19 | rulesguru-1b7a4d73a5422ac2 | legality | no | Aluren with an MDFC; Forge offers only legal casts |
| 20 | rulesguru-a149dbd33b18e5ea | board | maybe | `a_8.pzl` (Snake Umbra vs Anger of the Gods; same rule as a_2, solver-side board) |
| 21 | rulesguru-cd918eb5b1bff55e | legality | no | casting in cleanup under Sphinx's Decree; legality |
| 22 | rulesguru-51a1b148dd998256 | multiplayer | no | an owner leaving a multiplayer game |
| 23 | rulesguru-77b623b9af902bb5 | board | maybe | not drafted: mutate must be built in-puzzle (disturb + mutate, 8 mana) and no misread line differs in play |
| 24 | rulesguru-de8ccd8d99eb0a80 | legality | no | Reflecting Pool's colours are the menu Forge computes; the only failing line is not playing the land, which any solver does for River of Tears anyway |
| 25 | rulesguru-0578b7da4f9dab72 | board | no | replacement order, result fixed (two Angels), no solver choice |
| 26 | rulesguru-22bc420636904872 | board | maybe | not drafted: the opponent casts and attacks; sacrificing Forge-Tender later also works, so no misread line fails |
| 27 | rulesguru-65cfd257725aa3fa | legality | no | linked abilities: Forge never offers Quicksilver's return with no legal target |
| 28 | rulesguru-2ce35ae968dea107 | board | good | `a_3.pzl` (the Saga's end is a sacrifice: Smothering Abomination draws) |

Drafted: 9 (6 good, 3 maybe). Not drafted: 20.

---

## 1. #1 - Revolt is checked on resolution (a_0)

- question_id: `rulesguru-4f88642d6e0a25e4`
- Why it holds: Fatal Push checks mana value only as it resolves. A copy cast during the chapter III ability is put
  on the stack while the ability resolves; then state-based actions sacrifice the Saga (lore 3, no chapter ability on
  the stack), so when Push resolves a permanent you controlled has left: Revolt destroys the 4-drop.
- Forge check: Fatal Push `A:SP$ Destroy | ValidTgts$ Creature | ConditionDefined$ Targeted | ConditionPresent$
  Creature.cmcLEX`, `SVar:X:Count$Revolt.4.2` (evaluated on resolution; `AbilityUtils` "Revolt" reads
  `player.hasRevolt()`). Founding the Third Path `K:Chapter:3:...,DBCopy`; `DBCopy` exiles target instant/sorcery from
  your graveyard, `DBPlay ... Defined$ Remembered | Optional$ True | CopyCard$ True` (no `WithoutManaCost`, so the
  copy costs {B}: one Swamp). `GameAction.stateBasedAction_Saga` sacrifices a Saga at its final chapter once
  `!hasSourceOnStack(c, isChapter)`; the Push copy is not a chapter ability. Acolyte of Affliction is {2}{B}{G} (MV 4).
  Starting at UPKEEP with `LORE=2` follows RP_177/RP_197; the lore counter comes at the precombat main phase.

```
(see a_0.pzl)
[state] turn=2, p0 UPKEEP; p0: Founding the Third Path|Counters:LORE=2; Swamp; graveyard Fatal Push
p1: Acolyte of Affliction; Grizzly Bears
[acceptance]
count p1 Graveyard "Acolyte of Affliction" = 1
count p1 Battlefield "Grizzly Bears" = 1
```

- Solution: at the main phase chapter III triggers; target Fatal Push in the graveyard, cast the copy (pay {B}),
  target Acolyte of Affliction. The Saga is sacrificed before Push resolves: Revolt, Acolyte destroyed.
- Misread lines: "Revolt is off (the Saga is still there) / MV is checked on targeting" -> Push the Grizzly Bears
  (legal, MV 2, dies; acceptance fails on both lines); or decline to cast the copy.
- Uniqueness: one Push, two possible targets (no solver creatures); only Acolyte satisfies line 1, and line 2 pins
  the Bears. Chapter III's "up to one" target and the optional cast must both be taken. Pass: nothing cast, fails.
- Weakness: the second acceptance line is there only to make "Push the Bears" fail visibly; it is implied by line 1
  (one Push). Opponent has no actions.

---

## 2. #13 - Countering three times triggers Baral once (a_1)

- question_id: `rulesguru-a320578e3d81f127`
- Why it holds: the first counter mode moves the spell to the graveyard; the repeated modes find no spell to counter,
  so Baral triggers once. Library count separates the lines: counter + draw + draw + one loot = 3 cards drawn.
- Forge check: Mystic Confluence `A:SP$ Charm | Choices$ DBCounter,DBReturn,DBDraw | CharmNum$ 3 | CanRepeatModes$
  True`; `DBCounter ... UnlessCost$ 3 | UnlessPayer$ ThisTargetedController` (the solver is the controller and has no
  mana left: Baral `ReduceCost ... Amount$ 1` makes Confluence {2}{U}{U}, all four Islands). Baral `T:Mode$ Countered
  | ValidCause$ SpellAbility.YouCtrl | ValidSA$ Spell`, `TrigLoot: AB$ Discard ... Cost$ Draw<1/You>` (optional
  draw, then discard). Whether Forge's second counter instance on a card already in the graveyard fires `Countered`
  again is exactly what the gate tests: if it does, "counter, counter, draw" also reaches 3 and the uniqueness search
  reports a second line (a Forge disagreement, not a board error).

```
(see a_1.pzl)
[state] p0 MAIN2; Baral, Chief of Compliance; 4 Islands; hand Memnite, Mystic Confluence; library 6 Dreadmaw
[acceptance]
count p0 Graveyard "Memnite" = 1
count p0 Library * = 3
```

- Solution: cast Memnite, hold priority, cast Mystic Confluence choosing counter (Memnite), draw, draw; do not pay
  {3}; Baral's trigger: draw, discard a Dreadmaw. Library 6 - 3 = 3.
- Misread lines: counter x3 ("three counters, three loots"): library 5. counter x2 + draw: 4. counter + bounce Baral +
  draw: 4. draw x3 without Memnite on the stack: library 3 but Memnite not in the graveyard.
- Uniqueness: Confluence's only spell target is Memnite; the discard is among identical Dreadmaws. Declining the loot
  leaves 4. Pass: nothing cast, fails.
- Weakness: if Forge records mode order as chosen (counter, draw, draw vs draw, counter, draw), the uniqueness search
  may call reorderings a second line; read `uniqueness_difference` before rejecting.

---

## 3. #16 - Umbra armor before the exile replacement (a_2)

- question_id: `rulesguru-049c905a00e915b7`
- Why it holds: Incendiary Flow's lethal damage leads to destruction; umbra armor replaces the destruction, so the Elf
  stays (damage removed, Eel Umbra destroyed). Flow's "if it would die this turn, exile it instead" lasts the turn, so
  a later death (Disfigure, toughness 0, which umbra armor does not stop) exiles it. Disfigure first kills the 2/2
  through toughness with no exile effect yet: graveyard.
- Forge check: Incendiary Flow `SP$ DealDamage | NumDmg$ 3 | RememberDamaged$ True | ReplaceDyingDefined$
  Remembered.Creature` (a this-turn dies replacement on the damaged creature). Eel Umbra `K:Umbra armor`, `+1/+1`.
  Disfigure `Pump -2/-2`. Willow Elf 1/1. RP_036 already uses `Eel Umbra|AttachedTo:1` on the opponent's side; it
  tests sacrifice vs destroy, not this ordering.

```
(see a_2.pzl)
[state] p0 MAIN2; 2 Mountains, 2 Swamps; hand Incendiary Flow, Disfigure; p1 Willow Elf|Id:1; Eel Umbra|AttachedTo:1
[acceptance]
count p1 Exile "Willow Elf" = 1
```

- Solution: Incendiary Flow on Willow Elf (umbra destroyed, Elf 1/1 survives), then Disfigure on Willow Elf: it dies,
  Flow's replacement exiles it.
- Misread lines: "Flow's exile applies first" -> Flow alone (Elf survives). Disfigure first (Elf to the graveyard),
  then Flow with nothing to hit. Disfigure in response to Flow: graveyard.
- Uniqueness: two spells, one creature; only Flow-then-Disfigure on the Elf exiles it. Two Swamps so no payment of
  Flow strands Disfigure. Pass: Elf stays, fails.
- Weakness: a solver who casts everything in hand in card order happens to win.

---

## 4. #28 - A finished Saga is sacrificed (a_3)

- question_id: `rulesguru-2ce35ae968dea107`
- Why it holds: the Saga's removal after chapter III is a sacrifice (CR 714.4); with Opalescence it is a creature, so
  Smothering Abomination's "whenever you sacrifice a creature, draw" triggers. So the upkeep sacrifice should be
  Memnite, keeping the Saga for its own (free) sacrifice draw.
- Forge check: `GameAction.stateBasedAction_Saga` adds the Saga to `sacrificeList`, then `sacrifice(sacrificeList,
  ...)` (a real sacrifice, fires `Sacrificed`). Smothering Abomination `T:Mode$ Phase | Phase$ Upkeep ... Sacrifice |
  SacValid$ Creature` and `T:Mode$ Sacrificed | ValidCard$ Creature.YouCtrl ... Draw 1`. Opalescence `Affected$
  Enchantment.nonAura+Other | AddType$ Creature | SetPower/SetToughness$ CardManaCost` (Torment 4/4). Chainer's
  Torment III: X/X token, X = half your life (10), deals X to you (20 -> 10, safe). Lore counters go on at the main
  phase (`PhaseHandler` line ~286, `isSaga() && hasChapter()`). Starting at UPKEEP fires upkeep triggers (RP_197).

```
(see a_3.pzl)
[state] turn=2, p0 UPKEEP; Smothering Abomination; Chainer's Torment|Counters:LORE=2; Opalescence; Memnite; library 6
[acceptance]
count p0 Library * = 3
```

- Solution: sacrifice Memnite to the upkeep trigger (draw 1), draw step (1), chapter III resolves, the Saga is
  sacrificed (draw 1). Library 6 - 3 = 3.
- Misread line: "the Saga just goes to the graveyard, it isn't sacrificed" -> sacrificing the Torment at upkeep looks
  equal and avoids 10 damage: draws 2, library 4. Sacrificing Abomination: no later draw, library 4 or 5.
- Uniqueness: the only solver choice is the upkeep sacrifice (three creatures); only Memnite reaches 3. Combat and the
  token do not draw.
- Weakness: the pass arm still has to answer the forced sacrifice; if its fixed option is Memnite it solves. Abomination
  is listed first so a first-option pass sacrifices it; check the pass arm is rejected.

---

## 5. #12 - Dredge the draw Chains gives you (a_4)

- question_id: `rulesguru-bd94082f9a2d54a7`
- Why it holds: each Urban Evolution draw is replaced by Chains (discard, then draw); the new draw is a separate draw
  Chains does not touch again (CR 614.5) but dredge can replace. Discard Loam, dredge it back, three times: nine cards
  milled, Loam in hand.
- Forge check: Chains `R:Event$ Draw | ReplaceWith$ DiscardOne | NotFirstCardInDrawStep$ True`; `DiscardOne` discards
  (Mandatory, TgtChoose), then `DBDraw` draws if a card was discarded. Life from the Loam `K:Dredge:3`. Urban Evolution
  draws three. With Loam in hand dredge cannot apply to the original draw (Loam not in the graveyard), so every draw
  goes through Chains, and the inner draw is the dredge point. If Forge does not let dredge replace the inner draw,
  or reapplies Chains to it, the search will not find 3: record as a Forge disagreement.

```
(see a_4.pzl)
[state] p0 MAIN2; Chains of Mephistopheles; 2 Forests, 3 Islands; hand Urban Evolution, Life from the Loam; library 12
[acceptance]
count p0 Hand "Life from the Loam" = 1
count p0 Library * = 3
```

- Solution: cast Urban Evolution; for each of three draws, discard Life from the Loam to Chains, then dredge 3.
  Library 12 - 9 = 3, Loam in hand.
- Misread lines: "the Chains draw can't be dredged / you only dredge once": decline a dredge (draw a Dreadmaw), later
  discards choose between Loam and Dreadmaws; every such line leaves more than 3 or Loam not in hand.
- Uniqueness: hand holds only Loam at each Chains discard on the correct line; any declined dredge loses 2+ cards of
  milling. No mana left for Loam itself. Pass: library 12, fails.
- Weakness: the misread is less a wrong play than a missed one (a solver offered "dredge?" may just accept).

---

## 6. #10 - Starfield animates Iroas (a_5)

- question_id: `rulesguru-36a386d145200408`
- Why it holds: both effects are in the type layer; Starfield's is later, so Iroas is a creature, base 4/4 (MV 4),
  and it can attack (controlled since the turn began).
- Forge check: Iroas `S:Mode$ Continuous | AffectedDefined$ Self | RemoveType$ Creature | CheckSVar$ X | SVarCompare$
  LT7` (devotion 2 here). Starfield `IsPresent$ Enchantment.YouCtrl | PresentCompare$ GE5 | Affected$
  Enchantment.YouCtrl+nonAura+Other | SetPower$ AffectedX | AddType$ Creature`. Forge sorts statics by `getTimestamp()`
  (host layer timestamp), Starfield is newer. Enchantment count: Iroas + 3 Wild Growth (Auras, not animated) +
  Starfield = 5. Wild Growth `TapsForMana` adds {G}: Plains gives W G G, Mountain R G = 5 mana.

```
(see a_5.pzl)
[state] p0 MAIN1; Iroas; Plains|Id:1 with 2 Wild Growth; Mountain|Id:2 with 1 Wild Growth; hand Starfield, Bolt; p1 life 4
[acceptance]
life p1 <= 0
```

- Solution: cast Starfield of Nyx (all five mana), attack with Iroas (4/4 menace): 4 damage.
- Misread line: "Iroas's own effect keeps it a noncreature (devotion 2)" -> Lightning Bolt for 3 (opponent at 1);
  after Bolt only four mana remain, so Starfield is no longer castable.
- Uniqueness: Starfield and Bolt cannot both be cast; Bolt alone falls short. Pass fails.
- Weakness: the P/T half of the question (4/4, not printed 7/4) is not tested: 7/4 would also be lethal.

---

## 7. #9 - Rune of Flight's grant applies after the Armor (a_6)

- question_id: `rulesguru-ba2da7f391d32917`
- Why it holds (judge): the flying granted through Rune of Flight applies after Magebane Armor's "loses flying", so the
  equipped creature flies.
- Forge check: Magebane Armor `S:... AffectedDefined$ Equipped | AddPower$ 2 | AddToughness$ 4 | RemoveKeyword$
  Flying`, `K:Equip:2`. Rune of Flight `S:... Affected$ Equipment | AddStaticAbility$ RuneFlightST` (`Equipped creature
  has flying`). Both statics then live on the Armor; `StaticAbility.getTimestamp()` returns the host's layer
  timestamp, so they tie and fall to list order (printed first, granted after), which gives flying. Not verified by a
  run; if Forge orders them the other way the search finds no line, which is a Forge disagreement.

```
(see a_6.pzl)
[state] p0 MAIN1; Tobias Andrion; Magebane Armor|Id:1; Rune of Flight|AttachedTo:1; Island; Plains; hand Jump
p1 life 6, Wall of Stone
[acceptance]
life p1 <= 0
```

- Solution: equip Magebane Armor to Tobias (both lands), attack: 6/8 flier, Wall of Stone cannot block, 6 damage.
- Misread line: "the Armor removes flying (it was re-timestamped on equip)" -> Jump on unequipped Tobias, 4 in the air
  (opponent at 2), and equip is no longer affordable. Attacking unequipped on the ground: blocked or 4.
- Uniqueness: equip and Jump cannot both be paid; only the equip line deals 6. Pass fails.
- Weakness: the CR reasoning after the equip re-timestamp (613.7e) is subtle; the judge's answer and Forge's tie-break
  agree, but the gate must confirm.

---

## 8. #8 - A chapterless Saga stays (a_7)

- question_id: `rulesguru-6517cfe8637a7da0`
- Why it holds: under Magus of the Moon, Urza's Saga loses its chapter abilities, so no lore counter is added and the
  sacrifice rule (only for Sagas with chapter abilities) does not apply.
- Forge check: Magus `Affected$ Land.nonBasic | AddType$ Mountain | RemoveLandTypes$ True` (loses its rules-text
  abilities, including `K:Chapter`). `PhaseHandler` adds lore only `if (c.isSaga() && c.hasChapter())`;
  `stateBasedAction_Saga` returns early on `!c.hasChapter()`. Without Magus, turn 3's lore counter triggers chapter III
  (no artifact to find) and the Saga is sacrificed.

```
(see a_7.pzl)
[state] turn=1, p0 MAIN2, Turns:3; Urza's Saga|Counters:LORE=2; 3 Mountains; hand Magus of the Moon
[acceptance]
count p0 Battlefield "Urza's Saga" = 1
```

- Solution: cast Magus of the Moon now (three Mountains). On turn 3 the Saga, a Mountain, gets no counter and stays.
- Misread line: "a Saga with no chapters left is sacrificed at once" -> keep Magus in hand; the Saga is sacrificed on
  turn 3.
- Uniqueness: one spell, castable only on turn 1 before the chapter (turn 3's lore counter comes before any main-phase
  cast). Pass fails.
- Weakness: three turns (opponent's turn 2 is empty but slows the search); the setup Saga lacks the abilities chapters
  I and II would have granted (no Construct line to tempt the misread solver); the misread solver has no better
  alternative, only inaction.

---

## 9. #20 - Snake Umbra outranks Anger of the Gods (a_8)

- question_id: `rulesguru-a149dbd33b18e5ea`
- Why it holds: as a_2, from the solver's side: the umbra armor replaces the destroy, so Envoy is not exiled by Anger.
- Forge check: Anger of the Gods `SP$ DamageAll | NumDmg$ 3 | RememberDamaged$ True | ReplaceDyingDefined$
  Remembered`. Snake Umbra `K:Umbra armor`, +1/+1 (Envoy 3/2, 3 damage is lethal). Hill Giant 3/3.

```
(see a_8.pzl)
[state] p0 MAIN2; Expedition Envoy|Id:1; Snake Umbra|AttachedTo:1; 3 Mountains; hand Anger of the Gods, Lightning Bolt
p1: Hill Giant x2
[acceptance]
count p1 Exile "Hill Giant" = 2
count p0 Battlefield "Expedition Envoy" = 1
```

- Solution: cast Anger of the Gods: both Giants exiled, Snake Umbra destroyed, Envoy stays.
- Misread line: "Anger exiles my Envoy too" -> Bolt one Giant instead (then Anger is unaffordable).
- Uniqueness: Anger and Bolt cannot both be cast; only Anger exiles both. Pass fails.
- Weakness: same rule as a_2 (near-duplicate; keep one if the suite wants one per rule), and Anger is the only line
  that touches both Giants, so a solver can win without knowing the rule.
