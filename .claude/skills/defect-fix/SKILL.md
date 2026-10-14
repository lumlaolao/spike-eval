---
name: defect-fix
description: Take ONE open harness-defect issue that nobody has claimed, claim it by the CLAIM protocol, show a test failing on main before touching code, fix it only in forge-overlay/, show the test green, run acceptance, take it through pr-review then simplify then standards as fresh subagents, and merge it only through scripts/pr_merge.sh. Use when asked to fix a harness defect or to work the harness-defect backlog.
---

# Defect fix: one issue, claimed, test first, gated

A hunt (`defect-hunt`) files issues; this skill closes one. The unit of work is a single issue and
the order is fixed: claim, fail, fix, pass, accept, review, merge. Skipping a step is a deviation to
record, not a shortcut.

The rule of the fix: **the test comes first and you watch it fail on main.** A test written after the
fix, or one that passes before it, is decoration (`docs/STANDARDS.md` §5).

## 0. Preconditions and the shared Forge lock

A fix installs into the Forge checkout and rebuilds it, so it holds the same lock as a hunt — never
both at once.

```bash
LOCK="$HOME/.cache/mtg-hunt/forge.lock"
mkdir -p "$(dirname "$LOCK")"
take_lock() {
  mkdir "$LOCK" 2>/dev/null && return 0
  p=$(awk '{print $1}' "$LOCK/owner" 2>/dev/null)
  if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null && return 0; fi
  return 1
}
take_lock || { echo "Forge lock held: $(cat "$LOCK/owner" 2>/dev/null)" >&2; exit 1; }
printf '%s %s %s\n' "$$" "$(date -u +%FT%TZ)" "$(id -un)" > "$LOCK/owner"
trap 'rm -rf "$LOCK"' EXIT INT TERM

git status --short          # clean tree, on main
git fetch -q origin && git checkout -q main && git pull -q
pgrep -f LlmSpikeHeadless | wc -l    # must be 0: never rebuild while a game runs
```

## 1. Pick one issue the queue allows

```bash
gh issue list --label harness-defect --state open --limit 100 --json number,title,labels \
  --jq '.[] | select([.labels[].name] | (index("claimed") or index("needs-owner") or index("cannot-reproduce")) | not)
       | "\(.number)\t\([.labels[].name] | join(","))\t\(.title)"'
```

Skip any issue labelled `claimed` (someone is on it), `needs-owner` (the fix needs the owner's
decision first) or `cannot-reproduce` (the evidence no longer reproduces). Among the rest, take the
highest `sev:` first and the smallest fix first: `sev:blocker` before `sev:high`, and a one-line
router defect before a design change. If nothing is left, that is a result: record it and go back to
`defect-hunt`.

## 2. The CLAIM protocol

A claim is the `claimed` label plus its comment. Nothing else reserves an issue.

```bash
N=<issue number>
me=$(gh api user --jq .login)
gh issue edit "$N" --add-label claimed
gh issue edit "$N" --add-assignee @me
gh issue comment "$N" --body "CLAIM $me $(date -u +%FT%TZ) branch fix/<short-name>"
```

- While the `claimed` label is present the issue is not in anyone else's queue. Do not work a
  claimed issue. Do not claim two issues at once.
- Stale claim: if the CLAIM comment is older than 12 h and the branch it names has had no push in
  12 h, comment `STALE CLAIM <me> <utc> (<reason>)`, remove `claimed`, drop the assignee, and claim
  it yourself from the top.
- Release when you stop without a merge: comment `RELEASE <me> <utc> <reason>`, remove `claimed`
  and the assignee. Do not leave a claim you are not working.
- The PR body says `Closes #<N>`; the merge ends the claim and closes the issue.

## 3. Show the defect, then show a test failing on main

Reproduce from the issue's own evidence before writing anything: open the cited JSONL row and the
prompt it quotes. If it does not reproduce, say so in an issue comment with the command you ran and
decide whether the issue needs `cannot-reproduce` — do not "fix" a defect you cannot see.

Then write the test, in the overlay's test tree, named as the behaviour it protects
(`docs/STANDARDS.md` §5), grounded in the recorded row (cite the run and the turn). Java tests live
in `forge-overlay/forge-gui-desktop/src/test/java/forge/spike/`; `LineFindingProbeTest`,
`NativeToolsTest`, `LookaheadProbeTest` and `SeatObservationTest` are where routing and observation
defects belong.

```bash
STAGES="compile javatests" OUT=$PWD/runs/fix/fail-$N bash eval/harness_acceptance.sh
# it must be RED here, on main, before the fix. Save the failing output.
```

