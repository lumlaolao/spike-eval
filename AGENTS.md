# Agent instructions

The instructions for every coding agent working in this repository are in [CLAUDE.md](CLAUDE.md);
how to change the code is in [docs/STANDARDS.md](docs/STANDARDS.md). Read CLAUDE.md first and STANDARDS.md's
sections as they apply (1-2 before a PR). This file only points at them and repeats what is never optional.

## Always

- **Use the required skills** in `.claude/skills/`. Claude Code and OpenCode both load them from
  there by name, so there is one copy and nothing to install (checked on OpenCode 2.0.28:
  `opencode api skill.list --param "location[directory]=$PWD"` lists them all). Each bullet below
  says when its skill is required; run the review skills as fresh subagents when your runtime has
  them and the owner has not asked for none:
  - `pr-format` before every commit, PR title/body and merge;
  - `pr-review`, then `simplify`, then `standards` on **every** PR before it is merged;
  - `game-loop` whenever a game is played: one match, read it, fix the harness, then the next;
  - `harness-audit` whenever game logs are reviewed;
  - `defect-hunt` when a game is played to fill the defect backlog: each harness defect is filed as
    its own labelled issue, and nothing is fixed;
  - `defect-fix` when taking one filed harness defect from the backlog: claim it, prove it on
    `main`, fix in `forge-overlay/`, then the merge gate.
- **Merge only with** `bash scripts/pr_merge.sh <number>` (format, three recorded verdicts for the
  current head, an acceptance run when code changed; merge commit, never squash).
- **The seat's tools are native tool calls.** Anything the model can look up or do beyond answering
  is a native function definition (`NativeTools.java`, mirrored in `eval/tooluse/tools.json`), never
  a text command parsed out of the reply.
- `forge-overlay/` is the source of truth; never edit the Forge checkout.
- **Path-scoped rules** live in `.claude/rules/` (Claude Code loads them by path; other runtimes do not): before
  touching `forge-overlay/` read `forge-overlay.md`, `eval/`, `runs/` or `datasets/` read `eval.md`, the seat's prompts or
  tools read `seat-prompts.md`.
- Never fall back to Forge's AI for strategy.
- **Game model:** GPT-6 Luna for every seat and tier unless the owner names another; never Opus (CLAUDE.md,
  "Owner rules", for the one local-development exception).
- **Egress:** never set `OPENROUTER_ALLOW_EGRESS` yourself; that consent is the user's. Any paid run passes
  `run_batch.py --budget-usd`, and its expected cost is said before it starts.
- Run `FORGE_HOME=/root/forge bash eval/harness_acceptance.sh` (the Forge checkout; `~/code/forge` is the default)
  before claiming a harness change works.
- Never `git add -A` at the repository root; `runs/` and `datasets/` are generated.
