# The demo: watching the seat play

A browser viewer for one game, recorded or running: both players' zones as the seat's prompt showed them, every
decision with what the model was asked, what it looked up, what it planned, the reasoning its provider returned,
what it answered, and the game objects it named. It reads the game's `EVAL_OUT` JSONL and nothing else, so it needs no
engine, no model and no network, and it cannot change what the seat sees or decides.

## Replay a recorded game (free, offline)

```bash
python3 eval/demo_server.py --game runs/loop/g33        # a run directory or a game-*.jsonl
# open http://127.0.0.1:8765/   (#d=37 opens decision 37; the URL follows the cursor, so a moment can be bookmarked)
```

Keys: left and right arrows step one decision, Home and End jump, space plays at the chosen speed ("real time"
waits each decision's recorded latency). The filter chips narrow the decision list (plays, combat, tool calls, plans,
model asked, forced, flags). Any run directory with a `game-*.jsonl` works, at any epoch: a prompt from before the
faithful observation draws with fewer fields, and a block the parser does not know stays readable in the full prompt.

## Follow a running game

```bash
mkdir -p runs/demo/live
python3 eval/demo_server.py --live runs/demo/live &
# then start any game with EVAL_OUT=runs/demo/live (run-llm-headless.sh, run-llm-spike.sh, or run_batch.py --out)
```

The page waits for the file, follows it row by row (the JVM flushes every row), shows how long the model has been
thinking since the last row, and stops on the game row. A loopback fake-model game (`eval/fake_model_server.py
--policy random --answer-tool`, no egress, no cost) is the rehearsal; a paid game needs the owner's go-ahead, a stated
cost and `OPENROUTER_ALLOW_EGRESS=1` set by the owner in their own shell.

## What is on screen, and where it comes from

| Pane | Source |
|---|---|
| board, life, counters, stack, combat, FACTS | the decision row's `prompt` (`demo_state.parse_prompt`); a forced pick (`tier: auto`, no prompt) shows the seat's last prompt and says so |
| opponent's hand | a count of face-down tiles: the seat never sees it, and neither does the viewer |
| decision text and options, the chosen one marked | the decision row (`options`, `choice`, `picks`) and the `answer` call |
| what the model did, in order | the `context_call` rows (latency, provider, tokens, reasoning) and their `context_tool` rows (each tool's arguments and result) |
| why | the `answer` call's `why` |
| flags | `fallback`, `answer_invalid`, `first_failed_reply` on the decision row |
| plan | the seat's latest `set_plan`, else the YOUR PLAN line of its prompt; opponent profile and card interactions from the prompt |
| summary strip | the `[TALLY]` line of the console log (`ai=0`, `textParses=0`), `match_review.py` counts, `cost_report.py`, the game row's version stamps |

Highlights (`demo_highlights.py`), strongest first: **chosen** (the option or block the answer selected), **looked up**
(an `inspect` target, the options a `simulate` previewed), **plan** (names in the plan), **mentioned** (names and
`@handles` in the reasoning or `why`); a red inner frame marks what the engine put in front of the seat (combat, the
stack). Hover a card for the evidence. Only objects visible in that decision's prompt are ever highlighted: a guess in
the reasoning about the opponent's hand stays text.

## Never during a demo

Build Forge, run acceptance, or set `OPENROUTER_ALLOW_EGRESS` on the owner's behalf.
