# The frontier5 run (2026-10-26)

20 models, 5 boards each, 1 attempt per board: the gen2 hard suite's 5 hardest open boards
(`eval/suites/puzzles-hard-gen2-v1.json`; the boards and the rule that picked them are in `notes/WRITEUP-NOTES.md`
section 4b on `docs/writeup-notes`). 57 of the 100 boards were solved.

**This is an exploratory comparison, not a PUZ score.** The owner lifted the limits during the day and kept every game
in which no limit fired, so the kept games come from several conditions: the game cap, the per-call deadline and the
token cap, each recorded in the puzzle's manifest (PR #329). `eval/puzzle_score.py` refuses to pool them.

One attempt a board cannot separate models that differ by a board or two: a difference of one or two solves is not a
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
| Kimi K2.6 | S | F | S | S† | F | 3 |
| Grok 4.6 | S | S | F | S | F | 3 |
| Claude Haiku 5.5 | S | F | F | F | S | 2 |
| DeepSeek V4 Pro | F | S | S | F | F | 2 |
| Mistral Large 4 | S | F‡ | F‡ | S | F‡ | 2 |
| Grok Build 0.1 | F | F | F | S | F | 1 |
| Mistral Medium 3.5 | F | F | F | F | F | 0 |
| GPT-6 Luna | F | F | F | F | F | 0 |
| Jev 1.14 | F | F | F | F | F | 0 |
| Grok 4.3 | F | F | F | F | F | 0 |

The game kept for each board, with its row file and status, is `chosen.json` on the branch
`results/frontier5-2026-10-26`; the table is its `status` field (57 `solved`).

- F\*: lost to a harness defect and scored a failure (owner). See below.
- †: kept as solved (owner). One decision of the game was cut at the old 32000-token cap and answered with reasoning
  off (`grep -c "finish_reason=length" runs/puzzles/frontier5-long/moonshotai_kimi-k2.6/hard-drain2-*/jvm*.log`: 1).
- ‡: stopped for time and cost, and scored a failure (owner). The owner's note: Mistral was making good progress and
  was not stuck, but each attempt was heading for over 8 hours.

Removed by the owner and not in the table: GLM 5.3 Prime (called off, $19.40) and GPT-6.1 Sol Pro (not needed, $2.97).

## The harness defect GLM 5.3 found

GLM 5.3's elf2 game was lost to the harness, not to its play
(`runs/puzzles/frontier5-fill/z-ai_glm-5.3__hard-elf2-2753b3ca0d06`). On its first decision it sent its plan and two
`answer` calls in one reply: its analysis (`lines` only) and then its move (`option` 14). The seat read only the first
`answer` call, found no option in it, and asked again; the retry held only `lines`. So the decision was lost to PASS,
which skipped main phase 1 and made the kill impossible. The play loop then tried to read `YIELD:` from the reply's
text (`[TEXT-PARSE] refused` in its `jvm.log`). The next play decision was lost the same way, but there GLM sent only
`lines`, twice: a miss of its own.

These are the run's only lost decisions: `grep -c "no usable CHOICE" runs/puzzles/frontier5*/*/*/jvm*.log` is 0 for
every other game. The fix makes the first `answer` call that validates the answer, and a lost play decision reads no
text (answer-tool-v12, harness-epoch-73).

## How it ran

In the board-1 trial (`runs/puzzles/frontier5` and, for Kimi K2.6 on int4 hosts,
`runs/puzzles/frontier5-kimi-k2.6-int4`), 3 games of 2 models reached the win after the 30-minute cap: Kimi K2.6 on
both host sets, and GLM 5.3 Prime in its replay. These are the rows with status `invalid` and `solved` true in
`puzzle-results*.jsonl` whose manifest (`batch-manifest[.partial<n>].json`) records no `game_cap_ms`. The owner
then lifted the limits during the day, so that no model was cut off mid-thought:
- the game cap went from 30 to 90, then 240 (`frontier5-long`) and 480 minutes (`frontier5-fill`);
- the per-call deadline went from 300 s to 20 and then 60 minutes;
- the token cap went from 32000 to each model's own maximum output.

These are the `puzzle_solve.py` flags `--game-cap-minutes`, `--call-timeout-minutes` and `--max-tokens` (PR #329).
A game in which no limit fired was kept. Each remaining board was played once with no limits (`frontier5-fill`).

Open-weight models ran at their makers' released precision (`--quantizations`). Closed models ran at or under list
price (`--max-price-at-list`).

The spend across every attempt, removed models included, was $133.63: the sum of `puzzle_solve.spent` over the model
directories of `runs/puzzles/frontier5`, `frontier5-kimi-k2.6-int4`, `frontier5-long` and `frontier5-fill`.

## Where the record is

- The result rows, manifests, `chosen.json`, the board table (`STATUS.txt`) and the owner's decisions (`NOTES.md`):
  branch `results/frontier5-2026-10-26`.
- The full record (every model request and response, the engine logs), packed and verified by `eval/archive_run.py`:
  branch `archives/frontier5-2026-10-26`. It is temporary, deleted once saved elsewhere.
- The run directories on the machine that ran it: `runs/puzzles/frontier5`, `frontier5-kimi-k2.6-int4`,
  `frontier5-long` and `frontier5-fill`.
