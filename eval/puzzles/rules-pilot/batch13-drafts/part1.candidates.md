# part1 screen: candidates that the drop reason does not rule out

Input: /tmp/puzzle-scratch/screen/part1.jsonl (121 dropped rows). I read all 121 and kept four. For every other
row the drop reason holds. Most need the opponent to act, depend on the order replacements apply (Forge never
offers that), are random, are forced by the engine, or duplicate an existing RP. For a few the misread only
makes a solver overrate a line that the right reading also accepts, which gives no clean test: 13 (Panharmonicon
and Simic Ascendancy), 6 (morph), 76 (Sculpting Steel), 188 (Custodi Soulbinders), 971 (Doorkeeper Thrull) and
764/954 (Agate Assault, Obliterating Bolt). Under the misread, every line in those either looks the same as the
right one or looks impossible, so a misreader's default play passes about as often as it fails.

Every card named below is in /root/forge/forge-gui/res/cardsfolder (checked with `grep -rl "^Name:<card>$"`).
I read each card's script; the notes below give the parts that matter.

---

## 1. Triage line 429: rulesguru-f9baca61ea79bba9

**Rule.** Equipping a creature to the creature it is already on does nothing, so the Equipment gets no new
timestamp (CR 701.3b, 613.7e). Moving it to another creature and back does give it a new one.

**Why the drop reason fails.** The drop said that equipping another creature is "the obvious play under either
reading". This board takes that play away. Moat stops creatures without flying from attacking, and the only other
creature is summoning sick, so Eager Cadet itself has to fly. The solver's decision is then: re-equip in place
(cheap, and what the misread says works), or spend twice as much to move the Windbreaker away and back (the only
thing that works). The rule decides that choice.

**Forge.** `Card.attachToEntity` returns without doing anything when the target is the creature the Equipment is
already on. Otherwise it calls `setLayerTimestamp(getNextTimestamp())` with the comment "613.7e". Equip's
`ValidTgts` is `Creature.YouCtrl` (CardFactoryUtil), so re-equipping in place is offered. At setup,
`GameState.handleCardAttachments` walks a `HashMap<Card,Integer>` keyed by card id (`GameEntity.hashCode` is the
id). p0's cards get lower ids, so the Windbreaker attaches before p1's Grounded, and Grounded ends up with the
newer timestamp. **Check at build time** that Eager Cadet starts without flying.

```
[metadata]
Name:Rules pilot NNN - Same creature, same timestamp
Goal:Survive
Turns:1
Difficulty:Medium
Description:Your Eager Cadet is equipped with Cursed Windbreaker (equip {3}) and enchanted by the opponent's Grounded, which came later. You also control Grizzly Bears, which came under your control this turn, and six Plains. The opponent controls Moat and is at 20 life. End the turn with the opponent at 19 life.
Rule:rulesguru-f9baca61ea79bba9 | Equipping a creature to the creature it is already on does nothing and gives no new timestamp (CR 701.3b, 613.7e), so Grounded still wins (CR 613.7); equip Grizzly Bears and then Eager Cadet again, and the Windbreaker's flying applies after Grounded
[state]
turn=1
activeplayer=p0
activephase=MAIN1
p0life=20
p0hand=
p0battlefield=Eager Cadet|Id:1;Cursed Windbreaker|AttachedTo:1;Grizzly Bears|SummonSick;Plains;Plains;Plains;Plains;Plains;Plains
p0library=Plains;Plains;Plains
p1life=20
p1battlefield=Grounded|AttachedTo:1;Moat
p1library=Plains;Plains;Plains
[acceptance]
life p1 = 19
```

**The solution.** Equip the Windbreaker to Grizzly Bears ({3}), then back to Eager Cadet ({3}). Attack with the
Cadet, which now has flying: 1 damage.

**The misread line.** "Re-equipping refreshes the timestamp": equip to the Cadet in place ({3}). The Cadet still
has no flying, Moat stops the attack, and the opponent stays at 20. A solver who thinks Grounded always wins sees
no line at all.

**Why the solution is unique.**
- Grizzly Bears is summoning sick, so it can never be the attacker.
- Six Plains pay for exactly two equips.
- The opponent has no creatures and no cards, so nothing blocks or answers.
- Doing nothing leaves the opponent at 20.

---

## 2. Triage line 597: rulesguru-4434799761610007

**Rule.** A nonbasic land that becomes a Mountain loses the abilities its rules text gives it (CR 305.7). It
keeps the effects of an ability that has already resolved, such as an animation, because those effects come from
that ability and not from the land's text (CR 305.7, 613.1d, 613.1f). Animate first; after the Moon arrives, the
animation ability is gone.

**Why the drop reason fails.** The drop said "no solver choice". There is one: whether to animate before Magus of
the Moon or after it. The opponent's Glacial Chasm prevents all damage to them until Magus turns it into a
Mountain, so Magus has to be on the battlefield before combat damage. Mutavault therefore has to be animated
before Magus resolves. The judge question itself is about animating Inkmoth Nexus in response to Magus. Infect
is replaced by Mutavault and life, because the acceptance grammar has no poison condition.

