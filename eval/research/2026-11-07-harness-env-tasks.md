# Harness-env tasks from merged "red test, then fix" PRs (2026-11-07)

Extends the whole-seat build episode (`eval/harness_env/`, [feasibility note](2026-11-06-rlvr-env-feasibility.md)) with
many small tasks mined from this repository's own history, in the SWE-bench style. Free: no model call,
`OPENROUTER_ALLOW_EGRESS` unset, JVM work only under the shared lock, `/root/forge` read and never written.

## What a task is

- **Start state:** the repository at the merge's first parent (`base_sha`) plus the PR's test files.
- **Prompt:** the text of the issue the PR closes. The gold patch, the PR body and the fix commits stay out
  (`harness_env_tasks.agent_view`).
- **Reward:** 1 if every target test (a test method the PR added or changed) passes and no other test of those test
  classes fails, skips or goes missing; else 0. Partial fields: `target_fraction`, `others_fraction`, `partial`
  (0.7 target + 0.3 others, 0 if the build fails). `eval/harness_env/harness_env_task_grade.py grade`.
- **Gold patch:** the non-test, non-doc files the merge changed (`gold_files`, `gold_lines`), for validation and sizing.
- **Floor:** the base state scores 0 by construction; `validate` proves it per task.

Files: `eval/harness_env/harness_env_tasks.py` (manifest), `harness_env_task_grade.py` (grade, validate, prepare),
`tasks-v0.json` (the manifest), tests `eval/tests/test_harness_env_tasks.py` and `test_harness_env_task_grade.py`.

## How many PRs qualify

Run: `harness_env_tasks.py recent --limit 60` at main 1c1c778 (the last 60 PR merges, #337 to #416): **22 tasks, 38
skipped.**

| Skipped for | PRs |
|---|---|
| no closing issue with text (so no prompt) | 26 |
| no test method added or changed (no red test): research, CI, docs-with-script PRs | 8 |
| no source file changed besides tests and docs (nothing to fix) | 4 |

The 22 tasks hold 108 target tests over 20 distinct issues; 19 need a JVM, 3 are Python-only. Gold patch size (lines
added plus deleted over the gold files): 4 to 498, median about 33; 10 tasks are 30 lines or fewer, 6 are 31 to 100,
6 are over 100. #403, #407, #409, #411, #414 and #416 all qualify.

**Weak prompts.** #411, #412 and #414 qualify only through tracking issue #269 (named in their Why section, no closing
keyword). Its text is a multi-item list and each PR fixes one item, so the prompt is the whole list, not the item.
They carry `prompt_quality: "weak"` and a `prompt_note` in the manifest. A better prompt would be the item's
paragraph or the PR's Why; not done (the Why names the fix). Use only `prompt_quality: "ok"` tasks for scored runs.

## Red/green validation

`harness_env_task_grade.py validate`: the base state plus the PR's tests must score 0; the base plus the gold patch
plus the tests must score 1; the engine checkout must be unchanged. Each ran under the shared lock. Runs are
`validate` on the manifest generated from main 1c1c778, on 2026-11-07.

| Task | Test class | Base + tests | Base + gold + tests | Red kind | Well posed |
|---|---|---|---|---|---|
| pr390 (issue 385, gold 26 lines) | Python `test_defect_analyzer` | 0 (0 of 1 target) | 1 (1 of 1) | tests | yes |
| pr409 (issue 408, gold 20 lines) | Java `SeatObservationTest` | 0 (1 of 2 targets: the changed test already passed at base, the added test failed) | 1 (2 of 2, 0 regressions) | tests | yes |
| pr404 (issue 251, gold 9 lines) | Java `LlmSpikeTest` | 0, **build failure** (the test names a symbol the fix adds; javac `cannot find symbol`) | 1 (1 of 1, 0 regressions) | build | yes, but a weak red |

All three are red-then-green. pr404's red is a compile error, not a failing assertion: it is correct (the target
cannot pass) but gives no partial credit signal and a model could pass it by only adding the symbol. `red_kind` records
which kind each task has so a curriculum can filter on it. Not validated: the other 19 tasks (each Java validation
is a javac of the whole overlay plus a JVM, minutes under a lock other agents hold for acceptance runs).

Harness faults found and fixed on the way (each would have scored a good fix 0): the literal `$HOME/forge` in
`FORGE_HOME` broke the engine-patch step; running the JVM in a bare scratch directory lost Forge's resource bundles
(`MissingResourceException: en-US`, every test skipped), now a scratch tree with a `forge-gui` link to the checkout.
The grader cannot run `mvn` (it would touch the checkout), so it needs a `deps.txt` from an earlier acceptance run.

## How this feeds RL training

- **pass@k.** Each task is a binary verifiable reward, so k samples per task give pass@k directly; `partial` gives a
  dense signal between runs. Report pass@k beside the base-state floor (0 for every validated task), and keep the
  `red_kind: build` tasks separate (a floor of "adds the missing symbol").
- **Curriculum by gold size.** Order by `gold_lines` (4 to 498) and `gold_file_count`; the 10 tasks under 30 lines are
  a first stage. Test-class size and runtime (`SeatObservationTest`, `LlmSpikeTest` are big classes) set cost per
  rollout and are not yet in the manifest.
- **Contamination.** `merged_at` and `merge_sha` are in each task. Only tasks merged after a model's training cutoff
  are clean; all 22 are 2026-11-05 to 2026-11-07, so for a model with a later cutoff the whole set is contaminated.
  The repository's history is public, so also hide `git log --all` in the sandbox (SWE-bench issue 465; the
  existing `harness_env_sandbox.py` checks this for the build episode, not yet for these tasks).
- **Gold in the sandbox.** The agent view carries no gold files or commits; the prepared worktree is the base commit
  with no later history.

## What is left

- Validate the other 19 tasks (JVM ones are slow); record `red_kind` and the runtime of each in the manifest.
- Per-function results for script-style Python test files (today a module-level `test_` function passes or fails with
  its whole file, so one failing function marks its siblings as regressions).
- A non-target test that already failed at base counts as a regression; a baseline comparison would fix it.
- Better prompts for the weak tasks; PRs with no linked issue (26) need an issue-writing step to join the set.
- Flaky tests: re-run the green state a few times before trusting a task.
- Wire `prepare` into the sandbox builder (history stripped, no `.git` remote) and the episode runner.
