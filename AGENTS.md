# Agent instructions

The instructions for every coding agent working in this repository are in [CLAUDE.md](CLAUDE.md);
how to change the code is in [docs/STANDARDS.md](docs/STANDARDS.md). Read both first. This file
only points at them and repeats what is never optional.

## Always

- **Use the required skills** in `.claude/skills/`. Claude Code and OpenCode both load them from
  there by name, so there is one copy and nothing to install (checked on OpenCode 2.0.28:
  `opencode api skill.list --param "location[directory]=$PWD"` lists all eight). Run the three
  review skills as fresh subagents when your runtime has them and the owner has not asked for none:
  - `pr-format` before every commit, PR title/body and merge;
  - `pr-review`, then `simplify`, then `standards` on **every** PR before it is merged;
  - `game-loop` whenever a game is played: one match, read it, fix the harness, then the next;
  - `harness-audit` whenever game logs from many games are reviewed.
  - `defect-hunt` when a game is played to fill the defect backlog: the same reading, but every
    harness defect is filed as its own labelled issue and nothing is fixed;
  - `defect-fix` when one filed harness defect is fixed: claim it, watch a test fail on main, fix in
    `forge-overlay/` only, then the gate.
- **Merge only with** `bash scripts/pr_merge.sh <number>` (format, three recorded verdicts for the
  current head, an acceptance run when code changed; merge commit, never squash).
- **The seat's tools are native tool calls.** Anything the model can look up or do beyond answering
  is a native function definition (`NativeTools.java`, mirrored in `eval/tooluse/tools.json`), never
  a text command parsed out of the reply.
- `forge-overlay/` is the source of truth; never edit the Forge checkout.
- Never fall back to Forge's AI for strategy.
- Run `bash eval/harness_acceptance.sh` before claiming a harness change works.
- Never `git add -A` at the repository root; `runs/` and `datasets/` are generated.
