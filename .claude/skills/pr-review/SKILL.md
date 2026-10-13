---
name: pr-review
description: Reviews a pull request or staged diff in this repository before it is merged and finds what its author has not seen. REQUIRED on every PR before it is merged (docs/STANDARDS.md section 1). Read-only. Returns findings in tiers with file:line, a failure scenario and a fix; BLOCKER or HIGH blocks the merge.
---

# pr-review

Run this as a fresh subagent whenever your runtime has one (Claude Code: the Agent tool, with
this file as the brief and the diff as the input), so the reviewer is not the author. Without
subagents, follow it yourself, start to finish, before you write another line of the change.

You review one change to the LLM-plays-Magic harness. You are the reader who was not there when it
was written. You do not edit, stage, commit, build or play games: live paid games may be running
against the Forge checkout, so never run `mvn`, `install.sh` or `eval/harness_acceptance.sh`.
Running `python3 -m unittest` in `eval/` or `scripts/` is fine.

## Input

A PR number (`gh pr diff <n>`, `gh pr view <n>`), or "the staged diff" (`git diff --cached`).
Read `CLAUDE.md` and `docs/STANDARDS.md` first, then every file the diff touches, whole, not just
the hunks: most defects here live in what a hunk assumes about code it does not show.

## What to look for, in this order

1. **Behaviour that is wrong.** For each changed function: what inputs reach it, what does it
   return on each path, what state does it leave. Construct the input that breaks it.
2. **The invariants this repository has paid for** (`CLAUDE.md`):
   - never the Forge AI for a strategic choice; failure branches take a deterministic legal option
     and log it;
   - legacy context profiles and a plain model ask stay byte-identical;
   - what the model sees is versioned and stamped; runs of different conditions cannot be pooled or
     resumed into one directory;
   - JSONL is additive-only;
   - a reply holding a tool request never reaches a permissive answer parser; tool budgets hold;
   - loopback endpoints never receive an API key; nothing sets the egress flag;
   - pass priority with `null`, never an empty list; targets set before a cast is returned.
3. **Tests that cannot fail.** For each new test, name the change to the implementation that would
   turn it red. If there is none, that is a finding. So is a gate nobody has seen fail.
4. **Claims the evidence does not carry.** Every number in a doc or PR body: is the run named, is
   the baseline next to it, does the sample resolve the difference claimed, were conditions pooled.
5. **What is missing.** The path with no test, the flag with no provenance stamp, the reader of a
   renamed field, the doc that still describes the old behaviour.

Verify before you report: read the code path end to end, and run the unit test or a three-line
script when that settles it. A finding you could not confirm is reported as PLAUSIBLE, not dropped
and not inflated.

## Output

```
VERDICT: CLEAN | LOW | MEDIUM | HIGH | BLOCKER      (the highest tier found)
HEAD: <sha reviewed>

BLOCKER  data loss, a broken invariant from the list above, a wrong published number
HIGH     wrong behaviour on an input that will occur; a test or gate that cannot fail
MEDIUM   wrong on an unusual input; a missing test for a real path; a misleading doc
LOW      naming, a stale comment, a small duplication

For each finding:
  [TIER] file:line  one-sentence defect
    scenario: concrete input/state -> wrong result
    fix: what to change
    confidence: CONFIRMED (how) | PLAUSIBLE (what would confirm it)

CHECKED CLEAN: the areas you examined and found nothing in, by name.
NOT CHECKED: what you could not examine, and why.
```

No praise, no summary of the diff, no style opinions the standards do not back.
