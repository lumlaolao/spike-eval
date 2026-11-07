# Do seats know when a puzzle is lost? A reply-text pattern cannot say (negative, 2026-11-06)

**Question.** The board models label every position of an attempt as winnable or lost (drain: `board_drain.py`; splice:
`step_regret_board.py`). After the regret step, does the seat say it can no longer win, or does it keep claiming a
winning line?

**Answer: not measurable from the replies' text.** A pattern over each reply's `WHY:` sentence gives a striking split.
In drain, seats claim a win as often in lost positions as in winnable ones, and "concede" in only 6 to 7% of lost positions. But
a sample check showed the concession pattern does not mean what it says, so the split is not evidence. The measure
needs a stated belief, not a word match.

## What was run (free: Python over the board rows and their games)

Command: `python3 eval/research_stats.py loss-awareness <board.jsonl> [...]` (tested in
`eval/tests/test_research_stats.py`). **Reproduced here for drain, not for splice.** Run on this machine's drain
rows it gives 1463 and 1080 replies, 19.7% and 6.8% for the lost positions and 1.0% concessions in winnable ones,
as below; it counts 304 winnable claims (20.8%; the first run said 20.7%, one reply fewer) and 20 of 46 failed
attempts that never match the concession pattern after the step (the first run said 18). The splice cells below come
from a one-off run over 75 attempts, 14 of whose game files are not on this machine; the script on the 61 attempts
it can read gives 545 and 444 replies and different rates, so the splice figures are **not reproducible here**.

The inputs are the board rows from the two notes (drain `runs/where-lose-drain/board.jsonl`, 68 attempts; splice
`runs/where-lose/board.jsonl`; both git-ignored, read in the worktrees that wrote them).
Each labelled position k was joined to the seat's k-th non-auto decision row, and its `WHY:` sentence was matched
against two patterns:
- **claims a win:** `lethal|finish (them|the opponent)|win (this|the game)|enough (damage|drain|to (kill|win))|exactly enough|reach(es)? (0|zero)|deal(s)? the (final|remaining)|for the win`
- **concedes:** `cannot|can't|no (way|line|remaining)|not enough|out of reach|impossible|short of|unreachable`

| Family | Position | Replies with a reason | Claims a win | Concedes |
|---|---|---|---|---|
| drain | winnable | 1463 | 20.8% | 1.0% |
| drain | lost | 1080 | 19.7% | 6.8% |
| splice | winnable | 607 | 19.9% | 0.7% |
| splice | lost | 619 | 24.7% | 6.3% |

These rates are withdrawn as evidence: the patterns fail the sample check below, so the table is description of a
invalid measure, not a score, and carries no interval or baseline claim (positions within an attempt are not
independent either, so a per-position interval would overstate its precision). Per failed attempt with a regret step, the
seat never matched the concession pattern after the step in 20 of 46 drain attempts (the splice one-off said 44 of
60; not reproducible here).

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
