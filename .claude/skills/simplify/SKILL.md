---
name: simplify
description: Finds what a pull request or staged diff in this repository can lose - dead code, duplication, needless indirection, comments that narrate - without changing behaviour. REQUIRED on every PR before it is merged after pr-review (docs/STANDARDS.md section 1). Read-only; proposes exact edits, never applies them.
---

# simplify

Run this as a fresh subagent whenever your runtime has one (Claude Code: the Agent tool; OpenCode:
a subagent task), with this file as the brief and the diff as the input, so the reviewer is not
the author. Without subagents, or when the owner has asked for none, follow it yourself, start to
finish, before you write another line of the change, and say in the recorded verdict that the
author's session did the review.

You make a change smaller and plainer. You propose; the author applies or declines with a reason.
You never edit, and you never trade away behaviour, a provenance stamp, a test, or evidence in a
comment for fewer lines.

## Input

A PR number or "the staged diff". Read `docs/STANDARDS.md`, the diff, and the whole of each touched
file. Look only at what the change added or made obsolete; a file's older mess is out of scope
unless this change made it dead.

## What to look for

- **Dead after this change**: a flag nothing reads, a helper with no caller, an overload kept "for
  compatibility" with no remaining user, a doc paragraph about behaviour that is gone.
  Prove it with `grep` before proposing the deletion.
- **Said twice**: the same constant, parser, or rule in two places with nothing that keeps them
  equal. Either one goes, or a test pins them together (as `NativeToolsTest` pins the Java tool
  definitions to `eval/tooluse/tools.json`).
- **A second tool for a job a tool already does**: check `eval/` for the existing analyzer before
  accepting a new one.
- **Indirection with one user**: an interface, option or abstraction that exists for a case that
  does not.
- **Comments that narrate** the code or the edit ("now we loop", "changed to use X"). Keep the ones
  that say why, what breaks, or cite a measurement.
- **Names that need the comment beside them** to be understood.
- **Docs**: the same fact stated in three files; keep one and link.

## What you must not propose

- Anything that changes prompt bytes, JSONL fields, canary pins, or a default.
- Removing a test, an assertion, or a failure branch's log line.
- Merging two conditions, flags or runs that are kept apart for provenance.
- A rewrite. If the right simplification is large, describe it in two sentences as a follow-up.

## Output

```
VERDICT: CLEAN | SUGGESTIONS
HEAD: <sha reviewed>

For each suggestion, most valuable first:
  file:line-range  what goes or changes, in one sentence
    why it is safe: the grep / test / reading that shows behaviour is unchanged
    edit: the exact replacement, or "delete lines a-b"
    saves: <lines>, or what becomes clearer

FOLLOW-UPS: larger simplifications not for this PR.
```

At most ten suggestions. If there is nothing worth the author's time, say CLEAN.
