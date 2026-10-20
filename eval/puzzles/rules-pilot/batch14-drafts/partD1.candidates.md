# partD1 screen: batch 14, questions-d1.jsonl (35 RulesGuru questions)

Read-only screen; nothing here has been run. Every card in a draft was checked with
`grep -rlx "Name:<card>" /root/forge/forge-gui/res/cardsfolder` and its script read. Drafts are `d1_K.pzl` with
`Name:Rules pilot NNN - ...` (number them RP_2xx when kept). Each needs the usual gate: search arm proven and
accepted, pass arm rejected, uniqueness unique. None of the 35 question ids appears anywhere under `eval/puzzles/`.

Notes that apply to several drafts:
- Where land taps don't matter the mana is in the pool (`p0manapool`), per the batch 13 repairs. d1_4 (equip on
  the next turn) and d1_15 (Extraplanar Lens counts land taps) need real lands.
- Game end: `Goal:Survive` with `Turns:N` wins at the upkeep of turn N+1 (`Puzzle.java`), so acceptance is read
  *after* turn N's cleanup. d1_0 depends on this: Act of Treason's control has already ended when it is read.
- The pass arm (`PuzzleArms.Fixed(pass)`) declares no blockers, declines every optional choice, and takes option 0
  otherwise. No draft here has a forced choice on the pass line, and in the three blocking boards (d1_4, d1_7, d1_14)
  not blocking fails.

