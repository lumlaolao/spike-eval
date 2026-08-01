---
description: Stage changes and dispatch the verifier agent against git diff --cached before committing (the repo's pre-commit rule)
---

The repo's pre-commit rule (CLAUDE.md "Before committing"): non-trivial
changes get an independent audit BEFORE the commit, not after. The verifier
has caught a BLOCKER (baseline contamination) and multiple HIGHs that
validation runs missed — code that passes tests can still be wrong about
provenance, consent boundaries, or statistical honesty.

1. `git add` the intended files (review `git status` first; never `add -A`
   blindly — a mid-run `verify-keys-work/` sweep once put 10,208 scratch
   files in a docs commit).
2. `git diff --cached --stat` — confirm the diff is what you think it is.
3. Dispatch the `verifier` agent from the claude-obsidian plugin against
   `git diff --cached`, with a short context note: what the change claims to
   do, which validation already ran, and which invariants it touches
   (ai_tier_count=0, silent_drop_total=0, additive-only jsonl schema,
   seed-policy bands, egress consent).
4. Triage findings: BLOCKER/HIGH must be fixed or explicitly justified in
   the commit message before committing. MEDIUM/LOW may be deferred with a
   note.
5. Then commit. Run `make test` once more if the fixes touched code.
