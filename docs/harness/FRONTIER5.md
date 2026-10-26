# The frontier5 run (2026-10-26)

20 models, 5 boards each, 1 kept game per board: the gen2 hard suite's 5 hardest open boards
(`eval/suites/puzzles-hard-gen2-v1.json`; the boards and the rule that picked them are in `notes/WRITEUP-NOTES.md`
section 4b on `docs/writeup-notes`). 58 of the 100 boards were solved.

**This is an exploratory comparison, not a PUZ score.** The kept games come from several conditions (the game cap,
the per-call deadline and the token cap; "How it ran"), each recorded in the puzzle's manifest, and
`eval/puzzle_score.py` refuses to pool them. Replaying
only the games a limit cut short also leans the table toward models that solve fast, the bias `eval/PUZZLES.md` names
for a cap raised mid-run.

One kept game a board cannot separate models that differ by a board or two: a difference of one or two solves is not a
ranking. The floor is 0: a board is in the suite only when every zero-model arm failed it (random, PASS, first option;
`eval/PUZZLES.md`).

## Results

| Model | atog | hoofdrain | splice2 | drain2 | elf2 | Solved |
|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | S | S | S | S | S | 5 |
| Gemini 4 Argon | S | S | S | S | S | 5 |
| Kimi K3 | S | S | S | S | S | 5 |
| GPT-6.1 Sol | S | S | S | S | S | 5 |
| Grok 4.7 | S | S | S | S | S | 5 |
| DeepSeek V4.1 Flash | S | F | S | S | S | 4 |
| Muse Spark 1.3 | S | S | S | S | F | 4 |
| Muse Wonder | S | S | S | S | F | 4 |
| GLM 5.3 | S | S | S | S | F\* | 4 |
| Gemini 3.8 Flash | S | F | S | S | F | 3 |
| Kimi K2.6 | S | F§ | S | S† | F§ | 3 |
| Grok 4.6 | S | S | F | S | F | 3 |
| Claude Haiku 5.5 | S | F | F | F | S | 2 |
| DeepSeek V4 Pro | F | S | S | F | F | 2 |
| Mistral Large 4 | S | F‡ | F‡ | S | F‡ | 2 |
| Grok Build 0.1 | F | F | F | S | F | 1 |
| Grok 4.3 | S¶ | F | F | F | F | 1 |
| Mistral Medium 3.5 | F | F | F | F | F | 0 |
| GPT-6 Luna | F | F | F | F | F | 0 |
| Jev 1.14 | F | F | F | F | F | 0 |

The game kept for each board, with its row file, status and any owner call, is `chosen.json` on the branch
`results/frontier5-2026-10-26`; the table is its `status` field (58 `solved`). It is the first game that ended with no limit
fired and no call refused by routing, looking in `frontier5`, `frontier5-kimi-k2.6-int4`, `frontier5-long`, then `frontier5-fill`, and within a puzzle
at the current game before set-aside ones; the owner's two exceptions are † and ¶. Three boards had two such games:
Sonnet 5.5's drain2 and splice2 (solved both times) and Grok 4.3's atog (¶).

- F\*: lost to a harness defect and scored a failure (owner). See below.
- F§: lost to context-length refusals (`HTTP 400`; cause under investigation), scored a failure (owner). See below.
- †: kept as solved (owner). One decision of the game was cut at the old 32000-token cap and answered with reasoning
  off (`grep -c "finish_reason=length" runs/puzzles/frontier5-long/moonshotai_kimi-k2.6/hard-drain2-*/jvm*.log`: 1).
- ‡: stopped for time and cost, and scored a failure (owner). The owner's note: Mistral was making good progress and
  was not stuck, but each attempt was heading for over 8 hours.
- ¶: the first game counts (owner). Grok 4.3 solved atog in 6 minutes under the 30-minute cap, with no limit fired
  (`runs/puzzles/frontier5/x-ai_grok-4.3/hard-atog-*/puzzle-results.partial0.jsonl`). Raising the cap to 90 minutes
  replayed it, and the replay failed.

Removed by the owner and not in the table: GLM 5.3 Prime (called off, $19.40) and GPT-6.1 Sol Pro (not needed, $2.97).

## Decisions the harness lost

