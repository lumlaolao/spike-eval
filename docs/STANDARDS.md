# Standards

The rules that keep this repository workable for the next agent or person. Each rule has a reason
and a check: a script, or a named item in a skill's checklist. Anything a script cannot check is
checked by the `pr-review`, `simplify` or `standards` skill (`.claude/skills/`) before a
merge. `CLAUDE.md` holds what the project is and how to run it; this file holds how to change it.

Changing a rule here is a normal PR. The `standards` skill proposes a change when the same
kind of finding shows up in two reviews.

## 1. The merge gate (every PR, no exceptions)

A PR is merged only with `bash scripts/pr_merge.sh <number>`, which refuses unless:

1. `python3 scripts/pr_format.py check-pr <number> --gate` passes: title, body sections, and the
   message of every commit on the PR as GitHub holds it;
2. the body's `## Review` section holds one line per skill for the PR's current head commit,
   written by `pr_format.py record-review`. `pr-review` records CLEAN/LOW/MEDIUM/HIGH/BLOCKER,
   `simplify` CLEAN or SUGGESTIONS, `standards` CLEAN or VIOLATIONS. The gate refuses BLOCKER, HIGH
   and VIOLATIONS; a violation is cleared by a fix and a new review, or by a standards change;
3. the body's `## Evidence` section has a line
   `- acceptance @ <head sha>: OUT=<dir> <N>/<N> stages PASS` (written by
   `pr_format.py record-acceptance`) for any PR that touches `forge-overlay/`, `eval/`, `scripts/`,
   `install.sh` or a `run-llm-*` launcher. The sha must be the PR's current head.

It merges exactly the commit it checked, with a merge commit. Never squash: the commit list is the
record of what was reviewed. Merging your own PR is fine once the gate passes.

The gate is a record, not a proof. Verdict and acceptance lines are written by the author's
session, and GitHub does not enforce the script (no branch protection, no CI). Writing a verdict
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

Branch: `type/short-name` (`fix/own-trigger-window`).

PR title: the same form as a commit subject. PR body, in this order:

```
## What        the change, in terms of behaviour
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
| `forge-overlay/` | all Java | the only place Java is edited; never the Forge checkout |
| `eval/*.py` | drivers, analyzers, statistics | stdlib only; each file added or changed after 2026-10-13 has `eval/test_<name>.py`. Files without one that day are listed in `eval/UNTESTED.txt`; the list only shrinks |
| `eval/<name>/` | data a driver reads (`tooluse/`, `decks/`, `suites/`) | versioned inside the file |
| `scripts/` | repository tooling (gate, format, hooks) | stdlib / bash 3.2; `pr_format.py` holds the logic and is tested from `eval/test_pr_format.py`; the shell scripts are thin wrappers |
| `docs/harness/` | what the harness does and what was measured | a claim cites a file, a test or a run |
| `.claude/skills/` | the skills every agent must use; Claude Code and OpenCode both discover them here, so they are not copied elsewhere | tracked; local settings and worktrees are not |
| `runs/`, `datasets/` | generated data | never committed; never `git add -A` at the root |

One concept, one home. Before adding a file, look for the one that already does the job
(`defect_analyzer.py`, `plan_audit.py`, `line_report.py`, `tool_audit.py`, `mine_puzzles.py` cover
most log questions).

## 4. Code

- **Match the file you are in**: its naming, comment density and idiom.
- **A comment says why**, or what would break, and cites the evidence when there is some (a run, a
  count, an engine line). It does not narrate the code or the history of the edit.
- **No silent fallbacks.** A failure branch takes a deterministic legal option and logs it. Never
  the Forge AI for a strategic choice. `eval/defect_analyzer.py` holds the hard invariants
  (`ai_tier_count=0`, `silent_drop_total=0`); never weaken the analyzer to make a run pass.
- **The seat's tools are native tool calls** (`CLAUDE.md`). No new text command a reply must
  consist of.
- **What the model sees is versioned.** A change to prompt bytes bumps the version that covers it
  (`SeatObservation.VERSION`, `ContextMemory.VERSION`, `NativeTools.VERSION`) and is stamped on the
  game line. Legacy profiles stay byte-identical. Each stamp is asserted by literal value in a
  stage of `eval/harness_acceptance.sh`, so a version that moves without the gate moving turns it
  red. Runs across a version, or across an opt-in flag, are never pooled for a comparison; a
  pooled count may be shown for description only when it is labelled, names the arms, and the
  per-arm counts are beside it.
- **JSONL is additive-only.** New fields; never a renamed or re-typed one. Readers use `.get()`.
- **An opt-in flag is a condition.** It is pinned by `run_batch.py`, recorded in the manifest, and
  refused on resume into a directory made under a different value.
- **Python**: standard library only, Python 3.9, a module docstring that says what it measures and
  what it cannot. **Shell**: bash 3.2 (macOS); no apostrophe inside a heredoc in `$( )`.
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
- `bash eval/harness_acceptance.sh` is the whole-harness gate; it must pass on the PR's head.

## 6. Measurement

- Report every rate next to its trivial baseline and its interval. No baseline, no number.
- A single game is noise: use `--pairs`, and say what size of difference the sample can resolve.
- Check `uptime` first; above 4 load average per core, timing and capped games are not evidence.
- Real paid runs use only a model the owner has authorised (currently
  `openrouter:deepseek/deepseek-v4.1-flash`), always with `EVAL_OUT` set. Never set
  `OPENROUTER_ALLOW_EGRESS` on the owner's behalf.
- A finding is a harness defect unless the prompt shows the model had what it needed
  (`.claude/skills/harness-audit/`).
- Reserved seed blocks (501-525, 20000-29999, 30000-30099) are never training-data sources;
  `eval/seed_policy.py` is the single source of truth.
- Never rebuild (`mvn`, `install.sh`) while games run. `FORGE_COMMIT` moves only with the canaries
  re-pinned and a dated reason.
- The JSONL winner field is identity-keyed (A = deck identity A whatever the seat).

## 7. Docs

- State what is true now; the history belongs in the commit.
- Every number is reproducible from a named command and run directory.
- Say what was not determined.
- A doc does not record the acceptance run of the PR that edits it: that belongs in the PR's
  `## Evidence`, or recording it would need a commit that invalidates it.
