# Per-mutant rows of the 2026-11-08 scorer mutation run

The note: [eval/research/2026-11-08-scorer-mutation.md](../../../../eval/research/2026-11-08-scorer-mutation.md).

Both runs use `scripts/mutate_scorers.py` from this branch, `--max 40 --seed 0` (the default timeout, 60 s per
mutant; each verdict run twice and required to repeat), on the eight default targets. No model call, no JVM.

| File | Tree it mutated | Command |
|---|---|---|
| `before.json` | `origin/main` at `0ec3593` (its own tests) | see below |
| `after.json` | the PR branch at `744409a` (its tests plus the new ones) | see below |

```bash
# before: export main at the merge-base, mutate that tree with this branch's tool
B=$(mktemp -d)                                  # under TMPDIR
git archive 0ec3593 | tar -x -C "$B"
python3 scripts/mutate_scorers.py --max 40 --seed 0 --repo "$B" --json before.json

# after: the branch at 744409a. The run was made in the worktree at that commit with the default --repo; a
# clean export of the commit is the same tree:
A=$(mktemp -d)
git archive 744409a | tar -x -C "$A"
python3 scripts/mutate_scorers.py --max 40 --seed 0 --repo "$A" --json after_run.json
python3 docs/harness/evidence/2026-11-08-scorer-mutation/recheck_survivors.py after_run.json "$A" after.json
```

`after.json` is `after_run.json` plus a `killed_by_other` list on each of the 52 survivors: the other test files that
name the module, and fail on that mutant (the "8 killed by another test file" of the note are the 8 rows with a
non-empty list; the other survivors have `[]`). `recheck_survivors.py` makes the calls `--recheck` makes, on the
survivors only, so the recheck did not rerun the 257 killed mutants. The mutant sets are identical across the two
trees (same sites, same seed), so the rows pair one to one by `file` and `site`.

Scorer files are byte-identical in both trees (`git diff 0ec3593 744409a -- <file>` is empty); sha256:

```
07e17c6e817a941d0b28e245da32bf7d748444d027785f868ba7f21ace243336  eval/aggregate.py
fb20d29e927375760a4496954eef8b8384010776f244442e60fc48284d1fecc2  eval/panel_score.py
1417cbe37e0ff2fadc54a74ef74b140a3c3140d6a2926845bced861d4994ecbe  eval/puzzles/puzzle_score.py
63c867b13efdc5838c4ef2e74fa31f8b9232a3c7d8db51cd19f110dd7c1f96e1  eval/sanity_arms.py
ab633769cc5f5bef1201c9190708b667b89baa86a5bac13eb37c5956aa2d750f  eval/rating.py
3ee4f2404bd9f948c4e6d225c1fdfbb67a08d3e165813eea845716c4f8e3b07b  eval/sprt.py
4a252971eae7c17a8fe4b9f3f5a1c03abc2d3aa296b4088e072f18e5b1bf3eb3  eval/seed_policy.py
14864157a886509d88b9a529e612465f6ee536b333c93137f1fdcda221ca8b87  eval/rl/eval_only.py
```

What is not here: the per-file kill counts reproduce from the rows (`python3 -c` over `results[].rows[].status`), and
`mutate_scorers.py` prints the table from them. A rerun can differ by a mutant or two (see "What the first runs taught
about the tool" in the note): one mutant, `rating.py` line 84, was `survived` in the earlier after run (256 killed in
all) and `killed` in this one (257). The earlier runs' rows are not kept.
