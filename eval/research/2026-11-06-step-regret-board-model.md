# Step regret from the splice board's model: an exact step and floor on all four attempts, model-derived (2026-11-06)

**Question.** The exact search (`2026-11-06-step-regret-search.md`) proves the late positions of the 4 failed
attempts at `hard-splice-06dad20af5b3` lost but returns `not_found` more than about 7 decisions from the end, so no
attempt got an exact regret step. Can the board's own model (`puzzle_gen.splice_best`) give a winnable or unwinnable
verdict at every position along those attempts, does it agree with every position the search did decide, and if so,
where is each attempt's regret step and what is its floor?

**Answer so far: yes, under the model.** The model decides all 66 positions (0 refused). On the 31 positions the
search decided it agrees on 31 (1 proven, 30 unwinnable); the trivial baseline "every position is lost" agrees on 30
of 31, so this validation is weak: it has one winnable position to get right. Under the model every attempt has an
exact regret step, and in all four it is the same decision: casting the second Desperate Ritual on its own (K=4 or
K=5), when a Lava Spike could have carried it as a splice. The floor there is 1 of 4 options in three attempts (only
PASS keeps the win) and 2 of 6 in C seed 1 (either Lava Spike). These are **model-derived** labels: exact for this
splice board only if the assumptions below hold, and not engine-proven.

## What was run (free: Python only, no JVM, no model)

```bash
python3 eval/puzzles/step_regret_board.py \
  /home/user/tvs-wt/runs/tvs/A-low-k1/hard-splice-06dad20af5b3 /home/user/tvs-wt/runs/tvs/C-low-k3/hard-splice-06dad20af5b3 \
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

## The model against the exact search

| Attempt | Positions | Search decided | Proven / unwinnable | Model agrees | Baseline "all lost" agrees |
|---|---|---|---|---|---|
| A seed 1 | 17 | 8 | 0 / 8 | 8 | 8 |
| C seed 1 | 16 | 8 | 1 / 7 | 8 | 7 |
| C seed 2 | 15 | 7 | 0 / 7 | 7 | 7 |
| C seed 3 | 18 | 8 | 0 / 8 | 8 | 8 |
| **All** | **66** | **31** | **1 / 30** | **31 (100%)** | **30 (96.8%)** |

Four of the 30 unwinnable positions are the end positions (the turn is over); without them the model agrees on 27 of
27 and the baseline on 26. The search note counted 28 unwinnable; per unique position the rows hold 30 (the A attempt
was scanned twice and K=10 to 11 were refined at 2400). C seed 1's K=0 and K=1 were never searched (the scan stopped
at the proven K=4); the model decides them too.

Internal check: along each recorded attempt the model's shortfall (opponent's life minus the most damage still
reachable) never decreases, as it must against an opponent that cannot act: 0 up to the regret step, then 3 (A, C
seeds 1 to 3), then 4 or 5 as the seat wasted more (the splice of Glacial Ray onto Lava Spike at C seeds 1 and 3 cost 2
more damage, in a position already lost).

## The regret step and floor per attempt (model-derived)

| Attempt | Regret K | Position | The seat took | Options that keep the win | Floor | After the step |
|---|---|---|---|---|---|---|
| A seed 1 | 4 | opp 17, pool 3, a ping on the stack, Alchemist tapped | cast Desperate Ritual | PASS | 1/4 | 14 reachable of 17 |
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

## The model's assumptions (each must hold for the labels to be exact)

1. Every card is in `puzzle_gen.SPLICE_CARDS`, all mana is red, mana comes only from the pool, untapped Mountains
   and Desperate Ritual, the pool lasts the main phase, and nothing else deals damage.
2. The opponent has no cards, mana or permanents (cannot act), and the turn ending loses (`Turns:1`).
3. No line gains by casting in response: every spell can wait for the stack to resolve, and a splice is offered
   whenever the spell plus its splices are payable from the pool and untapped lands (mana still on the stack is not).
4. Thermo-Alchemist pings once per untap and every instant or sorcery cast untaps it; an untap trigger pending over an
   untapped Alchemist is one more ping, taken in response.
5. At a target or splice question the spell is committed (CANCEL is offered at the first target but is not a recorded
   option, and the search arm does not take it either).
6. The prompt is the true state (it is `SeatObservation`, the engine's own view); an item the model does not know
   (another card, an X spell, an opponent's permanent) is refused, never guessed. None was refused here.

## Not determined

- **An engine witness at the regret step.** The labels are the model's; the engine has proven only C seed 1 K=4. A
  forced search (`REPLAY_FORCE`) of the kept option at each step (PASS at A K=4, C seed 2 K=4, C seed 3 K=5; a Lava
  Spike at C seed 1 K=5) would turn the "keeps the win" side into an engine proof: 4 searches, zero-model, but the
  600-rollout search did not prove these positions before, so a larger budget or the board's written line forced
  from K would be needed.
- **How strong the validation is.** 31 of 31 against a 30 of 31 baseline is one winnable position of evidence; of
  the model's 18 winnable positions only C seed 1 K=4 is engine-proven, and of its 48 lost ones the 30 the search
  decided are checked, the 18 in the middle are not.
- Any board family other than splice (the model exists only for splice; splice2 has `red_search`, not wired here).