| # | question_id | bucket | verdict | draft / reason |
|---|---|---|---|---|
| 0 | rulesguru-d37babe5fca689e5 | board | maybe | not drafted: the interleaving can be built (Mentor + Legionnaire + Rite + Raise the Alarm, four triggers), but mana floats between trigger resolutions, so untap-untap-pay-pay with a manual tap in between also draws two: several correct lines differing in actions, uniqueness "multiple" |
| 1 | rulesguru-316fb98e9145bab4 | board | no | the choice between the two exile replacements changes only a void counter, and the Voidwalker that could use it is exiled too; no outcome turns on it (and replacement order is rarely offered) |
| 2 | rulesguru-7f210b8c12413d3a | board | no | near-duplicate of b2_9 (same rule, CR 712.8a/712.11b: find Valki, cast Tibalt) |
| 3 | rulesguru-6d58637ef6f16b0d | board | good | `d1_0.pzl` (Act of Treason for Press into Service; Closet the stolen Guardian, it stays yours) |
| 4 | rulesguru-61aef9d2e62bb4a8 | board | good | `d1_1.pzl` (Bloodchief drains once per card of a dying merged creature: Cast Down beats Thought Scour) |
| 5 | rulesguru-4c4e5aca3fb62f41 | board | maybe | not drafted: only the opponent untaps in response, and the solver's play is the same either way. Worth a forced-line check: Forge's Dream Leash is `CantTarget ... EffectZone$ Stack`, live while it resolves, so the resolution target check may count the untapped Merrow as illegal (a possible Forge disagreement) |
| 6 | rulesguru-bb44751fbe7a0a13 | board | good | `d1_2.pzl` (two one-card draws pass Alms Collector: Think Twice + flashback, not Divination; equivalent board) |
| 7 | rulesguru-0e81bbed755e53d7 | board | no | at your upkeep with an empty library the draw step loses anyway; no solver choice turns on "reveal is not draw" |
| 8 | rulesguru-281451ea9012b26e | board | good | `d1_3.pzl` (Kalitas exiles the Naga, Broodmoth never triggers: Cast Down the Naga, not the Broodmoth) |
| 9 | rulesguru-02b138bb51b05153 | board | maybe | not drafted: the counter is the opponent's choice and the solver plays the same whatever the reply; Remanding your own Plumb is contrived and has no misread line that differs |
| 10 | rulesguru-e492fbecd8c19c56 | board | good | `d1_4.pzl` (wither + annihilation leaves no counters for Luxior: don't block, equip, swing 5) |
| 11 | rulesguru-d2fe449354030495 | board | maybe | `d1_5.pzl` (sacrifice Divinity, not Memnite, to Altar: Heliod stops being a creature, no Harsh Mentor damage) |
| 12 | rulesguru-0f1b911141640826 | board | no | near-duplicate of RP_035 (Animate Dead on a card that is not a creature, CR 303.4j/704.5m) |
| 13 | rulesguru-b997eeeccd8ef425 | board | good | `d1_6.pzl` (Stage copies Dark Depths with no ice; keep the Stage under the legend rule for Marit Lage) |
| 14 | rulesguru-20473fa4f4afa02c | legality | no | the opponent fetches; Archive Trap's alternative cost is legality, and Forge offers only legal casts |
| 15 | rulesguru-638cb9cee9e6e086 | board | no | needs a Teferi emblem, a Dark Deal from the opponent and emblem triggers on the solver's side; the answer's "won't prevent" is loose (an exiled Wall can't be sacrificed); no clean solver choice |
| 16 | rulesguru-031fece7640a3d44 | board | good | `d1_7.pzl` (lifelink gains full power off a 3/1: block the 3/1, not the 2/3) |
| 17 | rulesguru-588e42031ee3ab8f | legality | no | "a card named Elvish Clancaller" fixes what the search can find; Forge offers only that, so there is no misread line |
| 18 | rulesguru-11cc8df3f4c47c96 | board | maybe | `d1_8.pzl` (devour the last creature; Porphyry Nodes stays). Likely Forge disagreement, see the section |
| 19 | rulesguru-c2ae1d99c4d8cced | board | maybe | `d1_9.pzl` (the replaced Angel gets Warboss's haste: Divine Visitation, not Lightning Bolt) |
| 20 | rulesguru-a4c0c58f72888809 | legality | no | whether Urza's Saga still taps for {C} is on the play menu, which Forge computes; nothing to misread in play |
| 21 | rulesguru-17e880e1095dca42 | legality | no | casting face down from Monk Class exile is legality; the exiled card's permission can't be set up without the upkeep trigger |
| 22 | rulesguru-9edaa8334bcdf4c9 | board | good | `d1_10.pzl` (Domri's mana gives Spellbreaker a second riot: counter and haste, 4 damage) |
| 23 | rulesguru-eec2a2738abc5224 | board | no | the discard happens in cleanup whatever anyone does; no solver choice |
| 24 | rulesguru-5167054987c7092e | legality | no | the trigger's targets are chosen on the stack while the Primordial is on the battlefield; Forge offers only legal targets |
| 25 | rulesguru-36006fee251ef90a | board | maybe | `d1_11.pzl` (Augustin discounts Beacon Bolt's jump-start to {U}{R}: discard Shock, Bolt for 3) |
| 26 | rulesguru-edfbfe3a62b1dfb3 | board | good | `d1_12.pzl` (the copy makes Polukranos monstrous, the original does nothing: X = 6 for exactly six counters) |
| 27 | rulesguru-a6e41ae29a8eef76 | legality | no | Forge doesn't offer a summoning-sick attack; the rule is RP_049's (CR 302.6) |
| 28 | rulesguru-51b6a78dff81f895 | legality | no | how to pay a {2/B} hybrid is a payment choice, not an outcome; no board can tell the payments apart |
| 29 | rulesguru-ed6832ad42fd86f6 | board | good | `d1_13.pzl` (an umbra'd creature is not destroyed: Naturalize the Umbra, then Decree draws two) |
| 30 | rulesguru-015054e71168af07 | board | no | Morningtide's Light's "until your next turn" prevention needs the opponent's earlier cast; attacking the planeswalker is the only productive play anyway, so no misread line differs |
| 31 | rulesguru-b4828341be2b41c7 | board | good | `d1_14.pzl` (block with Death's Shadow: damage is simultaneous, it is 5/5 at the check). Near RP_080 (Shadow + SBA after a spell) but tests combat simultaneity |
| 32 | rulesguru-94206e12dbbd3d93 | legality | no | near-duplicate of RP_049 (continuous control, CR 302.6); Forge decides who may attack |
| 33 | rulesguru-49901ad49964da9a | legality | no | the returned Specter is a new object, and Forge simply doesn't offer the cast; legality |
| 34 | rulesguru-82384a118b1b83a9 | board | good | `d1_15.pzl` (Lens matches names, not Urborg's Swamp type: imprint a Mountain, Blaze for 5) |

Drafted: 16 (11 good, 5 maybe). Not drafted: 19 (3 maybe, 16 no).

---

## 1. #3 - The Closet keeps what it returns (d1_0)

- question_id: `rulesguru-6d58637ef6f16b0d`
- Why it holds: Conjurer's Closet returns the card "under your control"; the returned permanent is a new object (CR
  400.7) that the temporary control effect no longer applies to. A stolen creature flickered at your end step is
  yours for good.
- Equivalent board: Act of Treason in place of Press into Service. Same control-until-end-of-turn effect, without
  support's two optional counter targets, which would only add search branches.
- Forge check: Act of Treason `GainControl | LoseControl$ EOT | Untap | AddKWs$ Haste`. Conjurer's Closet
  `Phase$ End of Turn | ValidPlayer$ You | OptionalDecider$ You`, `ChangeZone ... Creature.YouCtrl -> Exile`,
  then `ChangeZone Defined$ Remembered -> Battlefield | GainControl$ True`. Elvish Visionary has an ETB draw (the
  misread target). Sworn Guardian is a vanilla 1/3. Acceptance is read at turn 2's upkeep, after cleanup has ended
  Act of Treason's effect.

```
[state] p0 MAIN2, pool R R R; Conjurer's Closet, Elvish Visionary; hand Act of Treason; p1 Sworn Guardian
[acceptance]
count p0 Battlefield "Sworn Guardian" = 1
```

- Solution: Act of Treason on Sworn Guardian. At your end step, Closet targets the Guardian: exiled and returned under
  your control.
- Misread line: "the Closet returns it to its owner's control, so flickering it gains nothing". So you flicker
  Elvish Visionary for a card, or decline, and the Guardian goes back to the opponent at cleanup.
- Uniqueness: one spell and one Closet target. Act of Treason on your own Visionary does nothing. Pass: nothing cast,
  and the optional Closet trigger is declined, so it fails.
- Weakness: (1) whether Forge's EOT control-loss command (bound to the old card object) leaves the flickered card
  alone. If it doesn't, the search finds nothing: a Forge disagreement. (2) A solver who just tries the Closet on
  the stolen creature wins without reasoning about control.

---

## 2. #4 - A merged creature dies as three cards (d1_1)

- question_id: `rulesguru-61aef9d2e62bb4a8`
- Why it holds: a merged permanent that leaves the battlefield becomes each of its component cards (CR 729.3, 721.3).
  Three cards reach the graveyard, and Bloodchief Ascension triggers three times.
- Forge check: Bloodchief Ascension `ChangesZone ... Destination$ Graveyard | ValidCard$ Card.!token+OppOwn |
  IsPresent$ Card.Self+counters_GE3_QUEST | OptionalDecider$ You`, lose 2 / gain 2. Setup `MergedCards:` is used by
  RP_211 (`Gemrazer|MergedCards:Bear Cub,Dirge Bat`). Forge splitting merged cards on death is already relied on by
  the undying puzzle (cranial-insertion-d2cf5e927fd00011). Cast Down `Creature.nonLegendary`, Thought Scour mills
  2 from the target player. Pool B U: Cast Down ({1}{B}) or Thought Scour ({U}), never both.

```
[state] p0 MAIN2, pool B U; Bloodchief Ascension (3 quest); hand Cast Down, Thought Scour
p1 life 6; Plated Wurm merged with Huntmaster Liger x2; library 5 Plains
[acceptance]
life p1 <= 0
```

- Solution: Cast Down the merged Plated Wurm. Three cards go to the graveyard and you take all three drains: 6 -> 0.
- Misread line: "it's one permanent, so one trigger (2 life); milling two is worth 4". So you cast Thought Scour
  on the opponent: 6 -> 2.
- Uniqueness: one castable spell. Cast Down's only target is the merged creature. Each "may" must be yes. Pass: fails.
- Weakness: none known. The Ligers' mutate triggers never fire (the merge is set up, not cast).

---

## 3. #6 - One card at a time slips past the Collector (d1_2)

- question_id: `rulesguru-bb44751fbe7a0a13`
- Why it holds: Alms Collector replaces one event of drawing two or more cards. Two separate one-card draws are two
  events, and neither is replaced. Same rule as the judge's turn draw plus Rites of Flourishing's draw.
- Equivalent board: the solver makes the draws on its own turn with Think Twice and its flashback, instead of the
  judge's opponent-side draw step, where the solver would have no choice.
- Forge check: Alms Collector `R:Event$ DrawCards | ValidPlayer$ Player.Opponent | Number$ GE2 -> DrawEach (You &
  ReplacedPlayer)`. Think Twice {1}{U} draw 1, `Flashback:2 U`. Divination {2}{U} draw 2. Pool five {U}.

```
[state] p0 MAIN2, pool U x5; hand Think Twice, Divination; library 5; p1 Alms Collector, library 3
[acceptance]
count p0 Library * = 3
count p1 Library * = 3
```

- Solution: cast Think Twice, then flash it back (2 + 3 mana). You draw two, the opponent none.
- Misread line: "the Collector stops any second draw" leaves no way to do it. The careless line, Divination plus
  Think Twice (3 + 2), gives you 1 + 1 but the opponent draws 1.
- Uniqueness: five mana allows TT + flashback or Divination + TT, and only the first passes. Pass: no draws, fails.
- Weakness: the misread itself has no line of its own: a misreading solver just finds nothing that works.

---

## 4. #8 - Kalitas leaves the Broodmoth nothing (d1_3)

- question_id: `rulesguru-281451ea9012b26e`
- Why it holds: Kalitas replaces "would die" with exile (CR 614.6). The Naga never dies (CR 700.4), so Luminous
  Broodmoth's dies trigger has nothing to see.
- Forge check: Kalitas `R:Event$ Moved | Origin$ Battlefield | Destination$ Graveyard | ValidLKI$
  Creature.!token+OppCtrl -> Exile + b_2_2_zombie` ("Zombie Token"). Broodmoth `T:Mode$ ChangesZone | Origin$
  Battlefield | Destination$ Graveyard`: the move is replaced, so it never fires. Cast Down can't target the
  legendary Kalitas. Kalitas's own ability ({2}{B}, sacrifice) is unaffordable with {B}{B}.

```
[state] p0 MAIN2, pool B B; Kalitas; hand Cast Down; p1 Luminous Broodmoth, Harrier Naga
[acceptance]
count p1 Exile "Harrier Naga" = 1
tokens p0 Battlefield "Zombie Token" = 1
```

- Solution: Cast Down Harrier Naga. It is exiled and you get a Zombie.
- Misread line: "the Broodmoth would bring the Naga back with flying, so kill the Broodmoth first". With one removal
  spell, the Naga stays.
- Uniqueness: two legal targets, and only the Naga solves. Pass: fails.
- Weakness: a solver who ignores the Broodmoth wins by default (the plain line is the right one).

---

## 5. #10 - Wither cancels the counters Luxior counts (d1_4)

- question_id: `rulesguru-e492fbecd8c19c56`
- Why it holds: wither damage arrives as -1/-1 counters (CR 702.80a). Pairs of +1/+1 and -1/-1 counters are removed
  as a state-based action (CR 704.5q, 122.3). A blocking Raider ends with no counters, and Luxior ("+1/+1 for each
  counter") then gives nothing.
- Equivalent board: the solver chooses whether to block, so the counters are cancelled during the puzzle; the judge
  had Nick already blocking. The board starts at the opponent's declare-attackers step (RP_007's setup) and runs to
  the end of the solver's next turn, because equip is sorcery speed.
- Forge check: Goblin Sky Raider 1/2 flying. Sickle Ripper 2/1 `K:Wither`. Luxior `Equip:3` (and the
  planeswalker equip, which has no target), `AddPower$ X | X = Equipped$CardCounters.ALL`. Forge's SBA cancels
  +1/+1 against -1/-1 counters.

```
[state] p1's turn 1, declare attackers; Sickle Ripper attacking
p0: Goblin Sky Raider (2 +1/+1 counters), Luxior (unattached), 3 Wastes; p1 life 5   Turns:2
[acceptance]
life p1 <= 0
```

- Solution: don't block (take 2). On your turn, equip Luxior to the Raider ({3}): a 5/6 flier. Attack for 5.
- Misread line: "block: the Raider (3/4) kills the Ripper, survives, and has four counters for Luxior (5/6)". In
  fact it is a 1/2 with no counters, and Luxior makes it a 1/2: 1 damage.
- Uniqueness: only the no-block, equip, attack line deals 5. Without Luxior the Raider deals 3. Pass: no block, no
  equip, no attack, so it fails.
- Weakness: three Wastes are real lands (the equip is on the next turn, so a pool would empty). The draw on turn 2 is
  a Dreadmaw, which can't be cast.

---

## 6. #11 - Heliod stops being a creature first (d1_5)

- question_id: `rulesguru-d2fe449354030495`
- Why it holds: Harsh Mentor triggers when the ability has been activated (CR 602.2b, 601.2i), and it looks at the
  source then. Sacrificing Divinity of Pride (five hybrid {W/B}) drops devotion to white from 6 to 1, so Heliod is a
  plain enchantment, and Mentor ignores enchantments. Mana abilities never trigger it (CR 605.1a).
- Forge check: Harsh Mentor `Mode$ AbilityCast | ValidCard$ Artifact.inZoneBattlefield,Creature.inZoneBattlefield,
  Land.inZoneBattlefield | ValidSA$ SpellAbility.!ManaAbility`, read when the ability is put on the stack. Heliod
  `RemoveType$ Creature | CheckSVar$ X (Devotion.White) | LT5`, token `Cost$ 2 W W` (`Cleric Token`). Phyrexian
  Altar `AB$ Mana | Cost$ Sac<1/Creature> | Produced$ Any`. Pool C C W, so the Altar's mana must be {W}: one colour
  choice.

```
[state] p0 MAIN2, life 2, pool C C W; Heliod, Divinity of Pride, Memnite, Phyrexian Altar; p1 Harsh Mentor
[acceptance]
tokens p0 Battlefield "Cleric Token" = 1
count p0 Battlefield "Memnite" = 1
life p0 = 2
```

- Solution: sacrifice Divinity of Pride to the Altar for {W}, then activate Heliod with C C W W. No trigger, a Cleric.
- Misread line: "Heliod is a creature as I start activating, so Mentor triggers whatever I sacrifice". The cheap-looking
  sacrifice is then Memnite: Heliod stays a creature and Mentor deals 2, killing you.
- Uniqueness: sacrificing both creatures also avoids the trigger but loses Memnite (acceptance). Sacrificing Heliod
  leaves nothing to activate. Pass: no token, fails.
- Weakness (why maybe): the seat floats the Altar's mana before activating (it is on the play menu, b2_9). Heliod is
  then already not a creature when activation starts. That tests the devotion consequence more than the judge's "mid-
  activation" timing. If the harness's payment can't reach the Altar during the cost, only this order exists.

---

## 7. #13 - The Stage copies no ice (d1_6)

- question_id: `rulesguru-b997eeeccd8ef425`
- Why it holds: copy effects copy no counters (CR 707.2). The Stage-as-Dark-Depths has zero ice counters, so its
  state trigger fires. The legend rule makes you keep one (CR 704.5j); keep the Stage, and its trigger sacrifices it
  for Marit Lage (CR 603.8).
- Forge check: Thespian's Stage `AB$ Clone | Cost$ 2 T | ValidTgts$ Land | GainThisAbility$ True`. Dark Depths
  `T:Mode$ Always | IsPresent$ Card.Self+counters_EQ0_ICE`, `Cost$ Mandatory Sac<1/CARDNAME>`, token `marit_lage`.
  RP's Blood Moon puzzle reads `tokens p0 Battlefield "Marit Lage" = 1`. Depths' own {3} is unaffordable from C C.

```
[state] p0 MAIN2, pool C C; Thespian's Stage, Dark Depths (ICE=10)
[acceptance]
tokens p0 Battlefield "Marit Lage" = 1
```

- Solution: Stage copies Dark Depths. Under the legend rule keep the Stage; its trigger makes Marit Lage.
- Misread line: "the copy has ten ice counters too" means nothing happens. The rule-blind line keeps the original
  Dark Depths, so the Stage goes and no token comes.
- Uniqueness: one activation, two targets (copying itself does nothing), one legend choice. Pass: fails.
- Weakness: the legend-rule choice's prompt must be a recorded seat choice (it is a solver choice on the main line).

---

## 8. #16 - Lifelink is not capped by toughness (d1_7)

- question_id: `rulesguru-031fece7640a3d44`
- Why it holds: a blocker deals combat damage equal to its power, whatever the attacker's toughness (CR 510.1a).
  Lifelink gains that much (CR 702.15b): 3 from the 3/1.
- Forge check: Scrounger of Souls 3/4 `K:Lifelink`. Dromoka Warrior 3/1 and Minotaur Warrior 2/3, both vanilla.
  Minotaur is listed first, so a first-option block would be the wrong one; the pass arm doesn't block anyway.

```
[state] p1's turn 1, declare attackers; Minotaur Warrior (2/3) and Dromoka Warrior (3/1) attacking
p0 life 10; Scrounger of Souls
[acceptance]
life p0 = 11
```

- Solution: block Dromoka Warrior. You gain 3 and take 2: 11.
- Misread line: "lifelink gains only what the blocked creature can absorb". Blocking the 3/1 nets +1 - 2, blocking
  the 2/3 nets +3 - 3, so you block the Minotaur: 10.
- Uniqueness: block the 3/1 (11), block the 2/3 (10), no block (5). Pass: 5, fails.
- Weakness: "block the bigger attacker" is also the habit line.

---

## 9. #18 - Devour leaves no empty moment (d1_8)

- question_id: `rulesguru-11cc8df3f4c47c96`
- Why it holds: devour sacrifices as the creature enters, in one event (CR 702.82a, 614.1c). The battlefield never
  has zero creatures, and Porphyry Nodes's state trigger never triggers.
- Forge check: Tar Fiend `K:Devour:2`, ETB `Discard | ValidTgts$ Player | NumCards$ X (Count$Devoured Card) | Mode$
  TgtChoose`. Porphyry Nodes `T:Mode$ Always | IsPresent$ Creature | PresentCompare$ EQ0 -> Sacrifice`. **Likely
  Forge disagreement**: `GameAction.changeZone` runs `checkStaticAbilities()` after every zone change (~line 576), and
  that runs `TriggerType.Always` triggers (~line 1244). So the Orgg's sacrifice inside the devour replacement, while
  Tar Fiend is still on the stack, probably fires Nodes's trigger. `WrappedAbility` (~line 405) does not recheck an
  Always trigger's condition on resolution. Kept as a draft so the gate decides: not found means a Forge
  disagreement row, not a bad board.

```
[state] p0 MAIN2, pool B x6; Porphyry Nodes, Trained Orgg; hand Tar Fiend; p1 hand 1 Plains
[acceptance]
count p0 Battlefield "Porphyry Nodes" = 1
count p1 Hand * = 0
```

- Solution: cast Tar Fiend, devour Trained Orgg, and its trigger makes the opponent discard their card. Nodes stays.
- Misread line: "devouring the only creature leaves none, and Nodes is sacrificed". So you don't devour, and the
  opponent keeps the card.
- Uniqueness: devour Orgg and target the opponent. Targeting yourself does nothing (empty hand). Pass: fails.
- Weakness: besides the Forge risk, a solver who ignores Nodes devours anyway.

---

## 10. #19 - The Angel inherits the haste (d1_9)

- question_id: `rulesguru-c2ae1d99c4d8cced`
- Why it holds: Divine Visitation replaces the token's creation (CR 614.1a), and Legion Warboss's "that token gains
  haste ... attacks this combat" applies to the token actually created (CR 608.2c). The Angel attacks this turn.
- Forge check: Divine Visitation `R:Event$ CreateToken -> ReplaceToken w_4_4_angel_flying_vigilance`. Legion Warboss
  `Token r_1_1_goblin | RememberTokens | PumpKeywords$ Haste`, then a `MustAttack` effect on Remembered.
  `TokenEffectBase` runs the CreateToken replacement (line 88) before the pump and remember (lines 102, 198), so
  the Angel gets both. Warboss is `SummonSick` so it can't attack itself; otherwise Angel + Warboss would be a second
  solution.

```
[state] p0 MAIN1, pool W W W R R; Legion Warboss (SummonSick); hand Divine Visitation, Lightning Bolt
p1 life 4; Grizzly Bears
[acceptance]
life p1 <= 0
```

- Solution: cast Divine Visitation in main 1. At the beginning of combat Warboss makes a 4/4 flying Angel with haste,
  which must attack: 4.
- Misread line: "the Angel is a different token, so no haste". Then Visitation does nothing this turn, and you take
  Bolt (3) + the Goblin (1), which the Bears block.
- Uniqueness: five mana is exactly Visitation; Bolt first leaves 4. The Bolt line wins only if the opponent doesn't
  block, so it is not proven against every reply. Pass: the forced Goblin attack alone, which fails.
- Weakness (why maybe): the misread has no winning line, so a misreading solver plays the Bolt line by elimination.

---

## 11. #22 - Two riots, two choices (d1_10)

- question_id: `rulesguru-9edaa8334bcdf4c9`
- Why it holds: each instance of riot works separately (CR 702.136b). Gruul Spellbreaker cast with Domri's mana has
  two: a +1/+1 counter from one, haste from the other.
- Equivalent board: the judge only remarks that both instances may pick haste; the board makes the split useful.
- Forge check: Domri `AB$ Mana | Cost$ AddCounter<1/LOYALTY> | Produced$ Combo R G | AddsKeywords$ Riot |
  AddsKeywordsValid$ Spell.Creature`. `SpellAbility` (~line 903) adds the keyword to the card with no end. Riot
  (`CardFactoryUtil` ~2470) is an ETB replacement per instance: `Animate Haste | UnlessCost$ AddCounter<1/P1P1>`,
  asked through `payCostToPreventEffect`. Spellbreaker {1}{R}{G}: the pool is R R, so Domri must make {G}.

```
[state] p0 MAIN1, pool R R; Domri, Chaos Bringer (5); hand Gruul Spellbreaker; p1 life 4, no creatures
[acceptance]
life p1 <= 0
```

- Solution: Domri +1 for {G}. Cast Spellbreaker; one riot gives the counter, the other haste. Attack as a 4/4: 4.
- Misread line: "riot twice is still one choice". That gives a hasty 3/3 (3 damage), or a 4/4 that can't attack.
- Uniqueness: Domri's {R} leaves Spellbreaker uncastable. -3 instead of +1 gives no mana. Pass: fails.
- Weakness: (1) if Forge drops the mana-added keyword as the card moves to the battlefield, or merges duplicate riot,
  the search finds nothing (a Forge disagreement). (2) The two riot answers in either order (counter/haste or
  haste/counter) may read as two solutions if the fingerprint orders noted choices.

---

## 12. #25 - The Arbiter discounts a jump-start (d1_11)

- question_id: `rulesguru-36006fee251ef90a`
- Why it holds: cost reductions apply to any spell you cast, from any zone (CR 601.2f). Jump-start's cost is the
  mana cost plus a discard (CR 702.133a), and Augustin takes {1} off: {U}{R}.
- Forge check: Augustin `ReduceCost | ValidCard$ Card.Blue | Type$ Spell | Activator$ You | Amount$ 1`. Beacon Bolt
  {1}{U}{R} `K:Jump-start`, damage `Count$ValidGraveyard,Exile Instant.YouOwn,Sorcery.YouOwn`: Opt, Opt and the
  discarded Shock = 3 (the Bolt itself is on the stack). Hill Giant 3/3.

```
[state] p0 MAIN2, pool U R; Grand Arbiter Augustin IV; hand Shock; graveyard Beacon Bolt, Opt, Opt; p1 Hill Giant
[acceptance]
count p1 Graveyard "Hill Giant" = 1
```

- Solution: jump-start Beacon Bolt for {U}{R}, discarding Shock, at Hill Giant: 3 damage.
- Misread line: "the reduction doesn't apply from the graveyard, so it costs 3". Then you Shock the Giant: 2, and it
  lives.
- Uniqueness: one discard and one useful target (Augustin is the other legal target). Pass: fails.
- Weakness (why maybe): if the play menu lists only affordable plays, the solver sees the jump-start offered and needs
  no rule knowledge to try it.

---

## 13. #26 - Monstrous only once (d1_12)

- question_id: `rulesguru-edfbfe3a62b1dfb3`
- Why it holds: Unbound Flourishing copies the monstrosity ability. The copy resolves and makes Polukranos monstrous
  with X counters and one X-damage trigger. The original then does nothing, since it is already monstrous (CR
  701.37a-b).
- Forge check: Polukranos `AB$ PutCounter | Cost$ X X G | Monstrosity$ X`. `CountersPutEffect` line 489 skips
  Monstrosity when `isMonstrous()`. Trigger `BecomeMonstrous`, damage `MonstrosityX = TriggerCount$Amount`,
  `TargetMin$ 0`, then each target damages Polukranos. Unbound Flourishing `SpellAbilityCast | ValidSA$
  ...Activated | HasXManaCost$ True -> CopySpellAbility`. Pool 13 {G}: X at most 6.

```
[state] p0 MAIN2, pool G x13; Unbound Flourishing, Polukranos; p1 Colossal Dreadmaw
[acceptance]
count p1 Graveyard "Colossal Dreadmaw" = 1
counters p0 Battlefield "Polukranos, World Eater" P1P1 = 6
```

- Solution: activate with X = 6, so one set of 6 counters (11/11). The trigger deals 6 to the Dreadmaw, and it deals
  6 back, which Polukranos survives.
- Misread line: "the copy doubles it: X = 3 gives 6 counters and two 3-damage triggers", exactly the acceptance. It
  really gives 3 counters and 3 damage. The exact counter count is what makes the misreader pick a smaller X.
- Uniqueness: X = 6 is the only value with six counters. Zero targets fails. Pass: fails.
- Weakness: the copy must carry X (`xPaid`). If Forge's copy has X = 0, the counters are 0 and the search finds
  nothing: a Forge disagreement to note.

---

## 14. #29 - The umbra'd creature is not destroyed (d1_13)

- question_id: `rulesguru-ed6832ad42fd86f6`
- Why it holds: umbra armor replaces the creature's destruction: its damage is removed and the Aura is destroyed
  instead (CR 702.89a). The creature isn't "destroyed this way", and the Aura isn't a creature, so Decree draws
  nothing for either.
- Forge check: Decree of Pain `DestroyAll | RememberDestroyed$ True`, draw `Remembered$Amount`. `DestroyAllEffect`
  (~line 95) remembers only when `destroy()` returns true; the umbra replacement makes it false, and the Aura's own
  destruction is the umbra's sub-ability, not remembered. Eland Umbra `K:Umbra armor`, +0/+4. Naturalize targets
  artifacts or enchantments: only the Umbra. The pool has an extra {B} in case the auto-payer spends a {B} on
  Naturalize's generic.

```
[state] p0 MAIN2, pool B B B G + C x7; hand Naturalize, Decree of Pain; library 5
p1 Raptor Companion (Id 1) + Eland Umbra attached; Grizzly Bears
[acceptance]
count p0 Library * = 3
count p1 Graveyard "Raptor Companion" = 1
```

- Solution: Naturalize Eland Umbra, then Decree of Pain: both creatures destroyed, two cards.
- Misread line: "the umbra'd creature still counts as destroyed", so Decree alone. The Raptor survives and you draw 1.
- Uniqueness: Decree then Naturalize draws 1 (same action set, and it fails). Cycling Decree (draw 1, -2/-2) plus
  Naturalize kills both but draws 1. Pass: fails.
- Weakness: a cautious solver who removes the Umbra "just in case" wins without the rule.

---

## 15. #31 - The Shadow grows with the damage (d1_14)

- question_id: `rulesguru-b4828341be2b41c7`
- Why it holds: all combat damage is dealt at once (CR 510.2). Lethal damage is checked afterwards as a state-based
  action (CR 704.5g), when you are at 8 and Death's Shadow is a 5/5 with 3 damage.
- Forge check: Death's Shadow `AddPower$ -X | AddToughness$ -X | X = YourLifeTotal`. Synchronous Sliver 3/3, all
  Slivers vigilance (so attacking untapped). Forge deals combat damage as one map and then checks SBAs.

```
[state] p1's turn 1, declare attackers; Synchronous Sliver x2 attacking; p0 life 11, Death's Shadow (2/2)
[acceptance]
life p0 = 8
count p0 Battlefield "Death's Shadow" = 1
```

- Solution: block one Sliver with Death's Shadow. Take 3 (11 -> 8); the Shadow is 5/5 with 3 damage and survives.
- Misread line: "the 2/2 Shadow takes 3 and dies before the life loss counts". So you don't block: 5 life (the
  Shadow is 8/8, but life is wrong).
- Uniqueness: block or not, and the two Slivers are identical. Pass: no block, 5, fails.
- Weakness: near RP_080 (a spell's damage and life loss, then SBAs). Same "SBAs come after" idea, a combat event here.

---

## 16. #34 - Urborg changes types, not names (d1_15)

- question_id: `rulesguru-82384a118b1b83a9`
- Why it holds: Urborg adds the Swamp type to every land but changes no names (CR 205.3, 201.2). Extraplanar Lens
  matches names. Exiling the Swamp doubles nothing else; exiling a Mountain doubles the other two Mountains.
- Forge check: Urborg `AddType$ Swamp`. Extraplanar Lens imprint `ValidTgts$ Land.YouCtrl | OptionalDecider$ You`,
  then `TapsForMana | ValidCard$ Land.sharesNameWith Imprinted -> ManaReflected (Produced)`. Blaze `X R`, any
  target. Lens is paid from the pool (C C C), so all five lands stay untapped.

```
[state] p0 MAIN2, pool C C C; Urborg, Swamp, Mountain x3; hand Extraplanar Lens, Blaze; p1 life 5
[acceptance]
life p1 <= 0
```

- Solution: cast Lens, imprinting a Mountain. Urborg 1 + Swamp 1 + two Mountains x2 = 6 mana: Blaze X = 5.
- Misread line: "every land is a Swamp now, so exiling the Swamp doubles all of them (8 mana)". Really Urborg 1 +
  three Mountains = 4: Blaze for 3.
- Uniqueness: no imprint gives 5 mana (X = 4). The Swamp or Urborg gives 4. Only a Mountain gives 6 (any of the
  three; handles are dropped). Pass: fails.
- Weakness: (1) land taps are search branches here (unavoidable: the Lens counts taps). (2) If the auto-payer does
  not count the Lens's extra mana, the solver must tap the lands first and cast Blaze from the pool. The search can
  do that, but it is a longer line.

---

## Not drafted (maybe)

- #0 Mentor of the Meek: board sketch `Cryptolith Rite; Mentor of the Meek|Tapped; Sunstrike Legionnaire|Tapped`,
  pool W W, hand Raise the Alarm, acceptance draw 2. Resolution order untap, pay, untap, pay works. But mana floats
  between trigger resolutions, so untap, untap, *tap for mana*, pay, pay also works, with different actions. A
  uniqueness "multiple" is almost certain. All the lines apply the rule correctly; there is no clean one-solution
  board.
- #5 Dream Leash: see the table. A forced line (opponent untaps the Merrow in response) would show whether Forge's
  `EffectZone$ Stack` CantTarget static makes the Aura fizzle: a Forge-disagreement probe, not a puzzle.
- #9 Plumb the Forbidden: the copy trigger is independent of the spell (CR 113.7a), but only an opponent's counter
  shows it, and the solver's line is the same whatever the reply.
