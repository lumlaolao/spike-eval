# Contrast pairs of splice puzzles: one or two visible details, a different winning line (2026-11-06)

**Question.** Can we build contrast pairs of splice puzzles: two boards that differ in one small, visible detail (a
card in hand, the opponent's life by 1 to 3, a Mountain tapped or one more, Thermo-Alchemist tapped or not) where the
winning line changes, so that a model playing the same line on both fails one? Such a pair separates reading the board
from a remembered pattern (survey idea E5, contrast sets, taken from rules items to play;
`2026-11-05-agent-compute-and-feedback-survey.md`; Gardner et al., arXiv:2004.02709). The tool is the splice board's
model (`puzzle_gen.splice_best`), engine-witnessed on 48 of 48 positions in `2026-11-06-step-regret-board-model.md` and
59 of 60 regret steps in `2026-11-06-where-models-lose-splice.md`.

**Answer so far: yes, but almost never with one detail of the kinds asked for; a card swap is the one single detail
that works, and the cleanest pairs move a resource and the opponent's life together.** From 35 base boards
(29 `splice_*` in `eval/puzzles/hard/gen/` and the 6 `splice2_*` in `eval/puzzles/hard/gen2/` whose cards are all in
the model; 71 splice2 boards refused, each naming a card outside it) the script tried 6,122 perturbed boards:

- **No change of the opponent's life, a Mountain, Thermo-Alchemist or one card added or removed, made alone, ever gives
  a pair** (0 of 701 tried, 323 of them winnable). This is a property of the model, not a sampling gap: more of any
  resource at the same life keeps every winning line of the smaller board (a line is still castable with more mana, a
  card more, or an untapped Alchemist, and deals the same), and a lower life keeps every winning line of the higher.
  So one side's lines always win the other, and a model playing them passes both. The hard boards are also exactly
  lethal, so taking a resource away (or raising the life) loses outright: 0 of 70 Alchemist changes are winnable.
- **A card swapped for another is the only single detail that contrasts:** 198 of 564 swaps have no shared winning
  line, but in 140 of those the only difference is that a winning spell of one side is not in the other's hand
  (the contrast is a missing card, which a model cannot fail by pattern). 58 trap: at the point the lines must part,
  one side's winning spell is castable on the other side and loses there (56 one-way, 2 partial, **0 two-way**).
  None is the minimal Lava Spike / Lightning Bolt swap (same cost, same damage, only Arcane differs: 0 qualify).
- **With the opponent's life moved by 1 to 3 as a second detail**, 1,570 of 4,857 pairs have no shared winning line;
  **36 trap both ways** (each side's winning spell is castable on the other and loses there, at every point the
  lines part), 987 one way. A Thermo-Alchemist change plus life never contrasts (0 of 105 winnable): a ping is one
  damage wherever it goes.

The single-detail pair closest to the example asked for is `splice_a80fa0f9f24d` with one Desperate Ritual swapped for
a Lava Spike: on the original (three Mountains) the only winning first spell is Desperate Ritual cast alone; on the
swapped board the only one is Lava Spike carrying the Ritual as a splice, and the Ritual cast alone there (castable)
leaves the best line 3 short (16 of 19). It is one-way: the swapped board's winning spell is not castable on the
original (no Lava Spike to carry, and three mana cannot pay a Ritual or Glacial Ray carrying a Ritual).

**In the engine, all 10 selected pairs hold** (5 one-detail swaps, 5 two-detail resource-and-life changes): both
sides of every pair are solved by their own line (20 of 20, zero-model script arm), and every cross line that can be
played, the other side's winning move followed by this board's best line, falls short (15 of 15, by exactly the
model's margin, 1 to 4 life).

**Casting in response** is outside what this can find: the model assumes no line gains by casting in response
(assumption 3 of `step_regret_board.py`), and a puzzle board starts on an empty stack, so a pair "won only by waiting /
only by casting in response" is not in its space.

## What was run (free: Python, then zero-model engine games)

```bash
python3 eval/puzzles/contrast_pairs.py eval/puzzles/hard/gen/splice_*.pzl eval/puzzles/hard/gen2/splice2_*.pzl \
  --out runs/contrast/pairs.jsonl --select 10 --write eval/puzzles/contrast --scripts runs/contrast/scripts
```

`eval/puzzles/contrast_pairs.py` (new, `contrast-pairs-v1`; tests `eval/tests/test_contrast_pairs.py`, written red
first) reads each base `.pzl`, enumerates its perturbations (one detail: the life by -3 to +3, one Mountain more,
fewer or tapped, Thermo-Alchemist tapped, removed or added, one card of the five added or removed, one card swapped
for another; two details: each resource change or swap with the life moved 1 to 3 the same way), and for each pair:

1. Both sides winnable (`splice_best`, the model's maximum, at least the opponent's life).
2. **No shared winning line.** A line is the spells cast in order, each with its splice set; Thermo-Alchemist's pings
   are free, taken before every spell, and so are each board's own, not part of the line. The two boards are walked
   together with the same spells while both stay winnable; a joint position where no common spell keeps both winnable
   is a **divergence**, and if the walk can reach a position where both opponents are dead, a line wins both and the
   pair is out.
3. **Its trap kind** at each divergence: "both" when a winning spell (with splices) of each side is castable on the
   other side and loses there; one way when only one side's is; "none" when neither side's winning spells can be cast
   on the other. The pair is two-way if every divergence is "both", one-way if every divergence traps at least one way.
4. **Rank**, smallest change first: details (1 before 2), trap (two-way, one-way, partial), depth of the shallowest
   divergence (the spells cast before the lines must part; 0 is the first decision), then cost (a life step of n
   costs n; a card swap 1 when only Arcane differs, else 2).

`--select 10` takes the best-ranked qualifying pairs, one per base board, a change not yet taken first, alternating
one- and two-detail pairs. For each it writes both puzzles (`eval/puzzles/contrast/contrast_NNa.pzl` is the base board
re-written with its own line through the divergence as `[solution]`; `...b.pzl` the perturbed board; the generator's
form, `Mountain|Tapped` and `Thermo-Alchemist|Tapped` for a tapped permanent) and four scripts: each side's own line,
and each **cross line**: the shared prefix, the other side's winning spell at the divergence, then this board's best
line from there (the most this board can deal once it has made the other side's move). A cross line is not written when
the other side's winning spell cannot be cast here (one-way pairs, one direction).

Rows (git-ignored): `runs/contrast/pairs.jsonl` (every pair tried, with its verdict and divergence), the scripts in
`runs/contrast/scripts/`.

The engine check (zero-model script arm against Forge's AI, seed 1, 2 JVMs, under the shared Forge lock; build
`sr-green`, compiled from this branch's overlay `4c5de4a329c8`):

```bash
python3 eval/puzzles/puzzle_baselines.py --build <sr-green> line --pzl eval/puzzles/contrast/contrast_NNx.pzl \
  [--script runs/contrast/scripts/contrast_NN-<side>_on_<side>.txt] --seeds 1 --out-dir runs/contrast/engine/<run>
```

one game per own line (the `.pzl`'s `[solution]`, no `--script`) and per cross line; verdicts in
`runs/contrast/engine-verdicts.txt`, result rows copied to `runs/contrast/rows-*.jsonl` (git-ignored), game logs
deleted (disk). A side's own line is engine-verified when its verdict is
`played` (every line played, no drift, solved); a cross line when every line played with no drift and the puzzle was
not solved.

## Counts

| Change | Details | Tried | Both winnable | No shared winning line | Trap two-way | Trap one-way or partial |
|---|---|---|---|---|---|---|
| Opponent's life -3 to +3 | 1 | 210 | 105 | 0 | 0 | 0 |
| A Mountain more, fewer or tapped | 1 | 105 | 37 | 0 | 0 | 0 |
| Thermo-Alchemist tapped, removed or added | 1 | 70 | 0 | 0 | 0 | 0 |
| A card added or removed | 1 | 316 | 181 | 0 | 0 | 0 |
| A card swapped | 1 | 564 | 222 | 198 | 0 | 58 |
| Mountain change and life | 2 | 315 | 77 | 71 | 0 | 71 |
| Alchemist change and life | 2 | 210 | 105 | 0 | 0 | 0 |
| Card added or removed and life | 2 | 948 | 377 | 359 | 30 | 329 |
| Card swapped and life | 2 | 3,384 | 1,212 | 1,140 | 6 | 608 |
| **All** | | **6,122** | **2,316** | **1,768** | **36** | **1,066** |

The qualifying single-detail pairs come from 27 of the 35 base boards; the two-way pairs from 9. Depth of the shallowest
divergence (qualifying pairs): one detail, 1 at depth 0, 10 at 1, 12 at 2, 13 at 3, 20 at 4, 2 at 5; two details, 22
at 0, 114 at 1, 204 at 2, then 154, 181, 180, 149 and 40 at 3 to 7.

## The pairs

Engine check: **20 of 20 own lines solved** (verdict `played`: every line played, no drift, solved) and **15 of 15
cross lines failed** with every line played, no drift and no play that did not happen; each engine shortfall (the
opponent's life left) equals the model's, pair by pair. 5 cross lines were not run because the other side's winning
spell cannot be cast there (one-way pairs). About 3 minutes for 35 games, 2 JVMs. The 20 `.pzl` files are committed
in `eval/puzzles/contrast/` (84 KB; the command above regenerates them byte for byte).

"X spliced with Y" is X cast carrying Y as a splice (Y stays in hand); "alone" is an Arcane spell cast with no splice.
The prefix shown is one shared line to the shallowest divergence (others may exist); "A's move on B" is A's winning
spell there made on B, then B's best line from that point.

| Pair | Base board (A) | Detail(s) changed for B | Trap | Spells both sides cast first (depth) | A's winning spells there | B's winning spells there | A's move on B, then B's best: model / engine | B's move on A, then A's best: model / engine | Own lines solve A and B (engine) |
|---|---|---|---|---|---|---|---|---|---|
| contrast_01 | `splice_a80fa0f9f24d` (opp 19) | Desperate Ritual -> Lava Spike | one-way | none (0) | Desperate Ritual alone | Lava Spike spliced with Desperate Ritual | 16 of 19 / fails, 3 short | not castable | yes |
| contrast_02 | `splice_30feb75fc23b` (opp 17) | one Glacial Ray fewer; opponent at 16 | two-way | Desperate Ritual alone, Lava Spike spliced with Desperate Ritual (2) | Desperate Ritual alone; Glacial Ray alone; Glacial Ray spliced with Desperate Ritual; Lightning Bolt | Desperate Ritual spliced with Glacial Ray | 14 of 16 / fails, 2 short | 16 of 17 / fails, 1 short | yes |
| contrast_03 | `splice2_1b9c1807c487` (opp 21) | Lava Spike -> Desperate Ritual | one-way | Lava Spike spliced with Desperate Ritual (1) | Lava Spike spliced with Desperate Ritual | Desperate Ritual alone | not castable | 18 of 21 / fails, 3 short | yes |
| contrast_04 | `splice_4916b9e3acef` (opp 13) | one more Glacial Ray; opponent at 14 | two-way | Desperate Ritual alone, Desperate Ritual alone (2) | Desperate Ritual spliced with Glacial Ray | Desperate Ritual alone; Glacial Ray alone; Glacial Ray spliced with Desperate Ritual; Lightning Bolt | 13 of 14 / fails, 1 short | 11 of 13 / fails, 2 short | yes |
| contrast_05 | `splice_f3bc218b2fb5` (opp 15) | Shock -> Desperate Ritual | one-way | Desperate Ritual alone (1) | Lava Spike spliced with Desperate Ritual | Desperate Ritual spliced with Desperate Ritual | 13 of 15 / fails, 2 short | not castable | yes |
| contrast_06 | `splice_3507112343d5` (opp 16) | one more Glacial Ray; opponent at 17 | two-way | Desperate Ritual alone, Desperate Ritual alone, Lava Spike spliced with Desperate Ritual (3) | Desperate Ritual spliced with Glacial Ray | Desperate Ritual alone; Glacial Ray alone; Glacial Ray spliced with Desperate Ritual; Shock | 16 of 17 / fails, 1 short | 14 of 16 / fails, 2 short | yes |
| contrast_07 | `splice_de0284ae89a7` (opp 17) | Desperate Ritual -> Lightning Bolt | one-way | Desperate Ritual alone, Lava Spike spliced with Desperate Ritual, Desperate Ritual alone (3) | Desperate Ritual spliced with Glacial Ray | Glacial Ray alone; Lightning Bolt | not castable | 15 of 17 / fails, 2 short | yes |
| contrast_08 | `splice_578f7dac0fb6` (opp 20) | one Shock fewer; opponent at 19 | two-way | Desperate Ritual alone, Lava Spike spliced with Desperate Ritual, Lava Spike spliced with Desperate Ritual (3) | Desperate Ritual alone; Glacial Ray alone; Glacial Ray spliced with Desperate Ritual; Shock | Desperate Ritual spliced with Glacial Ray | 17 of 19 / fails, 2 short | 19 of 20 / fails, 1 short | yes |
| contrast_09 | `splice2_7245b7790faf` (opp 17) | Lightning Bolt -> Desperate Ritual | one-way | Desperate Ritual alone, Lava Spike spliced with Desperate Ritual, Desperate Ritual alone, Glacial Ray alone (4) | Lightning Bolt; Shock | Desperate Ritual alone | 13 of 17 / fails, 4 short | not castable | yes |
| contrast_10 | `splice_5c731f69fe82` (opp 18) | one more Glacial Ray; opponent at 19 | two-way | Desperate Ritual alone, Desperate Ritual spliced with Desperate Ritual, Lava Spike spliced with Desperate Ritual + Glacial Ray (3) | Desperate Ritual spliced with Glacial Ray | Desperate Ritual alone; Glacial Ray alone; Glacial Ray spliced with Desperate Ritual; Shock | 18 of 19 / fails, 1 short | 16 of 18 / fails, 2 short | yes |

## Not determined

- **No model has been run on any pair.** Whether a model fails these more than the base boards is the experiment
  (paid; parked with the other paid probes on HTTP 402, issue #369).
- **How to score a pair.** The pair score is the share of pairs with both sides solved (Gardner et al.'s consistency),
  reported next to the single-board solve rate over the same boards. Its trivial baseline is a model that ignores the
  changed detail and plays one line on both: by construction it solves at most one side of every pair, so its pair
  score is 0 whatever its single-board rate (on a one-way pair a model can still solve both by playing each side's
  line, which is the point). A random legal policy's pair rate is the product of its two sides' random solve rates,
  each measurable with `puzzle_baselines.py run --arms random`; not run. The gap between single-board and pair rate is
  the reading-versus-pattern signal; with 10 pairs its interval is wide (a 95% Wilson interval on 5 of 10 is 24% to
  76%), so a scored set wants the 36 two-way pairs and more boards.
- **The lost side of each cross line is the model's.** The engine shows that one line (the other side's move, then
  this board's best line) falls short; that every line after the move loses is the model's verdict, as for the regret
  steps (a script cannot prove a loss).
- **Naturalness** is a hand-set cost (life step size, Arcane-only swap); no reader has rated the pairs.
- **Single-detail two-way pairs** may exist outside these 35 boards' neighbourhood (a fresh board searched for one,
  rather than perturbed from a hard board); not searched. Within the model, a single resource or life change never
  contrasts on any board (the monotonicity above), so only swaps can.
- **Responses and mid-turn positions** (a Ritual on the stack, mana floating), where many of the where-lose note's
  regret steps sit (24 of its 45 bare Rituals were cast in response), cannot be written as a puzzle start here and are
  outside the model's no-response assumption.
