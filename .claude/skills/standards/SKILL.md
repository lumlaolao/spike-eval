---
name: standards
description: Checks a pull request or staged diff against docs/STANDARDS.md and keeps that file true. REQUIRED on every PR before it is merged (docs/STANDARDS.md section 1), and when asked what the standard is for something. Read-only on code; reports each violated rule by section, and proposes a change to the standards when the same finding recurs or a rule no longer matches the code.
---

# standards

Run this as a fresh subagent whenever your runtime has one (Claude Code: the Agent tool; OpenCode:
a subagent task), with this file as the brief and the diff as the input, so the reviewer is not
the author. Without subagents, or when the owner has asked for none, follow it yourself, start to
finish, before you write another line of the change, and say in the recorded verdict that the
author's session did the review.

You are the memory of how this repository is kept. Two jobs.

## 1. Check a change against the standards

Input: a PR number or "the staged diff". Read `docs/STANDARDS.md` and `CLAUDE.md` in full, then the
diff. Run the mechanical checks first and quote their output:

```bash
python3 scripts/pr_format.py check-branch          # commit subjects and bodies on this branch
python3 scripts/pr_format.py check-pr <n>          # title and body sections (when there is a PR)
python3 -m unittest discover -s eval/tests -p 'test_*.py' 2>&1 | tail -3
git diff --cached --name-only | grep -E '^(runs|datasets)/' && echo "generated data is staged"
# every eval script has a test or is on the shrinking list (the merge gate also fails a changed script still
# listed, docs/STANDARDS.md section 1 item 4); every path the agent-facing docs name exists
# (both also run in the suite above; run alone they say which file):
python3 eval/tests/test_untested_list.py && python3 eval/tests/test_doc_paths.py
```

Then go through the standards section by section and, for each rule the change touches, say
whether it holds. The ones a script cannot see:

- a new Java file or edit outside `forge-overlay/`; a Python file in `eval/` with a third-party
  import or without `eval/tests/test_<name>.py`;
- a change to what the model sees without the version that covers it moving, or a moved version
  without the grep in `eval/harness_acceptance.sh` and the docs following;
- a new opt-in flag that `run_batch.py` does not pin, record and refuse to mix;
- a new text command for the model instead of a native tool definition;
- a renamed or re-typed JSONL field;
- a number in a doc with no run directory, no baseline, or pooled conditions;
- a re-pinned canary without a dated reason;
- a new file that duplicates an existing one's job;
- a comment that narrates instead of explaining.

## 2. Keep the standards true

After the check, look at the standards themselves:

- **A rule the code no longer follows anywhere**: propose deleting or rewriting it.
- **A finding that is not covered by any rule but has now appeared twice** (search merged PR
  bodies: `gh pr list --state merged --limit 30 --json number,title,body`): propose the rule, in the
  file's form: the rule, its reason, how it is checked.
- **A rule that could be checked by a script and is not**: say which script should grow the check.

Propose the exact text. Do not edit `docs/STANDARDS.md` yourself; a standards change is a PR.

## Output

```
VERDICT: CLEAN | VIOLATIONS      (record it with `scripts/pr_format.py record-review <n> <sha> standards <verdict>`)
HEAD: <sha reviewed>

MECHANICAL: the output of the checks above, trimmed to failures.

VIOLATIONS:
  STANDARDS §<n> "<rule, quoted>"  file:line
    what the change does instead
    fix: the smallest change that complies

STANDARDS CHANGES PROPOSED:
  add / change / delete §<n>: exact text, and the evidence (which PRs, which files)
```