**Forge.**
- Magus is `AddType$ Mountain | RemoveLandTypes$ True`. `Card.hasRemoveIntrinsic` removes intrinsic abilities
  only (its comment cites CR 305.7). Mutavault's Animate effect adds types and P/T as changed traits, not
  intrinsic ones, so it survives.
- Glacial Chasm's prevention is an intrinsic `R:` replacement, so Magus removes it. Its "creatures you control
  can't attack" applies only to the opponent.

```
[metadata]
Name:Rules pilot NNN - Animate before the Moon
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Mutavault and four Mountains and have Magus of the Moon in hand. The opponent controls Glacial Chasm and is at 20 life. End the turn with the opponent at 18 life.
Rule:rulesguru-4434799761610007 | A nonbasic land that becomes a Mountain loses the abilities from its rules text, but not the effect of an animation ability that has already resolved (CR 305.7, 613.1d): animate Mutavault before Magus of the Moon arrives; afterwards the ability is gone
[state]
turn=1
activeplayer=p0
activephase=MAIN1
p0life=20
p0hand=Magus of the Moon
p0battlefield=Mutavault;Mountain;Mountain;Mountain;Mountain
p0library=Mountain;Mountain;Mountain
p1life=20
p1battlefield=Glacial Chasm
p1library=Plains;Plains;Plains
[acceptance]
life p1 = 18
```

**The solution.** Pay {1} from a Mountain to animate Mutavault. Cast Magus with the other three Mountains (or
animate in response to Magus). Glacial Chasm is now a Mountain with no prevention. Attack with the 2/2 Mutavault.

**The misread lines.**
- "Magus makes the animated Mutavault a plain Mountain again": the solver sees no line.
- "Order does not matter": the solver casts Magus first. Mutavault has no animation ability left, so nothing can
  attack.
- Attacking without Magus: the Chasm prevents the damage.

**Why the solution is unique, and two build risks.**
1. Animating in response to Magus is a second ordering of the same rule-correct play. If the uniqueness search
   compares the order of actions, it may report "multiple". Accept it, or record both orders as one solution
   class.
2. Mutavault can pay its own {1}. The seat must keep it untapped (`keep_untapped` or the mana-sources ask);
   confirm the prover's payment does not tap it.

Apart from those two points:
- Magus is summoning sick, so Mutavault is the only attacker.
- The mana is exact: {1} plus {2}{R} from four Mountains.
- Doing nothing leaves the opponent at 20.

---

## 3. Triage line 725: rulesguru-374a0d1fbb5f4ca8

**Rule.** Maraxus of Keld's power is a characteristic-defining ability that applies on the stack (CR 604.3a,
113.6a). Costs are paid before Sarkhan's Unsealing checks the cast (CR 601.2g-i), so Mountains tapped to pay for
Maraxus no longer count toward its power.

**Why the drop reason fails.** The drop said the deciding choice is which sources pay, and that payment is
automatic. On this board the choice is a spell instead: cast Seething Song first. It taps only three lands for
five red mana, which leaves seven permanents untapped after Maraxus is paid. Paying straight from lands leaves
five. No source choice is needed: the {4}{R}{R} comes from the pool plus one Mountain, either way.

**Forge.**
- Sarkhan's Unsealing: `SpellCast | ValidCard$ Creature.powerGE4+powerLE6` (4 damage to any target) and
  `Creature.powerGE7` (4 damage to each opponent and each creature and planeswalker they control).
- Maraxus: `CharacteristicDefining$ True`, `Count$Valid Artifact.untapped,Creature.untapped,Land.untapped` that
  you control.
- `MagicStack.add` runs `checkStaticAbilities` on every mana ability, so power is recomputed after the last tap.
  The SpellCast trigger then reads an LKI copy.
