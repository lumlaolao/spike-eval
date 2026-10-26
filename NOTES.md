# frontier5 run notes (2026-10-26, owner decisions)

The conditions and every decision that shapes how the results read. `STATUS.txt` marks a board S (solved),
F (failed), X (stopped by the owner, no result) or . (not yet played).

## Conditions

- 5 boards of the gen2 hard suite's open split, 1 attempt each.
- Limits lifted so no model is cut off mid-thought: each model's own maximum output tokens, a 60-minute
  deadline per call and an 8-hour game cap (`batch-manifest.json` `record`: `max_tokens`, `call_timeout_ms`,
  `game_cap_ms`).
- A board already played without any limit firing was kept rather than replayed. Most of the board-1 games
  were played under the earlier limits: 32000 tokens, a 300 s call deadline and a 30- or 90-minute game cap.
  Their manifests say which limits applied. `chosen.json` names the game kept for each board.

## Models removed (not in the totals)

- **GLM 5.3 Prime**: called off by the owner. It had spent $19.21, with 1 board solved.
- **GPT-6.1 Sol Pro**: not needed, per the owner. It had solved the 3 boards it played.

## Kept as played

- **Kimi K2.6, drain2: solved, kept.** 1 of its 80 decisions was cut at the old 32000-token cap and answered
  with reasoning off. The owner chose to keep it rather than replay it.

## Stopped: Mistral Large 4, elf2, splice2 and hoofdrain (X)

Stopped by the owner at 11:00 UTC for time and cost. **Mistral was making good progress on all three; it was
not stuck.** Each was on a coherent winning plan and moving, with no errors beyond one dropped connection
that was retried. Every hard decision ran 74,000 to 144,000 reasoning tokens at Mistral's own 80-100 tokens/s
host, which is 15 to 28 minutes a decision. At that pace each attempt was heading for more than 8 hours:

| board | decisions made in 2.7 h | pace | decisions a solve takes (median of solved games) | projected |
|---|---|---|---|---|
| elf2 | 23 | 6.7 min | 71 | about 8 h, at the cap |
| splice2 | 9 | 17.3 min | 31 | about 9 h |
| hoofdrain | 10 | 15.6 min | 61 | about 16 h |

On hoofdrain, one reply also reached Mistral's own maximum of 262,144 output tokens, the most it can write in
one reply. Read these three boards as "did not finish within our time budget", not as failures. Mistral
solved both of the boards it finished (atog and drain2). Spend on Mistral was about $11.50, $10.50 of it on
the four long games.
