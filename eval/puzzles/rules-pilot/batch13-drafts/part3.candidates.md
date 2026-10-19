# Part 3 screen: candidates rebuilt from "not buildable" drops

Rows screened: 121 (`part3.jsonl`). Candidates: 5. Puzzle numbers 223-227 are placeholders; renumber when building.
Every card named was checked with `grep -rl "^Name:<card>$" /root/forge/forge-gui/res/cardsfolder` and its script
read. Engine behaviour on the deciding point was traced in the Forge source (cited per candidate). Nothing was run.

---

## 1. Triage line 164: Bloodletter doubles the loss, not the damage, so lifelink gains 3

- question_id: `cranial-insertion-01f70dbb23033620`
- Why the drop does not hold: the drop said "attacking is the only play", because the question's board has one
  lifelink attacker. Give the solver a second, optional life-gain spell and an exact life target, and the rule decides
  whether that spell is cast. Misread, Bolt alone already reaches the goal and the second spell overshoots it.
- Forge check: `GameAction.dealDamage` gains lifelink life from `sum`, the damage dealt (3). Bloodletter's
  `R:Event$ LifeReduced ... LoseTwice` doubles only the life the opponent loses, later in `processDamage`. Soulfire
  Grand Master grants Lifelink to instants and sorceries on the stack. Healing Salve is a Charm (gain 3 / prevent 3).

```
[metadata]
Name:Rules pilot 223 - Lifelink counts the damage
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Bloodletter of Aclazotz, Soulfire Grand Master, a Mountain and a Plains, and have Lightning Bolt and Healing Salve in hand. End the turn at exactly 26 life with the opponent at 14 life.
Rule:cranial-insertion-01f70dbb23033620 | Bloodletter of Aclazotz doubles the life the opponent loses, not the damage dealt (CR 120.3a, 614.1a), and lifelink gains life equal to the damage dealt (CR 702.15b): a 3-damage lifelink Bolt gains 3, not 6
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Lightning Bolt;Healing Salve
p0battlefield=Bloodletter of Aclazotz;Soulfire Grand Master;Mountain;Plains
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
life p0 = 26
life p1 = 14
```

- Solution: Lightning Bolt the opponent (lifelink from Soulfire: p0 gains 3; Bloodletter doubles the loss: p1 20 -> 14),
  and Healing Salve's gain mode targeting yourself (+3): 26 and 14.
- Misread line: "lifelink gains what the opponent lost", so Bolt alone gives +6 = 26. The misreader stops (Salve
  would make 29) and ends at 23.
