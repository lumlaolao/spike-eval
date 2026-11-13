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

Files: `eval/harness_env/harness_env_tasks.py` (manifest; `red_kind` and `validated` come from `eval/harness_env/validation/`), `harness_env_task_grade.py` (grade, validate, prepare),
`tasks-v0.json` (the manifest), tests `eval/tests/test_harness_env_tasks.py` and `test_harness_env_task_grade.py`.

## How many PRs qualify

Run: `harness_env_tasks.py recent --limit 60 --rev 1c1c778` (first-parent history of that commit, read with `git log`
and `git show`, no checkout of main; regenerated after the `DOC_PATH` fix, same 22 and 38; pr393 grew from 93 to 95 gold
lines because `docs/harness/controller-ownership.tsv` is now source) (the last 60 PR merges, #337 to #416): **22 tasks, 38
skipped.**

| Skipped for | PRs |
|---|---|
| no closing issue with text (so no prompt) | 26 |
| no test method added or changed (no red test): research, CI, docs-with-script PRs | 8 |
| no source file changed besides tests and docs (nothing to fix) | 4 |

The 22 tasks hold 108 target tests over 20 distinct issues; 19 need a JVM, 3 are Python-only. Gold patch size (lines
added plus deleted over the gold files): 4 to 498, median 35.5; 10 tasks are 30 lines or fewer, 6 are 31 to 100,
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
| pr409 (issue 408, gold 20 lines) | Java `SeatObservationTest` | 0 (1 of 2 targets: only the added test was red at base; the changed test already passed) | 1 (2 of 2, 0 regressions) | tests | yes |
| pr404 (issue 251, gold 9 lines) | Java `LlmSpikeTest` | 0, **build failure** (the test names a symbol the fix adds; javac `cannot find symbol`) | 1 (1 of 1, 0 regressions) | build | yes, but a weak red |

The validation outputs are committed: `eval/harness_env/validation/pr390-validation.json`, `pr409-validation.json` and
`pr404-validation.json`. All three were re-run together on 2026-11-07 under the fourth-review grader: each file
carries the keys `grader_sha256` (of `harness_env_task_grade.py`), `engine_head` (`/root/forge` HEAD, 34a8662),
`repo_head` and `validated_at`, so a validation is evidence only for the grader and engine that produced it.
`repo_head` names the commit that holds that grader (the grader was committed first, then validated, then the JSONs),
and `ValidationStampTest` fails when a committed `grader_sha256` is not the hash of the current grader file. Results are unchanged: pr390 red 0 / green 1, pr409 red 0 (1 of 2) / green 1 (2 of 2),
pr404 red 0 (build) / green 1. `tasks-v0.json` carries `red_kind` and `validated` for these three and
null for the other 19.

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
- **Gold in the sandbox.** The agent view carries no gold files or commits, no PR number and no sha: its `id` is an
  opaque hash of the task (`view_id`; the map to `pr<N>` stays in the manifest) and `grade_command` uses it.
  `prepare` builds the sandbox with `git archive <base tree> | tar -x` plus the PR's tests and one fresh `git init`
  commit, so it shares no object, ref or remote with this repository: `git log --all` shows one commit and
  `git show <merge>` fails. (The first version used `git worktree add`, which shares every object and ref and let
  `git show <merge>` print the fix; the sentence here saying otherwise was false.)

## Status: dev-grade, not yet isolation-grade

**dev-grade: tasks and grading logic validated; not yet isolation-grade.** The tasks are well posed (red then green,
for the three validated) and the reward rule is checked against the hacks below. But a grader that runs the candidate's
code as its own user cannot be made forgery-proof against that code. Training-grade use needs:

- the candidate's tests run as a different user or in a container;
- the results channel (the grader's scratch directory) and `/root/forge` out of its reach, no network, and the grader's
  process outside it;
- `/root/forge` holds the overlay at the last install (`forge/spike/*.class` built from main, possibly the fix), so the
  agent's sandbox must not be able to read it.

Until then a score of 1 means "the candidate's tree passed under a grader that closes the forgeries listed below that
a file in the tree, a common test run, or a read of the runner's module globals can do"; it does not mean the candidate
could not have forged it. What is closed and what is not, exactly:

- **Closed:** printing a result line; a stale or pre-planted result file; a `testng-results.xml`; editing the tests or
  the read-only data they read; early `sys.exit`; planted bytecode; a shadowing `json.py`; the prebuilt-class
  fallback; a candidate class that replaces TestNG's (or any dependency's) class; a result forged from `__main__`'s
  globals (`import __main__`; the runner keeps the nonce, the result path and the results in locals of `main()`).
- **Open, in principle:** any code that runs in the runner's process and looks at its live frames
  (`sys._getframe(n).f_locals` reaches `main()`'s nonce and result path) or at its heap (`gc.get_objects()`), a thread or
  `atexit` hook that outlives the tests, `os._exit` after writing a file it found, and the same for the JVM (a shutdown
  hook rewriting the reporter's file). The sandbox's path to the result file is also guessable from `sys.argv`.
  Nothing short of a different user or a container closes these.

## Reward hacks closed (four reviews of PR 422)

Each closed hole has a test that attempts it, in `eval/tests/test_harness_env_task_grade.py` (`RewardHackTest`,
`PrebuiltClassTest`, `JavaGradeTest`, `ResultChannelTest`, `ValidationStampTest`). `NoteTest` fails if a test the
table names (a name ending in `...` is a prefix) or a class named here is not in that file. "Red first" means the test
fails on the grader before the fix.

| Hole | Closed by | Test |
|---|---|---|
| Candidate edits, empties or deletes the PR's tests | every `test_files`/`support_files` path restored from the merge commit of the grader's repository; the candidate scores 0 and `tests_modified` lists it | `test_rewriting_the_target_to_assert_true...`, `test_a_fixed_candidate_that_also_edits...`, `test_deleting_a_non_target_test...`, `test_deleting_the_test_file...`, `test_a_script_test_that_exits_zero_first...` |
| Stale `testng/` output, `result.json`, `javac.log` of an earlier grade | deleted at the start of `grade()` and `run_jvm`; a crash or timeout has no result | `test_the_graders_files_from_an_earlier_grade_are_deleted_first`, `test_a_timed_out_jvm_has_no_result`, `test_a_crashed_jvm_has_no_result` |
| Python result forgery: imported code prints `RESULT {...}` | the runner writes a nonce'd file the grader chose; stdout is never parsed | `test_imported_code_that_prints_a_forged_result_line_scores_zero` |
| Candidate `json.py` / `unittest.py` at the root shadows the stdlib in the grader's `python -c` | runner is a file in the grader's scratch run as `python -I -B`; the candidate's directories are appended after the stdlib | `test_a_json_py_at_the_worktree_root_cannot_shadow_the_graders_runner` |
| Early exit: `sys.exit(0)` in imported code or inside a test marks a script-style file PASS | the runner imports the module and calls every `test_` function itself; a `SystemExit` is a FAIL for that function; a module `FAILURES` list that grew is a FAIL | `test_imported_code_that_exits_zero_fails_every_function...`, `test_the_target_calling_sys_exit_zero_inside_the_test_is_a_fail`, `test_a_script_tests_neighbours_are_judged_one_by_one`, `test_a_script_tests_own_failure_tally_counts` |
| Bytecode swap: a planted UNCHECKED_HASH `.pyc` replaces the source or the test | every `__pycache__` and `*.pyc` deleted first; `-B` and `sys.pycache_prefix` in the scratch | `test_an_unchecked_hash_pyc_cannot_stand_in_for_the_source_or_the_test`, `test_a_sourceless_pyc_next_to_the_tests_is_deleted` |
| Prebuilt-class fallback: delete a buggy overlay source, javac links the installed build | classpath is a hard-linked copy of the modules' `target/classes` without `forge/spike/**` and without any class the overlay or an engine patch supplies; the grade fails if a supplied source that the gold patch keeps is missing | `test_the_classpath_holds_no_class_the_overlay...`, `test_a_deleted_source_the_gold_patch_keeps_fails_the_grade...` |
| Java result forgery by overwriting `testng-results.xml` | results come from the grader's own TestNG reporter (`HarnessGraderReporter.java`, compiled in the scratch) writing a nonce'd file; the XML is never read (see "open" below for what remains) | `test_a_testng_xml_the_candidate_left_is_not_read`, `test_a_result_file_with_another_nonce_is_not_read` |
| Malformed result payload raised an exception | a missing, unparsable or wrongly shaped payload is a 0 with a note | `test_a_malformed_payload_is_a_zero_with_a_note_not_an_exception` |
| Read-only inputs edited to make a test pass | restored from the merge commit like the tests, except files the gold patch changes (those are the fix); the rules text comes from the grader's checkout. The covered set (`READONLY_DATA`): every tracked `.md`; every `.json`, `.txt`, `.tsv`, `.csv` under `eval/` and `docs/`; every `.pzl` and `.jsonl` under `eval/`; the tree `eval/decks/` | `test_a_read_only_input_the_candidate_edited_is_restored...`, `test_a_json_data_file_under_eval...`, `test_a_txt_data_file_under_eval...`, `test_a_tsv_data_file_under_docs...`, `test_a_pzl_file_under_eval...`, `test_a_jsonl_file_under_eval...`, `test_the_inputs_the_gold_patch_changes_are_not_restored` |
| Secrets in the Python child's environment | allowlisted environment (`ENV_KEEP`) for both children | `test_the_python_child_sees_no_api_key_or_egress_flag` |
| `FORGE_COMMIT` read from the candidate's tree | the pin is the grader's, at the merge commit; the patches stay the candidate's, as data | `test_the_pin_for_the_engine_patches_is_the_graders...` |
| Symlinked parent directory makes a restore write outside the worktree | `restore_files` refuses (`Tampered`, score 0) when the parent's `realpath` leaves the worktree | `test_a_symlinked_parent_directory_is_refused...` |
| Python forgery from the runner's globals: `import __main__` and read `_nonce`, `_out`, `_names` (third review) | the runner does its work in `main()`; its secrets are locals, `__main__` holds only imports and `main` | `test_imported_code_that_reads_the_runners_globals_cannot_write_a_forged_result` (red first: score 1) |
| Old engine jars win over the candidate's fix: `deps.txt` holds old-build forge-core/-game/-ai/-gui jars that preceded the candidate's classes and `engine_mods`, so a correct fix to a non-spike overlay class or an engine patch scored 0 (fourth review) | `runtime_classpath` drops the engine's own jars from the dependency list (`engine_mods` holds those four modules; the non-engine jars such as TestNG stay ahead of the candidate's classes) | `test_a_class_of_an_old_forge_jar_loses_to_the_candidates_compiled_class` (red first: real `javac` and `java` printed OLD), `test_the_runtime_classpath_drops_the_forge_jars_of_the_dependency_list` |
| TestNG shadowing: a candidate `forge-overlay/x/src/main/java/org/testng/TestNG.java` compiled into `classes`, which preceded the TestNG jar (third review) | runtime classpath is the reporter, the non-engine dependency jars (the engine's `forge-*` jars are dropped since the fourth review), then the candidate's classes and the engine modules' classes (`runtime_classpath`); after javac the grade is refused (score 0, `Tampered`) if the sources compiled to any class outside `forge/` or with the path of a class in a dependency jar. The check reads the compiled output, so a `package` line the path does not show is caught | `test_a_candidate_testng_main_cannot_write_an_all_pass_result` (red first: the JVM ran the forged main and scored 1), `test_a_candidate_class_in_a_package_outside_forge...`, `test_a_package_declaration_the_path_does_not_show...`, `test_a_candidate_class_with_the_name_of_a_dependency_class...`, `test_the_runtime_classpath_puts_the_dependencies_before...` |
| A source path with a space split in the javac `@argfile` | each path is quoted | `test_a_source_path_with_a_space_in_it_reaches_javac` (red first: `invalid flag`) |
| A hung Python test | per-file timeout, 0 with a note | `test_a_python_test_that_hangs_scores_zero_with_a_note` |

Also guarded, as before: the grader runs its own `eval/engine_patches.py`; the JVM's cwd has a `forge-gui/res` symlink
farm; the Forge guard fails the grade on any file under `/root/forge` (bar `.git`) newer than the grade's start. The
sandbox from `prepare` holds one commit and no object of this repository (`test_the_prepared_sandbox_holds_one_commit...`).

### Open (not closed, and why)

- **In-process forgery by the code under test** (see Status for exactly what is closed). Candidate code that runs in
  the same process as the runner (Python) or the JVM can read the result path and nonce while the run is live
  (`/proc`, frame inspection, `gc.get_objects`, a scan of the scratch directory), can monkeypatch `unittest` or TestNG in
  memory, can call `os._exit`, or can write a file the grader reads afterwards. The nonce stops a stale or pre-planted
  file, a stdout line and a read of `__main__`, nothing more. Only a different user or a container closes it.
- **The engine checkout** is still readable and, to the same user, writable: the guard reports a write after the fact.
- **Tests that derive the repository root from the rules file** (`PuzzleSetupTest`) read the grader's checkout, not the
  candidate's tree, for what they find under that root.
- **A test helper or fixture outside the task's files that the tests read** other than the inputs listed above (a
  fixture with another extension, or a data file outside `eval/` and `docs/`).
- **A candidate class in `forge.*` that replaces a class the engine or an overlay class supplies** is allowed: that is
  what a gold patch to the overlay does.
- **The Java classpath filter needs the candidate to keep the sources the gold patch keeps.** A valid fix that merges
  two overlay files scores 0.

## What is left

- Validate the other 19 tasks (JVM ones are slow); record `red_kind` and the runtime of each in the manifest.
- A non-target test that already failed at base counts as a regression; a baseline comparison would fix it.
- Better prompts for the weak tasks; PRs with no linked issue (26) need an issue-writing step to join the set.
- Flaky tests: re-run the green state a few times before trusting a task.
- Wire `prepare` into the sandbox builder (history stripped, no `.git` remote) and the episode runner.
- Run the candidate's tests as another user or in a container (the isolation-grade step).
