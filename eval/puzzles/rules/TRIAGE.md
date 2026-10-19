# Rules-question triage

Triage of the 996 judge rules questions in `puzzles/modern/puzzles.jsonl` for conversion into engine-checked puzzles
(target shape: `eval/BENCHMARK-V1.md`, "The shape every puzzle takes"). One line per question, same order, in
`eval/puzzles/rules/triage.jsonl`. Each verdict was made by reading the question and the judge answer; "maybe" means
the answer changes the board but the solver's choice is weak, needs opponent interaction, or the setup is awkward.

Card check: every name in `cards` was matched against the `Name:` lines and filenames of
`/root/forge/forge-gui/res/cardsfolder` (normalised: case, accents and punctuation dropped; split/DFC halves accepted).
Only one name is missing: Zethi, Arcane Blademaster (one question, already "no").

## Counts

| bucket | good | maybe | no | total |
|---|---|---|---|---|
| board | 216 | 538 | 6 | 760 |
| legality | 0 | 21 | 81 | 102 |
| procedure | 0 | 0 | 12 | 12 |
| multiplayer | 0 | 0 | 54 | 54 |
| hidden | 0 | 0 | 6 | 6 |
| unobservable | 0 | 0 | 62 | 62 |
| **all** | **216** | **559** | **221** | **996** |

Good questions whose cards are all in Forge: **216 of 216**.

## Rules areas among the good questions

A question can carry several areas. Areas with at least 3 good questions:

| area | good |
|---|---|
| triggered-abilities | 83 |
| replacement | 44 |
| state-based-actions | 41 |
| combat | 40 |
| zone-changes | 36 |
| damage | 36 |
| keywords | 24 |
| counters | 23 |
| resolving | 23 |
| continuous-effects | 20 |
| stack | 14 |
| replacement-effects | 13 |
| layers | 12 |
| targets | 12 |
| copying | 11 |
| tokens | 8 |
| casting | 7 |
| damage-assignment | 7 |
| life-totals | 7 |
| type-changing | 7 |
| dependency | 6 |
| last-known-information | 6 |
| costs | 5 |
| activated-abilities | 5 |
| priority | 5 |
| auras | 5 |
| abilities | 5 |
| deathtouch | 4 |
| control | 4 |
| static-abilities | 4 |
| drawing | 4 |
| trample | 3 |
| control-change | 3 |
| lifelink | 3 |
| face-down | 3 |
| turn-structure | 3 |
| leaving-the-game | 3 |

## Recommended first 40 conversions

All are good, all cards in Forge, one engine-checkable outcome, and the solver (not the opponent) makes the choice the
rule decides. Ranked by how cleanly the right and the misread line separate, spread across areas. Step 3 of the
BENCHMARK-V1 recipe (Forge agrees with the judge) still gates each one.

