#!/bin/bash
# The only way a PR is merged (docs/STANDARDS.md section 1): format, three review verdicts for the
# current head, an acceptance run when code changed, no changed eval script still on eval/UNTESTED.txt;
# then a MERGE COMMIT, never a squash.
# REST only (`gh api`): `gh pr view/merge` use GraphQL, which Claude cloud sessions are refused (#118).
#   bash scripts/pr_merge.sh <number>
set -u
N="${1:?usage: pr_merge.sh <pr number>}"
ROOT="$(git rev-parse --show-toplevel)"
HEAD="$(python3 "$ROOT/scripts/pr_format.py" head "$N")" || exit 1
if ! python3 "$ROOT/scripts/pr_format.py" check-pr "$N" --gate; then
  echo "MERGE REFUSED: PR #$N does not pass the gate above." >&2
  exit 1
fi
STATE="$(python3 "$ROOT/scripts/pr_format.py" state "$N")" || exit 1
if [ "$STATE" != "OPEN false" ]; then echo "MERGE REFUSED: PR #$N is '$STATE' (need an open, non-draft PR)." >&2; exit 1; fi
# Merge exactly the commit the gate just checked: GitHub refuses (409) when the head is no longer
# $HEAD, so a push in between cannot ride along unreviewed.
gh api -X PUT "repos/{owner}/{repo}/pulls/$N/merge" -f merge_method=merge -f sha="$HEAD"
