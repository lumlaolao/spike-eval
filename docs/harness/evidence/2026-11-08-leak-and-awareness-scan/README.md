# Leak and eval-awareness scan, compact record (2026-11-08)

What `eval/research/2026-11-08-leak-and-awareness-scan.md` rests on, in kilobytes. The full hit list (12 MB) and the
runs are not committed.

Command, from the repository root (the runs directory is the owner's `runs/`, 16,383 files read):

    python3 eval/leak_scan.py runs --jobs 3 --out summary.json --hits hits.jsonl \
        --evidence docs/harness/evidence/2026-11-08-leak-and-awareness-scan
    python3 eval/leak_scan.py runs --selfcheck 200

- File count: 16,383 (`game-*.jsonl`, `http-*.jsonl`, `puzzle-results.jsonl`; 13,687 are games).
- List hash: sha256 of the sorted run-relative file paths joined by newlines,
  `2221954b7db65dafccec78e9f401dd497cd9d3b8e13f69cc491b85282bf934da` (also `file_list_sha256` in `summary.compact.json`).
- Scan code: `eval/leak_scan.py` at the commit that adds this folder.

| File | Holds |
|---|---|
| `summary.compact.json` | counters per (corpus, model, kind, epoch): files, games, rows, hit rows and hit games per category, result rows and header rows per file-mtime date |
| `awareness-hits.jsonl` | the 38 reply rows with an awareness phrase hit (non-echo, game files), with 90 characters of context |
| `header-rows-model-seats.jsonl` | the 312 result rows (310 model seats, 2 fake) whose brief carried the pre-epoch-67 Name/Difficulty header, with file mtime date; the zero-model rows are counts in `summary.compact.json` |
| `selfcheck-200.json` | the planted-leak recall, through the production path |
| `haiku-excerpts-and-verdicts.jsonl` | the 30 excerpts sent to `claude -p --model haiku` (local-development lane, not a seat, never a score) and its verdicts verbatim; `n` numbers the first scan's list |

Not reproducible from these files: the per-row text beyond the snippets, and any count that needs the run
directories (they are git-ignored). `summary.compact.json` alone re-derives every rate in the note.
