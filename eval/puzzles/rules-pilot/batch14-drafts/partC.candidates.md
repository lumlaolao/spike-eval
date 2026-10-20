# partC screen: batch 14, questions-c.jsonl (39 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in a draft was checked with
`grep -rlx "Name:<card>" /root/forge/forge-gui/res/cardsfolder` and its script read. Drafts are `c_K.pzl` with
`Name:Rules pilot NNN - ...` (number them RP_2xx when kept). Each needs the usual gate: search arm proven and
accepted, pass arm rejected, uniqueness unique. All drafts start in MAIN2 of turn 1 (no combat line for the uniqueness
search to find, lesson of a_7) with `Goal:Survive`, `Turns:1`.

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-9b833d29d0af8789 | board | maybe | not drafted: land types are not in the acceptance grammar and the colours a land taps for are on Forge's mana menu; Zhao's moon needs a conqueror counter ({7}) and the Dryad/Zhao timestamp order would come from puzzle load order |
| 1 | rulesguru-c9cfbaa835930998 | hidden | no | whether a library card can be looked at during the draw step; nothing on the final state |
| 2 | rulesguru-7a3ff29cea5d3309 | board | no | duplicate of RP_124 (a face-down permanent has no replacement as it dies, CR 708.2, 614.12); the block is the opponent's choice |
| 3 | rulesguru-81c1bb586e82f750 | board | no | Time Stop only removes the extra combat; no solver choice the rule decides that a solver would make |
| 4 | rulesguru-170f46d519d646e4 | legality | no | which creatures may be declared under Master Warcraft and Propaganda; Forge validates the declaration |
| 5 | rulesguru-769b97dd888f0bc6 | legality | no | Drought's additional cost counts the printed mana cost; Forge computes and pays the cost, no choice |
| 6 | rulesguru-f8a6297644d92ab3 | hidden | no | what a player knows while choosing for Throne of Eldraine |
| 7 | rulesguru-41844257f4ec0056 | legality | no | sacrificing for KCI mana during activation; Forge orders cost payment itself |
| 8 | rulesguru-836938c99553805d | legality | no | casting a Misthollow Griffin exiled by Ixalan's Binding; Forge offers only legal casts |
| 9 | rulesguru-33566cc5916ea2f4 | board | maybe | not drafted: the "until Driftgloom Coyote leaves" link cannot be set in the puzzle state (`ExiledWith` does not create the return), building it in-game hands the cast to the opponent, and the misread line (kill the Coyote, gain life later) also gives the counter |
| 10 | rulesguru-2cff2840e5567d27 | board | no | the judge answer is "unclear" |
| 11 | rulesguru-dda35b02b9fc398d | board | no | duplicate of RP_181 (triggers during Day's Undoing are exiled with the turn's end, CR 723.1a-b) |
| 12 | rulesguru-ce7dd0d871635321 | board | no | the engine asks for each Baral loot, so the trigger count is shown, not chosen; the "counter" area is covered by RP_237 |
| 13 | rulesguru-b2d1c13fc85011aa | board | no | the control change and the copy choice belong to the player who cast Gather Specimens on the other's spell; a solver-side board has no misread play |
| 14 | rulesguru-876b5c700ff78481 | legality | no | goad attack requirements with a planeswalker; Forge validates attacks (and it is the opponent's turn) |
| 15 | rulesguru-1e03cbc3d263166d | board | maybe | not drafted: "enters tapped" is not in the grammar (the stun counter is, but under the Tonberry text the Shapeshifter is named "Tonberry"), no solver choice the rule decides, and Forge's 614.12 look-ahead through `GainTextOf` is unverified |
| 16 | rulesguru-43faa9943e1b39b8 | board | maybe | `c_5.pzl` (Ultimate Price cannot hit the Bears under Dress Down: still blue) |
| 17 | rulesguru-18e7ad0ac001c185 | legality | no | Ulamog's cast trigger cannot target a pro-creatures Teysa; the target menu is Forge's |
| 18 | rulesguru-1c89b4e84b1d1599 | legality | no | delve with a granted flashback; Forge's payment menu |
| 19 | rulesguru-ce162ad32993d27e | board | maybe | `c_4.pzl` (undying sees the +1/+1 counter annihilated in the same check; same 704.8 point as RP_029, different card) |
| 20 | rulesguru-0f0383d1b6c3ecd0 | board | no | Nightmare Shepherd's token copy is a noncreature Vehicle; no solver choice changes it, and P/T of an uncrewed Vehicle is not in the grammar |
| 21 | rulesguru-19214282e3c5da5c | board | good | `c_0.pzl` (Containment Priest exiles a manifest, even of a land; Soul Summons stands in for Ghastly Conscription) |
| 22 | rulesguru-d76b7064ed04af26 | board | no | the engine asks for each Psychic Surgery trigger; the count is shown, not chosen, and the opponent picks the discards |
| 23 | rulesguru-22cce8b61478f6c1 | legality | no | a Scepter copy of an MDFC has only the front face; Forge's cast menu |
| 24 | rulesguru-e35db08625ed7a64 | board | no | the face-down Robo-Pinata dies to the opponent's block, no solver choice; same face-down family as RP_124 |
| 25 | rulesguru-80c804de84e1fec2 | legality | no | which bands may be declared; Forge validates declarations |
| 26 | rulesguru-142d0a6cc0a1001b | multiplayer | no | Karn restart after a player left |
| 27 | rulesguru-96d878c47e0ae213 | board | good | `c_3.pzl` (Embodiment of Agonies counts itself as it returns; Unearth stands in for Finale of Eternity) |
| 28 | rulesguru-a962f3f5da7507c7 | board | no | the countered spell is cast through Kess by one player and Hindered by the other; a puzzle state cannot put a Kess-cast spell on the stack (`putonstack` does not record the permission) |
| 29 | rulesguru-4341d357d21b871b | board | no | the affected player (the discarding opponent) picks the replacement, and Ferox needs an opponent-caused discard |
| 30 | rulesguru-d6d7dbbaa7ffb984 | multiplayer | no | Karn restart after a player left |
| 31 | rulesguru-5883f1ef7253acf4 | legality | no | turning a morph face up under Magus; Forge offers the action or not |
| 32 | rulesguru-287ac4ad6239a2fb | multiplayer | no | Two-Headed Giant draw order |
| 33 | rulesguru-d093696fd09b05fd | legality | no | the {0} attack cost may be declined; Forge's declare-attackers menu decides it |
| 34 | rulesguru-c6027549bf7e2575 | board | good | `c_2.pzl` (discard a Forest in cleanup to draw, then a nonland in the second cleanup) |
| 35 | rulesguru-1f0f298bd229bb10 | legality | no | casting Zoetic Cavern face down via Crucible; Forge offers the cast or not |
| 36 | rulesguru-c4c9977b920f2009 | board | good | `c_1.pzl` (a second face-down creature has no name, Guardian Project draws) |
| 37 | rulesguru-16dfd0ba216ddd46 | board | maybe | not drafted: the solver has no reason to pick Tatterkite over another artifact, Genesis Wave needs 7+ mana, and Forge may move the Wave's cards one at a time (Humility first would let the counter stay: a disagreement, not a board) |
| 38 | rulesguru-6c935fc3cb9636fd | board | no | duplicate of RP_136 (614.12 with Dress Down: an entering creature's counter replacement is checked with Dress Down applied) |

Drafted: 6 (4 good, 2 maybe). Not drafted: 33 (4 maybe: #0, #9, #15, #37; 29 no, of which 4 are near-duplicates
of committed puzzles: #2 and #24 of RP_124, #11 of RP_181, #38 of RP_136).

---

## 1. #21 - A manifest was not cast (c_0)

- question_id: `rulesguru-19214282e3c5da5c`
- Why it holds: a manifested card is turned face down before it is put onto the battlefield (CR 701.40a), so it
  enters as a nontoken 2/2 creature that was not cast, and Containment Priest exiles it, whatever the card is.
- Change from the question: Ghastly Conscription costs 7 and manifests the opponent's graveyard creatures; Soul
  Summons ({1}{W}, manifest the top card of your library) makes the same entry. The library is all Forests, which
  sharpens the misread ("a land is not a creature, the Priest does not care").
- Forge check: Containment Priest `R:Event$ Moved | Destination$ Battlefield | ValidCard$ Creature.!token+!wasCast |
  ReplaceWith$ Exile`. Soul Summons `SP$ Manifest`. `Card.manifest` calls `turnFaceDown(true)` (face-down state: a 2/2
  creature) before `moveToPlay`, so the replacement sees a creature that was not cast. Shock: 2 to any target
  (Priest is 2/2). Exile is checked with `*` because a face-down card in exile may keep no name.

```
(see c_0.pzl)
[state] p0 MAIN2; Mountain, 2 Plains; hand Soul Summons, Shock; library 5 Forests; p1 Containment Priest
[acceptance]
count p1 Graveyard "Containment Priest" = 1
count p0 Exile * = 0
count p0 Library * = 4
```

- Solution: Shock the Containment Priest, then Soul Summons: the top Forest enters face down. Library 4, exile empty.
- Misread lines: "the top card is a land / manifest is not a creature entering" -> Soul Summons first (Forest exiled),
  then Shock (Priest dead, exile 1). Shock to the face, then Summons: exiled, Priest alive.
- Uniqueness: two spells, one target that matters; the only passing order is Shock (Priest) then Summons. No combat
  (MAIN2), opponent has no mana. Pass: Priest alive, library 5, fails.
- Weakness: "kill the hate piece first" passes without the rule; the land on top is what makes the misread tempting.

---

## 2. #36 - Nameless is not the same name (c_1)

- question_id: `rulesguru-c4c9977b920f2009`
- Why it holds: face-down creatures have no name, and objects with no name do not share a name, so Guardian
  Project's intervening-if passes for a second face-down Stormwing Dragon and you draw.
- Forge check: Guardian Project `T:Mode$ ChangesZone | ValidCard$ Creature.!token+YouCtrl+doesNotShareNameWith
  YourGraveyard+doesNotShareNameWith OtherYourBattlefield`, rechecked by `ConditionCheckSVar$ X` on resolution.
  `CardUtil.getFaceDownCharacteristic` sets the name to `""`; `Card.sharesNameWith(String)` returns false for an empty
  name. Stormwing Dragon `K:Megamorph:5 R R` (cast face down for {3}). Grizzly Bears {1}{G}.

```
(see c_1.pzl)
[state] p0 MAIN2; Guardian Project; Stormwing Dragon|FaceDown; 3 Forests; hand Grizzly Bears, Stormwing Dragon
graveyard Grizzly Bears; library 5
[acceptance]
count p0 Library * = 4
```

- Solution: cast Stormwing Dragon face down for {3}; Guardian Project draws one.
- Misread lines: "the two face-down creatures share a (blank) name, no draw" -> the solver sees no drawing play, or
  casts Grizzly Bears, which shares its name with the Bears in the graveyard (no draw; library 5).
- Uniqueness: three Forests pay for one of the two (Stormwing face up is {5}{R}); only the face-down cast draws. MAIN2,
  so no attack with the existing face-down 2/2. Pass: library 5, fails.
- Weakness: the misread is mostly inaction; a solver who casts the morph because it is the bigger play wins.

---

## 3. #34 - Cleanup comes around again (c_2)

- question_id: `rulesguru-c6027549bf7e2575`
- Why it holds: discarding a land to hand size in cleanup triggers The Gitrog Monster; with a trigger to put on the
  stack players get priority and then a new cleanup step begins (CR 514.3a), in which the hand (8 again after the
  draw) is discarded down once more.
- Forge check: `PhaseHandler` CLEANUP: `chooseCardsToDiscardToMaximumHandSize`, then
  `zoneMovements.triggerChangesZoneAll`; if the stack is non-empty `bRepeatCleanup = true` (514.3a). Gitrog
  `T:Mode$ ChangesZoneAll | ValidCards$ Land.YouOwn+!token | Destination$ Graveyard -> Draw 1`. The Survive goal wins
  at turn 2's upkeep, after turn 1's cleanup (`Puzzle.java`).

```
(see c_2.pzl)
[state] p0 MAIN2; The Gitrog Monster; no lands; hand Colossal Dreadmaw + 7 Forests; library Lightning Bolt
[acceptance]
count p0 Hand "Lightning Bolt" = 1
count p0 Graveyard "Colossal Dreadmaw" = 1
```

- Solution: play no land. Cleanup: discard a Forest; Gitrog draws Lightning Bolt (library empty); second cleanup:
  discard Colossal Dreadmaw.
- Misread lines: "a trigger in cleanup is too late to draw" -> discard the Dreadmaw first (Bolt stays in the library).
  "After the draw the turn just ends / any discard is fine" -> a second Forest: Gitrog draws from an empty library and
  the solver loses (Dreadmaw still in hand). Discarding the Bolt in the second cleanup also fails. Playing a Forest
  (Gitrog allows two) drops the hand to 7: no discard, no draw.
- Uniqueness: the Forests are identical; only Forest-then-Dreadmaw passes. No mana, MAIN2. Pass: the forced discard is
  answered with a fixed option; the Dreadmaw is listed first so a first-option discard fails.
- Weakness: if the pass arm's fixed discard is a Forest and its second is the Dreadmaw (first in the list again), the
  pass arm solves: check it is rejected.

---

## 4. #27 - It counts itself on the way (c_3)

- question_id: `rulesguru-96d878c47e0ae213`
- Why it holds: Embodiment of Agonies' enters-with-counters replacement is applied while it is still in the graveyard,
  so its own mana cost counts: returned from an otherwise empty graveyard it is a 1/1, not a 0/0.
- Change from the question: Finale of Eternity X=10 costs 12; Unearth ({B}, MV 3 or less) returns it the same way.
- Forge check: Embodiment `K:etbCounter:P1P1:X`, `X:Count$ExactManaCostGraveyard Card.nonLand+YouOwn`; the etbCounter
  is a `Moved` replacement evaluated before the zone change (`ReplacementHandler` puts the counters on the LKI), so the
  card should still be in the graveyard. If Forge counts after the move it enters 0/0 and dies: the search finds no
  line, record a Forge disagreement. Gitaxian Probe `ManaCost:UP` (2 life with no blue source).

```
(see c_3.pzl)
[state] p0 MAIN2; Swamp; hand Gitaxian Probe, Unearth; graveyard Embodiment of Agonies
[acceptance]
count p0 Battlefield "Embodiment of Agonies" = 1
life p0 = 20
```

- Solution: Unearth Embodiment of Agonies: one counter (its own {1}{B}{B}), a 1/1 flier.
- Misread line: "it does not count itself, it would enter 0/0" -> cast Gitaxian Probe for 2 life first so a card is in
  the graveyard, then Unearth (two counters, life 18). Not casting Unearth at all also fails.
- Uniqueness: Probe after Unearth also costs 2 life; Unearth has one target. Pass: Embodiment in the graveyard, fails.
- Weakness: "at 20 life" is what makes the safety-first misread line fail; the counter count itself is not checked.

---

## 5. #19 - Undying sees the counter it lost (c_4)

- question_id: `rulesguru-ce162ad32993d27e`
- Why it holds: -1/-1 counters that bring a creature with a +1/+1 counter to 0 toughness kill it in the same
  state-based check that annihilates the counters, and undying looks back at it with its +1/+1 counter: no return.
  Fewer -1/-1 counters annihilate and leave it alive without a counter.
- Change from the question: Strangleroot Geist (2/1 undying, here 3/2 with a counter) stands in for Nardole (needs X=3),
  and the Geist is the solver's, so the misread costs the solver.
- Overlap: RP_029 proves Forge's 704.8 look-back for a counter count; this is the same point applied to undying.
  Keep only if the suite wants it. Forge: `stateBasedAction704_5q` runs after the toughness check marks the creature,
  in the same pass; RP_029 shows the dies trigger sees the pre-annihilation counters.

```
(see c_4.pzl)
[state] p0 MAIN2; Strangleroot Geist|Counters:P1P1=1; 4 Swamps; hand Black Sun's Zenith, Disfigure
p1 Grizzly Bears, Llanowar Elves
[acceptance]
count p1 Graveyard "Grizzly Bears" = 1
count p1 Graveyard "Llanowar Elves" = 1
count p0 Battlefield "Strangleroot Geist" = 1
```

- Solution: Disfigure the Bears and Black Sun's Zenith with X=1: the Elves die, the Geist's counters annihilate (2/1).
- Misread line: "the counters annihilate first, so the Geist dies with none and undying brings it back" -> Zenith X=2
  alone (four mana, kills both): the Geist stays in the graveyard.
- Uniqueness: Disfigure on the Elves or the Geist, Zenith X=1 alone, or X=0 fail. Zenith X=1 before Disfigure on the
  Bears also passes; it is the same plan in the other order, but the uniqueness search may report it (read
  `uniqueness_difference`). Pass: fails.
- Weakness: the order pair above; and a solver can reach the line by avoiding the Geist without knowing 704.8.

---

## 6. #16 - Still painted after Dress Down (c_5)

- question_id: `rulesguru-43faa9943e1b39b8`
- Why it holds: Painter's Servant's colour change is layer 5; Dress Down's ability removal is layer 6, so the colour
  stays. Under Dress Down the Grizzly Bears are green and blue (multicoloured), so Ultimate Price cannot target them
  until the Servant (mono-blue) is gone.
- Change from the question: the colour is made visible through Ultimate Price (monocoloured only).
- Forge check: Painter's Servant `S:Mode$ Continuous | Affected$ Card | AddColor$ ChosenColor | AffectedZone$ All`;
  Dress Down `Affected$ Creature | RemoveAllAbilities$ True`. Forge applies statics layer by layer, so the colour
  should already be applied when abilities are removed; if Forge drops the Servant's static instead, the misread line
  also passes and the uniqueness search reports it: a Forge disagreement. Ultimate Price `ValidTgts$
  Creature.MonoColor`. `ChosenColor:blue` on the opponent's Servant follows RP_219.

```
(see c_5.pzl)
[state] p0 MAIN2; 2 Islands, 2 Swamps; hand Dress Down, Ultimate Price x2; p1 Painter's Servant (blue), Grizzly Bears
[acceptance]
count p1 Graveyard "Grizzly Bears" = 1
```

- Solution: Ultimate Price the Servant (mono-blue), then Ultimate Price the now mono-green Bears.
- Misread line: "Dress Down switches the painting off" -> cast Dress Down (2 mana) to make the Bears mono-green, then
  find only the Servant targetable; one Price left, the Bears live.
- Uniqueness: four mana pays two of the three spells; only Price, Price in that order kills the Bears. Pass: fails.
- Weakness: a solver who just reads the target menu wins without the rule; the rule only stops the misread.

---

## Not drafted, notes

- #0 (Dryad + Zhao): a Blood Moon-style board could show "Dryad Arbor stays a creature", but nothing the solver
  chooses differs between the readings, and the colours a land makes are on the menu.
- #9 (Aerith returns before the life gain): worth a board if a puzzle can start with an "until leaves" exile in place.
- #37 (Tatterkite): if Forge moves Genesis Wave's cards one by one with Humility first, the judge's answer cannot hold;
  a run would tell, but the board still lacks a solver reason to choose Tatterkite.
