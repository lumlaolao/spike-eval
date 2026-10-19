# part0 screen: candidates rescued from the 122 dropped rows

Read-only screen. Every card named below was checked with
`grep -rl "^Name:<card>$" /root/forge/forge-gui/res/cardsfolder` and its script read. Puzzle numbers RP_223..RP_227
are placeholders (RP_222 is the last in `eval/puzzles/rules-pilot/`). None of these has been run; each needs the
usual proof, uniqueness search and a misread-line rejection check.

The other 117 rows: the drop reason holds (no solver choice the rule decides, an opponent's action or choice,
replacement order, several turns, random reveal, or a rule-level duplicate of a built RP; e.g. #829 Anax is the
Anafenza look-back puzzle, #666 is RP_172, #20/#400 are the deathtouch-trample puzzles).

---

## 1. Triage #3 - Teysa does not double a haunt trigger

- question_id: `ask-a-magic-judge-8f22bb2608e03761`
- Drop note: "the haunt trigger simply happens once; no solver choice depends on Teysa not doubling it."
- Why it does not hold: give the solver a second, independent 2-point drain (Shock) and an exact life target. Under the
  misread the haunt drain is worth 4 and Shock would overshoot, so the misread solver keeps Shock; correctly it is
  worth 2 and Shock to the face is required.
- Forge check: Teysa is `Mode$ Panharmonicon | ValidCard$ Permanent.YouCtrl`; `applyPanharmoniconAbility` requires
  the trigger host to be in `ValidZone` (default Battlefield). The haunted-dies trigger is built with
  `TriggerZones$ Exile` (CardFactoryUtil, Haunt), so it is never doubled. Blind Hunter's own "when this dies, exile
  it haunting target creature" IS doubled; `HauntEffect` makes the second instance a no-op once the card has left
  the graveyard (`equalsWithGameTimestamp`). The puzzle state cannot set haunting, so the solver makes it.
  Spawning Pit is the sac outlet (noncreature, not a mana ability, used in RP_020).

```
[metadata]
Name:Rules pilot 223 - Haunt is not doubled
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Teysa Karlov, Blind Hunter, Memnite, Spawning Pit and a Mountain, and have Shock in hand. End the turn with the opponent at exactly 16 life and Teysa Karlov still on your battlefield.
Rule:ask-a-magic-judge-8f22bb2608e03761 | Teysa doubles only triggered abilities of permanents (CR 603.2d, 603.10a); a haunt trigger belongs to the exiled haunting card (CR 702.55c), which is not a permanent, so it triggers once: the haunted Memnite's death drains 2, and Shock to the face makes 4
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Shock
p0battlefield=Teysa Karlov;Blind Hunter;Memnite;Spawning Pit;Mountain
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
life p1 = 16
count p0 Battlefield "Teysa Karlov" = 1
```

- Solution: sacrifice Blind Hunter to Spawning Pit, haunt Memnite (both doubled instances), sacrifice Memnite (Blind
  Hunter's drain, target the opponent: 18), Shock the opponent (16). Order of Shock is free.
- Misread line: "Teysa doubles the haunt drain" makes the Memnite death worth 4, so the solver stops at the two
  sacrifices and keeps Shock (opponent 18), or Shocks too and expects 14.
- Uniqueness: haunt targets are Teysa or Memnite only (no opposing creatures, Spawning Pit is not a creature).
  Haunting Teysa gives no drain without losing Teysa. Shock on Memnite or Blind Hunter instead of the face leaves 18.
  Two sacrifices then {1} for a Spawn spends the Mountain and loses Shock. Only Hunter-haunts-Memnite + Shock face
  reaches 16. Doing nothing: 20.

---

## 2. Triage #9 - Pestilent Spirit's deathtouch ends when it leaves

- question_id: `ask-a-magic-judge-1738fe6fc11266be`
- Drop note: "Needs the opponent to destroy Pestilent Spirit with the spell on the stack."
- Why it does not hold: the solver can remove Pestilent Spirit itself, either as Fling's additional cost or with its
  own Village Rites in response. Both are legal misread plays that fail.
- Forge check: Pestilent Spirit's grant is `S:Mode$ Continuous | Affected$ Instant.YouCtrl,Sorcery.YouCtrl |
  AddKeyword$ Deathtouch | AffectedZone$ Stack`, a static that exists only while it is on the battlefield. Fling is
  `Cost$ 1 R Sac<1/Creature>`, X = `Sacrificed$CardPower`; Village Rites is `Cost$ B Sac<1/Creature>`, draw 2.

```
[metadata]
Name:Rules pilot 224 - Deathtouch leaves with the Spirit
Goal:Survive
Turns:1
Difficulty:Medium
Description:You control Pestilent Spirit, Memnite, a Mountain and two Swamps, and have Fling and Village Rites in hand. The opponent controls Colossal Dreadmaw. End the turn with Colossal Dreadmaw in the opponent's graveyard and two cards drawn (three cards left in your library).
Rule:ask-a-magic-judge-1738fe6fc11266be | Pestilent Spirit's deathtouch grant is a static ability that works only while it is on the battlefield (CR 604.1, 611.3a, 702.2b); a spell resolving after the Spirit left (sacrificed for Fling or Village Rites) has no deathtouch: Fling Memnite while the Spirit stays, then sacrifice the Spirit to Village Rites
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Fling;Village Rites
p0battlefield=Pestilent Spirit;Memnite;Mountain;Swamp;Swamp
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1battlefield=Colossal Dreadmaw
p1library=Plains;Plains;Plains
[acceptance]
count p1 Graveyard "Colossal Dreadmaw" = 1
count p0 Library * = 3
```

- Solution: cast Fling sacrificing Memnite, targeting Dreadmaw; let it resolve (1 deathtouch damage); then Village
  Rites sacrificing Pestilent Spirit.
- Misread lines ("the spell keeps deathtouch it had when cast" / "a granted keyword is locked in"): Fling sacrificing
  Pestilent Spirit (3 damage to a 6/6), Village Rites on the Spirit before Fling, or Rites in response to Fling. All
  are legal and each leaves Dreadmaw alive.
- Uniqueness: both spells are needed (Rites for the draws, Fling for the kill; Rites needs a creature, so Fling must
  sacrifice Memnite and Rites the Spirit), and Fling must resolve while the Spirit is on the battlefield. Fling's
  target is forced to Dreadmaw. Mana: any payment of Fling leaves a Swamp. Doing nothing fails both conditions.
- Weakness: a misread solver can still pick the right order by chance (about one line in four), so it discriminates
  less sharply than #1.

---

## 3. Triage #41 - Elesh Norn cannot beat "triggers only once each turn"

- question_id: `ask-a-magic-judge-9be164f6f13f9861`
- Drop note: "the once-each-turn clause gives one draw whatever the solver does."
- Why it does not hold: add a second, independent draw (Opt) and an exact library count. Under the misread Memnite
  alone draws two and Opt overshoots; correctly Memnite draws one and Opt is needed.
- Forge check: Welcoming Vampire's trigger has `ActivationLimit$ 1`; `StaticAbilityPanharmonicon.countPanharmonicon`
  returns 0 when `t.limitByActivations(2) < 2` ("already at its limit ... triggers only once each turn"), so Norn
  adds nothing.

```
[metadata]
Name:Rules pilot 225 - Once each turn beats Norn
Goal:Survive
Turns:1
Difficulty:Easy
Description:You control Elesh Norn, Mother of Machines, Welcoming Vampire and an Island, and have Memnite and Opt in hand. End the turn having drawn exactly two cards (three cards left in your library).
Rule:ask-a-magic-judge-9be164f6f13f9861 | Elesh Norn makes an ability trigger an additional time (CR 603.2d), but an ability that "triggers only once each turn" cannot trigger more often (CR 603.2h): Memnite draws one card, so Opt is needed for the second
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Memnite;Opt
p0battlefield=Elesh Norn, Mother of Machines;Welcoming Vampire;Island
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
count p0 Library * = 3
```

- Solution: cast Memnite (Vampire draws 1) and Opt (draws 1), either order.
- Misread line: Memnite alone, expecting two Vampire draws (4 left); Opt would seem to make three.
- Uniqueness: the only draws are the Vampire trigger (once this turn) and Opt; the drawn Dreadmaws are uncastable with
  one land. Scry top/bottom does not change the count (the search may still list it as a choice; if it does, use
  a cantrip without scry, e.g. a plain "draw a card" instant). Doing nothing: 5.

---

## 4. Triage #50 - "One or more tokens" draws once per event

- question_id: `ask-a-magic-judge-1e3c7c2b6d8fe796`
- Drop note: "Marneus draws once per event whatever the solver does."
- Why it does not hold: give the solver a two-token spell and a one-token spell and an exact draw count of two.
  Misread (one draw per token): Raise the Alarm alone draws two and both spells draw three. Correct: one draw per
  spell, so both are needed.
- Forge check: Marneus is `T:Mode$ ChangesZoneAll | ValidCards$ Card.token+YouCtrl | Destination$ Battlefield ...
  DB$ Draw` (mandatory draw, one trigger per batch); Raise the Alarm makes its two tokens in one Token effect.
  The {6} Chapter Master ability is out of reach (4 lands).
- Overlap: rule-level kin of the built Desecrated Tomb puzzle (three cards exiled as one cost trigger once) and the
  Ajani's Pridemate one; keep only if a second "one event" puzzle is wanted.

```
[metadata]
Name:Rules pilot 226 - One draw per token event
Goal:Survive
Turns:1
Difficulty:Easy
Description:You control Marneus Calgar, two Plains and two Forests, and have Raise the Alarm and Sprout in hand. End the turn having drawn exactly two cards (three cards left in your library).
Rule:ask-a-magic-judge-1e3c7c2b6d8fe796 | "Whenever one or more tokens you control enter" triggers once per event however many tokens it makes (CR 603.2c): Raise the Alarm's two Soldiers draw one card, so Sprout is needed for the second
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Raise the Alarm;Sprout
p0battlefield=Marneus Calgar;Plains;Plains;Forest;Forest
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
count p0 Library * = 3
```

- Solution: cast Raise the Alarm and Sprout (any order).
- Misread line: Raise the Alarm alone, expecting a draw per Soldier (4 left).
- Uniqueness: only two token events are possible; drawn Dreadmaws are uncastable; every way of paying Raise the
  Alarm leaves a Forest for Sprout. Doing nothing: 5.

---

## 5. Triage #650 - Gravestorm counts as its trigger resolves

- question_id: `rulesguru-2f62c3db5bed1507`
- Drop note: "Gravestorm searches the opponent's library with a free pick of cards; no clean acceptance separates the
  counts."
- Why it does not hold: use Follow the Bodies (gravestorm, investigate) so the count is a Clue count, and make the
  only sacrificable permanent appear after the cast (Young Pyromancer's cast trigger), so the solver must order its
  triggers and sacrifice the Elemental with the gravestorm trigger still on the stack.
- Forge check: Gravestorm is a `SpellCast` trigger whose copy amount is the SVar
  `Count$ThisTurnEntered_Graveyard_from_Battlefield_Permanent`, evaluated when the copy ability resolves. A token
  dying is added to the graveyard zone (GameAction lets tokens leave the battlefield to the graveyard) so it should be
  in `cardsAddedThisTurn`; confirm with the first proof run. Young Pyromancer triggers on cast only, not on the copy.
  Trigger order is branched by the search since RP_044.

```
[metadata]
Name:Rules pilot 227 - Gravestorm counts late
Goal:Survive
Turns:1
Difficulty:Hard
Description:You control Young Pyromancer, Spawning Pit and three Islands, and have Follow the Bodies in hand. End the turn with exactly two Clue tokens and Young Pyromancer still on your battlefield.
Rule:rulesguru-2f62c3db5bed1507 | Gravestorm's trigger counts the permanents put into a graveyard this turn as it resolves (CR 702.69a, 608.2h): put Young Pyromancer's trigger on the stack above it (CR 603.3b), then sacrifice the new Elemental with gravestorm still waiting, for one copy
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Follow the Bodies
p0battlefield=Young Pyromancer;Spawning Pit;Island;Island;Island
p0library=Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw
p1life=20
p1library=Plains;Plains;Plains
[acceptance]
tokens p0 Battlefield "Clue Token" = 2
count p0 Battlefield "Young Pyromancer" = 1
```

- Solution: cast Follow the Bodies; order Young Pyromancer's trigger to resolve first; with the gravestorm trigger on
  the stack, sacrifice the Elemental to Spawning Pit; gravestorm makes one copy; two Clues.
- Misread line: "the count is fixed when the spell is cast" makes the solver see no way to a copy (nothing has died
  before casting) and let everything resolve: one Clue. Sacrificing the Elemental after gravestorm resolves also
  gives one Clue.
- Uniqueness: before the cast the only sacrificable creature is Young Pyromancer, which acceptance keeps; after
  the cast only the Elemental can feed the count; sacrificing Pyromancer too makes three Clues and fails. The Clues
  ({2}) and Spawning Pit's Spawn ({1}, two counters) are out of reach with no mana left. Doing nothing: no Clue.

---

## Summary

Five rows where the drop reason does not hold:
- #3 (Teysa does not double haunt)
- #9 (Pestilent Spirit's deathtouch ends when it leaves)
- #41 (Elesh Norn can't beat once each turn)
- #50 (Marneus draws once per token event; overlaps the Desecrated Tomb puzzle)
- #650 (gravestorm counts on resolution)
