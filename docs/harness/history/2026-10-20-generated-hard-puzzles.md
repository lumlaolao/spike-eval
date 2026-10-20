# Generated hard puzzles (2026-10-20)

The owner asked for 200 hard puzzles of 10 to 15 steps that are neither easy nor obvious. This note records
what the research said, how the boards are made and how each one is checked. Counts are filled in from the
gate's own output (`scripts/puzzle-gate/gen_gate.py`, `OUT/gate.json`).

## What makes a puzzle hard (reading, 2026-10-20)

- Possibility Storm's own commentary on its hardest puzzles names three sources: opponent choices that multiply
  the paths, damage or life that can be split many ways (pingers and healers), and a dense set of triggers and
  choices with no odd rule at all ([first responders](https://articles.starcitygames.com/articles/possibility-storm-first-responders/),
  [2017 in review](https://beta.mtggoldfish.com/articles/possibility-storm-2017-a-year-of-puzzling-in-review)).
- Planning benchmarks find length is the lever: SokoBench sees performance fall consistently past about 25 moves
  ([arXiv 2601.20856](https://arxiv.org/html/2601.20856v1)); PuzzlePlex and the Countdown benchmark generate fresh
  instances so a suite cannot be memorised ([2510.06475](https://arxiv.org/html/2510.06475v1),
  [2508.02900](https://arxiv.org/html/2508.02900v1)).
- Our own suite: 452 of 525 proven rules-puzzle lines are one or two actions (`retier` of the 526, 2026-10-20).

## How a board is made (`eval/puzzle_gen.py`)

A family draws a board's numbers, computes its line exactly and writes it as the puzzle's `[solution]` in the
words the seat is shown. The opponent's life is set to exactly what the best line deals, so there is no slack.

| Family | Steps come from | What hides the line |
|---|---|---|
| ping | Thermo-Alchemist pinging before every instant or sorcery (it untaps on each) | ping before each spell, never after; Fireblast only once both Mountains are tapped; with Kiln Fiend every spell before combat; Goblin Guide costs a burn spell's mana for less |
| storm | every spell cast before Grapeshot | rituals in a payable order (Seething Song needs three open, Rites back to back), burn before Grapeshot |
| splice | Arcane spells, pings | which Arcane spell carries Desperate Ritual or Glacial Ray; the line is the exhaustive-search best and must beat every unspliced line |
| red | all of the above, plus Lava Dart's flashback, Goblin Electromancer, Kiln Fiend | the searched best line; kept only when a plain player (ping, then the biggest spell, never splice, Grapeshot last) falls short |

What the engine taught the written lines: a choice with one legal answer gets no line; a splice is asked only
for cards the seat could pay; two of the seat's triggers at once are an order question; a Grapeshot copy is
logged as a cast, so each solution states its real step count (`# steps: N`).

A combat puzzle whose answer depends on the opponent's blocks cannot be a fixed line, so those stay with the
search prover and are not in this set.

## How a board is kept (`scripts/puzzle-gate/gen_gate.py`)

1. The written line plays in the engine as written and solves, in 10 to 15 steps (casts, non-mana activations,
   land plays, the attack).
2. Every floor fails it: random 0 of 20, PASS 0 of 4, first option 0 of 4, the greedy preview floor 0 of 4, and
   Forge's AI at most 1 of 4.

Nothing paid is run; no model is called.

## Where it stands (paused 2026-10-20, owner asked to wrap up)

Kept so far: **59** boards in `eval/puzzles/hard/gen/kept/` (ping 24, splice 29, storm 6), each passed both gate
steps above; the gate's rows are in `eval/puzzles/hard/gen/gate/<family>.json`.

| Batch | Kept | Main drop reason |
|---|---|---|
| ping (36) | 20 | the old Kiln Fiend trigger-order bug (fixed in e80b523), one random-floor solve |
| ping-rest | 4 | |
| storm (40) | 6 | Lotus Petal's colour and Gitaxian Probe payment questions (Petal removed in 43ea29c) |
| splice (40) | 29 | 11 lines did not solve as written (`solved=False unconsumed=12..19`): the line stops early |

Not gated yet (the gate was stopped to pause): `eval/puzzles/hard/gen/ungated/red` (60), `ungated/ping2` (60) and
`ungated/storm2` (50, generated after the Petal fix). Two more families (drain and elf; fling) are on their own
branches, see their notes.

Next, in order:
1. Read one dropped splice board's line log (`puzzle_baselines.py line --pzl ...`): find where the line stops and
   fix the generator, then re-gate the 11.
2. Gate the ungated batches, one at a time (the box has 4 cores; `-j 2`, about 1 to 2 hours a batch of 60):
   `FORGE_HOME=/root/forge python3 scripts/puzzle-gate/gen_gate.py --build <build> --forge /root/forge
   --boards eval/puzzles/hard/gen/ungated/red --out <out> -j 2`, then copy `<out>/kept/*.pzl` into `kept/`.
3. Generate more of whichever families keep best (`python3 eval/puzzle_gen.py --family <f> --count N --seed S
   --out DIR`) until 200 are kept, deduping by board key.
4. Land the generator, `gen_gate.py`, the kept boards and this note through a PR and the full gate (acceptance is
   owed: `eval/` changes).
