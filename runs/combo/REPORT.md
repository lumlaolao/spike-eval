# Haiku 5.5 on two combo decks (2026-10-15)

`anthropic/claude-haiku-5.5` as seat A against Forge's AI on UR Delver, seeds 81–82 in both seat
orders, one tier, `v4-faithful`, native and memory tools, lookahead,
`MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0`, harness-epoch-6, the 32000-token cap.

| Deck | Wins | Game lengths (Forge turns, both players counted) | Cost |
|---|---|---|---|
| ANT Storm | 0 / 4 | lost T13, T22, T11, T10 | $0.54 |
| Sneak and Show | 2 / 4 | won T33 and **T9**; lost T7, T11 | $0.43 |

Haiku 5.5 costs about $0.12 a game here, against $4.35 for one Opus game.

**On game length.** These games are shorter than the fair matchup: most end between turns 7 and 13,
that is by each player's fourth to seventh turn, and Sneak and Show won on turn 9. Even so, nothing
here is a turn-1 to turn-3 combo kill. Storm cast Ad Nauseam three times and Tendrils of Agony three
times across four games without winning, and Delver's clock killed it first. Four games cannot say
how much of that is Haiku's piloting and how much is the matchup; the Forge AI on the same decks (the
mirror panel from `eval/BENCHMARK-RESEARCH.md`) is the comparison that would.

**Harness.** Across 8 games, nothing was taken or skipped, and no Forge-AI decisions were made.
`match_review` listed 3 LOST answers in the Storm games. Reading them, all three were false:
- the question was a subset question (a reveal, a Duress discard, a mana choice);
- the model wrote a stray `CHOICE: n` next to the real `PICKS: n`;
- the PICKS answer was the one used (Force of Will was discarded as asked).

The review now checks CHOICE only on single-pick questions. That a model writes both formats at
once is the text-format confusion the answer tool removes. One network drop (`chunked transfer
encoding`) was recovered by the retry.
