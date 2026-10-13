# Agent instructions

The instructions for every coding agent working in this repository are in [CLAUDE.md](CLAUDE.md).
Read it first; it is the single source of truth and this file only points at it.

The standards most often relevant:

- **The seat's tools are native tool calls.** Anything the model can look up or do beyond answering
  is a native function definition (`NativeTools.java`, mirrored in `eval/tooluse/tools.json`), never
  a text command parsed out of the reply. See "Standard: the seat's tools are native tool calls".
- `forge-overlay/` is the source of truth; never edit the Forge checkout.
- Never fall back to Forge's AI for strategy.
- Run `bash eval/harness_acceptance.sh` before claiming a harness change works.
