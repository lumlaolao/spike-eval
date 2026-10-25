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
# `gh pr create` uses GraphQL, which cloud sessions are refused (CONTRIBUTING.md); the REST form:
gh api -X POST repos/{owner}/{repo}/pulls -f head=<branch> -f base=main -f title="<line 1>" -F body=@<body file>
python3 scripts/pr_format.py check-pr <n>
```

## Review, then merge

Run all three skills on the PR's current head, in this order, and record each verdict:

```bash
# skills: pr-review, simplify, standards  (.claude/skills/)
# <sha> is the commit that was tested / the HEAD: line of the skill's report; refused if it is not the PR head
python3 scripts/pr_format.py record-acceptance <n> <sha> /tmp/<OUT dir> 12 12 "284 Java, 150 Python"
python3 scripts/pr_format.py record-review <n> <sha> pr-review LOW "two nits"      # CLEAN/LOW/MEDIUM/HIGH/BLOCKER
python3 scripts/pr_format.py record-review <n> <sha> simplify SUGGESTIONS "8 of 10 applied"   # or CLEAN
python3 scripts/pr_format.py record-review <n> <sha> standards CLEAN                # or VIOLATIONS
bash scripts/pr_merge.sh <n>                  # refuses unless the gate passes; merge commit, never squash
```

A new commit makes the recorded verdicts and the acceptance line stale: the gate compares them to
the PR head. So finish the fixes first, then run acceptance and the three skills on the final
commit. Record only a verdict a skill actually returned. Simplifier suggestions are applied or
declined with one line of reason in the PR body.
