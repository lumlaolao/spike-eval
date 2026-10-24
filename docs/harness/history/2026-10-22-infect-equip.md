# Super-hard infect and equip puzzles: the third hard suite (2026-10-22)

Two new generated families, mechanically unlike the fourteen before them (ping, storm, splice, splice2, red, drain,
drain2, elf, elf2, hoofdrain, fling, copy, grave, atog): **infect** counts poison counters instead of life, and
**equip** is one combat decided by what Equipment and Auras each attacker carries. Boards are in
`eval/puzzles/hard/gen3/`; the generators, searches and line writers in `eval/puzzle_gen.py`. Nothing paid was run; no
model was called.

## The bar (unchanged from the second suite)

Every kept board has 13 to 15 steps (casts, land plays, non-mana activations including equip and Inkmoth Nexus's
animation, the attack); its goal (the opponent's poison counters, or life) set to the exact maximum of an exhaustive
search over a model of the board; no mana left unused at the end of the best line (every land tapped, the land drop
taken); and a **trap**: one natural habit imposed on the otherwise exhaustive search, the best of the habits that
apply to the board falling 1 to 3 short. The written `[solution]` is in menu words, with `# steps:`, `# search:` and
`# trap:` lines; `eval/test_puzzle_gen.py` (`Gen3BoardsTest`) re-derives every committed board from them.

## Infect

**How poison is set and checked.** A `.pzl` gives the opponent poison with `p1counters=POISON=N` (Forge's own puzzles
do it, e.g. `PS_ONE1.pzl`). `[acceptance]` has no poison condition (`PuzzleAcceptance`: life, counts, tokens, card
counters, winner), and `winner p0` alone proves nothing under `Goal:Survive` (surviving the turn wins it). So the
boards use the engine goal `Goal:Win`, `Turns:1`, with `winner p0`. Probed: the same line at 10 poison won
("Poisoned"), at 9 lost (`winner p0 (was p1)`).

**Cards** (every rule read from `cardsfolder`): Glistener Elf, Blighted Agent, Ichorclaw Myr (1/1 infect, not sick),
Inkmoth Nexus ({1}: a 1/1 flying infect creature; or {T}: {C}); Mutagenic Growth ({G/P}), Groundswell (+4 after a
land), Might of Old Krosa (+4 only in a main phase), Rancor, Become Immense (+6, delve), Battlegrowth (+1/+1 counter),
Travel Preparations (two counters), Courage in Crisis (a counter, then proliferate), Thirsting Roots (proliferate, or a
basic land to hand), Tezzeret's Gambit ({3}{U/P}: draw two, proliferate), Unnatural Restoration (a permanent card back
from the graveyard, proliferate), Gitaxian Probe, Mishra's Bauble and Land Grant (free with no land in hand). The
library is all Forests, so every draw and search is known.

**The search** (`infect_search`): every order of land, animation, Bauble cracks, casts (with each payment: a Forest or
2 life for {G/P}, Inkmoth's {C} for generic, delve), the attack and the combat window, and the second main phase.
Proliferate gives the opponent a counter once it has one and, before combat, each creature with a +1/+1 counter
another; the mana pool empties between phases, so after-combat mana is an untapped land. **The goal is exact**: the
opponent starts at 10 minus the maximum (`poisoned`: before combat a proliferate already reaches it), or at 0 when
only a line that proliferates after combat damage reaches 10 (the maximum with an unpoisoned opponent is then 10).

**Why the hand is built around Become Immense.** Poison cannot exceed 10, and an infect step usually gives 1 to 4: a
random hand that reaches 13 steps reaches 15 poison. The steps that give nothing but are needed are the graveyard's
(each Bauble is two steps, each Probe one, each Restoration returns a Bauble to crack again) and the mana's (Land
Grant, the land drop), all paying for Become Immense's delve.

**Plain plans** (`INFECT_PLANS`): Become Immense as soon as it can be paid; pump only after attackers are declared;
proliferate only after combat; never proliferate; Inkmoth Nexus always attacks; play the land first.

## Equip

**Cards**: Fencing Ace, Boros Swiftblade (double strike), Savannah Lions, Glory Seeker, Hill Giant, Puresteel Paladin
(equip {0} with three artifacts; may draw when Equipment enters); Bonesplitter, Short Sword, Leonin Scimitar, Vulshok
Morningstar, Cranial Plating (+1 per artifact), Fireshrieker (double strike), Loxodon Warhammer, Grafted Wargear (equip
{0}), Embercleave (flash, {1} less per attacker, attaches as it enters, +1/+1 and double strike); Ethereal Armor (+1
per enchantment), All That Glitters (+1 per artifact and enchantment), Rancor; Sigarda's Aid (Equipment and Auras with
flash; each Equipment attaches as it enters) and Foundry Inspector (artifact spells {1} less). Every land is a Jungle
Shrine: one land name, so no payment asks which lands stay untapped.

**The search** (`equip_search`): the plays in a fixed order that loses nothing (Inspector, Aid and Paladin first, then
the Equipment, the Auras, the equips, the attack, Embercleave in combat), each one played or skipped; which creature
carries what is chosen at the end in closed form (`_equip_assign`): every bonus but double strike adds to one
creature's power, so all of them go on the creature that doubles them best, and Fireshrieker and Embercleave are
placed by trying each creature. No cost depends on the target, so the order loses nothing.

