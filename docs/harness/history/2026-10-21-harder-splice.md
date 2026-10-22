# Super-hard splice puzzles (2026-10-21)

The first generated suite's paid check (`2026-10-20-generated-hard-puzzles.md`) found splice the hardest family:
GPT-6 Luna solved 1 of 21 splice boards, and none of its 20 attempts on the ten hardest boards (four of them
splice at 15 steps), missing by 3 to 8 life. This note records the splice defects fixed first, the bar for a
"super-hard" board, and the boards kept (`eval/puzzles/hard/gen2/`). Nothing paid was run; no model was called.

## Defects fixed first (`eval/puzzle_gen.py`)

1. **11 of 40 splice lines stopped before the win** (`solved=False unconsumed=12..19` in the gate's line stage).
   Replaying splice_2b37741c0e25 showed why: the script arm casts each card the moment its own cost is payable, so
   the third Desperate Ritual went on the stack over the second, unresolved one; the Glacial Ray splice it was
   meant to carry needed that ritual's mana, so it was not offered (`[AUTO-LEGAL] splice(none payable)->none`), and
   every later line fell out of step. One writer, `written_line`, now plays every splice, splice2 and red line on a
   model of the stack and writes a `pass` (one an object) before a cast until no ritual is left unresolved. Three of
   the 11, rewritten from the same hands, now solve in the engine (splice_2b37741c0e25, splice_9410f1fe25f6,
   splice_9c7a6f5bedc7).
2. **Splice cards were picked by name**, so no spell carried two copies of one card; Forge offers each copy. Both
   searches now enumerate multisets (`splice_sets`). On 4 Desperate Ritual, 2 Glacial Ray and Lava Spike with
   Thermo-Alchemist and 3 Mountains the old search found 19, the true maximum is 21; splice_9c7a6f5bedc7's stated
   maximum of 18 is beaten by 2. The engine took "Desperate Ritual; Desperate Ritual" and "Glacial Ray; Glacial Ray"
   as written.
3. **Kiln Fiend's attack was added after the search**, so a cast that only pumps the Fiend (a free Gitaxian Probe)
   was missed; `red_search` now counts the +3 a cast in its objective.

## The bar (family `splice2`)

Every kept board has:
- 13 to 15 steps (casts, Thermo-Alchemist's pings, the attack);
- the opponent's life set to the exact maximum of `red_search` over the board (no slack);
- no mana left at the end of the best line (among the lines that deal the most, the search prefers the one that
  leaves the least, then the fewest steps; a board whose best leaves any is not drawn);
- **a trap**: two plain plans, each played as well as it can be (an exhaustive search under its restriction), and
  the better of them falls 1 to 3 short:
  - *never splice*: `red_search(..., splice=False)`;
  - *the greedy splicer*: each Arcane spell carries every splice card it can still pay for (a maximal payable
    multiset), `red_search(..., splice="max")`.
  A board where a plain plan wins is no trap; one where the better plan is 4 or more short is too plainly
  different. The trap's damage is in the Rule line ("trap T (never splicing deals A, splicing every payable card
  onto each Arcane spell B)") and in the board dict (`trap`, `never`, `greedy`);
- the written `[solution]` in menu words, `# steps: N`, and `[acceptance] life p1 <= 0`, as before.

Cards: Desperate Ritual and Glacial Ray (the splices), Lava Spike (Arcane), Lightning Bolt, Shock, Pyretic Ritual,
Rite of Flame, Seething Song, Gitaxian Probe, Grapeshot; Thermo-Alchemist on most boards, Kiln Fiend and Goblin
Electromancer attacking on some. Fireblast and Lava Dart are left out: their sacrifice wants every Mountain tapped,
which the search models by order only, not by floating mana, so a line that floats first could beat its maximum.

`eval/test_puzzle_gen.py` re-derives every committed gen2 board from its own .pzl (life, steps, no mana left, the
trap as stated).

## The gate (`scripts/puzzle-gate/gen_gate.py`, one board at a time)

Build: the overlay of this branch's base (main at 96caa9b) compiled for the gate. Gate
records: `runs/puzzles/hard-gen2-gate/gen2-splice-gate-b*/gate.json` and `floors.json`.

| Batch | Seed | Drawn (after de-duplication) | Kept |
|---|---|---|---|
| b1 | 101 | 26 | 26 |
| b2 | 202 | 21 | 21 |
| b3 | 303 | 20 | 20 |
| b4a | 404 | 10 (the first 10 of b4 by name) | 10 |
| b4b | 404 | 21 (the rest of b4) | not gated: the target was met |

Every kept board's line played as written and solved, and every floor failed it: random 0 of 20, PASS, first
option and greedy 0 of 4 each, and Forge's AI 0 of 4 on every board (the gate allows 1). No board was dropped.
Batches drawn with different seeds repeat boards (11 of 78 in b1 to b3), so each batch is de-duplicated against
the earlier ones by board key before it is gated; a single draw of 30,000 finds 283 distinct boards. The first
b1 gate and the first b4 gate were killed mid-run (an interrupted session, then a container restart) and rerun
from the start; the gate has no resume.

## The kept set (77 boards)

- Opponent's life 14 to 43 (median 20); steps: 22 boards of 13, 17 of 14, 38 of 15.
- The trap falls 1 short on 17 boards, 2 on 54, 3 on 6. The binding plain plan is the greedy splicer on 61 boards,
  never splicing on 6, and both deal the same on 10: splicing is plainly right, but splicing everything is not.
- 41 lines pass to let a ritual resolve before a splice; 14 splice two copies of one card onto one spell.
- Every board has Thermo-Alchemist; 60 have nothing else, 5 add Kiln Fiend, 7 Goblin Electromancer, 5 both.
- Besides the splices (every board): Rite of Flame on 56, Lava Spike 48, Lightning Bolt 33, Gitaxian Probe 20,
  Pyretic Ritual 16, Grapeshot 15, Shock 15, Seething Song 4.

## Not done

- No model has tried these boards; whether the trap catches a model is unmeasured. A paid check (one attempt a
  board, a few dollars) would say.
- The gen2 boards are not in a frozen suite (`gen_suite.py`), and have no open / held-out split yet.
- 21 drawn boards (b4b) were not gated: the target of 70 was met without them.
- Most boards are Thermo-Alchemist alone; Kiln Fiend and Goblin Electromancer boards are a minority, because those
  boards more often miss the 13-15 steps or the trap bound.
- Fireblast and Lava Dart's flashback stay out until the search models floating mana before a sacrifice; the red
  family's search has the same gap (its stated maximum could be beaten by a line that floats first).
- The old splice family's 11 dropped boards were not regenerated; new splice boards draw with the fixed writer.
