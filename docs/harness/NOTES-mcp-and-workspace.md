# Notes: MCP packaging and a sandboxed workspace (not built)

Status 2026-10-13: **neither is being built.** Both were considered while deciding how the seat
should get its tools. This note records why, and what evidence would reopen each.

## What was decided instead

The seat gets its tools through **native function calling** on the model API the bridge already
talks to (`NativeTools.java`, `eval/tooluse/tools.json`); it is the repository standard (`CLAUDE.md`). That is the protocol the model
was trained to use: in the recorded games the model wrote its own native call syntax into a text
reply (`<｜｜DSML｜｜ invoke name="memory_search">`, `runs/lines/ctl/lookahead`), and the text
grammar refused 30 of the 43 tool requests it made across 48 games because the reply held a
sentence as well as the command.

## MCP server: no

MCP is a transport for offering tools to an agent runtime that someone else owns. Here the
harness owns the loop: Forge calls `PlayerControllerLLM` synchronously, the controller asks the
model, and the engine waits for the answer.

- It would not give the model anything native function calling does not. The same six tools, the
  same executor (`ContextMemory.execute`), one more process and protocol in between.
- It inverts control. An MCP client decides when to call; the engine decides when a decision is
  due. Bridging the two means a game that blocks on an external agent's schedule, which breaks the
  wall-clock caps and the per-ask tool budget the eval depends on.
- "An external agent runtime plays a seat" is a different benchmark. Its budget (turns, tokens,
  its own scratchpad and retries) is set by the runtime, so its scores could not be pooled with or
  compared to the seat's.

Cheap to add later: a tool definition in `tools.json` is already a name, a description and a JSON
Schema, which is what an MCP tool is. Reopen if there is a concrete wish to benchmark agent
runtimes (not models) against each other on this engine.

## Sandboxed workspace (code execution, files that persist): no, and never in the headline scores

The idea: give the seat a scratch machine and let it write its own solvers.

- It changes what is measured. STR-AI and PUZ ask how well a model plays with the game in front of
  it. With a workspace the score is dominated by how much search code the model writes and how long
  it is allowed to run, which is a compute budget, not play skill.
- Persistence is a leak. Files that survive a game carry seeds, decklists and opponents across the
  STR-HELDOUT boundary that `eval/seed_policy.py` exists to enforce.
- It is not reproducible in the way the canaries are: same seed, different generated code,
  different game.
- What the workspace is really wanted for, looking ahead, can be offered as a bounded,
  engine-backed tool. The lookahead arm (`SIMULATE`) was requested 7 times in 16 games; the
  bottleneck measured so far is that the model does not ask, not that it lacks a place to compute.

If it is ever built it is a separately declared track with its own provenance stamp, never pooled
with the three headline scores. Reopen when the tool-use suite shows the model reaches for lookahead
reliably **and** games are still being lost to lines a deeper search would have found.
