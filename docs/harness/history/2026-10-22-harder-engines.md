# Super-hard generated engines: drain2, elf2, hoofdrain (2026-10-22)

The first generated suite's drain and elf boards passed the gate trivially (52 of 52 kept) and GPT-6 Luna missed the
hardest of them for structure (it never found the Druid and Sentinel loop), not by a near miss. This note records the
stricter bar, what it does to each family, and the boards kept (`eval/puzzles/hard/gen2/`).

## The bar (`eval/puzzles/puzzle_gen.py`, families `drain2`, `elf2`, `hoofdrain`)

Every kept board has:
- 13 to 15 steps (casts, land plays, non-mana activations, the attack);
- the opponent's life equal to the exact maximum of the exhaustive search;
- no mana left unused at the end of the best line: drain, every Swamp tapped and the land drop taken (the Altar's
  {C} is not counted: the last deaths make it with nothing left to buy); elf, nothing floating past Craterhoof;
- a **trap**: a plain plan is one natural habit imposed on the otherwise exhaustive search, and the best of the plans
  that apply to the board falls 1 to 3 short. A board where a plan wins, or where the best is 4 or more short, is
  dropped. The plans and the trap are written in the Rule line and, with the search inputs, as `# search:` and
  `# trap:` comments in the [solution] (the line reader skips `#` lines); `eval/tests/test_puzzle_gen.py` recomputes every
  committed board from them;
- no life total repeated within a family in one gate batch, and no structure (the cards reduced to their roles:
  which drainer does not matter, which elf names do not) committed twice.

| Family | Plain plans |
|---|---|
| drain2 | play the land first; cast the drainers last; sacrifice as soon as a death drains |
| elf2 | Craterhoof as soon as the mana abilities alone can pay it; never tap a Sentinel; tap the mana creatures first; never tap the Druid itself; never tap a mana elf or the Priest |
| hoofdrain | elf2's, plus: sacrifice each elf once the Druid has tapped it; attack without sacrificing; sacrifice only the elves cast this turn |

drain2 draws two or three sacrifice outlets that differ (Ashnod's Altar, Carrion Feeder, Viscera Seer) and more
recursion (Skeleton, Bloodghast with the land drop, Gravecrawler). elf2 adds a starting Priest of Titania ({G} per elf
on the battlefield: it taps late, or it attacks). hoofdrain is the cross family: the elf engine with one or two
drainers and Viscera Seer; Craterhoof's trigger counts X when it resolves, so the line sacrifices every creature that
will not attack (the elves cast, the starting ones tapped) after the trigger and before the attack.

## What the bar does to each family

- **elf2 is nearly empty.** An elf plain plan either is the loop (the opening Druid activation, the order of costs:
  dropped as plans because they never lose) or misses it by far more than 3: one elf fewer costs one damage per
  attacker, and Craterhoof plus the Sentinels already make two or more. An enumeration of every elf2 search input
  (Forests 0-3, Sentinels 1-3, mana elves 0-3, Priest or not, 11 to 13 elves of each cost mix) finds the bar met only
  by two or three Forests, one Sentinel and a Priest (8 inputs, lives 53 and 59 only, the trap Craterhoof one elf
  early, 3 short). Two are kept; more would repeat them.
- **drain2's lives cluster.** 9 distinct totals (9 to 27) over all draws; each batch takes at most one board a total.
  The trap is mostly "play the land first" (Bloodghast must die before the land drop), then "cast the drainers last".
- **hoofdrain is rich but its trap is one habit.** Every kept board's best plain plan is "sacrifice only the elves
  cast": it forgets the tapped Druid, mana elves or Priest, one drain each (1 to 3 short). The other plans miss by 6 or
  more.

## The gate (`scripts/puzzle-gate/gen_gate.py -j 1`, build of this branch)

| Batch | drain2 | elf2 | hoofdrain | Kept |
|---|---|---|---|---|
| A | 6 | 0 | 19 | 25 of 25 |
| B | 8 | 2 | 15 | 25 of 25 |
| C | 7 | 0 | 17 | 24 of 24 |
| **all** | **21** | **2** | **51** | **74 of 74** |

Every written line played as written and solved; every floor (random 0 of 20, PASS, first option, greedy 0 of 4, Forge's
AI 0 of 4) failed every board. Kept: drain2 lives 9 to 27 (9 distinct), traps 1/2/3 short on 5/9/7 boards; elf2 53
and 59, 3 short; hoofdrain lives 60 to 172 (34 distinct over three batches), 1/2/3 short on 27/21/3. Steps 13/14/15:
32/6/36. No board was dropped by the gate: as for the first suite, the gate does not separate
these families; the bar above is the generator's. Gate output: `runs/puzzles/hard-gen2-gate/gen2-engines-{A,B,C}-gate`.
`gen_gate.py --resume` continues a gate stopped by a time limit from its line results and floor rows.

## Not done

- No model has tried these boards; they are not in a frozen suite (`gen_suite.py` freezes one from a gate's output).
- hoofdrain's trap is a single habit; drain2's mostly one. A second near-miss per family (another outlet that pays
  mana, Geralf's Messenger's undying) would need new cards in the searches and the gate's check of their wording.
- elf2 cannot grow under this bar without a new piece whose misuse costs 1 to 3 (a lord, or a second Druid).
