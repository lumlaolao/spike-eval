---
name: pr-format
description: Name and structure commits, branches and pull requests for this repository, and take a PR through the merge gate. Use whenever you are about to commit, open a PR, write or fix a PR title or body, or merge.
---

# Commits, PRs and the merge gate

The format lives in one script, `scripts/pr_format.py`, and is described in
`docs/STANDARDS.md` section 2. Do not write a title or body from memory; generate and check it.

## Commit

Subject and body rules: `docs/STANDARDS.md` section 2; the commit hook enforces them.

```bash
bash scripts/install_hooks.sh                 # once per clone: checks every commit message
git add <files by name>                       # never `git add -A` at the root
git commit -F <message file>
python3 scripts/pr_format.py check-branch     # every commit on the branch
```

End the message with the attribution lines your session was given.

## Pull request

```bash
git push -u origin <type/short-name>
python3 scripts/pr_format.py new-pr > /tmp/pr.txt     # title on line 1, blank, then the body skeleton
# fill in every section; "Not done" and "Provenance" are never left empty (write "none" if so)
gh pr create --title "<line 1>" --body-file <body file>
python3 scripts/pr_format.py check-pr <n>
```

## Review, then merge

Run all three skills on the PR's current head, in this order, and record each verdict:

```bash
# skills: pr-review, simplify, standards  (.claude/skills/)
python3 scripts/pr_format.py record-review <n> pr-review LOW "two nits, fixed in <sha>"
python3 scripts/pr_format.py record-review <n> simplify CLEAN
python3 scripts/pr_format.py record-review <n> standards CLEAN
bash scripts/pr_merge.sh <n>                  # refuses unless the gate passes; merge commit, never squash
```

A new commit after a review makes the recorded verdicts stale: the gate compares them to the PR
head. Fix BLOCKER and HIGH findings, then review again. Simplifier suggestions are applied or
declined with one line of reason in the PR body.