| # | id | areas | rule | acceptance | misread |
|---|---|---|---|---|---|
| 1 | `rulesguru-0bd0c6801d4ab6ae` | lands, abilities, replacement | Dark Depths entering under Blood Moon has no ice counters; Moon leaving makes Marit Lage | Marit Lage token; opponent at 0 life | doesn't play Dark Depths before removing Blood Moon |
| 2 | `ask-a-magic-judge-48736b4582b499d4` | combat, damage-assignment, deathtouch | Deathtouch with trample: 1 damage is lethal to blocker, rest tramples over (CR 702.19c) | opponent loses 6 life | assigns full toughness to the indestructible blocker |
| 3 | `rulesguru-5a2ccec3176985e8` | combat, triggered-abilities, stack | Trigger order determines whether tokens get battle cry and count for Hoplite | 28 damage to defender | stacks triggers in wrong order |
| 4 | `ask-a-magic-judge-12784f4ce69d2c0d` | triggered-abilities, zone-changes | Leaves-the-battlefield triggers on dying are doubled by Teysa Karlov | four creature cards returned to battlefield by two Reveillark triggers | thinks Teysa doubles only "dies" wording, plays as if one Reveillark return |
| 5 | `ask-a-magic-judge-5c785d53bd1ec837` | state-based-actions, indestructible, damage | Damage stays marked; a creature dies when it later loses indestructible (CR 704.5g) | indestructible creature in graveyard after indestructible removed | thinks damage was already 'used up' and doesn't remove indestructible after damage |
| 6 | `ask-a-magic-judge-73fa001c73264bce` | triggered-abilities, counters | Putting counters then doubling them are two separate counter events, triggering twice | two opponent creatures tapped; opponent at 0 life after attack | expects one trigger, so cannot clear both blockers |
| 7 | `ask-a-magic-judge-d7f0f9990ce90ac7` | storm, casting, copying | Storm counts spells cast before it; order exiled cards so Empty the Warrens is cast second | six Goblin tokens (storm count 2) | casts Empty the Warrens first and gets fewer Goblins |
| 8 | `ask-a-magic-judge-80af40e521c485de` | combat, damage-assignment, trample | A blocked trampler whose blocker leaves deals all damage to the player; regenerated blocker still needs lethal | opponent loses 7 | thinks a blocked creature with no blocker deals no damage |
| 9 | `ask-a-magic-judge-469e5669db2e323e` | triggered-abilities, combat, power | Cavalcade triggers per 1-power attacker; each noncombat hit pumps Chandra's Spitfire | opponent at 0 life | undercounts triggers and doesn't attack with all three |
| 10 | `ask-a-magic-judge-e8fd98a72768f96f` | layers, dependency, basic-lands | Rootpath Purifier applies first by dependency, so Blood Moon doesn't affect basic-typed lands | spell cast with nonbasic land's colored mana resolves | believes Blood Moon wins and can't produce the color |
| 11 | `rulesguru-a57943606ece14ce` | type-changing, layers, dependency | Dependency: Life and Limb applies before Blood Moon; Saproling becomes a Mountain | Saproling taps for red | expects Forest Saproling |
| 12 | `ask-a-magic-judge-e5cdeb22ba36b383` | replacement, loyalty, counters | Choose replacement order: compleated then Doubling Season gives 10 loyalty | Vraska with 10 loyalty / can ultimate | applies Doubling Season first (8) |
| 13 | `cranial-insertion-985469215b2360f5` | replacement-effects, counters, triggered-abilities | Doubling Season doubles counters a permanent enters with | Familiar returns with 8 revival counters | expects 4 |
| 14 | `cranial-insertion-8ada733c808c6e1e` | layers, state-based-actions, continuous-effects | Ability loss in layer 6 removes the CDA; Abomination becomes 0/0 and dies | Abomination in graveyard | expects 5/5 to remain |
| 15 | `cranial-insertion-f4d18543b5633aac` | replacement-effects, triggered-abilities | Hushbringer stops ETB triggers, not ETB replacement counters | Walking Ballista enters with counters and pings | thinks Ballista enters 0/0 and dies |
| 16 | `cranial-insertion-e3171a4ba5bc5ded` | last-known-information, triggered-abilities | Dies triggers use last-known power (CR 608.2h) | opponent takes 4 damage, at 0 life | counts 2 damage |
| 17 | `rulesguru-e6c313c8939bd034` | drawing, state-based-actions, replacement | Jace's ultimate wins during resolution before the empty-library SBA | solver wins the game | fears decking and doesn't ultimate |
| 18 | `rulesguru-0acc79e57ae9d2c3` | casting, mana-abilities, triggered-abilities | City of Brass damage is a trigger, so cast Angel's Grace in response after making mana | solver survives at 1 life | casts Grace first and taps City during casting |
| 19 | `cranial-insertion-179d4e47f65915d2` | state-based-actions, triggered-abilities, stack | SBAs are checked before triggers go on the stack | opponent loses the game at 0 life | thinks Baloth gain saves them; doesn't go for it |
| 20 | `rulesguru-f6372b612682d855` | combat, damage, life-totals | Vindicator's redirected damage is a trigger; SBAs end the game first | Noel loses; Aspen wins | expects simultaneous loss |
| 21 | `cranial-insertion-cf61d74e7b55e994` | triggered-abilities, zone-changes | Order dies triggers: Gift first returns Adamantoise, avoiding exile while keeping Treasures | Adamantoise on battlefield and ten Treasures | orders wrongly and Adamantoise is exiled |
| 22 | `cranial-insertion-3fbf5047f436dcae` | triggered-abilities, intervening-if, state-based-actions | Intervening-if rechecks on resolution; second Ophiomancer trigger does nothing | exactly one Snake token | expects two Snakes |
| 23 | `rulesguru-bacde9f7b4d206d0` | damage, resolving, replacement | Self-replacement applies before other replacement effects (CR 614.15) | Swans destroyed, no cards drawn | expects draws |
| 24 | `rulesguru-3b539f6011e98300` | zone-changes, combat, state-based-actions | Kalitas's replacement applies to creatures dying simultaneously with it | Thallid exiled, Zombie token | expects normal death |
| 25 | `rulesguru-f6209dbf39cc441c` | counters, replacement, face-down | Affected player orders counter replacements | Hooded Hydra with 12 counters | applies Corpsejack first (11) |
| 26 | `rulesguru-b76e877716bc05db` | counters, replacement | Two counter replacements each apply once | Walking Ballista with 6 counters | expects 5 |
| 27 | `rulesguru-a751678cf7a59ea6` | costs, planeswalkers, replacement | Doubling Season doesn't double loyalty cost counters | Sorin gains 2 loyalty | expects 4 loyalty and an ultimate |
| 28 | `cranial-insertion-3d83db38c85a874b` | combat, damage-assignment, keywords | A trampler whose blockers left assigns all damage to the player (CR 702.19e) | opponent loses 5 | thinks it deals no damage |
| 29 | `rulesguru-6618d5b726de9bcd` | combat, priority, damage | Equip between first-strike and regular steps lets the second creature deal damage | opponent takes 5 then 6 | thinks no equipping between damage steps |
| 30 | `ask-a-magic-judge-445291a82008b67d` | power-toughness, state-based-actions | Negative power counts as negative in sums like power plus toughness | Char-Rumbler in graveyard | treats -1 as 0 and doesn't cast Cut Down |
| 31 | `cranial-insertion-d2cf5e927fd00011` | triggered-abilities, zone-changes, mutate | A mutated creature with undying returns as separate cards each with a counter | both Gemrazer and Young Wolf on battlefield with counters | expects one merged return |
| 32 | `rulesguru-5f32e2748cc06cb8` | state-based-actions, triggered-abilities, counters | LKI counts all counters including annihilated -1/-1 counters | seven Kithkin tokens | expects 0 or 1 |
| 33 | `rulesguru-0a08c6fcf75dc6b0` | zone-changes, triggered-abilities, keywords | The Ozolith copies counters without removing them; persist sees the -1/-1 counter | Puppeteer Clique stays in graveyard | expects persist return |
| 34 | `wizards-official-card-rulings-72e20521cfb28ec5` | state-based-actions, zone-changes, delirium | A spell hits the graveyard before SBAs, so delirium can save Consuming Blob | Consuming Blob survives | expects it to die |
| 35 | `rulesguru-5cd9edc73c37ef65` | copying | Copying isn't casting; Twinning Staff adds one copy to each copy effect | five Brute Force resolutions | expects more or fewer copies |
| 36 | `rulesguru-2bbe83b99611c7f2` | triggered-abilities, copying, abilities | Strionic copy resolves first, so original is the second resolution | 4 life gained and RGWU added | expects two life gains |
| 37 | `rulesguru-68a55955c39b84d5` | tokens, triggered-abilities | Each Ocelot Pride trigger copies all tokens created this turn, compounding | ten tokens | expects six |
| 38 | `ask-a-magic-judge-50b82484c7071761` | deathtouch, damage, excess-damage | Deathtouch makes 1 damage lethal so Flame Spill's excess goes to the controller | opponent loses excess damage (at 0 life) | thinks excess requires full toughness |
| 39 | `rulesguru-4ab201ccee21d530` | keywords, resolving, replacement | Repeated modes resolve sequentially; one regen shield stops only the first | Stone Golem destroyed | thinks regeneration saves it |
| 40 | `rulesguru-e5c8cdbbb68d3a55` | auras, state-based-actions | Animate Dead can't attach to a noncreature copy; it falls off and the creature is sacrificed | Metamorph sacrificed, Animate Dead in graveyard | expects a noncreature copy to stay |

## Things to know before converting

- **Near-duplicates** (same rule, different cards or identical answer): 410/411, 425/426 (and 127), 518/519, 538/539,
  580/581, 678/679, 707/708, 556/742/744, 244/545/546, 360/651, 551/697, 460/461, 919/963, 760/975, 768/990, 385/749,
  252/557, 916/926, 487/605, 416/503, 264/444 (line numbers, 0-based). Convert one of each.
- **Answers to verify**: 453 (Master of Cruelties put onto the battlefield attacking still triggering "attacks and
  isn't blocked"); 634 names Thieves' Auction where Valakut is meant; 129 depends on the 2024 Foundations
  damage-assignment change and 961 on the 2024 suspend change, which the pinned Forge may not implement.
- **Randomness**: questions built on coin flips, random discards or library order (9, 228, 687, 352, 464) were marked no.
- **Judge-sourced public text**: these are contaminated like the Possibility Storm set; they belong in the public pool.
