# partB1 screen: batch 14, questions-b1.jsonl (21 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in a draft was checked with
`grep -rlx "Name:<card>" /root/forge/forge-gui/res/cardsfolder` and its script read. Drafts are `b1_K.pzl` with
`Name:Rules pilot NNN - ...` (number them RP_2xx when kept). Each needs the usual gate: search arm proven and
accepted, pass arm rejected, uniqueness unique.

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-c2b467a34f6951f8 | board | no | mutated Soul Seizer transforming: the result (still a creature, not attached) changes nothing the acceptance grammar can see, and Forge's transform of a merged permanent is unverified |
| 1 | rulesguru-e2f874a5a60b0c4a | board | maybe | not drafted: the sacrificed Ring's trigger uses LKI, so life lost is the same whichever Ring is kept; no solver choice changes the checked result, and phase-in at untap needs an UNTAP start |
| 2 | rulesguru-a963d778c9e7fb73 | board | good | `b1_3.pzl` (Altac Bloodseeker gains first strike between the damage steps and still deals 4) |
| 3 | rulesguru-6c7ca7e941f641bd | board | good | `b1_0.pzl` (Snakeform Thing in the Ice with its trigger on the stack: transforms, no bounce) |
| 4 | rulesguru-dda7970fab4941ac | board | no | the vehicle's look after the crew effect ends is not checkable (no type in the acceptance grammar), no solver choice, mutate setup costs 6+ mana |
| 5 | rulesguru-cda1f0cbcc4b9449 | board | no | Forge disagreement on the card text: Forge's Sand Scout (current Oracle) triggers "only once each turn" (`ActivationLimit$ 1`), the judge answer says twice; also cost order (Forge pays costs itself) |
| 6 | rulesguru-bc8e3e85fe978afb | board | no | the judge answer is "unclear"; no rule to test |
| 7 | rulesguru-3b2d569a84cb722a | legality | no | a third land play after flickering Thalia and Gitrog; Forge offers only legal land plays |
| 8 | rulesguru-1ea3552d8a0cba50 | hidden | no | whether a library card is revealed; hidden information, nothing on the final state |
| 9 | rulesguru-b0474e10b9403dde | unobservable | no | Phantasmal Dreadmaw is already sacrificed by the original spell; whether it "triggers again" leaves no trace |
| 10 | rulesguru-1b9d375ac7f32def | multiplayer | no | a player leaving a multiplayer game |
| 11 | rulesguru-5afafa71764dbadd | board | maybe | not drafted: Grimgrin entering tapped is not in the acceptance grammar; making it matter needs a tap cost Forge then simply refuses (legality), and Betrayal of Flesh costs 6 |
| 12 | rulesguru-06689783471c1841 | board | no | the opponent counters the spell; the Storm player has no choice that the rule decides |
| 13 | rulesguru-074e375f905fe0a1 | legality | no | attack requirements with Floodgate; Forge validates the declaration |
| 14 | rulesguru-2875a02ad500af1f | board | good | `b1_1.pzl` (order the Kami trigger before the miracle trigger, Dark Ritual pays the miracle cost) |
| 15 | rulesguru-e09cd903055dc330 | board | no | all four permanents phase out and in whatever happens; no solver choice |
| 16 | rulesguru-35f14a2715f29a77 | board | good | `b1_2.pzl` (Breathless Knight entering with Kindercatch triggers twice; Twilight's Call stands in for Patriarch's Bidding) |
| 17 | rulesguru-164642cd4f90e920 | legality | no | casting Skirk Prospector off Conspicuous Snoop by sacrificing the Snoop; Forge offers the cast or not |
| 18 | rulesguru-dd8c99623100b7cb | legality | no | zero targets for Atarka's trigger; the target menu is Forge's |
| 19 | rulesguru-a2a13ef0c082b93f | board | maybe | not drafted: the copy choice belongs to the opponent putting Phantasmal Image in; from the solver side there is no misread line that changes play (and Forge may move Show and Tell's cards one at a time) |
| 20 | rulesguru-e82ff95b7a1dc3f8 | board | no | the second Dance of the Dead trigger does nothing; result fixed, no solver choice |

Drafted: 4 (4 good). Not drafted: 17 (4 maybe, 13 no).

---

## 1. #3 - The trigger outlives its source (b1_0)

- question_id: `rulesguru-6c7ca7e941f641bd`
- Why it holds: Thing in the Ice's trigger on the stack resolves even after the Thing loses its abilities (CR 113.7a);
  the transformed permanent is the same object, so Snakeform's "loses all abilities" still applies (CR 712.18) and
  Awoken Horror's "when this transforms" trigger does not exist.
- Forge check: Thing in the Ice `T:Mode$ SpellCast | ValidCard$ Instant,Sorcery | ValidActivatingPlayer$ You`,
  `TrigRemoveCounter: RemoveCounter ... SubAbility$ DBTransform`, `DBTransform: SetState | ConditionPresent$
  Card.Self+counters_EQ0_ICE | Mode$ Transform`. Awoken Horror `T:Mode$ Transformed | ValidCard$ Card.Self ->
  ChangeZoneAll Creature.nonHorror -> Hand`. Snakeform `Animate | RemoveAllAbilities$ True | Types$ Snake |
  RemoveCreatureTypes$ True`, then draws. Divination is a sorcery with no choices ({2}{U}, draw two). Forge keeps
  changed traits on the card across a state change, so the Horror should have no trigger; if it does, the search finds
  no line: record a Forge disagreement. Acceptance names read `c.getName()` (the current face: "Awoken Horror").
- Why two ice counters, not one: Snakeform is itself an instant and triggers the Thing. With one counter, Snakeform's
  own trigger (above it on the stack) transforms the Thing before Snakeform resolves. With two, Snakeform's trigger
  takes the first counter and Divination's trigger, under Snakeform, the last.

```
(see b1_0.pzl)
[state] p0 MAIN2; Thing in the Ice|Counters:ICE=2; Grizzly Bears; 6 Islands; hand Divination, Snakeform; library 5
[acceptance]
count p0 Battlefield "Awoken Horror" = 1
count p0 Battlefield "Grizzly Bears" = 1
```

- Solution: cast Divination (trigger T1); in response cast Snakeform targeting Thing in the Ice (trigger T2 above it).
  T2: 2 -> 1. Snakeform: the Thing loses all abilities. T1: 1 -> 0, transform; no bounce. Bears stays.
- Misread lines ("an ability-less Thing's trigger does nothing" / "a transformed card gets its abilities back"):
  Snakeform first, then Divination: no trigger from Divination, the Thing keeps one counter. Divination resolved,
  then Snakeform: its trigger transforms the Thing with abilities, Bears bounced. Snakeform on the Bears: bounced.
- Uniqueness: two spells, both needed; only "Divination, Snakeform on the Thing in response" works. Six Islands pay
  both and nothing else; the Bears (green) cannot be recast after a bounce. Pass: one counter never leaves, fails.
- Weakness: relies on the search choosing to respond with T1 on the stack (it gets priority there naturally).

---

## 2. #14 - Draw the Kami's card before the miracle (b1_1)

- question_id: `rulesguru-2875a02ad500af1f`
- Why it holds: revealing the miracle card only triggers; the miracle trigger and Kami of the Crescent Moon's draw
  trigger go on the stack together and you choose the order (CR 702.94a, 603.3b). With the Kami trigger on top, Dark
  Ritual is drawn and cast before the miracle trigger resolves, so the {3}{W} cost is payable (BBB + Plains).
- Forge check: `PhaseHandler` DRAW: `playerTurn.drawCard()` first, then `runTrigger(TriggerType.Phase ...)`, so both
  wait together. Miracle (`CardFactoryUtil`): a static `Drawn` trigger (`Number$ 1`, `OptionalDecider$ You`) reveals,
  then an `ImmediateTrigger` whose `Play | Optional$ True | PlayCost$ 3 W` casts it. Kami `T:Mode$ Phase | Phase$
  Draw ... Draw | Defined$ TriggeredPlayer`. Redress Fate `ChangeZoneAll Artifact.YouOwn,Enchantment.YouOwn Graveyard
  -> Battlefield`, `K:Miracle:3 W`. Dark Ritual instant `Mana B x3`; the pool lasts until the draw step ends. The seat
  orders simultaneous triggers (`orderSimultaneousSa`). Starting at UPKEEP on turn 2 follows a_0 / RP_177.

```
(see b1_1.pzl)
[state] turn=2, p0 UPKEEP; Kami of the Crescent Moon; Swamp; Plains; graveyard Ornithopter
library (top first) Redress Fate, Dark Ritual, 3 Dreadmaw
[acceptance]
count p0 Battlefield "Ornithopter" = 1
```

- Solution: draw Redress Fate, reveal it; order the triggers so Kami's resolves first (draw Dark Ritual); with the
  miracle trigger still on the stack cast Dark Ritual (Swamp); when the miracle trigger resolves cast Redress Fate
  for {3}{W} (BBB + Plains). Ornithopter returns.
- Misread line: "the miracle must be cast as it is drawn, before anything else" -> miracle trigger first: two lands,
  cost unpayable, Redress Fate stays in hand (hard cast is 8). Declining the reveal: same.
- Uniqueness: one order of two triggers, one spell to cast in one window (Ritual is not in hand earlier); mana choices
  are forced. Pass: never casts Ritual, so the miracle cost cannot be paid, fails.
- Weakness: rule 603.3b ordering already has several puzzles; the new part is that miracle is a trigger, not an
  instant cast on draw. If the search's trigger-order choice is not a branch point the proof is not found.

---

## 3. #16 - The Knight sees what enters with it (b1_2)

- question_id: `rulesguru-35f14a2715f29a77`
- Why it holds: enters triggers check right after the event, so Breathless Knight entering together with Kindercatch
  triggers for itself and for Kindercatch (CR 603.6a, 603.10): two counters.
- Change from the question: Patriarch's Bidding asks each player for a creature type (several hundred options for
  the solver and an opponent branch the prover would expand); Twilight's Call ("each player returns all creature
  cards") makes the same simultaneous entry with no choice.
