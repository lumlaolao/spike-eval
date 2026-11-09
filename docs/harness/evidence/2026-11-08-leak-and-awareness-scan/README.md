# Leak and eval-awareness scan, compact record (2026-11-08)

What `eval/research/2026-11-08-leak-and-awareness-scan.md` rests on, in kilobytes. The full hit list (12 MB) and the
runs are not committed.

Commands, from the repository root, with the owner's `runs/` (16,383 files read). Scan code: `eval/leak_scan.py` at the
commit that last touches this folder (`git log -1 -- docs/harness/evidence/2026-11-08-leak-and-awareness-scan`).

    python3 eval/leak_scan.py runs --jobs 3 --evidence docs/harness/evidence/2026-11-08-leak-and-awareness-scan
    python3 eval/leak_scan.py runs --compare --jobs 3 --evidence docs/harness/evidence/2026-11-08-leak-and-awareness-scan
    python3 eval/leak_scan.py runs --selfcheck 200 --seed 1 > docs/harness/evidence/2026-11-08-leak-and-awareness-scan/selfcheck-200.json

- Input hash: sha256 of the sorted lines `run-relative path, size, mtime (whole seconds)` of the 16,383 files
  (`game-*.jsonl`, `http-*.jsonl`, `puzzle-results.jsonl`; 13,687 are games), also `file_list_sha256` in
  `summary.compact.json`: `2b4e0837cd6a833424625002d8e5316820fe301e11b5183d9940649fd46e3e61`. A rewritten or grown file
  changes it.
- The `command` field of `summary.compact.json` and `compare-frontier5.json` is the command that wrote it (run from a
  folder holding a `runs` link and the documented output path).
- The hit files are sorted by file and line; the scan runs its files in a pool, so an unsorted order varies.

| File | Holds |
|---|---|
| `summary.compact.json` | one line; counters per (corpus, model, kind, epoch): files, games, rows and `rows_<type>` (decision, context_call, context_tool, game, puzzle_result), reasoning rows and characters (`context_call` rows), `reasoning_hit_rows`, hit rows and hit games per category, the `http_` counters of the transcripts (`http_rows_sent` is the request count; `http_hit_rows_*` are kept apart from the game rates), puzzle-mapping counters, result rows and header rows per file-mtime date |
| `compare-frontier5.txt`, `.json` | per model on the `frontier5*` corpora: reply rows, games, rows with reasoning, reasoning characters, awareness phrase-hit rows and games with Wilson 95% intervals, phrase-hit rows in the `reasoning` field per million reasoning characters, rows with a recognition phrase |
| `awareness-hits.jsonl` | the 38 non-echo reply rows with an awareness phrase (game files), 90 characters of context each side |
| `recognition-hits.jsonl`, `recognition-read-by-hand.json` | the 22 non-echo reply rows with the Forge puzzle named near a word of recall or provenance, and my reading of each (13 yes, 9 no) |
| `forge-class-hits.jsonl` | the 8 non-echo reply rows naming a Forge class (`SpellAbility`, `GameAction`, `CardFactory`, `ComputerUtil*`) |
| `name-title-reply-hits.jsonl` | the 2 `bench-v1` rows where Luna quoted the puzzle title; both in the `reasoning` field |
| `header-rows-model-seats.jsonl` | the 312 result rows (310 model seats, 2 fake) whose brief carried the pre-epoch-67 Name/Difficulty header, with file mtime date; the zero-model rows are counts in `summary.compact.json` |
| `selfcheck-200.json` | the planted-leak recall through the production path (seed 1, 200 rows) |
| `haiku-excerpts-and-verdicts.jsonl`, `haiku-prompt.txt` | the 30 excerpts sent to `claude -p --model haiku` (a local classifier through `claude -p` under the owner's instruction of 2026-11-08, not a seat, never a score) and the verdicts verbatim; `n` numbers the first scan's list |

Haiku: the alias was `haiku`, resolved by the Claude Code CLI on the day; the resolved model id was not recorded in
the rows. The script that sent the calls was not kept, so `haiku-prompt.txt` is the wording the note recorded for the
question, not a byte-for-byte copy of what was sent.

The `.pzl` counts in the note come from tracked files, from the repository root:

    find eval/puzzles -name '*.pzl' | wc -l                                       # 1392
    find eval/puzzles -name '*.pzl' | xargs grep -lE '^Rule:' | wc -l             # 1390
    find eval/puzzles -name '*.pzl' | xargs grep -l '^\[solution\]' | wc -l       # 467

`summary.compact.json` re-derives every count and rate in the note's tables (rows scanned, row types, requests, per
corpus and per model hits, the header rows by date), except the per-model reasoning figures, which are in
`compare-frontier5.json`. Needs `runs/`: the text of a row beyond its 90-character snippet, the hand reading of the
seed and `Rule:`/`Difficulty:` reply rows (a `--hits` run lists them; not committed), and the 12 MB hit list.
