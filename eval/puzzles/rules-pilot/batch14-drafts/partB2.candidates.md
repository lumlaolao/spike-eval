# partB2 screen: batch 14, questions-b2.jsonl (21 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in a draft was checked with
`grep -rlx "Name:<card>" /root/forge/forge-gui/res/cardsfolder` and its script read. Drafts are `b2_K.pzl` with
`Name:Rules pilot NNN - ...` (number them RP_2xx when kept). Each needs the usual gate: search arm proven and
accepted, pass arm rejected, uniqueness unique. None of the 21 question ids appears anywhere under `eval/puzzles/`.

Two things the gate does not check, which matter to several drafts here. The uniqueness fingerprint is the *sorted*
set of solver actions (`LlmSpikePuzzle`, "As a set of lines"), so where the misread line is the same spells in another
order (b2_5, b2_8), the gate proves the right order wins but never that the wrong order fails; play the misread order
once as a forced line before keeping it. And the `clash` answer (top or bottom) is a noted choice, not on
`UNIQUENESS_NEUTRAL` (b2_6).

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-bbd69ab26d190bc5 | board | good | `b2_0.pzl` (Spirit Guide mana can't pay Myr Superion; Elves for Superion, Guides for Lightning Strike) |
| 1 | rulesguru-048e0e7a45ee4b3c | board | maybe | `b2_1.pzl` (Mana Reflection does not double Deathrite's mana; drain instead of Lightning Blast; correct line also wins under the misread) |
| 2 | rulesguru-42d471503ae16113 | board | good | `b2_2.pzl` (Cannibalize exiles the Constrictor before the counters: Battlegrowth first, exactly 4) |
| 3 | rulesguru-8afac6564db03109 | board | maybe | not drafted: Forge's Immortal Coil is a `Mode$ Always` trigger; whether Forge sees the empty graveyard in the middle of Living Death's resolution is unknown, and Forge shows the lose trigger on the stack, so any solver counters it with Dissonant Wave; no misread line differs in play |
| 4 | rulesguru-2a5a80b99afee280 | board | no | an exiled card's "cast as an Adventure" status cannot be set up, and Ertai's Meddling needs the opponent's cast and several upkeeps |
| 5 | rulesguru-c7c32c0195c41418 | procedure | no | commander format (Path of Ancestry checks the commander's creature types); the puzzle format has no commander |
| 6 | rulesguru-0b4765db1a2965f9 | board | no | the solver (Noel) has no choice that changes the result: casting Angel's Grace or not, the damage becomes poison counters; acceptance cannot read a player's poison counters |
| 7 | rulesguru-d4511ba7fef90d01 | legality | no | Chainer allows only the creature face of an MDFC; Forge offers only legal casts |
| 8 | rulesguru-3b6b4222b9855e6c | board | maybe | `b2_3.pzl` (Conspiracy naming Goblin feeds Dralnu's Crusade; search risk: ~250 creature types at one choice point) |
| 9 | rulesguru-9226825b8528a323 | board | good | `b2_4.pzl` (Deathrite's mana from the beginning of combat empties before the Sphere tax; drain instead) |
| 10 | rulesguru-0fa6c0b5da5e3c50 | board | no | under Lattice and March every land is a 0/0 creature and dies, so no mana; the Aura simply fails, with no solver choice it decides |
| 11 | rulesguru-c8f757e8da65af7a | board | maybe | `b2_5.pzl` (Blood Moon after Prismatic Omen leaves only Mountains: cast the blue spell first; ordering-only) |
| 12 | rulesguru-843cf11822bb68b3 | board | maybe | not drafted: the misread (Turn to Frog after first-strike damage adds a regular hit) is a solver line, but any correct alternative (a pump) also looks lethal to the misread solver and is the obvious play; the misread line is never preferred |
| 13 | rulesguru-5c2e89ec4a40d7db | board | maybe | `b2_6.pzl` (Titan's Revenge returns despite Leyline; cast it twice with X=2; clash top/bottom may be read as a second solution) |
| 14 | rulesguru-ba75233089d02a91 | board | no | the outcome (a land with a -1/-1 counter that survives cleanup) involves no solver choice; the opponent's Blur and a later re-animation would be needed |
| 15 | rulesguru-8ae9a5ca2af77ff9 | board | good | `b2_7.pzl` (Rugged Highlands enters untapped under Blood Sun, no life gained) |
| 16 | rulesguru-28cb5df678cc2f34 | hidden | no | a Wish chooses from outside the game; puzzles have no sideboard |
| 17 | rulesguru-a8714ba5d0b1e648 | board | no | replacement order (self-replacement first); the opponent must cast Nexus of Fate, and Forge never offers the order |
| 18 | rulesguru-8ab897f342f8a14e | board | maybe | `b2_8.pzl` (storm counts only earlier spells; same rule as RP_006, ordering-only) |
| 19 | rulesguru-215a9663dbba26f7 | legality | no | Biogenic Upgrade's "at least one per target" is a legality rule the harness enforces (epoch 64, CR 601.2d); RP_005 already uses the card |
| 20 | rulesguru-36011f578b7bdd29 | board | good | `b2_9.pzl` (Treasure Keeper finds Valki; cast the Tibalt face free) |

Drafted: 10 (5 good, 5 maybe). Not drafted: 11 (2 maybe, 9 no).

---

## 1. #0 - Spirit Guide mana is not creature mana (b2_0)

- question_id: `rulesguru-bbd69ab26d190bc5`
- Why it holds: Myr Superion's spend restriction counts only mana from creatures (permanents); a Simian Spirit Guide
  exiled from hand is a creature card, not a creature (CR 109.2). Both Elves must pay for Superion, so the Spirit
  Guides must pay for Lightning Strike.
- Forge check: Myr Superion `S:Mode$ ManaRestriction | ValidSource$ Creature.inZoneBattlefield` (the Guide is in hand
  when it makes mana). Simian Spirit Guide `AB$ Mana | Cost$ ExileFromHand<1/CARDNAME> | Produced$ R |
  ActivationZone$ Hand` (on the play menu as a mana ability that costs more than tapping, ARCHITECTURE "Combo
  lines"). Llanowar Elves, Elvish Mystic `{T}: Add {G}`, not summoning sick in setup. Lightning Strike {1}{R}, 3 to
  any target.

```
(see b2_0.pzl)
[state] p0 MAIN2; Llanowar Elves; Elvish Mystic; hand Myr Superion, Simian Spirit Guide x2, Lightning Strike
[acceptance]
count p0 Battlefield "Myr Superion" = 1
life p1 = 17
```

- Solution: cast Myr Superion with both Elves; exile both Spirit Guides for {R}{R}, cast Lightning Strike at the
  opponent.
- Misread line: "Spirit Guide mana is creature mana" -> Lightning Strike first, paid the usual way (an Elf for {1}, a
  Guide for {R}); Superion is then unpayable (one Elf left, the second Guide's mana does not count).
- Uniqueness: two spells; Superion needs both Elves, so only one payment split works. Strike must hit the opponent
  (life 17). Floating the Guides' mana first changes nothing (same casts). Pass: nothing cast, fails.
- Weakness: a solver who happens to cast Superion first is forced into the right payment.

---

## 2. #1 - Deathrite is not tapped for mana (b2_1)

- question_id: `rulesguru-048e0e7a45ee4b3c`
- Why it holds: Deathrite Shaman's first ability has a target, so it is not a mana ability and is not "tapping it for
  mana" (CR 106.12, 605.1a); Mana Reflection does not double it. Real mana: Swamp {B}{B} (doubled) + 1 = 3, so
  Lightning Blast ({3}{R}, 4 damage) is out of reach; the misread counts 4.
- Forge check: Mana Reflection `R:Event$ ProduceMana ... ValidCard$ Permanent`; `ReplaceProduceMana` defaults
  `ValidSA` to `Activated.hasTapCost+ManaAbility`, matched against the *root* ability (`AbilityManaPart`), and
  `SpellAbility.isManaAbility()` returns false when `usesTargeting()`: Deathrite's targeted root ability is not
  doubled; the Swamp is. Deathrite's drain `Cost$ B T`, needs an instant or sorcery card in a graveyard (only Bump in
  the Night, after it resolves). Bump in the Night {B}: target opponent loses 3 (flashback {5}{R} unaffordable).
  Wall of Wood (0/3 defender) is there so Bump + a Deathrite attack (3 + 1) is not a solution.

```
(see b2_1.pzl)
[state] p0 MAIN2; Mana Reflection; Deathrite Shaman; Swamp; hand Bump in the Night, Lightning Blast
p1 life 4; Wall of Wood; graveyard Plains
[acceptance]
life p1 <= 0
```

- Solution: tap the Swamp ({B}{B}), cast Bump in the Night (opponent to 1), then activate Deathrite's drain with the
  floating {B}, exiling Bump in the Night from your graveyard (opponent to -1).
- Misread line: "Reflection doubles Deathrite: Swamp {B}{B} + {R}{R} pays Lightning Blast" -> exile the Plains for
  mana; only 3 mana, Blast is not castable; Bump alone leaves the opponent at 1.
- Uniqueness: the drain's only target is Bump in the Night (no instant or sorcery elsewhere); Blast is never castable.
  Bump + attack is beaten by the Wall's block. Pass: fails.
- Weakness: the correct line also wins under the misread, so a misread solver fails only if it prefers the bigger
  (Blast) line; the floating {B} must survive between Bump and the drain (same main phase, it does).

---

## 3. #2 - Cannibalize exiles before it counts (b2_2)

- question_id: `rulesguru-42d471503ae16113`
- Why it holds: Cannibalize's instructions run in order (CR 608.2c): Winding Constrictor is exiled before the two
  counters go on Elite Vanguard, so they are exactly two. With the Constrictor still there, Battlegrowth's one counter
  becomes two.
- Forge check: Cannibalize `SP$ Pump | TargetMin$ 2 | TargetMax$ 2 | TargetsWithSameController$ True`, then `DBChoose
  (ChooseCard, Mandatory)` -> `ExileChosen` -> `DBPutCounter ... Targeted.!ChosenCard | CounterNum$ 2`: exile first.
  Winding Constrictor's replacement is `ActiveZones$ Battlefield` (gone once exiled). Battlegrowth {G}: one +1/+1
  counter on target creature. Elite Vanguard 2/1.

```
(see b2_2.pzl)
[state] p0 MAIN2; Winding Constrictor; Elite Vanguard; 2 Swamps, Forest; hand Cannibalize, Battlegrowth
[acceptance]
counters p0 Battlefield "Elite Vanguard" P1P1 = 4
```

- Solution: Battlegrowth on Elite Vanguard (1 + 1 = 2 counters), then Cannibalize on Vanguard and Constrictor, exiling
  the Constrictor (2 more): 4.
- Misread line: "the Constrictor still adds one to Cannibalize's counters" -> Cannibalize first (exile Constrictor,
  "3"), then Battlegrowth ("+1" = "4"): really 2 + 1 = 3. Under the misread the right order reads as 5.
- Uniqueness: Battlegrowth on the Constrictor, exiling Vanguard, or Cannibalize first all miss 4; the exile choice is a
  noted choice and only the Constrictor works. Pass: 0 counters, fails.
- Weakness: none known beyond the gate confirming Forge applies no replacement to the counters after the exile.

---

## 4. #8 - The Crusade depends on Conspiracy (b2_3)

- question_id: `rulesguru-3b6b4222b9855e6c`
- Why it holds: Conspiracy changes which permanents Dralnu's Crusade applies to, so the Crusade depends on it and
  applies after it (CR 613.8a-b). Gorehorn, named a Goblin, gets +1/+1: 8 power.
- Forge check: Conspiracy `K:ETBReplacement:Other:ChooseCT` (`ChooseType ... Type$ Creature`), `S:... Affected$
  Creature.YouCtrl | AddType$ ChosenType | RemoveCreatureTypes$ True`. Dralnu's Crusade: `Affected$ Creature.Goblin |
  AddPower$ 1 | AddToughness$ 1` and `Affected$ Goblin | AddType$ Zombie | SetColor$ Black`. Forge has a dependency
  pass (`GameAction` ~1297, `findStaticAbilityToApply`, a dependency graph comparing affected sets) and in any case
  re-reads `Affected` at layer 7c, after the type layer. Garruk's Gorehorn is a 7/3 Beast.

```
(see b2_3.pzl)
[state] p0 MAIN1; Dralnu's Crusade; Garruk's Gorehorn; 5 Swamps; hand Conspiracy; p1 life 8, no creatures
[acceptance]
life p1 <= 0
```

- Solution: cast Conspiracy naming Goblin; attack with Gorehorn (8/4): 8 damage.
- Misread line: "the Crusade is older, so it applies first and Gorehorn isn't a Goblin yet" -> no line reaches 8;
  name anything else or skip Conspiracy, 7 damage.
- Uniqueness: only "Goblin" adds power. Pass: no attack, fails.
- Weakness: (1) the naming choice has ~250 options and `PZL_SEARCH_LINES` defaults to 32 per choice point, so the
  search arm may not find Goblin at the default budget; raise the budget or check the arm's choice ordering before
  calling it not found. (2) A naive solver names Goblin for the obvious synergy without the layer reasoning.

---

## 5. #9 - The Shaman's mana empties before the tax (b2_4)

- question_id: `rulesguru-9226825b8528a323`
- Why it holds: Sphere of Safety's {X} per attacker is paid while attackers are declared, when only mana abilities can
  be activated (CR 508.1g); Deathrite's first ability is not one (CR 605.1a), and mana made at the beginning of combat
  empties when that step ends (CR 500.4). So Deathrite is worth more as a drain (2) than as mana.
- Forge check: Sphere of Safety `Mode$ CantAttackUnless ... Cost$ X`, `X = Count$Valid Enchantment.YouCtrl` (2: the
  Sphere and Glorious Anthem). The tax is `payCombatCost` (LlmSpikeGui ~6978: the seat says pay or not, then
  `PlayerControllerAi.payCombatCost` pays with mana abilities and the pool; Deathrite's targeted ability is not a mana
  ability, `isManaAbility()`). Deathrite drain `Cost$ B T`, target Lightning Bolt in the opponent's graveyard; first
  ability targets the Plains. Hill Giant 3/3.

```
(see b2_4.pzl)
[state] p0 MAIN1; Deathrite Shaman; Hill Giant x2; Swamp; Wastes x2
p1 life 5; Sphere of Safety; Glorious Anthem; graveyard Plains, Lightning Bolt
[acceptance]
life p1 <= 0
```

- Solution: in the main phase, drain with Deathrite (Swamp's {B}, exile Lightning Bolt): opponent to 3. Attack with
  one Hill Giant, paying {2} with the two Wastes: 3 damage.
- Misread line: "make a mana with Deathrite in the beginning of combat; with the three lands that pays {4} for both
  Giants (6 damage)" -> the mana is gone by the declaration; three lands pay for one Giant: 3 damage, opponent at 2.
- Uniqueness: three lands pay for one attacker without the drain (3), the drain needs the only {B}; Deathrite cannot
  both drain and attack. Drain after combat is the same action set (and fails if the tax payment took the Swamp).
  Pass: fails.
- Weakness: the correct line also wins under the misread; the misread is a bigger-looking line. First board with an
  attack tax in the rules suite (no precedent for the tax ask under the search arm).

---

## 6. #11 - Blood Moon after Prismatic Omen wins (b2_5)

- question_id: `rulesguru-c8f757e8da65af7a`
- Why it holds: no dependency between the two (CR 613.8a); timestamp order (CR 613.7): the later Blood Moon makes the
  Guildgates Mountains only. Cast the blue spell while the Gates still make {U}.
- Forge check: Prismatic Omen `Affected$ Land.YouCtrl | AddType$ AllBasicLandType`; Blood Moon `Affected$
  Land.nonBasic | AddType$ Mountain | RemoveLandTypes$ True` (layer 4, both). Simic Guildgate `Combo G U`, untapped in
  setup. Accumulated Knowledge {1}{U}, draws 1 here. Even if Forge's dependency check (`GameAction` ~1311) decided
  Moon depends on Omen, the Omen would apply first, the same result.

```
(see b2_5.pzl)
[state] p0 MAIN2; Prismatic Omen; Mountain; Simic Guildgate x4; hand Blood Moon, Accumulated Knowledge
[acceptance]
count p0 Battlefield "Blood Moon" = 1
count p0 Graveyard "Accumulated Knowledge" = 1
```

- Solution: Accumulated Knowledge first (a Gate's {U} + one more), then Blood Moon (Mountain + two Gates).
- Misread line: "Omen keeps the Gates every type" -> Blood Moon first; the two Gates left are Mountains, no {U}.
- Uniqueness: both casts are needed; the fingerprint is the sorted action set, so the orders are one line. Pass: fails.
- Weakness: ordering-only; the gate cannot see the misread order fail (same fingerprint): play it once as a forced line.
  Floating {U} before the Moon also wins (same casts). A solver who casts the cheap spell first wins by habit.

---

## 7. #13 - Revenge goes stack to hand (b2_6)

- question_id: `rulesguru-5c2e89ec4a40d7db`
- Why it holds: a resolving spell leaves the stack only when it finishes (CR 608.2n); the clash return is part of
  resolution, so Titan's Revenge never goes toward a graveyard and Leyline of the Void never applies. Two casts at X=2
  (8 mana) kill the Bears and finish the opponent; one cast can do only one.
- Forge check: Titan's Revenge `SP$ DealDamage | NumDmg$ X`, `DBClash ... WinSubAbility$ DBReturn`, `DBReturn:
  ChangeZone | Defined$ Parent | Origin$ Stack | Destination$ Hand` (during resolution). Leyline `R:Event$ Moved |
  Destination$ Graveyard | ValidCard$ Card.!token+OppOwn`. Clash: Colossal Dreadmaw (MV 6) beats Plains (0) every
  time, whatever either player puts on the bottom.

```
(see b2_6.pzl)
[state] p0 MAIN2; 8 Mountains; hand Titan's Revenge; library all Dreadmaws
p1 life 2; Leyline of the Void; Grizzly Bears; library all Plains
[acceptance]
count p1 Graveyard "Grizzly Bears" = 1
life p1 <= 0
```

- Solution: Titan's Revenge X=2 at the Bears (win the clash, it returns), then X=2 at the opponent (either order).
- Misread line: "Leyline exiles it, one shot only" -> one big cast (X=6 at the opponent or the Bears): one condition
  missed, no mana for the returned card.
- Uniqueness: 8 mana splits as X=2 + X=2 only (X=3 + X=1 leaves the opponent at 1). Pass: fails.
- Weakness: the solver's clash answer (top or bottom) is a noted choice (`askOne("clash", ...)`, not in
  `UNIQUENESS_NEUTRAL`) and both answers solve: the gate may say "multiple" for that alone; treat it like a scry.

---

## 8. #15 - Under Blood Sun the land enters untapped (b2_7)

- question_id: `rulesguru-8ae9a5ca2af77ff9`
- Why it holds: an "enters tapped" replacement of a land entering under Blood Sun is already gone (CR 614.12), and its
  enters trigger never exists on the battlefield (CR 603.10). Highlands gives {G} at once; no life gained.
- Forge check: Blood Sun `Affected$ Land | RemoveNonManaAbilities$ True` (keeps `Combo R G`). Rugged Highlands
  `R:Event$ Moved ... ETBTapped` and a ChangesZone life trigger. `ReplacementHandler.getReplacementList` (~line 85,
  "CR 614.12 ETB replacements look at what the card would be on the battlefield") builds an LKI on the battlefield and
  runs `checkStaticAbilities` on it, so Blood Sun strips the replacement. Kalonian Tusker {G}{G}; Rip-Clan Crasher
  {R}{G}.

```
(see b2_7.pzl)
[state] p0 MAIN2, no land played; Blood Sun; Forest; hand Rugged Highlands, Mountain, Kalonian Tusker, Rip-Clan Crasher
[acceptance]
count p0 Battlefield "Kalonian Tusker" = 1
life p0 = 20
```

- Solution: play Rugged Highlands (untapped), tap it and the Forest for {G}{G}, cast Kalonian Tusker.
- Misread line: "Highlands enters tapped" -> play the Mountain and cast Rip-Clan Crasher with {R}{G}; Tusker is not
  castable.
- Uniqueness: one land drop; only Highlands gives the second {G}. Pass: no land, fails.
- Weakness: `life p0 = 20` tests the trigger half; if Forge gained the life the search would find nothing (a Forge
  disagreement, not a board error). A solver who plays the nonbasic first by chance wins.

---

## 9. #18 - Storm counts only earlier spells (b2_8)

- question_id: `rulesguru-8ab897f342f8a14e`
- Why it holds: storm counts spells cast before the storm spell (CR 702.40a); Turn the Tide cast in response adds no
  copy.
- Forge check: Storm trigger `Mode$ SpellCast`, copies `TriggerCount$CurrentStormCount/Minus.1` (fixed when the
  trigger fires). Empty the Warrens: two `r_1_1_goblin` ("Goblin Token"). Turn the Tide {1}{U}, no targets or choices.

```
(see b2_8.pzl)
[state] p0 MAIN2; 5 Mountains, Island; hand Empty the Warrens, Turn the Tide
[acceptance]
tokens p0 Battlefield "Goblin Token" = 4
```

- Solution: Turn the Tide, then Empty the Warrens: storm 1, one copy, four Goblins.
- Misread line: Empty the Warrens, Turn the Tide in response to the storm trigger: two Goblins.
- Uniqueness: one order; the fingerprint is the same set either way, but the wrong order does not solve. Pass: fails.
- Weakness: same rule as RP_006 (storm counts earlier spells, there through Finale of Promise); casting the cheap
  spell first by habit wins.

---

## 10. #20 - The Keeper reads the front face (b2_9)

- question_id: `rulesguru-36011f578b7bdd29`
- Why it holds: in the library an MDFC has only its front face (CR 712.8a): Valki, mana value 2, is found. Keeper
  sets no limit on the cast, and the caster picks the face (CR 712.11b, 601.3e): Tibalt (MV 7) for free.
- Forge check: Treasure Keeper `DigUntil ... Valid$ Card.nonLand+cmcLE3` then `Play | Defined$ Remembered |
  WithoutManaCost$ True | ValidSA$ Spell | Optional$ True` (no mana-value limit on the cast). Valki
  `AlternateMode:Modal`, back Tibalt {5}{B}{R} planeswalker. Ashnod's Altar `AB$ Mana | Cost$ Sac<1/Creature>` (the
  seat activates a sacrifice-cost mana ability itself, ARCHITECTURE "mana ability whose cost sacrifices").
  Acceptance reads `Card.getName()`, the current face's name.

```
(see b2_9.pzl)
[state] p0 MAIN2; Treasure Keeper; Ashnod's Altar; library Valki, God of Lies, Dreadmaw x2
[acceptance]
count p0 Battlefield "Tibalt, Cosmic Impostor" = 1
```

- Solution: sacrifice Treasure Keeper to Ashnod's Altar; the dies trigger reveals Valki; cast it as Tibalt, Cosmic
  Impostor without paying its mana cost.
- Misread line: "Tibalt's mana value is 7, only Valki may be cast" -> cast Valki (or decline): no Tibalt.
- Uniqueness: one sacrifice outlet, one creature, one revealed card; only the Tibalt face solves. Pass: Keeper never
  dies, fails.
- Weakness: if Forge's Play effect offers only the front face (ValidSA filtered by the found card's front), the search
  finds nothing: a Forge disagreement. The Altar's {C}{C} is unused.
