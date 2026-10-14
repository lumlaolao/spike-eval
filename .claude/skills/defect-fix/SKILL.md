---
name: defect-fix
description: Stage two of the defect backlog for mtg-forge-llm-agent. Claim ONE open harness-defect issue, prove it still happens on main with a failing test, fix it in forge-overlay, show the test turn green, and take it through acceptance, the three review skills and a PR that closes the issue.
---

# Defect fix: one issue, proven, fixed, one PR

This is stage two of a two-stage loop. Hunters (`defect-hunt`) play games and file each harness
defect as a GitHub issue labelled `harness-defect`. You take exactly one, show it is real on
current `main`, fix it, show it is gone, and open the PR that closes it. Many fixers run at once,
so the claim protocol and the merge rules below are what keep you from colliding.

Read `CLAUDE.md`, `docs/STANDARDS.md`, and step 4 of `.claude/skills/game-loop/SKILL.md` (how a
harness fix is made here) before you start. Everything there applies; this file adds the claim,
the verification and the hand-off.

## 1. Pick one issue

```bash
gh issue list --label harness-defect --state open --limit 100 \
  --json number,title,labels,comments,createdAt
```

Skip anything labelled `claimed`, `needs-owner` or `cannot-reproduce`. From the rest, take the
highest severity (`sev:blocker` > `sev:high` > `sev:medium` > `sev:low`); among equals, the one
seen in the most games (count its **Seen** lines and "seen again" comments), then the oldest.
Read the whole issue and every comment before you claim it.

A claim is stale when it is more than 24 hours old with no linked PR and no `RELEASE`. You may take
a stale one: say so in your CLAIM comment and quote the stale claim.

## 2. Claim it

Every agent acts on GitHub as the same account, so assignment cannot tell fixers apart. The claim
is a comment, and the earliest live claim wins.

```bash
gh issue edit <n> --add-label claimed
gh issue comment <n> --body "CLAIM <your session link or branch name> <UTC time>"
gh issue view <n> --comments        # re-read now
```

If an earlier `CLAIM` comment with no later `RELEASE` from that claimer is there, yours lost:
comment `RELEASE lost the race to <their claim>` and go back to step 1 without removing the label.
Hold one claim at a time. If two issues turn out to share one cause, claim the second too and fix
both in one PR.

When you give up for any reason, comment `RELEASE <what you learned, what you tried>` and remove
`claimed`. A silent abandoned claim blocks the issue for a day.

## 3. Branch from current main

```bash
git fetch origin main && git switch -c fix/<short-name> origin/main
```

Use `type/short-name` from `docs/STANDARDS.md` section 2. If your session assigns you a branch,
use that one and say so in the PR, as #95 did.

## 4. Prove it still happens

The issue was filed against an older `main`. Before writing any fix:

1. **Is it already fixed?** `git log --oneline <issue's sha>..origin/main -- <files under Where>`,
   and read the code at **Where** on current main. If a commit since then fixed it, comment the
   commit and how you know, and close the issue (`gh issue close <n> --reason completed`).
2. **Reproduce it on the cheapest rung that reaches it**, as the issue's **Reproduce** section
   suggests, and confirm the suggestion rather than trusting it:
   - **a. A unit test (always try this first).** Build the position from the issue's Evidence in
     the test class that covers the path (`LlmSpikeTest`, `SeatObservationTest`,
     `NativeToolsTest`, `LineFindingProbeTest`, `LookaheadProbeTest`, `ControllerOwnershipTest`;
     `eval/test_<name>.py` for a Python detector). Name it for the behaviour it protects
     (`aReturnToHandCostIsTheSeatsChoice`). Run it on main and **watch it fail for the reason the
     issue gives**. A test that fails for another reason, or passes, is not a reproduction.
   - **b. A zero-model game** when the defect needs a real game to reach: the random arm
     (`--controller-a random`, zero cost, deterministic on one OS) with a deck holding the card,
     then grep the log or the rows for the decision. Floor never builds a play menu, so it proves
     nothing about plays, attacks or blocks (`CLAUDE.md`, validation ladder).
   - **c. One model-lane game** only when a and b cannot reach it: `--smart-a haiku --fast-a haiku`
     on the Claude CLI, or the owner-authorised OpenRouter model with `--budget-usd`. Never opus,
     never a batch.
