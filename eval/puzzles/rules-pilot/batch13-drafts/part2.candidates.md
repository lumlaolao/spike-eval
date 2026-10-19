# part2 screen: dropped rows that can be built after all

121 rows read. Most drop reasons hold: replacement or cost-reduction order (Forge never offers it), the
opponent's choice, randomness, several turns, a rule-level near-duplicate of a built RP, or no solver choice
the rule decides. Two drops look close but are covered already: 804 (Cursecrafter sees Goblins dying with it)
is the look-back rule built for Anafenza, and 157 (lifelink damage to many creatures is one life-gain event)
is next to RP_053 (Pridemate counts life-gain events). The five rows below do have a board where the rule
decides a solver choice. Card names were checked against /root/forge/forge-gui/res/cardsfolder, and the
scripts were read for how Forge handles each one. None of these was run. Each needs the usual proof and
uniqueness pass.

"NNN" is a placeholder number. Setup note: GameState puts p0's battlefield onto the battlefield before p1's,
in the order listed, so a p1 permanent has the later timestamp (this matters for 600).

---

## 1. Triage 615: Grenzo finds the card wherever it went

- question_id: rulesguru-c1484eb6e55d14f9
- Why the drop does not hold: the drop said "Grenzo returns the card from exile under Rest in Peace whatever
  the solver does". That is true only when activating is the solver's one option. Add a way to remove Rest in
  Peace and allow only enough mana for one action. Now the misreading ("Rest in Peace exiles the card, so Grenzo
  cannot find it") sends the solver to Naturalize first, and then nothing is left to pay for Grenzo.
- Forge: `grenzo_dungeon_warden.txt` uses `RememberChanged` and then `Defined$ Remembered | Origin$ All`.
  ChangeZoneEffect remembers `movedCard`, the card in its new zone (exile), so the second move finds it there.
  Rest in Peace's replacement covers "Destination$ Graveyard" from anywhere, the library included.

```
[metadata]
Name:Rules pilot NNN - Grenzo looks in exile
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Grenzo, Dungeon Warden (2/2), Rest in Peace, a Forest and a Mountain, and have Naturalize in hand. The bottom card of your library is Goblin Hero (2/2). End the turn with Goblin Hero on your battlefield.
Rule:rulesguru-c1484eb6e55d14f9 | An effect that moves a card and then acts on it finds the card in the zone a replacement effect sent it to (CR 400.7j, 614.6): under Rest in Peace, Grenzo's milled Goblin Hero is exiled and still put onto the battlefield
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Naturalize
p0battlefield=Grenzo, Dungeon Warden;Rest in Peace;Forest;Mountain
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Goblin Hero
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
count p0 Battlefield "Goblin Hero" = 1
```

- Misread line: Naturalize Rest in Peace (Forest + Mountain). No mana is left for Grenzo's {2}, so this fails.
- Unique: there are two lands and both actions cost 2, so only one action fits. Only the activation puts Goblin
  Hero onto the battlefield. Doing nothing fails.
- Check before building: confirm the library's last listed card is the bottom (Grenzo reads `BottomOfLibrary`).

---

## 2. Triage 931: a dead Driver copy can target its own card

- question_id: wizards-official-card-rulings-ff9a99af91f85835
- Why the drop does not hold: the drop said the target pick is "the same under either reading". It is not the
  same if the graveyard also holds a decoy creature with mana value 2 or less. A misreader thinks the dying copy
  is still a mana value 4 Driver, or that its trigger cannot return its own card, so they return the decoy.
  Returning the Phantasmal Image card instead lets it re-enter as a second Driver.
- Forge: Driver's trigger targets `Creature.cmcLE2` in your graveyard. Once the Image is in the graveyard it is a
  plain mana value 2 card, so it is a legal target. Phantasmal Image's copy is `Optional`, and the Image gets a
  "becomes the target -> sacrifice" trigger. Falkenrath Noble triggers on `Creature.Other,Card.Self`.

```
[metadata]
Name:Rules pilot NNN - The copy brings itself back
Goal:Survive
Turns:1
Difficulty:Hard
Description:You control Driver of the Dead, Falkenrath Noble and two Islands, and have Phantasmal Image and Mutagenic Growth in hand. Grizzly Bears is in your graveyard. End the turn with two Driver of the Dead on your battlefield and the opponent at 19 life.
Rule:wizards-official-card-rulings-ff9a99af91f85835 | A dies trigger looks back at the permanent, but its target is chosen in the graveyard (CR 603.10a, 400.7): a Phantasmal Image that copied Driver of the Dead dies as a Driver and its trigger can return the mana value 2 Image card itself
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Phantasmal Image;Mutagenic Growth
p0battlefield=Driver of the Dead;Falkenrath Noble;Island;Island
p0graveyard=Grizzly Bears
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
count p0 Battlefield "Driver of the Dead" = 2
life p1 = 19
```

- Solution:
  1. Cast Phantasmal Image as a copy of Driver of the Dead.
  2. Cast Mutagenic Growth (pay 2 life) targeting the Image. The Image is sacrificed.
  3. Point Falkenrath Noble's trigger at the opponent (opponent at 19).
  4. Aim the copy's dies trigger at the Phantasmal Image card. It returns and re-enters as a copy of Driver.
- Misread line: the same steps but return Grizzly Bears, which leaves one Driver. Or keep the Image as a Driver
  and never kill it, which leaves the opponent at 20.
- Unique:
  - Copying Falkenrath Noble and killing it fires two Noble triggers, and there is still one Driver.
  - Declining the copy makes a 0/0 that fires the Noble trigger but no Driver trigger, so there is one Driver.
  - Growth aimed at a real creature kills nothing.
  - Re-entering as Noble, or as a 0/0, misses the Driver count or the 19.
  - With no green source, Growth's life payment is forced.

---

## 3. Triage 782: Ancient Vendetta can name a basic land

- question_id: wizards-of-the-coast-release-notes-fbc08b02cb53d6d9
- Why the drop does not hold: the drop said the misread "has no alternative line". Give the opponent single
  nonbasic decoys and exactly four Plains, so naming Plains is the only name that exiles four. A misreader
  takes the Cranial Extraction wording ("other than a basic land") as the rule, so they name a nonbasic and
  exile one card. The harness handles name asks (`chooseCardName` with a predicate). Ancient Vendetta's
  `NameCard` has no `ValidCards`, so the predicate accepts every name, basics included.

```
[metadata]
Name:Rules pilot NNN - Naming a basic land
Goal:Survive
Turns:1
Difficulty:Easy
Description:You control four Swamps and have Ancient Vendetta in hand. The opponent's graveyard holds Plains, Serra Angel and Wrath of God; their hand is empty and their library is three Plains and two Islands. End the turn with four of the opponent's cards in exile.
Rule:wizards-of-the-coast-release-notes-fbc08b02cb53d6d9 | A "choose a card name" effect can name any card, basic lands included, unless the card says otherwise (CR 201.3): Ancient Vendetta naming Plains exiles four cards
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Ancient Vendetta
p0battlefield=Swamp;Swamp;Swamp;Swamp
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1graveyard=Plains;Serra Angel;Wrath of God
p1library=Plains;Plains;Plains;Island;Island
[acceptance]
count p1 Exile * = 4
```

- Misread line: name Serra Angel or Wrath of God, which exiles one card.
- Unique: Plains is the only name with four copies. Island has two, and every other name has one or none. The
  search picks of "up to four" among exactly four Plains have only one way to reach four.
- Weakness: a model that knows the card simply names Plains. This tests whether the solver believes the
  restriction exists, which is what the question is about.

---

## 4. Triage 913: proliferating with nothing chosen is still proliferating

- question_id: wizards-official-card-rulings-c04f26cff086979b
- Why the drop does not hold: the drop said "No solver choice". There is one if a "when you proliferate"
  payoff sits in the graveyard, no permanent or player has a counter, and spending mana to make a counter
  costs the mana the payoff needs. Forge: `CountersProliferateEffect` runs `TriggerType.Proliferate` on every
  iteration, even when the chosen list is empty. Voidwing Hybrid's trigger works from the graveyard
  (`TriggerZones$ Graveyard`).

```
[metadata]
Name:Rules pilot NNN - Proliferating nothing
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control two Islands, a Swamp and a Forest, and have Contentious Plan and Servant of the Scale in hand. Voidwing Hybrid is in your graveyard. No permanent or player has a counter. End the turn with Voidwing Hybrid on your battlefield.
Rule:wizards-official-card-rulings-c04f26cff086979b | Proliferate lets you choose no permanents or players and you have still proliferated (CR 701.34a, 603.2): Contentious Plan with nothing to add to returns Voidwing Hybrid
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Contentious Plan;Servant of the Scale
p0battlefield=Island;Island;Swamp;Forest
p0graveyard=Voidwing Hybrid
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
count p0 Battlefield "Voidwing Hybrid" = 1
```

- Solution: cast Contentious Plan, paying with an Island and the Forest. Voidwing returns to hand. Cast it with
  the other Island and the Swamp.
- Misread line: cast Servant of the Scale first "so there is a counter to proliferate", then Contentious Plan.
  Voidwing returns, but only one land is left, so it cannot be cast.
- Unique:
  - Four lands pay for exactly two 2-mana spells.
  - Paying Plan's generic with the Swamp or the second Island leaves no {U}{B} for Voidwing.
  - Plan's proliferate has nothing to choose.
- Check before building: the harness may skip the empty proliferate ask (`chooseEntitiesForEffect` with an
  empty list). The trigger fires either way.

---

## 5. Triage 600: Wayward Angel's threshold bonus survives Humility (CR 613.6)

- question_id: rulesguru-de65e10751709976
- Why the drop does not hold: the drop said Humility is the opponent's and "no choice". Humility can stay a
  static on the opponent's side. What gives the solver a choice is Fling with two creatures to sacrifice. Real
  sizes: Wayward Angel is 6/6 (base 1/1, plus +3/+3 that keeps applying because the ability began in layer 5,
  plus Petroglyphs' +2/+2 because it has no abilities). Grizzly Bears with a +1/+1 counter is 4/4. The misread
  ("Humility removed the threshold ability, so no +3/+3") makes the Angel 3/3, and a damage-maximising
  misreader flings the Bears.
- Forge: Wayward Angel's threshold is ONE static (SetColor, AddKeyword/AddTrigger, AddPower). GameAction applies
  CR 613.6 explicitly (`affectedPerAbility`, around line 1167). Humility is listed on p1, so it takes the later
  timestamp. That matters: with Humility earlier, the Angel would keep trample, get no Petroglyphs bonus, and
  be 4/4.

```
[metadata]
Name:Rules pilot NNN - The threshold bonus survives Humility
Goal:Survive
Turns:1
Difficulty:Hard
Description:You control Wayward Angel, Grizzly Bears with a +1/+1 counter, Muraganda Petroglyphs and two Mountains, have Fling in hand and seven cards in your graveyard. The opponent controls Humility and is at 6 life. Win this turn.
Rule:rulesguru-de65e10751709976 | An effect that starts to apply in one layer keeps applying in later layers even if its ability is removed (CR 613.6, 613.4c): under Humility and Muraganda Petroglyphs, Wayward Angel with threshold is a 6/6, so Fling deals 6
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Fling
p0battlefield=Wayward Angel;Grizzly Bears|Counters:P1P1=1;Muraganda Petroglyphs;Mountain;Mountain
p0graveyard=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=6
p1battlefield=Humility
p1library=Plains;Plains;Plains
[acceptance]
winner p0
life p1 <= 0
```

- Solution: Fling, sacrificing Wayward Angel, at the opponent: 6 damage.
- Misread lines:
  - Fling the Bears (4, the largest under the misread).
  - Under a dependency misread, the Angel is 4/4 and ties the Bears. Either way the opponent ends at 2.
- Unique: one Fling and two creatures. The Bears deal 4. Only the Angel at the opponent's face reaches 6.
- Check before building: the first proof should read the Angel as 6/6 in the state dump. If Forge shows 4/4,
  setup timestamps or the Petroglyphs `NoAbilities` check differ from this reading. Then drop the row or move
  Humility after the Angel in p0's list.

---

## Summary

Five candidates from part2 (triage lines):
- **615**: Grenzo finds the milled card in exile under Rest in Peace.
- **931**: a Phantasmal Image copying Driver of the Dead returns its own card.
- **782**: Ancient Vendetta names Plains.
- **913**: an empty proliferate still returns Voidwing Hybrid.
- **600**: Wayward Angel is 6/6 under Humility and Petroglyphs.

The other 116 drops hold.
