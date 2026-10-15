# One game each: Opus 5.5 and GPT-6 Luna (2026-10-15)

Seat A Death & Taxes against Forge's AI on UR Delver, seed 81 (unswapped), `v4-faithful`,
one tier, native and memory tools, lookahead, `MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0`,
harness-epoch-5 (the plan changeable at any decision; `report_unclear_decision` offered).

| Model | Result | Model calls | Cost | Plan revisions | Harness findings |
|---|---|---|---|---|---|
| `anthropic/claude-opus-5.5` | **won**, turn 18 (its 9th turn) | 74 | $4.35 | 21 | none taken, lost or skipped; no reports |
| `openai/gpt-6-luna` | lost, turn 13 | 28 | $0.03 | 17 | none taken, lost or skipped; no reports |

Forge counts each player's turn, so turn 18 is each player's ninth. Both games were identical up to
turn 8 (same seed): life 11 to 16 after Delver's early pressure. Opus then cast Palace Jailer
through Cavern of Souls (uncounterable), took the monarch, stabilised with Thalia and Revoker, and
won with Batterskull and an alpha strike on turn 18. Luna kept developing slowly, spent Rishadan
Port activations in its upkeep, and died on turn 13.

One game per model is an anecdote, not a measurement: it says the harness carried a frontier model
through a whole game with nothing taken from it, and that the plan line works as designed (Opus's
revisions track the board: "Jailer the Aberration" became "remove the flier, take the monarch"
after Delver flipped). Many of Luna's 17 revisions reword the same plan.

Opus is not used again: at about $4 a game it is 40 times Haiku 5.5 ($0.10/$0.50 per M tokens,
about $0.10 a game), which is the Anthropic model for games from here.

## The blunder judge on these games

`eval/blunder_judge.py` with Haiku 5.5, about $0.06 a game. The first version judged from memory
and was wrong about the cards (it said Rishadan Port enters tapped and Aether Vial has flash, and
called Opus's correct Port-then-Jailer turn a blunder). v2 gives the judge the Oracle text of every
card the prompt names, from Forge's card scripts; on the Opus game it then found two arguable
choices (Thalia over Recruiter of the Guard on turn 6; an all-out attack on turn 16) and no
blunders. That is the expected shape for an audit tool, and the reason its verdicts are read, not
counted.

The judge did find one real clarity problem in the seat's prompt: `mana available:
{W:3 U:1 B:1 R:1 G:1 C:1} (3 untapped sources)` from two Plains and a Cavern of Souls reads as more
mana than exists. Observation v23 says that each source is counted under every colour it can make.
