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

---

## Repairs

Gate failures in batch 13 and the v2 boards written beside the drafts (`<name>_v2.pzl`). None of them has been
gated yet.

### s1_0 -> s1_0_v2.pzl (uniqueness "multiple": the Cadet already flew at the start)

- What failed: the proven "main line" was one equip of Eager Cadet (a no-op) and an attack. The second solution
  was two no-op equips of the Cadet and an attack. In the first replay of the search log, Eager Cadet could attack
  over Moat before anything was equipped ("attackers: 1 can attack"), so the setup gave the Windbreaker the later
  timestamp.
- Why: `GameState.handleCardAttachments` attaches in `HashMap<Card,Integer>` iteration order, and
  `GameEntity.hashCode()` is the card id. Card ids run p0 hand, library, battlefield, then p1 the same way (the log
  shows Eager Cadet @4 and Grizzly Bears @6). So Windbreaker was id 5 (bucket 5) and Grounded id 16 (bucket 0):
  Grounded was attached first and the Windbreaker got the later timestamp. Forge itself is right:
  `Card.attachToEntity` returns without a new timestamp when re-attaching to the same object (CR 701.3b).
- Change: p0's library is 7 Plains and the Windbreaker is last on p0's battlefield, so it is id 16 (bucket 0) and
  Grounded is id 20 (bucket 4). The Windbreaker is attached first, Grounded later, so Grounded wins as described.
  Same cards, same mana and the same description.
- Intended line: equip Grizzly Bears ({3}), then equip Eager Cadet ({3}). That is a new attachment with a new
  timestamp, so flying applies after Grounded, and the Cadet attacks over Moat for 1.
- The misread line (equip the Cadet directly, once or twice) does nothing and the Cadet can't attack. The Bears are
  summoning sick. The pass arm doesn't attack. Six Plains pay for exactly two equips, so Bears-then-Cadet is the
  only solution.
- Check at the gate: in the first search replay the Cadet must not be able to attack ("attackers: none can
  attack"). If it still can, the id arithmetic above is off.

### s3_3 -> s3_3_v2.pzl (uniqueness "multiple": order and target)

- What failed: proliferate-then-+2 and +2-then-proliferate both reach 8. The fingerprint differs because the
  proliferate pick shows Sorin's loyalty ([loyalty 4] or [loyalty 6]) and because the +2 target was Solver on one
  line and Opponent on the other. Neither difference is the rule being tested.
- Change: Steady Progress and the Islands are gone. The proliferate is Guildpact Informant's combat-damage trigger,
  and the puzzle starts with the Informant already attacking (`COMBAT_DECLARE_ATTACKERS`, as RP_007 does). The
  proliferate must then come before Sorin can be activated in main 2, which fixes the order. `life p1 = 18` fixes
  the +2 target: the Informant deals 1 and Sorin 1. Targeting yourself leaves the opponent at 19, and targeting Sorin
  costs a loyalty and leaves 7.
- Intended line: proliferate Sorin on combat damage (one counter, doubled: 4 -> 6), then in main 2 Sorin +2
  targeting the opponent (cost not doubled: 6 -> 8; opponent 19 -> 18).
- Misread line: "the +2 is doubled" makes proliferating overshoot (6 then 10), so the misreader leaves Sorin out of
  the proliferate and expects 4 + 4 = 8. The real result is 6, which fails. The pass arm leaves Sorin at 4 or 6 and
  the opponent at 19. Sorin's -X has no target (empty graveyard).
- I first considered keeping a spell and pinning the order with Tezzeret's Gambit paid with life. I dropped it
  because the auto-payer (`ComputerUtilMana`) pays a phyrexian shard before generic mana and could spend an Island
  on it, which would make the spell look uncastable.

### s3_2 -> s3_2_v2.pzl (search not_found: the search space, not the board)

- What failed: in 600 rollouts the pairing prompt was offered 91 times, and every time Mutavault was already
  tapped ("Mutavault (2/2)[tapped]"). The auto-payment for Wolfir Silverheart tapped Mutavault for {C}. The tree
  also branched on six separate Forest mana activations and repeated Mutavault animations (2316 of them). The line
  was never played.
- Change: the six Forests became `p0manapool=G G G G G G` (floating mana, RP_103 / RP_204). Payment spends pool
  mana first, so Mutavault stays untapped, and the land mana abilities leave the search tree.
- Intended line: animate Mutavault ({G} from the pool), cast Silverheart (the other five), pair them as it enters,
  and attack with a 6/6 Mutavault: 20 -> 14.
- The misread line (cast Silverheart, then animate) gets no soulbond trigger, so a 2/2 attacks: 18. Animating twice
  leaves 4 mana, which can't cast Silverheart. Paying with Mutavault's {C} taps it, so it can't attack. The pass arm
  leaves 20.

### s0_0 -> s0_0_v2.pzl (search not_found: the search space, not the board)

- What failed: Forge agrees with the judge. Every rollout that killed the haunted Memnite with Teysa alive put
  exactly one drain on the stack, and Teysa doubled the haunt-exile trigger itself (two Blind Hunter targets).
  But the search spent its budget on sacrificing Teysa (404 times), tapping the Mountain at random and the Shock
  targets. No rollout combined: haunt Memnite, sacrifice Memnite, drain the opponent, Shock the opponent.
- Change: the Mountain is now `p0manapool=R`, which removes its mana ability from every decision point. Nothing
  else changed.
- Intended line: sacrifice Blind Hunter to Spawning Pit. Its haunt trigger is doubled; the first to resolve exiles it
  haunting Memnite. Sacrifice Memnite: one drain (the exiled card is not a permanent), so 20 -> 18. Shock the
  opponent: 16. Teysa stays.
- The misread line ("the drain is doubled", stop at the drain or Shock a creature) leaves 18. Haunting Teysa gives
  no drain unless Teysa dies. Spending the {R} on the Spawn token loses the Shock. The pass arm leaves 20.
- If this still comes back not_found, the next cut is replacing Spawning Pit (whose token ability is a dead branch)
  with a single-ability sacrifice outlet.

### s2_2: not fixable by changing the board (a harness limitation)

- What failed: Ancient Vendetta's "choose a card name" (`NameCard` with no list, so
  `ChooseCardNameEffect` calls `chooseCardName(sa, cpp, valid, message)`) is answered on the search arm by
  `LlmSpikeGui.chooseCardName` (predicate overload). It takes
  `StaticData...streamAllFaces().filter(cpp).limit(500)` and picks at random:
  `auto("card-name(random)->" + pick)`. It never calls `noteSolverChoice`, so the name isn't a search choice point.
  The first 500 faces don't include Plains: the log shows "Abundant Growth", "A-Tome Shredder" and so on, with no
  basic lands. The prover can never name Plains, whatever the board.
- Forge isn't wrong (naming Plains is legal), and the misread is fine. The puzzle needs a harness change: on the
  search and random arms, offer the names of cards visible in the game (or the list overload, which records the
  choice) as recorded choices. A card with a `ChooseFromList` / `ChooseFromDefinedCards` name choice would test a
  different rule.
