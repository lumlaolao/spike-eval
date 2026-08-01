---
description: Generate the next HANDOFF-<date>.md from git log, run registries, and open run dirs — grounded in artifacts, not memory
---

Write `HANDOFF-<today>.md` at the repo root, replacing hand-written prose
with claims grounded in artifacts. The 07-29 handoff mis-stated its own
run's progress (said 3/16 decks when disk showed 7/16 complete + 1 partial)
— every number in a handoff must come from a file, not from recollection.

Gather, in order:

1. **State of the machine:** running JVMs (`pgrep -fl LlmSpike`), background
   jobs, anything still writing to `runs/`.
2. **What shipped:** `git log --oneline` since the last HANDOFF-*.md commit;
   summarize per theme, not per commit.
3. **Run state, from disk:** for each `runs/` dir touched since the last
   handoff, count `game-*.jsonl` files per subdir and read `summary.json` /
   `rollout-summary.json` where present. Quote `infra_failure_rate` and
   `by_driver_reason` for any verify run. For PUZ runs, use
   `python3 eval/puz_index.py leaderboard` output verbatim — including its
   significance warning. Never quote a score without its floor.
4. **Spend:** OpenRouter + AWS if any was authorized this session.
5. **Open problems, most important first** — carry forward unresolved items
   from the previous handoff explicitly (resolved / still open / superseded).
6. **Suggested next session, in order** — concrete commands, not themes.

Structure it like HANDOFF-2026-07-29.md (it is the house style: state →
shipped → interrupted → open problems → next). Commit it with a `handoff:`
prefix. Also update the project memory file if a durable fact changed.
