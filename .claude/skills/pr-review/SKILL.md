---
name: pr-review
description: Reviews a pull request or staged diff in this repository before it is merged and finds what its author has not seen. REQUIRED on every PR before it is merged (docs/STANDARDS.md section 1). Read-only. Returns findings in tiers with file:line, a failure scenario and a fix; BLOCKER or HIGH blocks the merge.
---

# pr-review

Run this as a fresh subagent whenever your runtime has one (Claude Code: the Agent tool; OpenCode:
a subagent task), with this file as the brief and the diff as the input, so the reviewer is not
the author. Without subagents, or when the owner has asked for none, follow it yourself, start to
finish, before you write another line of the change, and say in the recorded verdict that the
author's session did the review.

You review one change to the LLM-plays-Magic harness. You are the reader who was not there when it
was written. You do not edit, stage, commit, build or play games: live paid games may be running
against the Forge checkout, so never run `mvn`, `install.sh` or `eval/harness_acceptance.sh`.
Running a test file, `python3 eval/tests/test_<name>.py` for the script the diff touches (`test_pr_format.py` for
`scripts/`), is fine.

## Input

A PR number, or "the staged diff" (`git diff --cached`). Fetch a PR by REST: `gh api
repos/{owner}/{repo}/pulls/<n> -H "Accept: application/vnd.github.diff"` for the diff, the same path without the
header for the description (`gh pr diff` and `gh pr view` use GraphQL, which cloud sessions are refused); where `gh`
has no token, the GitHub MCP tool `pull_request_read`.

Read `CLAUDE.md` and the sections of `docs/STANDARDS.md` the diff touches: 4 for code, 5 for tests, 8 for anything the
seat sees or does, 6 or 7 when it changes a number or a doc. Then every file the diff touches, not just the hunks: most
defects here live in what a hunk assumes about code it does not show. A file under about 1,500 lines is read whole. A
larger one (`LlmSpikeGui.java` is about 13,500 lines, 950 KB: more than a context holds) is read by method: the whole
of each method a hunk is in, every caller of a changed method and every reader of a changed field (`grep -n` the
name), and the fields the change uses. Name what you did not read under NOT CHECKED.

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
