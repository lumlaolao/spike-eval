# Demo viewer fixtures

Rows copied verbatim from recorded games, for `eval/tests/test_demo_state.py` and `eval/tests/test_demo_events.py`.
Each slice starts on the row after a decision row of the same seat, so the rows of each ask are whole.

| File | Source | Epoch | What it holds |
|---|---|---|---|
| g33-inspect.jsonl | runs/loop/g33/game-4133-1792440447938.jsonl rows 7-11 | harness-epoch-64 | mulligan-bottom with an `inspect` of Bilbo |
| g33-set-plan.jsonl | same, rows 21-34 | harness-epoch-64 | T2 MAIN1 play with `set_plan`, window rows before it |
| g33-simulate.jsonl | same, rows 45-51 | harness-epoch-64 | T2 MAIN1 play with two `simulate` previews |
| g33-t5-upkeep.jsonl | same, rows 126-147 | harness-epoch-64 | T5 UPKEEP play (the state sample in the demo work order) |
| g33-blockers.jsonl | same, rows 446-462 | harness-epoch-64 | T11 blockers, answered "BLOCKS: NONE" |
| g33-damage-assign.jsonl | same, rows 647-649 | harness-epoch-64 | T14 damage-assign, `tier: auto`, `prompt: null` |
| g33-game.jsonl | same, last row | harness-epoch-64 | the game row (won by A on T14) |
| mirror-42-start.jsonl | a loopback mirror, first 60 rows (command below) | harness-epoch-70 | two llm seats' asks in one file |
| puzzle/ | `puzzle_baselines.py line --pzl eval/puzzles/hard/gen/drain_3c09484e5e54.pzl` (an open-split puzzle) with DEMO_FEED=1: the game file's rows up to its third decision, and its result row trimmed to the fields demo_puzzle reads, with `source_file` (and the script path) moved so the lookup by id is exercised | harness-epoch-70 | the written line of a hard puzzle |
| tournament-2026-07-play.jsonl | runs/tournament-1/match-F-1/game-264-1784515705553.jsonl | before epochs | a July 2026 prompt ("yard(5):", "Name(0/1)[T]") |

`mirror-42-start.jsonl` was made with `python3 eval/fake_model_server.py --port 0 --policy random --seed 7 --answer-tool
--native-calls 30` and, from `/root/forge`, `GAME_SEED=42 DECK_A=eval/decks/grixis-reanimator.txt
DECK_B=eval/decks/mardu-energy.txt CONTROLLER_A=llm CONTROLLER_B=llm` with both seats' models
`openrouter:fake/m`, `OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1 ANSWER_TOOL=1 MEMORY_TOOLS=1 LOOKAHEAD=1
CONTEXT_PROFILE=v4-faithful EVAL_OUT=<dir> bash run-llm-headless.sh` (no egress, no cost; Mardu won on T20).
