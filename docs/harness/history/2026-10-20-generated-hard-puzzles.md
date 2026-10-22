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
- Our own suite: 452 of 525 proven rules-puzzle lines are one or two actions (difficulty `L <= 2` in
  `eval/puzzles/retier/retier-v2-measures.json`, branch `data/retier-v2` at 9f7bc75, the 2026-10-20 re-tier).

## How a board is made (`eval/puzzle_gen.py`)

A family draws a board's numbers, computes its line exactly and writes it as the puzzle's `[solution]` in the
words the seat is shown. The opponent's life is set to exactly what the best line deals, so there is no slack.

| Family | Steps come from | What hides the line |
|---|---|---|
| ping | Thermo-Alchemist pinging before every instant or sorcery (it untaps on each) | ping before each spell, never after; Fireblast only once both Mountains are tapped; with Kiln Fiend every spell before combat; Goblin Guide costs a burn spell's mana for less |
| storm | every spell cast before Grapeshot | rituals in a payable order (Seething Song needs three open, Rites back to back), burn before Grapeshot |
| splice | Arcane spells, pings | which Arcane spell carries Desperate Ritual or Glacial Ray; the line is the exhaustive-search best and must beat every unspliced line |
| red | all of the above, plus Lava Dart's flashback, Goblin Electromancer, Kiln Fiend | the searched best line; kept only when a plain player (ping, then the biggest spell, never splice, Grapeshot last) falls short |
| fling | every instant or sorcery before Fling, each one growing Kiln Fiend and the prowess creatures | Fling after combat deals the attacker's power a second time, but the pool empties when the first main phase ends: a Mountain and a ritual are held for the second; kept only when the searched best beats every line that casts it all before combat and every line that pays Fling only with held Mountains |

What the engine taught the written lines: a choice with one legal answer gets no line; a splice is asked only
for cards the seat could pay; two of the seat's triggers at once are an order question; a Grapeshot copy is
logged as a cast, so each solution states its real step count (`# steps: N`). A written line casts each card the
moment it is castable, so an instant goes on the stack over a ritual that has not resolved and taps a Mountain
the line meant to hold: the fling search plays the stack the way the line does.

A combat puzzle whose answer depends on the opponent's blocks cannot be a fixed line, so those stay with the
search prover and are not in this set.

## How a board is kept (`scripts/puzzle-gate/gen_gate.py`)

1. The written line plays in the engine as written and solves, in 10 to 15 steps (casts, non-mana activations,
   land plays, the attack).
2. Every floor fails it: random 0 of 20, PASS 0 of 4, first option 0 of 4, the greedy preview floor 0 of 4, and
   Forge's AI at most 1 of 4.

Nothing paid is run; no model is called.

## The first suite (`eval/suites/puzzles-hard-gen-v1.json`)

The boards kept by the gate on 2026-10-20 were re-gated from scratch on 2026-10-21 against a build of main at
e96c679 (harness-epoch-70) with this branch's generator (`runs/puzzles/hard-gen-gate`), and frozen by
`scripts/puzzle-gate/gen_suite.py`:

| Family | Active | Open / held-out | Steps | Luna, open split, 1 attempt each |
|---|---|---|---|---|
| drain | 46 | 35 / 11 | 10-15 | 1 of 21 |
| splice | 29 | 23 / 6 | 11-15 | 1 of 21 |
| ping | 24 | 17 / 7 | 10-15 | 15 of 17 |
| elf | 6 | 5 / 1 | 10-14 | 1 of 5 |
| storm | 6 | 2 / 4 | 10-14 | 2 of 2 |
| fling | 5 | 2 / 3 | 11-12 | 0 of 2 |
| **all** | **116** | 84 / 32 | | **20 of 68** |

Every active board passed, on that build: its written line replayed and solved (10 to 15 steps), and random (0 of
20), PASS, first option and greedy (0 of 4 each) failed it, and Forge's AI solved it at most once in 4.