- **Check at build time:**
  - The CDA power is live on the stack, which the */* cards in hand suggest.
  - The seat's payment uses the floating RRRRR before it taps Mountains (ComputerUtilMana pays from the pool
    first).

```
[metadata]
Name:Rules pilot NNN - Maraxus counts after paying
Goal:Survive
Turns:1
Difficulty:Hard
Description:You control Sarkhan's Unsealing and eleven Mountains and have Maraxus of Keld and Seething Song in hand. The opponent controls Hill Giant and is at 20 life. End the turn with Hill Giant in the opponent's graveyard and the opponent at 16 life.
Rule:rulesguru-374a0d1fbb5f4ca8 | Maraxus of Keld's power is a characteristic-defining ability that applies on the stack (CR 604.3a), and costs are paid before Sarkhan's Unsealing checks the cast (CR 601.2g-i): Mountains tapped for Maraxus do not count, so ramp with Seething Song to cast it with power 7
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Maraxus of Keld;Seething Song
p0battlefield=Sarkhan's Unsealing;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain;Mountain
p0library=Mountain;Mountain;Mountain
p1life=20
p1battlefield=Hill Giant
p1library=Plains;Plains;Plains
[acceptance]
count p1 Graveyard "Hill Giant" = 1
life p1 = 16
```

**The solution.** Cast Seething Song (tap 3 Mountains, 8 untapped), which adds RRRRR. Cast Maraxus with the five
floating R plus one Mountain. Seven Mountains are untapped, so Maraxus is cast with power 7, and the
power-7-or-greater trigger deals 4 to the opponent (20 to 16) and 4 to Hill Giant (it dies).

**The misread line.** "Power counts the lands before I tap them": the solver casts Maraxus from six Mountains,
expecting power 11. Maraxus is actually power 5, so only the 4-to-any-target trigger happens. It can kill the
Giant or hit the opponent for 4, not both. Seething Song looks pointless under this reading, so the misreader
never casts it.

**Why the solution is unique.**
- With the pool floating, paying Maraxus entirely from lands leaves two untapped: no trigger.
- No other card does anything.
- Doing nothing leaves the Giant alive and the opponent at 20.

---

## 4. Triage line 950: wizards-official-card-rulings-cb4fb354a0d69fae

**Rule.** An Aura you do not control does not make your creature modified. Only Equipment, Auras you control and
counters do (CR 700.9).

**Why the drop reason fails.** The drop said "no solver choice". There is one if the payoff is Flame Discharge
(X damage, or X+2 if you controlled a modified creature as you cast it) and the only creature is under the
opponent's Pacifism. The misread makes Flame Discharge alone, with all the mana, look like the strongest line.
The real line spends a card and a mana on Battlegrowth first.

**Forge.** `Card.isModified` returns true if the card is equipped or has counters, or if it is enchanted by an
Aura whose controller is the card's controller (`isController(this.getController())`). Flame Discharge's bonus
reads `LastStateBattlefieldWithFallback Creature.modified+YouCtrl` at cast. Cross-side Aura setup is the
RP_098/RP_189 pattern.

```
[metadata]
Name:Rules pilot NNN - Their Aura is no modification
Goal:Survive
Turns:1
Difficulty:Medium
Description:Your Grizzly Bears is enchanted by the opponent's Pacifism. You control a Forest and three Mountains and have Flame Discharge and Battlegrowth in hand. The opponent controls Rumbling Baloth (4/4). End the turn with Rumbling Baloth in the opponent's graveyard.
Rule:wizards-official-card-rulings-cb4fb354a0d69fae | A creature is modified only by Equipment, Auras its controller controls, and counters (CR 700.9): the opponent's Pacifism does not count, so put a counter on Grizzly Bears before casting Flame Discharge for X=2
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Flame Discharge;Battlegrowth
p0battlefield=Grizzly Bears|Id:1;Forest;Mountain;Mountain;Mountain
p0library=Mountain;Mountain;Mountain
p1life=20
p1battlefield=Pacifism|AttachedTo:1;Rumbling Baloth
p1library=Plains;Plains;Plains
[acceptance]
count p1 Graveyard "Rumbling Baloth" = 1
```

**The solution.** Cast Battlegrowth ({G}) on Grizzly Bears and let it resolve. Then cast Flame Discharge with
X=2 ({2}{R}) on the Baloth. Bears is modified, so it deals 4: lethal.

**The misread line.** "Pacifism already makes Bears modified": the solver casts Flame Discharge with X=3 (all
four lands), expecting 5. It deals 3, and the Baloth survives. Battlegrowth looks useless under this reading.

**Why the solution is unique.**
- Battlegrowth on the Baloth makes it 5/5.
- Battlegrowth in response to Discharge is too late: modified is checked as you cast.
- Discharge before Battlegrowth gets no bonus.
- With Battlegrowth cast, the most Discharge can be is X=2.
- Doing nothing fails.

The card's reminder text says "Auras you control", so this tests careful reading of the card as much as rules
knowledge.

---

## Summary

Four rows out of 121 have a real solver choice the rule decides. All other drops hold.

- **429** (rulesguru-f9baca61ea79bba9): re-equipping in place gives no timestamp. Under Moat, with the other
  creature summoning sick, the Windbreaker has to go to Grizzly Bears and back. Strongest of the four.
- **597** (rulesguru-4434799761610007): Magus of the Moon keeps an animation that has already resolved. Animate
  Mutavault before Magus to get through Glacial Chasm. The uniqueness search may count animating in response as
  a second line.
- **725** (rulesguru-374a0d1fbb5f4ca8): Maraxus's power is counted after paying. Seething Song first makes the
  power-7 Unsealing trigger. Check the CDA on the stack and pool-first payment.
- **950** (wizards-official-card-rulings-cb4fb354a0d69fae): the opponent's Pacifism is no modification. Cast
  Battlegrowth before a Flame Discharge for X=2.
