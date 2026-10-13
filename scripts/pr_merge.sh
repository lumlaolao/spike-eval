#!/bin/bash
# The only way a PR is merged (docs/STANDARDS.md section 1): format, three review verdicts for the
# current head, an acceptance run when code changed; then a MERGE COMMIT, never a squash.
#   bash scripts/pr_merge.sh <number>
set -u
N="${1:?usage: pr_merge.sh <pr number>}"
ROOT="$(git rev-parse --show-toplevel)"
HEAD="$(python3 "$ROOT/scripts/pr_format.py" head "$N")" || exit 1
if ! python3 "$ROOT/scripts/pr_format.py" check-pr "$N" --gate; then
  echo "MERGE REFUSED: PR #$N does not pass the gate above." >&2
  exit 1
fi
STATE="$(gh pr view "$N" --json state,isDraft --jq '.state + " " + (.isDraft|tostring)')"
if [ "$STATE" != "OPEN false" ]; then echo "MERGE REFUSED: PR #$N is '$STATE' (need an open, non-draft PR)." >&2; exit 1; fi
# Merge exactly the commit the gate just checked: a push in between must not ride along unreviewed.
gh pr merge "$N" --merge --match-head-commit "$HEAD"
