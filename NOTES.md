# frontier5 run notes (2026-10-26, owner decisions)

The conditions and every decision that shapes how the results read. `STATUS.txt` marks a board S (solved),
F (failed) or . (not yet played). `F*` marks the failure caused by a harness bug; `F§` the two lost to context-length
refusals (`HTTP 400`; cause under investigation).

## ⚠ Harness bug found: GLM 5.3, elf2 (scored a failure, owner)

**GLM 5.3's elf2 game exposed a defect in our harness.** The board is scored a **failure**, because the test
was not passed (owner, 2026-10-26). But the loss was caused by the harness, not by GLM's play:

- On its first decision (main phase 1) GLM replied with two `answer` tool calls. The first held only its
  analysis (`lines`, no move). The second held the move: `option 14`, tapping both Forests to start the
  Heritage Druid / Nettle Sentinel engine into a 59-damage Craterhoof kill.
- The harness reads only the first `answer` call. It judged the reply invalid and retried, GLM split its answer
  the same way again, and after 2 attempts the harness passed priority for GLM (`answer_via: lost`,
  `fallback: play(unparsed)->PASS`, `textParses=2` in jvm.log).
- The game was stopped at 11:09 UTC once it was lost, to save cost (owner), and scored a failure.
- Losing main phase 1 made the kill impossible. GLM saw this ("Main 1 was already passed ... the exact-59
  Craterhoof kill is unreachable") and played out a lost turn.
- It is the only game lost to this defect. (An earlier note here said it was the only game with a lost
  decision; that was wrong: see Kimi K2.6 below.)
- Fixed in PR #330 (answer-tool-v12, harness-epoch-73): a reply's answer is its first `answer` call that
  validates, and a play decision on a tool ask reads no text.

## ⚠ Context-length refusals: Kimi K2.6, elf2 and hoofdrain (F§, scored failures, owner)

In `frontier5-fill` Kimi K2.6 ran with `max_tokens` 235929, its listed maximum output, against a 262144-token
context. Some calls were refused (`HTTP 400`: "maximum context length is 262144 tokens ... you requested 231648
output tokens and your prompt contains at least 30497"). Every refused call was a follow-up carrying the model's
previous reply, reasoning included (about 0.55 MB); every first ask (about 16 KB) was answered. The mechanism is
under investigation. Each refused request was sent three times unchanged, then the seat passed (`[AUTO-LEGAL] play(bridge-down)->PASS`): 4 decisions on elf2 (the first was main
phase 1, so the kill was gone), 3 on hoofdrain and 1 on splice2, which it solved anyway. The owner scored elf2
and hoofdrain failures and asked for the cause to be investigated
(`grep -c "HTTP 400" runs/puzzles/frontier5*/*/*/jvm*.log` is non-zero only for these three games).

## Grok 4.3, atog: the first solve counts (owner)

Grok 4.3 solved atog in 6 minutes under the first 30-minute cap, with no limit fired
(`frontier5/x-ai_grok-4.3/hard-atog-*/puzzle-results.partial0.jsonl`). Raising the cap to 90 minutes replayed
it, and the replay failed. The owner counts the first game: 57 -> 58 solved.

## Conditions

- 5 boards of the gen2 hard suite's open split, 1 attempt each.
- Limits lifted so no model is cut off mid-thought: each model's own maximum output tokens, a 60-minute
  deadline per call and an 8-hour game cap (`batch-manifest.json` `record`: `max_tokens`, `call_timeout_ms`,
  `game_cap_ms`).
- A board already played without any limit firing was kept rather than replayed. Most of the board-1 games
  were played under the earlier limits: 32000 tokens, a 300 s call deadline and a 30- or 90-minute game cap.
  Their manifests say which limits applied. `chosen.json` names the game kept for each board.

## Models removed (not in the totals)

- **GLM 5.3 Prime**: called off by the owner. It had spent $19.40 (the sum of `puzzle_solve.spent` over its directories; $19.21 at call-off), with 1 board solved.
- **GPT-6.1 Sol Pro**: not needed, per the owner. It had solved the 3 boards it played.

## Kept as played

- **Kimi K2.6, drain2: solved, kept.** 1 of its 80 decisions was cut at the old 32000-token cap and answered
  with reasoning off. The owner chose to keep it rather than replay it.

## Stopped and scored as failures: Mistral Large 4, elf2, splice2 and hoofdrain (F)

Stopped by the owner at 11:00 UTC for time and cost, and **scored as failures** (owner): an attempt that cannot
finish inside the time budget fails the board. **Mistral was making good progress on all three; it was
not stuck.** Each was on a coherent winning plan and moving, with no errors beyond one dropped connection
that was retried. Every hard decision ran 74,000 to 144,000 reasoning tokens at Mistral's own 80-100 tokens/s
host, which is 15 to 28 minutes a decision. At that pace each attempt was heading for more than 8 hours:

| board | decisions made in 2.7 h | pace | decisions a solve takes (median of solved games) | projected |
|---|---|---|---|---|
| elf2 | 23 | 6.7 min | 71 | about 8 h, at the cap |
| splice2 | 9 | 17.3 min | 31 | about 9 h |
| hoofdrain | 10 | 15.6 min | 61 | about 16 h |

On hoofdrain, one reply also reached Mistral's own maximum of 262,144 output tokens, the most it can write in
one reply. They count as failures because they did not finish within the time budget, not because the play went wrong. Mistral
solved both of the boards it finished (atog and drain2). Spend on Mistral was about $11.50, $10.50 of it on
the four long games.
