# Super-hard landfall and tokens puzzles (2026-10-22)

Two new generated families, mechanically unlike every earlier one (ping, storm, splice, splice2, red, drain, drain2,
elf, elf2, hoofdrain, fling, copy, grave, atog): **landfall** (the order of land drops, fetch cracks and Harrow decides
the damage) and **tokens** (Goblins entering and dying: Impact Tremors, Witty Roastmaster, Mayhem Devil, Krenko,
Skirk Prospector, Siege-Gang Commander, Mogg War Marshal). Both hold the gen2 bar. The kept boards are in
`eval/puzzles/hard/gen3/`. Nothing paid was run; no model was called.

## The bar (`eval/puzzle_gen.py`, families `landfall` and `tokens`)

Every kept board has:
- 13 to 15 steps (land drops, fetch cracks, casts, Prodigal Pyromancer's pings, Krenko's and Siege-Gang's
  activations; a land's or Skirk Prospector's mana is not a step);
- the opponent's life equal to the exact maximum of the family's exhaustive search over its model of the board;
- no mana left unused: landfall ends with Blaze for every mana left (X at least 1); tokens taps every Mountain (Skirk
  Prospector's last {R}, made by a Goblin given to the Devil, is not counted, as drain2's Altar);
- a trap: each plain plan that applies to the board is searched to its own best, and the best of them falls 1 to 3
  short. The plans are written in the Rule line and, with the search inputs, as `# search:` and `# trap:` comments in
  the [solution]; `eval/test_puzzle_gen.py` recomputes every committed board from them (life, steps, nothing unused, the
  plain plans, and the written line itself);
- no life total repeated within a family in one draw, and no structure twice (`shape`).

| Family | Plain plans |
|---|---|
| landfall | play a land before anything else; cast each landfall creature (Lotus Cobra, Akoum Hellkite, Tunneling Geopede) as soon as it can be paid |
| tokens | sacrifice a token whenever there is one, never a creature card; cast Impact Tremors, Witty Roastmaster and Mayhem Devil as soon as the lands pay for them |

## The landfall model (`landfall_search`, `landfall_lines`)

Cards (each rule read from `cardsfolder`): Akoum Hellkite (1 to any target per land, 2 for a Mountain), Tunneling
Geopede (1), Valakut, the Molten Pinnacle (3 for a Mountain entering with five other Mountains; enters tapped), Retreat
to Coralhelm untapping Prodigal Pyromancer (one more ping per land), Lotus Cobra (one mana of any colour per land),
Wooded Foothills (its land enters untapped), Evolving Wilds (tapped), Harrow (two basics at once, untapped, for a land
sacrificed), Azusa (two more land drops), Exploration (one), Blaze (X to any target). The library holds five Mountains
and five Forests, so a search never fails.

The search is exhaustive over every order of land drops, cracks and casts, each fetch's and Harrow's basic, Harrow's
sacrifice and the colour named for each Cobra mana, and ends with Blaze for everything left (a Blaze earlier only
wastes the mana made after it). Its mana model is the engine's, learned from probes: floating mana pays first
(`ManaPool.payManaCostFromPool`); a line floats only what a cost needs beyond the pool; and floating mana of two
colours left for a generic cost is refused, because the engine would ask which pays (`chooseManaFromPool`), a question
a written line does not answer. The maximum is therefore over lines that never meet that question. The Pyromancer is
not searched: it pings at every priority it is untapped, once to start and once per land.

The writer plays the moves on a model of the stack, in the words the probes showed:
- a land's triggers are put in order by source when more than one source triggers ("Akoum Hellkite; Valakut, the
  Molten Pinnacle; Retreat to Coralhelm; Lotus Cobra"), and their choices are asked as they go on the stack, in reverse:
  Coralhelm's mode ("You may tap or untap") and creature (when there are two), a damage target ("opponent");
- on resolving: Valakut's "yes", Coralhelm's "Untap", the Cobra mana's colour ("red", "green");
- a crack or Harrow names its basic ("Mountain"), Harrow once per land ("Select a card from your library (1 / 2)");
  Harrow's sacrifice is named "Forest [tapped]" when an untapped Forest would also fit;
- a "pass" is written only before a move the seat could make at once with the stack not empty (a crack, Harrow) and
  only where the seat is asked (a Pyromancer untapped, a fetch out, Harrow payable); a priority where only a land's
  mana could be made is passed by the seat itself (`floatChangesNothing`), and a sorcery-speed play waits by itself.

## The tokens model (`token_search`, `token_lines`)

One main phase, no combat. Cards: Impact Tremors, Witty Roastmaster (another creature entering), Mayhem Devil (any
sacrifice, Skirk Prospector's mana included), Krenko, Mob Boss (X Goblins for the Goblins out), Skirk Prospector
(sacrifice a Goblin: {R}), Siege-Gang Commander ({1}{R}, sacrifice a Goblin: 2 damage; three tokens on entering),
Mogg War Marshal (a token entering and on dying), Goblin Instigator, Beetleback Chief, Dragon Fodder, Krenko's
Command, Hordeling Outburst. At most one of each payoff (two copies' triggers would be asked in an order the writer
does not write).

The search is exhaustive over every order of casts and activations and every kind of Goblin sacrificed, with two
dominance rules: a sacrifice is made as late as it can be (an earlier one only loses a Goblin Krenko would count or a
payoff cast later), so Skirk pays only what the Mountains cannot and every Goblin goes to it at the end while a Devil
drains; and Goblins with nothing left to do (a token, Instigator or Beetleback in play) are one kind, a token taken
first. Siege-Gang's mana is floated before its Goblin is sacrificed (a Marshal's token comes only after the
activation); its target is asked before the Goblin (probe). A sacrificed card is named so that one summoning sick goes
first; a single Goblin to sacrifice is picked by the engine, unasked (probe). The line ends where the opponent reaches 0.
Goblin Bushwhacker was dropped after a probe: whether its kicker is offered depends on how the engine prices Skirk
Prospector as a source.

## The gate (`scripts/puzzle-gate/gen_gate.py -j 1`, build of this branch)

GATE-TABLE

## Not done

NOT-DONE