- Uniqueness: two lands, so only Bolt and Salve can be cast (Soulfire's buyback ability costs 4). Bolt anywhere but
  the opponent leaves p1 at 20. Salve on the opponent or in prevent mode leaves p0 short. Doing nothing: 20/20. The
  two spells can be cast in either order. That is the same unordered pair as RP_218; if the uniqueness search calls
  the two orders different lines, accept it as RP_218 was accepted.

---

## 2. Triage line 331: under Soul-Scar Mage, Pillar of Flame's exile clause never applies

- question_id: `rulesguru-d6e715d7bd3a22b3`
- Why the drop does not hold: "no solver choice" holds only on the question's board. Make the goal "exile the
  Merfolk" and give the solver a way to remove its own Soul-Scar Mage first. The rule then decides whether the solver
  kills its own creature before casting Pillar, or casts Pillar straight away.
- Forge check: Soul-Scar Mage's DamageDone replacement runs `ReplacementHandler.runSingleReplaceDamageEffect`, which
  takes the result `default -> damageMap.remove(source, target)`. Pillar's `RememberDamaged` then remembers only
  entities still in `damageMap` (`GameAction.dealDamage`), so no exile replacement is created and the Merfolk dies
  to the -1/-1 counters into the graveyard. That matches the judge's answer.

```
[metadata]
Name:Rules pilot 224 - Dealt no damage this way
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Soul-Scar Mage and two Mountains, and have Flame Slash and Pillar of Flame in hand. The opponent controls Merfolk of the Pearl Trident. End the turn with Merfolk of the Pearl Trident in exile.
Rule:rulesguru-d6e715d7bd3a22b3 | Soul-Scar Mage replaces the damage with -1/-1 counters, so the replaced damage is never dealt (CR 614.6, 120.3) and no creature was "dealt damage this way" for Pillar of Flame's exile clause: the Mage must be gone before Pillar resolves
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Flame Slash;Pillar of Flame
p0battlefield=Soul-Scar Mage;Mountain;Mountain
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1battlefield=Merfolk of the Pearl Trident
p1library=Plains;Plains;Plains
[acceptance]
count p1 Exile "Merfolk of the Pearl Trident" = 1
```

- Solution: Flame Slash your own Soul-Scar Mage. Prowess makes it 2/3, and 4 damage kills it. Then Pillar of Flame
  the Merfolk: 2 damage, it would die, so it is exiled.
- Misread line: Pillar of Flame the Merfolk straight away, believing it was "dealt damage this way". It gets two -1/-1
  counters instead and goes to the graveyard.
- Uniqueness: both spells are sorceries, so the order is forced and Pillar cannot be answered with Flame Slash. That
  is why Flame Slash, and not Lightning Bolt, kills the Mage: Bolt cast in response to Pillar (before Pillar's prowess
  trigger resolves) would be a second line. Pillar on the Mage first fails, because prowess makes it 2/3 and it
  survives. Flame Slash on the Merfolk puts it in the graveyard. Doing nothing leaves it on the battlefield.

---

## 3. Triage line 546: soulbond needs the other creature when the soulbond creature enters

- question_id: `rulesguru-5842753004989acb`
- Why the drop does not hold: the question's Angel's Tomb animates by a trigger, so the solver chooses nothing. With
  Mutavault the solver picks when the land becomes a creature, before or after Wolfir Silverheart enters, and only
  "before" pairs them.
- Forge check: `CardFactoryUtil` soulbond is two ChangesZone (enters) triggers with
  `IsPresent$ Creature.Other+YouCtrl+!Paired`. Animating Mutavault later is not a zone change and fires neither
  trigger.

```
[metadata]
Name:Rules pilot 225 - Pair as it enters
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Mutavault and six Forests, and have Wolfir Silverheart in hand. The opponent controls no creatures. End the turn with the opponent at 14 life.
Rule:rulesguru-5842753004989acb | Soulbond triggers only when the soulbond creature or another creature enters (CR 702.95a, 603.4): a land animated after Wolfir Silverheart entered can never be paired, so Mutavault must already be a creature as Silverheart enters
[state]
turn=1
activeplayer=p0
activephase=MAIN1
p0life=20
p0hand=Wolfir Silverheart
p0battlefield=Mutavault;Forest;Forest;Forest;Forest;Forest;Forest
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
life p1 = 14
```

- Solution: animate Mutavault ({1}), then cast Wolfir Silverheart (5 Forests) and pair it with Mutavault. Mutavault
  is 6/6 and attacks for 6: 20 -> 14. Silverheart is summoning-sick.
- Misread line: cast Silverheart first and animate Mutavault later (at combat), expecting soulbond to pair them. No
  trigger, so Mutavault is a 2/2: the opponent ends at 18.
- Uniqueness: the only attacker is Mutavault, and +4/+4 comes only from the pair. A variant that applies the same rule
  also solves: cast Silverheart and animate Mutavault in response, so Mutavault is a creature as Silverheart enters.
  If the uniqueness search flags it, accept it as the same line or note it. Tapping Mutavault for mana makes it unable
  to attack, so the six Forests are exactly enough. Doing nothing: 20.

---

## 4. Triage line 665: Doubling Season does not double the loyalty cost, but does double proliferate

- question_id: `rulesguru-a751678cf7a59ea6`
- Why the drop does not hold: the drop said "+2 is the only way up". Add a proliferate spell and an exact loyalty
  target. Misread (+2 becomes +4), the +2 alone hits the target and proliferate overshoots, so the misreader skips a
  legal play the solution needs.
- Forge check: Doubling Season's AddCounter replacement has `EffectOnly$ True` (`ReplaceAddCounter.canReplace`).
  Loyalty costs go through `CostPutCounter.triggerCounterPutAll(ability, effect)` with the cost flag, so they are not
  doubled. Proliferate is an effect, so it is doubled.

```
[metadata]
Name:Rules pilot 226 - The cost is not doubled
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Doubling Season, Sorin, Vengeful Bloodlord (4 loyalty) and three Islands, and have Steady Progress in hand. End the turn with exactly 8 loyalty counters on Sorin, Vengeful Bloodlord.
Rule:rulesguru-a751678cf7a59ea6 | Doubling Season doubles counters put by effects, not counters put as a loyalty cost (CR 606.4, 609.1): Sorin's +2 adds 2, and a proliferate (an effect, CR 701.34a) adds 2 more
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Steady Progress
p0battlefield=Doubling Season;Sorin, Vengeful Bloodlord|Counters:LOYALTY=4;Island;Island;Island
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
counters p0 Battlefield "Sorin, Vengeful Bloodlord" LOYALTY = 8
```

- Solution: Sorin +2, targeting the opponent (4 -> 6), and Steady Progress proliferating Sorin (one counter, doubled
  to 2: 8). Either order works.
- Misread line: "Doubling Season makes the +2 a +4", so +2 alone reaches 8 and proliferate would make 10. The
  misreader activates +2 only and ends at 6.
- Uniqueness: one loyalty activation per turn, and the -X has no legal target (empty graveyard). Proliferate alone
  gives 6, and doing nothing gives 4. As in candidate 1, the two orders are one unordered pair.
- Distinct from RP_011/RP_012, which are about Doubling Season and the loyalty a permanent enters with.

---

## 5. Triage line 917 (927 is the same rule): only the newest Role from one controller stays

- question_id: `wizards-official-card-rulings-ad7bf0d88d294628` (alt: `wizards-official-card-rulings-9500fa5d828bfc46`)
- Why the drop does not hold: "no solver choice" holds only for a single Role. Two cursed Wurms, one cursed by a Role
  you control and one by a Role the opponent controls, make the "same controller" clause decide the target of
  Monstrous Rage. Only the Role you control is put into the graveyard.
- Forge check: `GameAction.stateBasedAction_Role` groups Roles by controller (`filterControlledBy`) and keeps the
  newest per player. Monstrous Rage creates `role_monster` attached to its target. Tokens are set up with
  `T:<script>` (Forge built-in puzzles use `T:c_4_4_a_construct`). `AttachedTo:` resolves through a global id map
  (`GameState.idToCard`), so attaching across players works.
- Load risk: `T:role_cursed` in a rules-pilot state has not been tried. Smoke-load it first (an unknown token stops
  the load with "non-existent token").

```
[metadata]
Name:Rules pilot 227 - Your Role, not theirs
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control two Craw Wurms and a Mountain. One Craw Wurm wears a Cursed Role you control; the other wears a Cursed Role the opponent controls. You have Monstrous Rage in hand and the opponent controls no creatures. End the turn with the opponent at 10 life.
Rule:wizards-official-card-rulings-ad7bf0d88d294628 | If a permanent has more than one Role controlled by the same player, all but the newest go to the graveyard as a state-based action (CR 704.5y, 303.7); a Role another player controls stays: Monstrous Rage frees only the Wurm whose Cursed Role you control
[state]
turn=1
activeplayer=p0
activephase=MAIN1
p0life=20
p0hand=Monstrous Rage
p0battlefield=Craw Wurm|Id:1;T:role_cursed|AttachedTo:1;Craw Wurm|Id:2;Mountain
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1battlefield=T:role_cursed|AttachedTo:2
p1library=Plains;Plains;Plains
[acceptance]
life p1 = 10
```

- Solution: Monstrous Rage on Wurm 1, whose Cursed Role you control. The SBA puts that Cursed Role into the graveyard,
  so the Wurm is 6/4 +1/+1 +2/+0 = 9/5 trample. Attack with both: 9 + 1 = 10, and the opponent is at 10.
- Misread lines: (a) "Roles stack": Rage on either Wurm gives 4/2 + 1/1 = 5, and the opponent ends at 15. (b) "any
  new Role removes the old one": a coin flip, and Rage on Wurm 2 keeps the opponent's Cursed Role (4/2 + 1/1 = 5): 15.
- Uniqueness: one spell and one Mountain. Rage on Wurm 2 gives 15, and no Rage gives 18. Rage can be cast in main 1 or
  in combat before damage. Those are the same action, but the uniqueness search may count the timing.
- Caveat: Monstrous Rage's reminder text says "If you control another Role on it". That makes the misread less
  likely; the opponent-controlled Role is what keeps the test real.

---

## Rows looked at closely and kept dropped

- 16 (Awakening / Simic Ascendancy): a choice can be built (Awakening, then Battlegrowth on the animated land, with
  growth = 1). But Forge runs CounterAddedOnce from the counter table after the whole spell resolves, so the land may
  already be a creature (an engine-fidelity risk). The misread also only skips a play.
- 79 (Ob Nixilis / Rakdos Charm): Rakdos Charm uses `DamageMap$ True`, which is one event, so Forge agrees. But every
  board with an alternative (Lava Dart and its flashback) solves under both readings. Ob's exiled card also adds
  plays.
- 67 (cascade / mutate MV), 789 (Attuned Hunter, one trigger per event), 974 (Annie Joins Up / replacement): each
  design tied (both readings find a solving line) or only made the misreader skip with no line left.
- All other rows: the drop reason holds (an opponent's action, more than one turn, replacement or trigger order
  Forge does not offer, an engine-enforced result, or a near-duplicate of a built RP).

## Summary

Five candidates from 121 rows:
- 164: Bloodletter doubles the loss, not the lifelink damage (Bolt plus Healing Salve to 26).
- 331: Soul-Scar Mage stops Pillar of Flame's exile (Flame Slash your own Mage first).
- 546: soulbond needs the creature as the soulbond creature enters (animate Mutavault before Silverheart).
- 665: Doubling Season does not double Sorin's +2 cost, but does double proliferate (+2 and Steady Progress to 8).
- 917/927: only the newest Role from the same controller goes (Monstrous Rage on the Wurm whose Cursed Role you
  control; the T:role_cursed setup needs a smoke load).
