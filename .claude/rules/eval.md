---
paths:
  - "eval/**"
  - "runs/**"
  - "datasets/**"
---

# Working on the eval and its runs

The benchmark is the deliverable: STR-AI (win rate against Forge's AI over the frozen panels), STR-HELDOUT (the same
on held-out seeds), PUZ (the puzzle suite). Design: `eval/EVAL-DESIGN.md`; scores: `eval/SCORING.md`; puzzles:
`eval/PUZZLES.md`.

## Every number reports its floor (the 88% lesson)

Replay match against the Forge-AI teacher read 88.1% while the student won 10%: 78.2% of rows had PASS as the answer,
so doing nothing scored 78.2%. Report the majority-class or trivial baseline next to any accuracy
(`replay_match.py` emits `majority_class_baseline`); prefer metrics whose trivial baseline is near zero; class-balance
training corpora (`--cap-per-source`).

## Rules

- Scripts are stdlib only. `python3 eval/test_<name>.py` runs each test file; acceptance runs them all.
- The JSONL schema is additive only; readers use `.get()`. The game line's `winner` is identity-keyed (A is deck
  identity A whatever the seat swap): do not "fix" it.
- Conditions never pool: native vs text tools, answer tool vs text, one tier vs two, each profile, each epoch.
  `aggregate.py` and `rating.check_conditions` refuse mixed conditions; a setting of the llm seat belongs in
  `rating.PLAYER_FIELDS`, not the environment.
- `eval/defect_analyzer.py` is the run oracle (`ai_tier_count=0`, `silent_drop_total=0`); never weaken it to pass a run.
- Seed governance (`eval/seed_policy.py`, `eval/seed_registry.json`): 501-525, 20000-29999 and 30000-30099 are
  eval-only and never training data. `run_batch.py` warns, `make_sft_data.py` refuses.
- Cited mode (`run_batch.py` without `--exploratory`) forces the mirror deck, the confounds off, held-out seeds, and
  `--jobs 1` only for claude-CLI seats. A panel's llm pilot plays the pinned `pilot_condition` (`eval/suites/panels-v1.json`).
- Parallel games: openrouter 6 to 10 JVMs (RAM-bound, about 1 GB each, counted across every batch), floor 4 to 8
  (`eval/CONCURRENCY.md`).
- Every llm seat names both its models (`--smart-x`, `--fast-x`); an unset one would fall to the harness default.

## Free checks before a paid game

`bash eval/keyword_gauntlet.sh` (553 cards, 28 fake-model games), with `GAUNTLET_POLICY=random`, `GAUNTLET_SEED=<salt>`
and `GAUNTLET_TOOLS=1`; `python3 eval/gauntlet_scan.py <run>` fails on any defect. Never edit the script while it runs.
After a game: `match_review.py` (TAKEN / LOST / SKIP / TOOL / TIME), `tool_audit.py`, `plan_audit.py`,
`defect_analyzer.py`, `cost_report.py`.
