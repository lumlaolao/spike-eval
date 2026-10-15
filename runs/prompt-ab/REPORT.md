# Old prompt vs new prompt, GPT-6 Luna (2026-10-15)

Work plan item 5.
- Setup: Sneak and Show (Luna) against UR Delver (Forge AI), seeds 81-82 in both seat orders,
  `v4-faithful`, memory tools and lookahead on, one tier, harness shortcuts off, answer tool.
- Old: commit 0638c85 (harness-epoch-7). New: commit b78c9f6 (harness-epoch-8).
- The new commit has the prompt rewrite (#170) and the shortcut defaults (#171), and nothing else.
  The shortcuts were forced off in both arms, so the prompt is the only difference.

| | Old prompt | New prompt |
|---|---|---|
| Wins (Luna) | 0 / 4 | 2 / 4 (T31, T13) |
| Model decisions | 151 | 168 |
| Full re-asks (a text reply with no `answer` call) | 9 | 2 |
| Prompt tokens per call | about 9.4K | about 6.6K |
| Share of prompt tokens read from cache | 70% | 60% |
| Cost | $0.08 | $0.09 |
| Taken / lost / Forge-AI decisions | 0 / 0 / 0 | 0 / 0 / 0 |

**What this shows.**
- The new prompt is about 30% smaller per call: the guide is said once, and the coaching, examples
  and answer contract are gone.
- It sends the model back for a second full ask less often: 2 against 9.
- The win difference (2 of 4 against 0 of 4) is not evidence at this size (Fisher exact p = 0.43).
  It needs a larger block before it says anything about play.

**Follow-up already built:** a reply that calls nothing is now asked once, in the same conversation,
to call `answer` (answer-tool-v4). That removes the remaining full re-asks.