**What "hard" rests on.** The floors say no blind or one-move-deep player wins. The opponent's life is exactly what
the line deals: for splice, red, drain, elf and fling that is the maximum of an exhaustive search over a model of
the board, so no line deals more and every weaker line loses (for fling, over lines that cast each spell at the
first priority it can, as a written line plays: a player who passes on purpose can deal up to 3 more on 3 of the 5
fling boards, so a line up to 3 short also wins there); for ping and storm it is what the constructed line deals,
not a proven maximum. The minimality replay (`puzzle_baselines.py line --minimality`) was tried and does not judge
these boards: a removed step leaves the rest of an interlocked line unplayable (drain_0026d851c85f: 1 of 53 groups
necessary, 16 inconclusive, the other 36 `pass` lines).

**The model check (paid).** GPT-6 Luna (`openrouter:openai/gpt-6-luna`, the faithful profile, tools off) took one
attempt at 68 of the 84 open boards, every family included (one board of each family in turn until 68, so the 16
left out are 14 drain and 2 splice) (`eval/puzzle_solve.py --k 1 --budget-usd 0.98`, run directory
`runs/puzzles/hard-gen-luna-check`, against a provisional suite with the same boards and files, `check-suite.json`
there; $0.912, plus $0.014 for a one-board pilot). It solved 20 of 68 (29%, Wilson 95% interval 20% to 41%), against
0 for every floor (random 0 of 20 a board). Without ping it solved 5 of 51 (10%, 4% to 21%); ping alone 15 of 17
(66% to 97%). The other families' counts are too small to tell apart. On its 48 misses it left the opponent at 2 to
39 life (median 4). The harness was clean in every game: `ai=0`, `textParses=0`; the 151 decisions taken for the
seat had no choice (no options, or one forced target). One attempt a board is a check, not a score: a PUZ score
takes k=4 (`eval/PUZZLES.md`). Ping is kept as its own family (owner, 2026-10-21): the floors fail it, Luna mostly
solves it, and its life total is not a searched maximum.

The active set is not filtered on the model's result: keeping only the boards one model fails would make the suite
hard for that model by construction.

## Not done

- Boards generated and never gated (red 60, ping 60, storm 50, fling 55, elf 40) are on branch `feat/hard-gen` at
  5dc07c4 (`eval/puzzles/hard/gen/ungated/`); they are not in this suite.
- 11 of 40 splice lines stopped before the win when replayed (`solved=False unconsumed=12..19`): found and fixed on
  2026-10-21 (`2026-10-21-harder-splice.md`): the line cast a card over a ritual that had not resolved, so the
  splice it was meant to carry was unpayable and never asked.
- Most fling lines open the second main phase with Rite of Flame; elf boards with one Sentinel repeat a few life
  totals (31, 35, 47). More variety before more boards.
- The suite has 116 boards, short of the 200 asked for.
- The splice and red searches picked splice cards by name, so they never spliced two copies of one card onto a
  spell; Forge offers each copy. A multiset search gives the same maximum on all 29 kept splice boards (pr-review of
  #290); it beat one dropped board's stated maximum by 2 (splice_9c7a6f5bedc7, 18 to 20). The red search maximised
  spell damage and added Kiln Fiend's attack afterwards, so a free cast that only pumps the Fiend could be missed.
  Both fixed on 2026-10-21 (`2026-10-21-harder-splice.md`).
- The held-out boards carry their `[solution]` and `Rule:` lines in the repository while it is private (owner,
  2026-10-21; `eval/PUZZLES.md`); they must be stripped before it is made public.
- Run faster next time (owner, 2026-10-21): a model check waits on the provider, not the CPU (about 15 s a call),
  so run it 8 to 12 attempts at a time, not 3; split the engine gates into batches run side by side rather than in
  one queue. This check took about two hours at 3 at a time.
