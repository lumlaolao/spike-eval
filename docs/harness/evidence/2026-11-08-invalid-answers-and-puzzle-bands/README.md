# Inputs and outputs of the 2026-11-08 invalid-answer and puzzle-band note

The note: [eval/research/2026-11-08-invalid-answers-and-puzzle-bands.md](../../../../eval/research/2026-11-08-invalid-answers-and-puzzle-bands.md).

Regenerated on 2026-11-08 at harness-epoch 97 (the checkout's `HARNESS_EPOCH`), from the worktree root, after the
review fixes (epoch `unknown` in place of `unstamped`, `sources` per tally, no verdict below 10 lost decisions).

- `invalid-answers.json`: `python3 eval/research_stats.py invalid-answers /home/user/mtg-forge-llm-agent/runs`
- `puzzle-bands.json`: `python3 eval/research_stats.py puzzle-bands eval/suites/puzzles-hard-gen-v1.json
  eval/suites/puzzles-hard-gen2-v1.json eval/suites/puzzles-hard-gen3-v1.json eval/suites/puzzles-forge-builtin-v1.json
  eval/suites/puzzles-rules-sample-v1.json` (reproduces exactly from the committed suites)

`runs/` is git-ignored and not saved here. What part A read: 13,687 files `game-*.jsonl` under that folder
(`find <runs> -name 'game-*.jsonl' | LC_ALL=C sort`; the list's sha256 is
`bc5d7b3bb9f40631e836b5b49fca566a56228815eebb770fba563df4b0bcf7f2`). A different `runs/` gives different counts;
the same count and hash mean the same file names, not the same contents (the files were append-only logs).
