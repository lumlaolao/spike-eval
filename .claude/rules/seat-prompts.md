---
paths:
  - "forge-overlay/**/spike/SeatObservation.java"
  - "forge-overlay/**/spike/AnswerTool.java"
  - "forge-overlay/**/spike/NativeTools.java"
  - "forge-overlay/**/spike/PlayerLookahead.java"
  - "forge-overlay/**/spike/GameHistory.java"
  - "eval/tooluse/**"
  - "eval/tool_use_suite.py"
  - "docs/harness/PROMPT-*.md"
---

# Changing what the seat reads or calls

The rules with their sources are in `docs/harness/PROMPT-CRAFT.md`; the audit in `docs/harness/PROMPT-AUDIT.md`.

- Facts, not advice: no coaching, strategy hints or worked examples in the faithful profile. The deck's plan may be
  stated as information.
- Every label says what it is: no bare numbers ("(2 damage)", not "(2)"), no engine ids, no CARDNAME, no Forge
  debug notes. `AskContract` lints every prompt a golden check captures; `match_review.py` flags LEAKs in games.
- Every block names its source (engine, Forge log, card database, harness record, the seat's own notes).
- The system prompt's state guide (`SeatObservation.GUIDE`) describes every section and tag the state can show; when a
  render or a hand tag changes, the guide changes with it.
- The tool list never changes within a game (it opens the prompt-cache prefix): the `answer` schema is fixed, and
  `set_plan` and `simulate` are declared on every ask.
- Tool definitions live in `NativeTools.java` / `AnswerTool.java` and, identically, in `eval/tooluse/tools.json` and
  `answer_tools.json`; the Python mirror of each instruction text is checked against the Java literal.
- A tool change is measured with `python3 eval/tool_use_suite.py` before a game is played.
- Versions are not put in the prompt (no value to the model; they would break the cache). Bump the stamp instead
  (`eval/versions.py`).
