# Do seats know when a puzzle is lost? A reply-text pattern cannot say (negative, 2026-11-06)

**Question.** The board models label every position of an attempt as winnable or lost (drain: `board_drain.py`; splice:
`step_regret_board.py`). After the regret step, does the seat say it can no longer win, or does it keep claiming a
winning line?

**Answer: not measurable from the replies' text.** A pattern over each reply's `WHY:` sentence gives a striking split.
Seats claim a win as often in lost positions as in winnable ones, and "concede" in only 6 to 7% of lost positions. But
a sample check showed the concession pattern does not mean what it says, so the split is not evidence. The measure
needs a stated belief, not a word match.

## What was run (free: Python over the board rows and their games)

The inputs are the board rows from the two notes (drain `runs/where-lose-drain/board.jsonl`, 68 attempts; splice
`runs/where-lose/board.jsonl`, 76 attempts with positions; both git-ignored, read in the worktrees that wrote them).
Each labelled position k was joined to the seat's k-th non-auto decision row, and its `WHY:` sentence was matched
against two patterns:
- **claims a win:** `lethal|finish (them|the opponent)|win (this|the game)|enough (damage|drain|to (kill|win))|exactly enough|reach(es)? (0|zero)|deal(s)? the (final|remaining)|for the win`
- **concedes:** `cannot|can't|no (way|line|remaining)|not enough|out of reach|impossible|short of|unreachable`

| Family | Position | Replies with a reason | Claims a win | Concedes |
|---|---|---|---|---|
| drain | winnable | 1463 | 20.7% | 1.0% |
| drain | lost | 1080 | 19.7% | 6.8% |
| splice | winnable | 607 | 19.9% | 0.7% |
| splice | lost | 619 | 24.7% | 6.3% |

Positions within an attempt are not independent, so no interval is given. Per failed attempt with a regret step, the
seat never matched the concession pattern after the step in 18 of 46 drain attempts and 44 of 60 splice attempts.

## Why it is not evidence

**The concession pattern fails a sample check.** Six random matches in lost positions were read. Most are local rules
statements, not concessions: "Lava Spike is a sorcery, so it cannot be cast while Glacial Ray ... are on the stack",
"Thermo is tapped, so I can't ping yet", and "Colossal Dreadmaw cannot affect this turn". Two of the six do say
nothing more can be done. In winnable positions, 19 matches include "cannot cast Gravecrawler while the stack is not
empty". The pattern counts "this cannot be done now" as "I cannot win".

**The claim pattern was not validated.** "lethal" also matches the FACTS line quoted back ("the engine indicates a
7-power attack is lethal"; #398) and plans stated as goals. It needs the same check before any use.

## What would measure it

A stated belief at each decision: a win probability or a yes/no "can you still win this turn?" in the answer. The
board model's label then scores it exactly (a Brier score against winnable/lost, with the base rate as the floor). The
STR games already have a forecast field (`2026-11-06-forecast-floors.md`). A puzzle arm that asks the same field
would give loss awareness as a calibrated number. That changes what the seat is asked, so it is its own condition with
a version stamp. It needs paid runs.

## Not determined

Whether seats know they have lost. The question stays open.
