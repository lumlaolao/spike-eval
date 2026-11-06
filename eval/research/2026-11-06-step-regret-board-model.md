# Step regret from the splice board's model: every winnable verdict engine-witnessed, the step on all four (2026-11-06)

**Question.** The exact search (`2026-11-06-step-regret-search.md`) proves the late positions of the 4 failed
attempts at `hard-splice-06dad20af5b3` lost but returns `not_found` more than about 7 decisions from the end, so no
attempt got an exact regret step. Can the board's own model (`puzzle_gen.splice_best`) give a winnable or unwinnable
verdict at every position along those attempts, does it agree with every position the search did decide, and if so,
where is each attempt's regret step and what is its floor?

**Answer so far: yes, with the winnable side now engine-proven.** The model decides all 66 positions (0 refused).
On the 31 positions the search decided it agrees on 31 (1 proven, 30 unwinnable), against 30 of 31 for the trivial
baseline "every position is lost". That alone is one winnable position of evidence, so each of the model's 18
winnable positions was then given an engine witness: the recorded first K decisions played again through the script
arm, then the model's own line written from K. All 18 reached the recording's position K exactly and solved: every
winnable verdict is engine-proven (was 1 of 18), against 0 for the baseline, and no assumption broke. Every attempt
therefore has a regret step whose winnable side is proven, the same decision in all four: casting the second
Desperate Ritual on its own (K=4 or K=5), when a Lava Spike could have carried it as a splice; at each the witness took
the option the model says keeps the win. The lost side of that step (the position after the bare Ritual) and the floor
(1 of 4 options in three attempts, only PASS; 2 of 6 in C seed 1, either Lava Spike) are still the model's: the search
proves those positions neither way.

## What was run (free: Python only, no JVM, no model)

```bash
python3 eval/puzzles/step_regret_board.py \
  <the recorded attempt dirs: runs/tvs/{A-low-k1,C-low-k3}/hard-splice-06dad20af5b3 in the worktree that played them> \
  --search runs/step-regret/A-low-k1/step-regret.jsonl runs/step-regret/C-low-k3/step-regret.jsonl \
  --out runs/step-regret/board/step-regret-board.jsonl
```

`eval/puzzles/step_regret_board.py` (new, `step-regret-board-v1`) reads the state the seat was shown in each recorded
decision's prompt (hand, mana pool, untapped Mountains, Thermo-Alchemist tapped or not, every stack item, the
opponent's life), adds the spell being cast at a target or splice question (from the decision rows), and values it:
the damage on the stack, the pings its untap triggers allow, then `splice_best` from the resolved position (which
now takes `untapped=`, so a tapped Alchemist is one ping fewer). Position K and the end position are those of
`step_regret.py`; the end is the last decision applied. At the regret step each option is applied and valued. Rows
(git-ignored): `runs/step-regret/board/step-regret-board.jsonl`. Search verdicts are per position, a decided verdict
over `not_found` (a refined search at 2400 replaces the 600 one); two decided verdicts never disagreed.

The engine witnesses (zero-model, script arm against Forge AI, the attempt's seed, 2 JVMs at a time; 18 games in about
2 minutes):

```bash
python3 eval/puzzles/step_regret_board.py <attempt dirs> --scripts runs/step-regret/board/scripts
python3 eval/puzzles/puzzle_baselines.py --build <the build that ran the search> --allow-stale-build line \
  --pzl <attempt dir>/splice_06dad20af5b3.pzl --script runs/step-regret/board/scripts/<game>-k<K>.txt \
  --seeds <seed> --out-dir runs/step-regret/board/witness/<game>-k<K>
python3 eval/puzzles/step_regret_board.py <attempt dirs> --search <...> --witnesses runs/step-regret/board/witness
```

A witness script is the seat's recorded model decisions before K as script lines (`cast Desperate Ritual`, `pass`,
`opponent`, a splice pick), then `puzzle_gen.written_line` of the model's line from the state at K (it now starts from
a pool, a tapped Alchemist and a stack). `witness_verdict` calls it proven only when the witness game asked the same
question with the same options at every row up to K, the state read from row K's prompt equals the recording's, the
witness took the option the model says keeps the win at row K, the script arm played every line with no drift, and the game was solved; else `failed` (a model error) or `drift`. The
build is the one the search ran on (overlay `c57ec70bb79d`; this branch's overlay has moved on with main's harness
fixes, none in this board's path), so the witnesses and the search see the same engine.

## The model against the exact search

| Attempt | Positions | Search decided | Proven / unwinnable | Model agrees | Baseline "all lost" agrees | Model winnable | Engine witness proven |
|---|---|---|---|---|---|---|---|
| A seed 1 | 17 | 8 | 0 / 8 | 8 | 8 | 4 (K 0, 1, 3, 4) | 4 |
| C seed 1 | 16 | 8 | 1 / 7 | 8 | 7 | 5 (K 0, 1, 2, 4, 5) | 5 |
| C seed 2 | 15 | 7 | 0 / 7 | 7 | 7 | 4 (K 0, 1, 3, 4) | 4 |
| C seed 3 | 18 | 8 | 0 / 8 | 8 | 8 | 5 (K 0, 2, 3, 4, 5) | 5 |
| **All** | **66** | **31** | **1 / 30** | **31 (100%)** | **30 (96.8%)** | **18** | **18 (0 failed, 0 drift)** |

Engine-checked verdicts now: 48 of 66 (the 18 winnable by witness, 30 lost by the search), all agreeing with the
model; the baseline "all lost" gets 30 of those 48. Unchecked: the 18 model-lost positions in the middle of the
attempts.

Four of the 30 unwinnable positions are the end positions (the turn is over); without them the model agrees on 27 of
27 and the baseline on 26. The 30 are the search's own count (`step_regret.py --summary`: 72 searches, 1 proven,
30 unwinnable, 41 not found); a refined search at 2400 replaces the 600 one at its position. C seed 1's K=0 and K=1
were never searched (the scan stopped at the proven K=4); the model decides them too.