Record that output; it goes in the PR's `## Why` and it is the proof the test can fail. A probe test
that was easier to write as a Python check goes in `eval/test_<name>.py` instead
(`docs/STANDARDS.md` §3), and is run with `python3 -m unittest eval.test_<name>`.

## 4. Fix it in `forge-overlay/` only

Never edit the Forge checkout: `forge-overlay/` is the source of truth and the checkout is
overwritten (`CLAUDE.md`). Keep the change to the issue: no drive-by refactors, no play heuristics,
no fallback to the Forge AI.

If the fix changes what the model sees, bump the version that covers it
(`SeatObservation.VERSION`, `ContextMemory.VERSION`, `NativeTools.VERSION`) and follow the stamp
through `docs/STANDARDS.md` §4. If a script should have caught the defect, teach the script
(`match_review.py`, `tool_audit.py`) or add an acceptance stage — that part belongs to this issue.

## 5. Show the test green, then run acceptance

```bash
STAGES="compile javatests" OUT=$PWD/runs/fix/pass-$N bash eval/harness_acceptance.sh
FORGE_HOME=~/code/forge bash install.sh
cd ~/code/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests   # check its exit code
cd - && bash eval/harness_acceptance.sh                                     # every stage, OUT kept
```

The last run is the acceptance line the merge gate wants. A canary that moved because an automatic
path changed on purpose is re-pinned with a dated reason (`eval/harness_acceptance.expected`); never
to make a run pass. Never `mvn`, `install.sh` or acceptance while a game is running.

## 6. Reviews, as three fresh subagents

Run on the diff, not on a description of it, and each in its own session so the reviewer is not the
author. In OpenCode that is the `subagent` tool (`general` agent); give it the skill file as the
brief and `git diff origin/main...HEAD` as the input.

1. `pr-review` (`.claude/skills/pr-review/SKILL.md`) — BLOCKER or HIGH blocks.
2. `simplify` — apply or decline each suggestion with one line of reason.
3. `standards` — `VIOLATIONS` blocks.

After any commit that follows a review, re-run the affected review: a verdict is for one head
(`docs/STANDARDS.md` §1). Record only a verdict a skill actually returned.

## 7. Name it with `pr-format` and open the PR

```bash
git checkout -b fix/<short-name>
git add <files by name>                  # never `git add -A`
git commit -F /tmp/msg.txt               # subject `fix(harness): what is now true`
python3 scripts/pr_format.py check-branch
git push -u origin fix/<short-name>
python3 scripts/pr_format.py new-pr > /tmp/pr.txt
gh pr create --title "<line 1>" --body-file /tmp/pr.txt
```

The test from step 3 is new code, so the acceptance line is required whatever the change touches.
Fill every body section; `## Not done` and `## Provenance` are never empty (write "none"). The body
names the issue: `Closes #<N>`.

## 8. Merge only through the gate

```bash
SHA=$(git rev-parse HEAD)
python3 scripts/pr_format.py record-acceptance <n> "$SHA" <OUT dir> <stages passed> <stages run> "<test counts>"
python3 scripts/pr_format.py record-review <n> "$SHA" pr-review <CLEAN|LOW|MEDIUM|HIGH|BLOCKER> "<one line>"
python3 scripts/pr_format.py record-review <n> "$SHA" simplify <CLEAN|SUGGESTIONS> "<applied or declined>"
python3 scripts/pr_format.py record-review <n> "$SHA" standards <CLEAN|VIOLATIONS> "<one line>"
bash scripts/pr_merge.sh <n>
```

`pr_merge.sh` refuses unless every gate passes: the format check, three recorded verdicts for the
current head, and the acceptance line. Never merge another way and never squash; the commit list is
the record of what was reviewed. If a gate fails, fix it and re-run this step — the verdicts are
stale the moment a new commit lands.

## 9. Close out

The merge closes the issue through `Closes #<N>`; confirm it, then release the lock (the `trap`
does it on exit). If the PR was abandoned instead, comment `RELEASE` and drop the `claimed` label
and the assignee so the issue returns to the queue. If a game was played to reproduce the defect,
its `cost_report.py` spend is part of what the fix cost: say it in the PR's `## Why`.

## What not to do

- Do not fix from the issue text alone: read the cited row first, and if it does not reproduce, say
  so rather than shipping a fix for a defect you never saw.
- Do not write the test after the fix, and do not keep a test that was green before it.
- Do not edit outside `forge-overlay/` for the fix itself, and never edit `~/code/forge`.
- Do not let a review be done by the author's session unless the runtime has no subagents, and say
  so in the recorded verdict if it is.
- Do not merge with the GitHub button, and do not squash.
- Do not work two issues at once, and do not claim an issue you are not about to start.