**Plain plans** (`EQUIP_PLANS`): everything on the creature with the most power (when that one is not a double
striker); cast every Equipment that can be paid before equipping (when not everything can be paid); Embercleave in
the main phase; equip each Equipment as soon as it is on the battlefield (with Puresteel Paladin and fewer than three
artifacts at the start).

## What the engine taught the written lines (probes, `puzzle_baselines.py line`)

- A {G/P} spell asks how to pay ("pay 1 Phyrexian symbol with 2 life") only when a Forest and life can both pay
  (life above 2); Gitaxian Probe and Tezzeret's Gambit, with no blue source, pay life unasked.
- With Inkmoth Nexus and a Forest untapped, every payment that leaves a source untapped asks which stay
  (`untap: Inkmoth Nexus; Forest`); the line names exactly the sources it keeps.
- Delve asks which cards to exile (the line exiles the Bolts and spells first, Baubles last, for Restoration).
- Proliferate asks one permanent or player at a time; Thirsting Roots asks its mode; a search asks for the Forest.
- Land Grant's free cost is its own menu entry ("cast Land Grant ... Reveal your hand").
- An Equipment entering with Sigarda's Aid and Puresteel Paladin out sets off two triggers: an order question, the
  Aid's target, then the Aid's accept and the Paladin's draw (declined).
- With Paladin's metalcraft the equip menu shows "Equip {0}"; the line names the cost it pays.
- A spell cast while another is on the stack goes on top (the script arm casts at once), so the line writes a `pass`
  exactly when the next play could be made over it.

## The gate

`scripts/puzzle-gate/gen_gate.py -j 1` on a build of this branch's overlay (unchanged from main at df493c4). Gate
records: `runs/puzzles/hard-gen3-gate/gen3-gate-*/gate.json`, `floors.json` and `line/`.

| Batch | Seeds (infect / equip) | infect | equip | Kept |
|---|---|---|---|---|
| A | 101 / 102 | 7 | 8 | 15 of 15 |
| B | 201 / 202 | 5 | 7 | 12 of 12 |
| **all** | | **12** | **15** | **27 of 27** |

Every written line played as written and solved in the steps it states; every floor (random 0 of 20, PASS, first
option, greedy, Forge's AI 0 of 4 each) failed every board. No board was dropped: as for the second suite, the bar is
the generator's, and the gate confirms the written lines and the floors. The batches share no board (by key).

The kept set:
- **infect (12)**: the opponent starts at 0 poison on every board, so 10 is the exact maximum and the last counter
  comes from a proliferate after combat (Unnatural Restoration on 11, Thirsting Roots on 1); steps 13 on 8 boards,
  14 on 4. The trap is 1 short on all 12: Become Immense cast as soon as it can be paid spends the mana that the
  after-combat proliferate needed (binding on 12), and never proliferating stops at the 9 combat gives (11). Every
  line cracks Baubles and casts Become Immense in combat; Mutagenic Growth on 9, Land Grant on 6, Rancor on 2.
- **equip (15)**: life 39 to 65; steps 13 on 12 boards, 14 on 2, 15 on 1; the trap 2 short on 5 boards and 3 short
  on 10. Binding plain plans: everything on the creature with the most power (12), cast every Equipment before
  equipping (5), equip at once (3), Embercleave in the main phase (2). Every kept board has Puresteel Paladin (its
  free equips are what make 13 steps affordable); Foundry Inspector on 12, Sigarda's Aid on 7, Embercleave cast in
  combat on 2; 2 to 4 attackers.

## Not done

- No model has tried these boards. They were not in a frozen suite when this note was written; they are frozen
  since as `eval/suites/puzzles-hard-gen3-v1.json` (`eval/PUZZLES.md`, "The third hard suite").
- **infect is one shape.** Every kept board is "fill the graveyard for Become Immense, hold a land for the last
  proliferate"; none starts poisoned, and Inkmoth Nexus, the +1/+1 counters (Battlegrowth, Travel Preparations,
  Courage in Crisis), Groundswell, Might of Old Krosa and Tezzeret's Gambit are in the search but in no kept line.
  Hands without Become Immense reach 4 to 9 steps under the 10-poison cap (a 1,500-draw run found none at 13), and a
  poisoned opponent means a lower maximum, so fewer steps. A second infect shape needs low-yield steps that are not
  the graveyard's (more Inkmoth-like lands, or a cost that taxes each pump).
- equip leans on Puresteel Paladin: without its free equips a 13-step line costs more mana than the boards hold.
  Embercleave in combat is the line on 2 boards only.
- Not modelled (so a line the engine allows could beat a stated maximum only through them): re-equipping, Rancor
  returning to hand, Cranial Plating's {B}{B} attach (no black source on these boards), casting an Equipment or Aura
  in combat with Sigarda's Aid for anything but Embercleave (no board gains by it), and Tezzeret's Gambit drawing
  anything but Forests (the library is all Forests).