Internal check: along each recorded attempt the model's shortfall (opponent's life minus the most damage still
reachable) never decreases, as it must against an opponent that cannot act: 0 up to the regret step, then 3 (A, C
seeds 1 to 3), then 4 or 5 as the seat wasted more (the splice of Glacial Ray onto Lava Spike at C seeds 1 and 3 cost 2
more damage, in a position already lost).

## The regret step and floor per attempt (winnable side engine-proven; lost side and floor model-derived)

| Attempt | Regret K | Position | The seat took | Options that keep the win | Floor | After the step |
|---|---|---|---|---|---|---|
| A seed 1 | 4 | opp 17, pool 3, a ping on the stack, Alchemist tapped | cast Desperate Ritual | PASS | 1/4 | 14 reachable of 17 (17 before) |
| C seed 1 | 5 | opp 16, pool 3, stack empty, Alchemist tapped | cast Desperate Ritual | either Lava Spike | 2/6 | 13 of 16 |
| C seed 2 | 4 | as A seed 1 | cast Desperate Ritual | PASS | 1/4 | 14 of 17 |
| C seed 3 | 5 | as A seed 1 | cast Desperate Ritual | PASS | 1/4 | 14 of 17 |

At each step the winning line spends the Ritual twice as a splice on the two Lava Spikes (net +1 mana each, and the
card stays in hand) and only then casts it; cast bare it is +1 once. Where a ping is on the stack, Lava Spike (a
sorcery) is not castable, so the one winning option is PASS, which resolves the ping first; Glacial Ray carrying the
Ritual costs 4 of the 3 available. In C seed 1 the stack is empty and PASS ends the turn, so it loses (the search note
assumed this). The exact search had bracketed C seed 1 at K 4 to 10; the model's K=5 is inside it, the decision right
after the proven K=4. In every attempt the regret step is reached with exactly enough damage (shortfall 0): the seat
was on a winning line until that cast.

The witness at each regret step (K=4 or 5) took the kept option in the table at row K (PASS, as the bridge waits for
Lava Spike's window, three times; `cast Lava Spike` in C seed 1) and solved: the "keeps the win" side of each step is
an engine proof. The other Lava Spike in C seed 1 was not run.

## The model's assumptions (each must hold for the labels to be exact)

1. Every card is in `puzzle_gen.SPLICE_CARDS`, all mana is red, mana comes only from the pool, untapped Mountains
   and Desperate Ritual, the pool lasts the main phase, and nothing else deals damage.
2. The opponent has no cards, mana or permanents (cannot act), and the turn ending loses (`Turns:1`).
3. No line gains by casting in response: every spell can wait for the stack to resolve, and a splice is offered
   whenever the spell plus its splices are payable from the pool and untapped lands (mana still on the stack is not).
4. Thermo-Alchemist pings once per untap and every instant or sorcery cast untaps it; an untap trigger pending over an
   untapped Alchemist is one more ping, taken in response.
5. At a target or splice question the spell is committed (CANCEL is offered at the first target but is not a recorded
   option; whether the search arm can take it was not checked). No regret step is at such a question.
6. The prompt is the true state (it is `SeatObservation`, the engine's own view); an item the model does not know
   (another card, an X spell, an opponent's permanent) is refused, never guessed. None was refused here.

## Not determined

- **The lost side of each regret step.** The position after the bare Ritual (K=6 or 7) and the 18 model-lost
  middle positions are the model's verdicts: the search returned `not_found` on all of them. A proof of a loss needs
  every line played, which a script cannot give; it needs the search to finish (a transposition table) or an
  argument that the model is an upper bound in the engine (assumptions 1 to 4).
- **The floor's lost options.** At each step the kept option is engine-proven by its witness (PASS three times, a
  Lava Spike in C seed 1, the other Lava Spike there not run); the options the model calls losing are not.
- **How independent the witnesses are.** 11 of the 18 scripts are, line for line, the board's own `[solution]` (A
  and C seed 2 opened exactly as the solution does, up to their step; C seed 1 at K=0 and 1, C seed 3 at K=0), so they show
  that the recorded prefix was the solution's prefix, not a new line; the 7 others (C seed 1 K=2, 4, 5; C seed 3
  K=2 to 5) are lines the solution does not contain, all from positions the solution never passes through.
- Any board family other than splice (the model exists only for splice; splice2 has `red_search`, not wired here).
