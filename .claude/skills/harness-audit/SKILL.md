---
name: harness-audit
description: Audit recorded Magic games (any decks, any model) for defects. Separates what the HARNESS did wrong (the model asked and was not heard, was not shown something, was not asked) from what the MODEL did wrong (it had what it needed and chose badly), and turns the model findings into puzzle candidates. Use when asked to review game logs, find harness defects, find play mistakes, or mine puzzles from a run directory.
---

# Harness audit

Input: one or more run directories written by `eval/run_batch.py` (`game-*.jsonl` plus
`game-*.log`). Output: a findings file, a short report, and puzzle candidates.

The one rule: **a finding is a HARNESS defect unless you can show the model had what it needed.**
A model cannot be blamed for a card whose text it was never shown, a decision it was never asked, or
a lookup the harness refused. Check the prompt of the decision row before you write MODEL next to
anything.

## 1. Run the mechanical detectors (never skip, never paraphrase their numbers)

```bash
RUN="runs/<arm> [more arms]"
python3 eval/defect_analyzer.py $RUN          # hard invariants: ai tier = 0, silent drops = 0, fallbacks, truncation
python3 eval/tool_audit.py $RUN               # every tool request: served / REFUSED / empty / bad-args / exhausted
python3 eval/plan_audit.py $RUN               # plays that contradict the seat's own stated plan
python3 eval/line_report.py $RUN              # sacrifices, optional triggers, activations, casts from outside the hand
python3 eval/mine_puzzles.py $RUN --out /tmp/puzzle-candidates.jsonl
```

Read `uptime` first. A run made above 4 load average per core has untrustworthy latency and may
have capped games; say so in the report.

## 2. Classify what the detectors found

| Signal | Class | Why |
|---|---|---|
| `tier=ai` row, `[FALLBACK->AI]` in a log | HARNESS, blocker | the engine's AI made a strategy decision |
| silent drop (log ask with no jsonl row) | HARNESS, blocker | a decision is missing from the record |
| tool request `REFUSED` | HARNESS | the model asked in a form the harness rejected |
| `fallback` set on a smart/fast row (unparseable reply -> first legal option) | HARNESS first | read the reply: a clear answer the parser missed is HARNESS; gibberish is MODEL |
| `finish_reason=length`, empty reply, `attempts > 1` | HARNESS (budget/config) | the model was cut off |
| `[ROUTER] play: FAILED to pay` | HARNESS | an offered option could not be carried out |
| game ended on the wall-clock cap | HARNESS (throughput) | the result is not a game result |
| tool `empty` (`Matches=0`) | HARNESS if the thing was in the archive under other words; else neither | search is literal-then-words |
| plan HOLD-VIOLATION, harness-executed | HARNESS | the harness acted against the model's plan |
| plan HOLD-VIOLATION, model-chosen | MODEL candidate | it said hold, then cast |
| sacrifice / trigger / target the seat chose | MODEL candidate | only after step 3 |

## 3. Read the decisions (this is the part a script cannot do)

For each MODEL candidate, and for every lost game's last three own-turn `play` decisions, open the
row (`file:line` is printed by the detectors) and answer, from the `prompt` field only:

1. **Was the deciding fact on the screen?** The rules text of every card the answer depends on, the
   opponent's visible resources, the option that would have been right. If not: HARNESS
   (`not-shown`), and name the missing fact.
2. **Was the right action in the menu?** If the option list lacks it: HARNESS (`not-offered`).
   Confirm against the engine before concluding (was the mana there? was it legal?) -- a missing
   option is usually a legal reason, not a bug.
3. **Was the model asked at all?** A decision that appears in the log as `auto(...)` or was made by
   a `plan step` was not the model's.
4. Only if 1-3 hold: MODEL. State the better line in one sentence and why, from the prompt.

Ground every claim in a `file:line` and in the engine source when a rule is involved
(`~/code/forge`, pinned commit in `FORGE_COMMIT`). No claim from memory of how a card works: read
the card script under `forge-gui/res/cardsfolder/`.

## 4. Write the findings

`<run>/audit/findings.jsonl`, one object per finding:

```json
{"class": "HARNESS|MODEL", "kind": "refused-tool|not-shown|not-offered|not-asked|parse|budget|plan-contradiction|misplay",
 "src": "game-44-....jsonl:812", "seed": 44, "swap": false, "turn": 6, "decision_type": "play",
 "evidence": "what the prompt showed / the reply said, quoted", "better": "the line, MODEL findings only",
 "puzzle": true}
```

`<run>/audit/REPORT.md`: counts by class and kind first, then the three HARNESS defects that cost
the most decisions, then MODEL findings grouped by pattern (a pattern needs two or more games).
Report every rate next to its trivial baseline and never pool runs whose `context_sha`,
`memory_tools_version` or `native_tools` differ.

## 5. Puzzle candidates

A MODEL finding with `"puzzle": true` is a position where the prompt held everything needed and the
model still chose badly. Add its `src` to the candidates from `mine_puzzles.py`. Candidates are
unverified: they become puzzles only after the fork verifier replays the position
(`eval/EVAL-DESIGN.md` section 3). Do not write an answer key by hand.

A HARNESS finding is never a puzzle. It is a ticket: add it to
`docs/harness/ARCHITECTURE-REVIEW.md` with its evidence, or fix it and add the test that would have
caught it.