Two kinds of lost decision appear in the kept games, and nowhere else in them:
`grep -c "no usable CHOICE\|bridge-down)->PASS"` over the `jvm.log` of each game in `chosen.json`, and of GLM 5.3's
stopped elf2 game, is non-zero only for the four games below.

**GLM 5.3, elf2 (F\*): a defect, fixed in PR #330.** In
`runs/puzzles/frontier5-fill/z-ai_glm-5.3__hard-elf2-2753b3ca0d06`, on its first decision GLM sent its plan and two
`answer` calls in one reply: its analysis (`lines` only) and then its move (`option` 14). The seat read only the first
`answer` call, found no option in it, and asked again; the retry held only `lines`. So the decision was lost to PASS,
which skipped main phase 1 and made the kill impossible. The play loop then tried to read `YIELD:` from the reply's
text (`[TEXT-PARSE] refused` in its `jvm.log`). The next play decision was lost the same way, but there GLM sent only
`lines`, twice: a miss of its own. The fix is the rule in [ARCHITECTURE.md](ARCHITECTURE.md) section 6 (answer-tool-v12,
harness-epoch-73): a reply's answer is its first `answer` call that validates. It does not change the second loss.

**Kimi K2.6, elf2 and hoofdrain (F§): calls refused for context length.** In `frontier5-fill` Kimi K2.6 ran with
`max_tokens` 235929 (`record.max_tokens`), its listed maximum output, against a 262144-token context. Some calls were
refused with `HTTP 400`, "maximum context length is 262144 tokens. However, you requested 231648 output tokens and your
prompt contains at least 30497". Each refused request was sent three times unchanged, then the seat passed
(`[AUTO-LEGAL] play(bridge-down)->PASS`): 4 decisions on elf2, the first of them main phase 1, 3 on hoofdrain, and 1 on
splice2, which it solved anyway. `grep -c "HTTP 400" runs/puzzles/frontier5*/*/*/jvm*.log` is non-zero only for these
three games. In their transcripts every first ask (2 messages, about 16 KB) was answered; every refused call was a
4-message follow-up (about 0.55 MB) that carries the model's previous reply, its reasoning included, back to it. The
provider reports a requested output below the one sent, and each refused total comes to the context plus one token. The
mechanism is open; the owner asked for it to be investigated ([WORK-PLAN.md](WORK-PLAN.md)).

## How it ran

In the board-1 trial (`runs/puzzles/frontier5` and, for Kimi K2.6 on int4 hosts,
`runs/puzzles/frontier5-kimi-k2.6-int4`), 3 games of 2 models reached the win after the 30-minute cap: Kimi K2.6 on
both host sets, and GLM 5.3 Prime in its replay. These are the rows with status `invalid` and `solved` true in
`puzzle-results*.jsonl` whose manifest (`batch-manifest[.partial<n>].json`) records no `game_cap_ms`. The owner
then lifted the limits, so that no model was cut off mid-thought:
- the game cap went from 30 to 90, then 240 (`frontier5-long`) and 480 minutes (`frontier5-fill`);
- the per-call deadline went from 300 s, which fired for four models
  (`grep -l "wall-clock timeout" runs/puzzles/frontier5/*/*/jvm.log`), to 20 and then 60 minutes;
- the token cap went from 32000 to each model's listed maximum output.

These are the `puzzle_solve.py` flags `--game-cap-minutes`, `--call-timeout-minutes` and `--max-tokens` (PR #329).
Each remaining board was played once under the last of these (`frontier5-fill`), all at k=1.

Open-weight models ran at their makers' released precision (`--quantizations`). Closed models ran at or under list
price (`--max-price-at-list`). Jev 1.14 runs on its own bridge (`jev:`), where neither routing filter nor the token cap
applies.

The spend across every attempt, removed models included, was $133.63: the sum of `puzzle_solve.spent` over the model
directories of the four run directories below.

## Where the record is

- The result rows, manifests, `chosen.json`, the board table (`STATUS.txt`) and the owner's decisions (`NOTES.md`):
  branch `results/frontier5-2026-10-26`.
- The full record (every model request and response, the engine logs), packed and verified by `eval/archive_run.py`:
  branch `archives/frontier5-2026-10-26` (temporary: [WORK-PLAN.md](WORK-PLAN.md)).
- The run directories on the machine that ran it: `runs/puzzles/frontier5`, `frontier5-kimi-k2.6-int4`,
  `frontier5-long` and `frontier5-fill`.
