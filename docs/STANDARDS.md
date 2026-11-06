# Standards

The rules that keep this repository workable for the next agent or person. Each rule has a reason
and a check: a script, or a named item in a skill's checklist. Anything a script cannot check is
checked by the `pr-review`, `simplify` or `standards` skill (`.claude/skills/`) before a
merge. `CLAUDE.md` holds what the project is and how to run it; this file holds how to change it.

Changing a rule here is a normal PR. The `standards` skill proposes a change when the same
kind of finding shows up in two reviews.

## 1. The merge gate (every PR, no exceptions)

A PR is merged only with `bash scripts/pr_merge.sh <number>`, which refuses unless:

1. `python3 scripts/pr_format.py check-pr <number> --gate` passes: title, body sections, the
   message of every commit on the PR as GitHub holds it, and `## What` listing every commit's subject
   (merges aside: a commit with two parents, whatever its subject, as `check-branch` leaves it out; PR #353), so a
   commit pushed after review is in the record too (PR #343);
2. the body's `## Review` section holds one line per skill for the PR's current head commit,
   written by `pr_format.py record-review`. `pr-review` records CLEAN/LOW/MEDIUM/HIGH/BLOCKER,
   `simplify` CLEAN or SUGGESTIONS, `standards` CLEAN or VIOLATIONS. The gate refuses BLOCKER, HIGH
   and VIOLATIONS; a violation is cleared by a fix and a new review, or by a standards change;
3. the body's `## Evidence` section has a line
   `- acceptance @ <head sha>: OUT=<dir> <N>/<N> stages PASS` (written by
   `pr_format.py record-acceptance`) for any PR that touches `forge-overlay/`, `engine-patches/`, `eval/`,
   `scripts/` or a root launcher (any `*.sh` at the root; `eval/tests/test_pr_format.py` fails when one is missing from
   `pr_format.CODE_PATHS`). The sha must be the PR's current head, and N covers every stage: one PASS line per stage
   in `ALL_STAGES` (`eval/harness_acceptance.sh`), two for canary, 19 today. `record-acceptance` checks the counts
   against the run's own `<OUT>/summary.txt` and refuses a `STAGES` subset, which is prose in Evidence, never this line
   (PR #357).

It merges exactly the commit it checked, with a merge commit. Never squash: the commit list is the
record of what was reviewed. Merging your own PR is fine once the gate passes.

The gate is a record, not a proof. Verdict and acceptance lines are written by the author's
session, and GitHub does not enforce the script (no branch protection); CI (`.github/workflows/tests.yml`)
runs only the Python suite, as a check that is not required. It is paused (owner, 2026-11-06: the month's Actions
minutes are spent) and runs only when started by hand; the local acceptance run is the evidence. Writing a verdict
no skill produced, or merging any other way, is a violation the next `standards` review reports.
Because a verdict is for one head, finish the fixes first, then run acceptance and the three
skills on the final commit; a re-review after fixes checks the fixes and the delta.

| Skill | Question it answers | Blocks the merge on |
|---|---|---|
| `pr-review` | What is wrong with this change that its author has not seen? | BLOCKER, HIGH |
| `simplify` | What can be deleted or made plainer without changing behaviour? | nothing; its accepted suggestions are applied or declined with a reason |
| `standards` | Does the change follow this file? Does this file need to change? | VIOLATIONS |

Run each as a fresh subagent where the runtime has them, on the diff, not on a description of it,
and give it the code rather than your conclusion. Re-run after any commit that follows a review: a verdict is for one head commit.

## 2. Commits and PRs (checked by `scripts/pr_format.py`)

Commit subject: `type(scope): what is now true`, at most 100 characters, no trailing period.

- `type` is the KIND of change, one of `feat fix perf eval data test docs refactor chore build`.
- `scope` is optional and names the AREA: `harness`, `obs v12`, `tools`, `G7`, `play`, `match`,
  `runpod`. History before 2026-10-13 often put the area where the type goes (`harness: ...`);
  write `fix(harness): ...` instead. Old commits are not rewritten; the check covers new ones.
- The rest is a sentence a reader understands without the diff: "the seat reads the opponent's
  cards", not "update SeatObservation".
- The body says why, what was measured, and what would have caught it. Wrap at 100.
- Merge and revert commits are exempt from the subject form. Any commit that is not trivial has a body.
- No model identifier (an `openrouter:` spec, or a slug priced in `eval/pricing/prices.json`) in a
  commit, PR title or PR body; the `Co-Authored-By`, `Claude-Session` and `Signed-off-by` trailers
  and the loopback `fake/` stub aside. `pr_format.py` refuses it, naming what it found and suggesting the
  name in prose. A model's name in prose ("Luna") is fine (owner, 2026-10-21): the rule keeps the spec
  and the priced slug out, not the name.

Branch: `type/short-name` (`fix/own-trigger-window`). A branch the runtime assigns (`claude/...`) may
be used as it is; the PR title then carries the type.

PR title: the same form as a commit subject. PR body, in this order:

```
## What        one bullet per commit subject (merges aside; `check-pr --gate` checks it), then the behaviour
## Why         the evidence that it was needed (file:line, a run directory, a count)
## Evidence    acceptance run (OUT dir, stages, test counts), measurements with their trivial baseline
## Provenance  which stamps moved (observation version, tool versions, canary pins) or "none"
## Review      pr-review / simplify / standards verdicts for <head sha>
## Not done    what was left out on purpose, and what would reopen it
```

`python3 scripts/pr_format.py new-pr` prints this skeleton filled in from the branch's commits.
Install the commit hook once with `bash scripts/install_hooks.sh`.

## 3. Where things go

| Path | Holds | Rule |
|---|---|---|
| `forge-overlay/` | all harness Java, and the game launchers `run-llm-headless.sh` / `run-llm-spike.sh` that `install.sh` copies into `$FORGE_HOME` | the only place harness Java is edited; never the Forge checkout; the launchers are bash 3.2 and parsed by `eval/tests/test_gate_parses.py` |
| `engine-patches/` | our changes to Forge's own files, one unified diff each against the file at `FORGE_COMMIT` | never a whole Forge file in `forge-overlay/` (Forge is GPL); each opens with its notice and is a row of `docs/harness/ENGINE-PATCHES.md` (`eval/tests/test_engine_patches.py`) |
| `eval/*.py`, `eval/demo/`, `eval/harness_env/`, `eval/puzzles/` | drivers, analyzers, statistics (the demo, harness-env and puzzle scripts in their folders) | stdlib only; a script imports a script in another folder by module name after `import evalpath` (`eval/evalpath.py` puts every script folder on `sys.path`); a script in its own folder is imported directly, without it (`import aggregate` in `eval/panel_score.py`; `eval/tests/test_scripts_start.py` resolves both); each file added or changed after 2026-10-13 has `eval/tests/test_<name>.py`. Files without one that day are listed in `eval/UNTESTED.txt`; the list only shrinks (`eval/tests/test_untested_list.py`; its pinned `MAX_UNTESTED` is lowered in the PR that removes a name) |
| `eval/<name>/` | data a driver reads (`tooluse/`, `decks/`, `suites/`) | versioned inside the file |
| `eval/research/` | dated literature and pilot notes behind a design decision (`YYYY-MM-DD-<topic>.md`), plus one `README.md` that catalogues them | as `docs/eval/`: each claim cites its source or a run directory; a note is history, and the current rule lives in `docs/harness/ARCHITECTURE.md` or `docs/eval/`; `README.md`, the only undated file, lists every note and dates its idea statuses (`eval/tests/test_research_index.py`) |
| `.github/workflows/` | CI: the Python suite (`tests.yml`) on the Python floor and a current one; on every PR and push to main, paused to a manual run since 2026-11-06 (the file says how to restore it) | no secrets, `permissions: contents: read`, `pull_request` (never `pull_request_target`), each action pinned to a commit SHA with its tag in a comment (`.github/dependabot.yml` proposes updates weekly); not the merge gate (section 1) |
| `eval/tests/` | every Python test: `test_<name>.py` for the script `<name>.py` in any eval folder, or for what is not a script there: a launcher (`test_setup.py`), a data set (`test_modern_puzzles.py`) or a repository rule (`test_doc_paths.py`, `test_untested_list.py`, `test_gate_parses.py`, `test_launchers.py`, `test_research_index.py`) | `python3 -m unittest discover -s eval/tests` passes on a fresh clone (section 5); a test imports the scripts after `import evalpath` |
| `docs/eval/` | how the benchmark is designed and scored (`EVAL-DESIGN.md`, `SCORING.md`, `PUZZLES.md`, `BENCHMARK-V1.md`, ...) | as `docs/harness/`: a claim cites a file, a test or a run |
| `scripts/` | repository tooling (gate, format, hooks) | stdlib / bash 3.2; `pr_format.py` holds the logic and is tested from `eval/tests/test_pr_format.py`; the shell scripts are thin wrappers |
| `*.sh` (root) | entry points run from the clone (`install.sh`, `setup.sh`, `play-vs-llm.sh`) | bash 3.2; parsed by `eval/tests/test_gate_parses.py`; each is in `pr_format.CODE_PATHS` (checked by `eval/tests/test_pr_format.py`), so a change needs an acceptance run (section 1) |
| `docs/harness/` | what the harness does and what was measured | a claim cites a file, a test or a run |
| `.claude/skills/` | the skills every agent must use; Claude Code and OpenCode both discover them here, so they are not copied elsewhere | tracked; local settings and worktrees are not |
| `runs/`, `datasets/` | generated data | never committed; never `git add -A` at the root |

One concept, one home. Before adding a file, look for the one that already does the job
(`defect_analyzer.py`, `plan_audit.py`, `line_report.py`, `tool_audit.py`, `mine_puzzles.py` cover
most log questions).

## 4. Code

- **Match the file you are in**: its naming, comment density and idiom.
- **A copy of a constant has one source and a test.** When a Python script needs a list the Java harness
  (or another script) owns, it imports it; where it cannot (Java to Python), a test reads the source and
  asserts the copy equal (`eval/tooluse/tools.json` against `NativeTools`, `rules_probe.KEEP_ENV` against
  `ClaudeCliBridge.KEEP_ENV`, `run_batch.PINNED_ENV_KEYS` imported by `tournament.py` and
  `puzzle_baselines.py`; `eval/tests/test_launchers.py` names the diagnostic launchers that need not).
- **A class that parses another class's prompt wording holds a copy.** A Java pattern that reads text another
  class writes has a test that builds that text through the writer, for every kind that writes it, and asserts the
  pattern reads it (`ReplayBridgeTest.everyAnswerRangeAnswerToolWritesIsReadWithBothEnds` pins `ReplayBridge.RANGES`
  to `AnswerTool.instruction`; #365, #373). `ScriptBridge`'s range patterns are a further copy, not yet pinned.
- **A comment says why**, or what would break, and cites the evidence when there is some (a run, a
  count, an engine line). It does not narrate the code or the history of the edit.
- **No silent fallbacks.** A failure branch takes a deterministic legal option and logs it. Never
  the Forge AI for a strategic choice. `eval/defect_analyzer.py` holds the hard invariants
  (`ai_tier_count=0`, `silent_drop_total=0`, `game_crash_count=0`, `bridge_down_total=0`; the list is
  `build_report`'s `hard_invariants`, and `eval/tests/test_defect_analyzer.py` checks this line names each of them);
  never weaken the analyzer to make a run pass.
- **The seat's tools are native tool calls** (`CLAUDE.md`). No new text command a reply must
  consist of.
- **What the model sees is versioned.** A change to prompt bytes bumps the version that covers it
  (every stamp is listed in `docs/harness/VERSIONS.md`, generated by `python3 eval/versions.py --write`;
  `eval/tests/test_versions.py` fails when it is stale, and when a `*VERSION*` or `*EPOCH*` constant in the spike is
  neither in `versions.STAMPS` nor in its `NOT_STAMPED` table with the reason it is not a result stamp:
  `test_every_version_constant_is_a_stamp_or_says_why`, #365, #370) and is stamped on the game line. Legacy profiles stay byte-identical. Each stamp is asserted by literal value in a
  stage of `eval/harness_acceptance.sh`, so a version that moves without the gate moving turns it
  red. Runs across a version, or across an opt-in flag, are never pooled for a comparison; a
  pooled count may be shown for description only when it is labelled, names the arms, and the
  per-arm counts are beside it.
- **JSONL is additive-only.** New fields; never a renamed or re-typed one. Readers use `.get()`.
- **An opt-in flag is a condition.** It is pinned by `run_batch.py`, recorded in the manifest,
  stamped on the game line and keyed by `aggregate.PROVENANCE_FIELDS`, and refused on resume into a
  directory made under a different value. The puzzle runner, `puzzle_baselines.Build.run` (so
  `puzzle_solve.py` and the baselines), drops it from the shell unless its own flag sets it: it strips
  every name in `run_batch.PINNED_ENV_KEYS` (`CONDITION_ENV_KEYS` and `SHADOW_AI`)
  (`test_a_puzzle_game_never_inherits_a_condition_from_the_shell` builds the same names from `CONDITION_ENV_KEYS`
  and `SHADOW_AI` and checks them against `PINNED_ENV_KEYS`; #342, #345), as does the tournament
  (`tournament._match_env`). The puzzle runner also strips every `OPENROUTER*` name by prefix, and
  `CLAUDE_DEADLINE_MS`, which `puzzle_solve.py` sets from `--call-timeout-minutes`
  (`test_a_puzzle_game_never_inherits_the_cli_seats_deadline_from_the_shell`, PR #363). A launcher that goes
  through `run_batch.py` (`improve_loop.py`) inherits its pinning; the diagnostics that launch a game JVM directly
  (`keyword_gauntlet.sh`, `shadow_canary.sh`, `demo_compare.py --fake`, `play-vs-llm.sh` and the
  acceptance canaries in `harness_acceptance.sh`) are never scored and rely on the game-line stamps.
  `eval/tests/test_launchers.py` classifies every file that launches a game, so a new one is named here.
  A condition the JVM reads from the environment but never stamps pools its on- and off-games (issue
  #117: `PLAN_EXECUTE`, `TIER_FAST_TYPES`); every `System.getenv` in the spike files the game reads through
  (`GameLineStampCoverageTest.SCANNED`; every other reader is named with its reason in `NOT_GAME_LINE`) is either
  stamped or on a named allowlist of settings that do not change the seat's decisions (the check is `eval/tests/test_experiments.py`'s
  `GameLineStampCoverageTest`). A setting that touches anything the seat reads, even as a side effect
  (a diagnostic that registers handles early: `PROPOSE_AUDIT`, PR #342), is a condition, not an allowlist
  entry; each allowlist entry carries the reason it changes nothing the seat sees (`NON_CONDITION_ENV` maps
  each name to its reason; `test_every_allowlist_entry_says_why`). Every condition the scan finds stamped or
  recorded elsewhere is also pinned by `run_batch.py`, or named in `NOT_PINNED` with why a shell value may reach a
  game, and every `NOT_PINNED` name is still a condition the scan finds (an entry that stops being one is removed;
  `test_every_stamped_condition_is_pinned_or_says_why`; PRs #342, #344, #354, #355).
  A puzzle run's condition that must stay off the build so a run can resume (the game cap, the
  per-call deadline and the token cap: `game_cap_ms`, `call_timeout_ms`, `max_tokens`, PR #329; `call_timeout_ms`
  is set through the variable the seat's bridge reads, `OPENROUTER_TIMEOUT_MS` for `openrouter:` and `jev:`,
  `CLAUDE_DEADLINE_MS` for `claude-cli:`, PR #363) may
  instead be recorded in each puzzle's `batch-manifest.json` `record`, provided
  `puzzle_score.load_rows` attaches it to every row it reads and the scorer refuses a mix. A resume
  under a lower value is refused. A resume under a higher one replays every puzzle it reaches that
  was played under the lower value, all its attempts, setting the old rows aside unscored
  (`puzzle_solve.already_done`, `prepare`); a puzzle the resume does not reach (the budget) keeps its
  old rows, and the scorer refuses the run until it is replayed. The deadline and the token cap refuse
  a resume under any other value; the scorer refuses to pool either, refuses to pair two deadlines,
  and labels a pair under two token caps `cross_max_tokens`. The checks are
  `eval/tests/test_puzzle_score.py`'s `test_a_row_takes_its_game_cap_from_its_puzzles_manifest`,
  `test_games_under_two_caps_are_never_pooled`, `test_games_under_two_call_deadlines_are_never_pooled_or_paired`
  and `test_games_under_two_token_caps_are_never_pooled_and_a_pair_says_so`, and
  `eval/tests/test_puzzle_solve.py`'s `GameCapTest`.
- **Python**: standard library only, Python 3.9, a module docstring that says what it measures and
  what it cannot. **Shell**: bash 3.2 (macOS); inside a heredoc in `$( )`, apostrophes and
  backticks must pair up as bash reads them (prefer none) — bash 3.2 scans for the closing paren
  before parsing the heredoc, so an unpaired one swallows the rest of the script (issue #219).
  Check: `eval/tests/test_gate_parses.py` runs `bash -n` over the `eval` and `scripts` shell
  scripts and the root launchers (`*.sh`) under the bash that runs it, and reads each script for
  a heredoc inside `$( )` that bash 3.2 would end early, reading its quotes in order as bash 3.2
  does, so the bash 3.2 case fails on Linux too. It cannot see run-time behaviour.
- **Delete what a change makes dead** in the same PR: the unused flag, the helper with no caller,
  the comment about code that is gone.

## 5. Tests

- A test is named for the behaviour it protects, as a sentence
  (`aMalformedCallDoesNotSpendTheBudget`).
- **Say what would make it fail.** A test that passes against the broken implementation is
  decoration. For a new gate or canary, break the thing once and watch it go red.
- Fixtures come from recorded games where they can (cite the run), invented cards where recall
  must be ruled out.
- A re-pinned canary carries a dated reason in `eval/harness_acceptance.expected`. Re-pin when an
  auto/floor path changed on purpose and the new value is explained; never to make a run pass.
- A canary may be pinned per OS: an `<os>.<key>` line (lowercase `uname -s`) wins over the plain
  key, and the plain keys are the reference Mac's. An OS pin is taken only from a reproduced run on
  that OS, carries the same dated reason as a re-pin, and shows that no commit moved it (the same
  number on that OS at the commit where the plain pin was set, or at the base of the change that
  adds the OS pin). A change that moves a canary re-pins it on
  every OS that has a pin, or names the OS left stale in the expectations file and the PR.
  The naming is script-checked (`eval/tests/test_os_pin_staleness.py`, stdlib only): it fails when a
  re-pinned plain key's `<os>.<key>` siblings are neither re-pinned with a date at or after
  the plain re-pin, nor named STALE with the movers (an issue ref, an epoch, or a dated line), nor
  named CONFIRMED with a date at or after the re-pin (measured on that OS and unchanged: a current
  pin must not have to be called stale).
  The check reads this file's shape only; the PR-side half of the naming is off its sight.
- A test never needs `runs/` or a downloaded file to pass: `runs/` is git-ignored and the Comprehensive Rules text
  is fetched (`eval/fetch_comp_rules.py`). A test that reads one skips, saying why, when it is absent, so
  `python3 -m unittest discover -s eval/tests` passes on a fresh clone, as a user without root and without a Forge
  checkout (the CI runner: a lookup of a default checkout such as `/root/forge` treats a folder it cannot read as
  absent); acceptance fetches the text first, so there it runs.
- A test of the seat's play path reads `ctrl.playsNotMade()` or the game state (the card moved, the mana tapped), never the
  return value of `PlayerControllerLLM.playChosenSpellAbility`: it never returns false, so a play that was not made
  still passes (issue #380; `grep -rn 'assert.*playChosenSpellAbility\|= *ctrl.playChosenSpellAbility'
  forge-overlay` finds a site that reads it, and finds none). `playsNotMade()` does not count a cancel; a cancel is
  read from the game or the log (`KeywordSurfaceSweepTest.playVerdict`). Checked by review.
- `bash eval/harness_acceptance.sh` is the whole-harness gate; it must pass on the PR's head.
- The acceptance compile rejects what the Forge checkout build is known to reject: unused and
  duplicate imports in `forge-overlay/` fail the `compile` stage (`eval/java_imports.py`). When the
  checkout build rejects something acceptance accepted, the check for it is added here.

## 6. Measurement

- Report every rate next to its trivial baseline and its interval. No baseline, no number.
- A single game is noise: use `--pairs`, and say what size of difference the sample can resolve.
- Check `uptime` first; above 4 load average per core, timing and capped games are not evidence.
- **Paid runs are budgeted and start small.** Every paid batch passes `--budget-usd`; its log ends
  with a `[cost]` line and `python3 eval/cost_report.py <dirs>` gives the spend of any run. Start
  with a block of about six games per arm, read it, and only then buy the next block; say what a
  block will cost before starting it (`cost_report.py --plan N`). Prefer the measurement that costs
  nothing (a loopback game, the unit tests) or cents (the tool-use suite) when it answers the
  question. A model with no entry in `eval/pricing/prices.json` is not run.
- Real paid runs use only the model the owner has authorised, named once in `CLAUDE.md` ("Owner
  rules"), always with `EVAL_OUT` set. Never set `OPENROUTER_ALLOW_EGRESS` on the owner's behalf.
- A finding is a harness defect unless the prompt shows the model had what it needed
  (`.claude/skills/harness-audit/`).
- Reserved seed blocks (501-525, 20000-29999, 30000-30099) are never training-data sources;
  `eval/seed_policy.py` is the single source of truth.
- Never rebuild (`mvn`, `install.sh`) while games run. `FORGE_COMMIT` moves only with the canaries
  re-pinned and a dated reason.
- The JSONL winner field is identity-keyed (A = deck identity A whatever the seat).

## 7. Docs

- State what is true now; the history belongs in the commit.
- Every repository path named in `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, this file,
  `.claude/rules/` and `.claude/skills/`, in a backtick span (alone or as a word of a command) or a fenced
  block, is a tracked file or folder: those are what every agent reads first and copies. Checked by
  `eval/tests/test_doc_paths.py`, for paths under the repository's folders (root files aside: a bare launcher
  name can mean the copy in `$FORGE_HOME`; placeholders with `<`, `*`, `?`, `{`, `$` or `...` aside). A path a
  command will create, or a fetched file that is not tracked, is written with a placeholder (`<out>.jsonl`).
- Every number is reproducible from a named command and run directory. Run directories are not in the repository
  (`runs/` is git-ignored): the doc names the one its author kept.
- Say what was not determined.
- A doc does not record the acceptance run of its PR's final head: that belongs in the PR's `## Evidence`, or
  recording it would need a commit that invalidates it. A run on an earlier head of the same PR that changed
  something (a failure found, a canary moved, a new stage count) is recorded in `docs/harness/ACCEPTANCE.md`
  with its sha.
- `docs/harness/ARCHITECTURE.md` stays true: a change that alters how the harness works (a decision path,
  a tool, the prompt, a bridge, a default, an eval rule) updates it in the same PR.

## 8. The model is the only bottleneck (the harness contract)

The harness is the bottleneck when it decides for the model, asks it blind, or does not use its
answer. It is not the bottleneck when it executes what the model chose: the model owns every
strategic choice, the engine owns every mechanism. Each rule has its check.

| Rule | Check |
|---|---|
| **Every engine decision is the model's, or says why not.** A decision row from the model, a `tier=auto` row naming the reason (forced, no budget, unparsed), or a `t:"window"` row for a priority window the play loop passed. A pass the harness guessed (`window-gate`, `own-stack`, `interaction-filter`) with something usable is a defect; a pass the model asked for (`yield:`) is the model's. | `match_review.py` (`SKIP`, `TAKEN`); `aWindowThePlayLoopPassesWithoutAskingIsRecorded` |
| **Never Forge's AI for strategy.** An inherited or `super.` hook that chooses is either routed or listed in the ledger with its reason. The silent strategic count may only fall. | `ControllerOwnershipTest` (baseline 0; the colour an any-colour source makes during a payment is asked through an engine patch, `docs/harness/ENGINE-PATCHES.md`; Phyrexian life-or-mana is the seat's, `phyrexianManaPaidWithLifeIsTheSeatsChoice`); `defect_analyzer.py` `ai_tier_count=0` |
| **Every ask carries what it turns on**: the current observation, the source's rules text, whose each option is, the resource a number spends, the play it follows and the model's own reason for that play, and a way to cancel a play that is not committed yet. Nothing the model reads to decide is cut short. | `PromptFidelityTest` (golden check `everyAskCarriesTheStateAndNoCutOption`; cancel at the target and cost steps: `cancellingAtTheTargetStepAsksTheMenuAgain`, `cancellingAtTheSacrificeCostAsksTheMenuAgain`, `cancellingAtTheDiscardCostAsksTheMenuAgain`); acceptance `faithful` (no option ending in `...`) |
| **Every answer is the one used.** The reply's last `CHOICE` is the recorded pick; a reply that was not used is a `LOST` finding. | `match_review.py`; `test_a_reply_that_was_not_the_option_used_is_lost` |
| **How hard the model may think is a condition, not a per-decision routing choice.** `ONE_TIER` (one model, reasoning on, for every decision) is stamped `one_tier`; a two-tier run is a different arm. | `run_batch.py --one-tier`, `TestOneTier`; `aggregate.py` experiment key |
| **No ask coaches one deck's line.** Examples in asks are format placeholders; deck plans, PRINCIPLES and the static reference are named assistance conditions (`run_batch.py --no-coaching` drops the last two, stamped `no_coaching`). | `noAskCoachesOneDecksLine`; `withoutCoachingTheSystemPromptHasNoPrinciplesOrReference`, `TestNoCoaching` |
| **A decision is what Forge's human controller asks.** Every `PlayerControllerHuman` method that prompts a human (directly or through a helper) is the seat's in the ownership ledger, or the ledger says why not (`none` / `rare` reach). | `eval/human_decisions.py` (0 unowned on the pinned engine); `test_the_pinned_engine_has_no_core_decision_the_seat_does_not_own` |
| **Every ask of every type keeps the contract**, not only the ones a test was written for: a `DECISION:` line, the observation header, no cut option, battlefield options that say whose, and the play it follows before the question. | `AskContract`, run by `PromptFidelityTest.everyAskKeepsTheContract` and `GoldenChecksTest.everyAskKeepsTheContract` after every test, on every prompt the seat asked |
| **Every decision type the seat owns is asked in a test**, through the live controller, with the model's answer asserted as the one used, so the contract above runs on all of them. A new type is added to the list and given a test. | `GoldenChecksTest.everyOwnedDecisionTypeIsExercisedHere` (fails on an owned type no test drives, or a type asked that the list lacks); inventory in `docs/harness/GOLDEN-CHECKS.md` |
| **The question is read whole and in the seat's terms**: not cut short (`...`), no engine card id (`Name (93)`), no engine placeholder (`CARDNAME`), no engine filter syntax (`Card.nonLand`), no deck's line coached; options numbered 1..n with no label repeated. | `AskContract` (question and option rules); `aNameFilterIsSaidInWords`; `match_review.py` LEAK |
| **The answer format matches the menu**: a `CHOICE:` ask offers at least two options, a `PICKS:` ask states `Choose between A and B` within the options it lists, a `NUMBER:` ask states its legal range. | `AskContract` (answer-contract rules); `numbersAreTheModels`, `dividedTargetsAndTheirSplitAreTheModels` |
| **The observation never contradicts the menu**: a hand card tagged `(castable)` is on the play menu, and a card the menu casts is not tagged otherwise. | `AskContract` (hand-tag rule); `theHandCallsCastableOnlyWhatTheMenuCanCast` |
| **A decision with a source card names it with its rules text**, whatever the hook (a number, a colour, a yes/no, a sacrifice, a name). | `AskContract.namesSource`, set per test in `GoldenChecksTest` |
| **A reply is read the way it was meant.** A tag counts only when its number follows it closely; a block pair is split where an attacker and a blocker match; a reply with no answer line is asked again, then recorded as `LOST`, never read as a PASS or "no blocks". | `parseTagNumber_aLaterTagFollowedByWordsIsNotAnAnswer`, `aBlockNamingACardWithACommaIsUsed`, `aBlockReplyWithNoBlocksLineIsAskedAgain`; `match_review.py` `LOST` |
| **The plan is the model's and changes when it wants.** Under the faithful profile a `PLAN:` line in any reply, at any decision, replaces the plan (a goal and the steps toward it); the plan line says when it was stated. The harness never writes, drops or judges the plan. | `PlanAnytimeTest` (`aPlanChangedAtATargetQuestionIsTheNextPromptsPlan`) |
| **The seat can say a question is broken.** `report_unclear_decision` records the complaint and the decision is still answered; every report is read: a true one is a harness defect, a false one a model mistake. | `aReportIsRecordedAndTheDecisionIsStillAnswered`; `match_review.py` `REPORT` |
| **A seat that never answers scores no better than one that passes.** With no answer the harness takes the do-nothing option where one exists (pass, no attack, decline) and the first legal option only where a choice is mandatory; never a land drop, an attack or Forge's AI. | acceptance `gaming` (empty-reply, garbage-reply and out-of-credits 402 seats vs the floor seat; the 402 game is counted as lost to a dead bridge, invalid in `aggregate.py` and a `defect_analyzer.py` failure, issue #369); `aSeatThatNeverAnswersNeitherPlaysALandNorAttacks` |
| **An arm that suspends a contract rule is a named exception, never a default.** It is off by default, refused outside `--exploratory` and in any panel, and the rule it suspends names it where that rule is stated (`CLAUDE.md`, `.claude/rules/`, this table): `PROPOSE_ACTIONS` hides the menu, so "a legal action is never hidden" names it (owner, 2026-11-04). | `run_batch.py` refusals (`propose_refusal`, `TestProposeActions`); review |
| **The harness decides at most 2% of the choices the model could make.** match_review's TAKEN and LOST plus any Forge-AI row, forced picks excluded, over the llm identities' decisions. | `aggregate.py` harness-decided gate (exit 4); `test_harness_decided_gate` |
| **The harness does not limit the model.** Nothing the seat reads is cut, and what it may look up, preview, write or think, and what the play menu offers, is capped only by a loop guard far above any recorded game, a condition that defines an arm, or a transport retry; each kept limit is listed with its reason in `docs/harness/MODEL-LIMITS.md`. Cost is not a reason for a cap. | `SeatObservationTest.nothingTheObservationShowsIsCut`, `ContextProfileTest.theFaithfulHistoryIsWhole`, `ContextMemoryTest.theToolBudgetsAreLoopGuardsNotDecisionLimits`, `ContextProfileTest.theCallBudgetsAndTheTurnCapAreLoopGuards`; menu limits: review against `docs/harness/MODEL-LIMITS.md` (no test pins them) |
| **The interface has one version.** `HARNESS_EPOCH` moves with any change to what the seat is asked, when, or what its answer does, beside the per-layer versions; games across an epoch are never pooled or compared in a count or rate (a labelled illustration that computes none, `demo_compare.py`, is allowed). | game line `harness_epoch`, asserted in acceptance `faithful`; `aggregate.py` experiment key |

A game read by `game-loop` that finds a defect outside these checks adds the check here with its fix.