- Forge check: Breathless Knight `T:Mode$ ChangesZone | Origin$ Graveyard | Destination$ Battlefield | ValidCard$
  Creature.YouCtrl+Other,Card.Self -> PutCounter P1P1 1`. Twilight's Call `ChangeZoneAll Creature Graveyard ->
  Battlefield` ({4}{B}{B}; `MayFlashCost:2` makes 8, unaffordable). Reanimate {B}, loses life equal to mana value
  (Knight 3, Kindercatch 6). Whether Forge fires the Knight's trigger for a card moved in the same `ChangeZoneAll`
  before the Knight itself is the Forge check the gate makes; if not, the search finds no line: a Forge disagreement.

```
(see b1_2.pzl)
[state] p0 MAIN2; 7 Swamps; hand Twilight's Call, Reanimate; graveyard Breathless Knight, Kindercatch
[acceptance]
counters p0 Battlefield "Breathless Knight" P1P1 = 2
life p0 = 20
```

- Solution: cast Twilight's Call alone: both enter, two triggers, two counters, life 20.
- Misread line: "the Knight only sees creatures that enter after it" -> Reanimate the Knight first (1 counter, life
  17), then Twilight's Call (Kindercatch: 2 counters) at 17 life, fails. Reanimate Kindercatch first: life 14, the
  Knight then enters alone (1 counter). Reanimate alone: 1 counter.
- Uniqueness: after Twilight's Call no creature card is left for Reanimate; every line with Reanimate loses life.
  Pass: nothing, fails.
- Weakness: a solver who just casts the big spell wins without weighing the rule; the life line is what makes the
  misread play visible.

---

## 4. #2 - First strike gained between the steps (b1_3)

- question_id: `rulesguru-a963d778c9e7fb73`
- Why it holds: a creature that had no first strike when first-strike damage was dealt deals damage in the regular
  step even if it gains first strike in between (CR 510.4, 702.7c). Altac Bloodseeker, pumped by the Elf's death
  between the steps, deals 4.
- Change from the question: Forge's puzzle state cannot set blockers, and the opponent choosing blocks would let it
  block Altac. Instead the Elf is tapped and the solver kills it in the gap: Goldvein Pick on the first-striking
  Scrollsmith makes a Treasure on first-strike damage, and that Treasure is the only mana for Shock, so the kill
  cannot happen before first-strike damage (where Altac would simply strike first).
- Forge check: `Combat.dealDamageThisPhase`: in the regular step a combatant deals damage unless it is in
  `combatantsThatDealtFirstStrikeDamage` (702.7c implemented). Altac `T:Mode$ ChangesZone ... Creature.OppCtrl ->
  Pump +2/+0, First Strike & Haste`. Practiced Scrollsmith 3/2 first strike. Goldvein Pick +1/+1, `DamageDone ...
  CombatDamage$ True -> Treasure`. Shock instant, 2 to any target. Willow Elf 1/1.

```
(see b1_3.pzl)
[state] p0 MAIN1; Altac Bloodseeker; Practiced Scrollsmith|Id:1; Goldvein Pick|AttachedTo:1; no lands; hand Shock
p1 life 20, Willow Elf|Tapped
[acceptance]
count p1 Graveyard "Willow Elf" = 1
life p1 = 12
```

- Solution: attack with both. First-strike step: Scrollsmith 4 (16), Treasure. With priority in that step, Shock
  Willow Elf (Treasure for {R}): Altac 4/1 first strike. Regular step: Altac deals 4 (12).
- Misread line: "Altac now has first strike, so it missed its step and deals nothing" -> keep the Shock for after
  combat and kill the Elf then: 4 + 2 = 6, opponent at 14. Shock to the face in the gap: 12 but the Elf lives.
- Uniqueness: Shock is castable only with the Treasure, which exists only from the first-strike step on; the only
  window before regular damage is the first-strike damage step. Altac alone: no Treasure. Scrollsmith alone: Altac's
  pump hits nobody. The opponent has no untapped creature and no mana: no replies. Pass: no attack, fails.
- Weakness: the board adds Goldvein Pick to force the timing; the question's block (Willow Elf blocking Scrollsmith)
  is not reproduced.

---

## Not drafted, notes

- #1 (One Ring, LKI): a draft would start at UNTAP with a `PhasedOut` Ring; the solver's only choice (which Ring the
  legend rule keeps) changes future draws, not the life loss the rule is about.
- #5 (Sand Scout): recorded as a disagreement with the judge's text, not with the rules: the card's Oracle and Forge
  both cap it at once per turn.
- #19 (Show and Tell + Phantasmal Image): would need the opponent to put the Image in; a solver-side board has no
  misread play.