3. **If nothing reproduces it,** do not fix on faith. Comment what you ran and what you saw, add
   `cannot-reproduce`, and `RELEASE`. If you think the issue's **What should have happened** is
   itself wrong (the engine allows what the hunter called a defect), say so with the engine source
   and close it as not planned.

Comment `REPRODUCED <test name or command>, fails on main @ <sha>: <the failure>` on the issue.
That comment is the proof the next reader trusts, so quote the actual failure.

## 5. Fix it

`game-loop` step 4, items 2 and 3:

- Fix in `forge-overlay/` only, never in the Forge checkout. No fallback to Forge AI for a
  strategic choice; a failure branch takes a deterministic legal option and logs it.
- Keep it to what the issue needs. Anything else you notice is a comment on the issue or a new
  issue in the hunter's format, not part of this PR.
- If the fix changes what the model sees, bump the version that covers it (`SeatObservation`,
  `ContextMemory`, `NativeTools`) and its literal assertion in `eval/harness_acceptance.sh`.
- Close the **Detector gap**: if the issue names a script that should have flagged it, teach that
  script in the same PR with its own test (`eval/test_<name>.py`), and break it once to see it go red.

## 6. Prove it is gone

1. The test from step 4 now passes, and the rest of its class still does.
2. If you reproduced on rung b or c, rerun the same command on the fixed build and show the
   **Fixed when** result in the new row or log line. Quote it.
3. `bash eval/harness_acceptance.sh` on the final commit. A canary that moved because the fix
   changed an automatic path on purpose is re-pinned with a dated reason naming the issue. Canaries
   are pinned per OS (`docs/STANDARDS.md` section 5): re-pin the OS you ran on, and name the OS you
   could not run, as stale, in `eval/harness_acceptance.expected` and the PR.

Before building: on a machine other agents share, no `install.sh` or `mvn` while any game runs
(`pgrep -f LlmSpikeHeadless` must print nothing). Hunters may be mid-game.

## 7. Merge main again, then the PR

Other fixers are landing at the same time, and most fixes touch the same few files
(`LlmSpikeGui.java`, the observation version, `harness_acceptance.expected`). So, last thing before
acceptance and review:

```bash
git fetch origin main && git merge origin/main     # a merge commit; never rebase a pushed branch
```

If a version you bumped also moved on main, bump again from main's value. If a canary pin moved on
main, re-pin from a run on the merged tree. Then run acceptance and the reviews on that commit.

Then `pr-format` from start to finish:

- Commit and PR title in the `type(scope): what is now true` form.
- **Why** quotes the issue's Defect and Evidence and your REPRODUCED comment. Add a line
  `Fixes #<n>` (one per issue) so the merge closes it.
- **Evidence** has the red-then-green test, the rung b/c rerun if any, and the acceptance line.
- **Not done** lists what the issue mentioned that this PR does not cover.
- Run `pr-review`, `simplify` and `standards` as fresh subagents on the final head, fix what blocks,
  and record each verdict with `scripts/pr_format.py record-review`.

Merge only with `bash scripts/pr_merge.sh <pr>`, and only when the owner has said agents may merge
their fixes. Otherwise stop at a gate-ready PR and say it is ready. If main moves while you wait
and the PR conflicts, merge main again and rerun step 6.3 and the reviews: verdicts are for one head.

## 8. Hand off

Comment on the issue: `PR #<pr>` and one line on what changed. Leave `claimed` on until the PR
merges or closes; the issue closes itself on merge. If the PR is closed unmerged, `RELEASE` with
the reason. Then go back to step 1 for the next issue, if the owner asked for more than one.

## What a fixer never does

- Work an issue it has not claimed, or hold two claims for unrelated issues.
- Write the fix before the reproduction fails on main.
- Weaken `defect_analyzer.py`, a test or a canary to make its own run pass.
- Push to another fixer's branch, or to `main`.
- Close an issue it could not reproduce as fixed. `cannot-reproduce` and a RELEASE, with what was
  tried, is the honest end.
