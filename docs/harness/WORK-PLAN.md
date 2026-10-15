# Work plan: what is left (updated 2026-10-15)

The living to-do list for the "the model is the only bottleneck" work. Only open work is listed here;
what is done is in git history, `docs/harness/GAME-LOOP-LOG.md` and the run reports. Update this file
when an item is finished or a new one is agreed, so the next session can pick up from it.

## Owner decisions this list rests on (2026-10-15)

- **Tool calls, not text parsing.** Every decision and every note the seat makes should come back as
  a native tool call with a schema built from the legal options, not a line scraped from prose.
  Text parsing is how most of the harness defects got in.
- **Prompts written for the model to use fully.** Every prompt the seat sees is to be reworked from
  research on prompt craft and Anthropic's own guides (prompt caching, tool use, skills, MCP,
  context engineering).
- **No heuristics deciding for the model.** The harness does not keep hands, skip windows the model
  could act in, or pick plays for it. The deck's strategy may be stated in the prompt as
  information.
- **The only skips allowed** are when the engine says there is nothing to decide, or nothing has
  changed:
  - the menu is PASS only (nothing castable or activatable, no land to play): pass without asking;
  - this exact spot (turn, phase, stack, board, menu) was already passed: pass again;
  - anything new asks again: an opponent's spell or ability, combat (attacks and blocks are always
    asked), a trigger, or a new option.
- **No Opus** for games; Haiku 5.5 for Anthropic models. Paid games always carry `--budget-usd`.
- **No subagents** for this work stream (for now); work happens in the main session.
- **The headline score** (strict vs other) is decided later.

## Priority 1: tool calls and prompts

1. ~~Research: prompt craft and Anthropic's guides.~~ **Done 2026-10-15:**
   `docs/harness/PROMPT-CRAFT.md`, with 22 sourced rules, the current state measured against them,
   and the follow-ups below.
1b. **Small request fixes the research found:**
    - Done 2026-10-15: `cache_control` is sent for `anthropic/` models (`PROMPT_CACHE=0` opts out,
      and `prompt_cache` is on the game line).
    - Done: `cached_tokens` and `cache_write_tokens` are on every row, priced at the provider's
      cache rates in `cost_report.py`.
    - Done: DeepSeek prices are re-checked.
    - Done: an acceptance run with an unknown stage name now fails; before, it ran nothing and
      printed PASS.
    - Reasoning across tool rounds was already correct.
    - **Open:** one cheap Haiku 5.5 game to read the cache hit rate, then decide whether `tool_choice`
      stays "none" on the last round of an ask (it invalidates the cached messages; a constant
      "auto" risks a lost decision).
2. ~~Audit every prompt the seat sees.~~ **Done 2026-10-15:** `docs/harness/PROMPT-AUDIT.md`, with
   19 findings (A1–H1) and the order the rewrite closes them in. The largest:
   - the system prompt's snapshot legend describes an older observation format than the one sent
     (A1);
   - every answer is a text grammar with four extra piggybacked line formats (B1, B2);
   - coaching and a reasoning cap are mixed into the protocol (C1–C5);
   - "Every attacker can be blocked" ignores which blockers the defender actually has (H1, a harness
     defect).
3. **An answer tool for every decision and every note**, on by default (`ANSWER_TOOL=0` opts out).
   The `feat/answer-tool` branch (built by a subagent before the no-subagent rule; its unfinished
   work is saved as a WIP commit) has:
   - a validator for every decision kind;
   - a correction round in the same conversation;
   - `set_plan`;
   - text answers still accepted as a fallback.

   **Design decision (2026-10-15): one fixed `answer` schema, not one per decision.**
   - Tool definitions are the start of the cache prefix, so a schema that changes every decision
     discards the whole cache (tools, the system prompt and the history) on every call.
   - Without `strict`, an enum in the schema is not enforced; it only documents options the decision
     text already lists.
   - So the schema is fixed, with every field optional: `option`, `options`, `blocks`, `amounts`,
     `keep`, `number`, `card`, `steps`, `yield`, `profile`, `lines`. Each call is validated against
     the decision by the existing validator, and an illegal call gets the reason and one more try.
   - The per-decision schema stays as an opt-in arm (`ANSWER_SCHEMA=per-decision`), measured against
     the fixed one: illegal-call rate on the tool-use suite, and cache hit rate and cost in a game.
4. **Rewrite the prompts from the audit:** fixed parts first and the current state last (cache
   order); clearer questions; coaching (the PRINCIPLES list and the static guide) cut down to
   information, with opinion removed.
5. **Measure the new prompts and tools:** `eval/tool_use_suite.py`, the golden checks, then a few
   cheap Haiku 5.5 and deepseek games comparing old and new, with cost stated first.
6. **Mana payment: auto-tap vs manual (#111).** Today Forge picks which lands pay, and the model is
   asked only which sources to keep untapped. Design it properly: auto-pay by default, the model able
   to name the sources when it matters (offered as a tool), and fix the filter-land payment defect
   through that design (`ROOT-INTERFACES.md` R2).
7. **Heuristic skips off by default.** Turn `SKIP_HEURISTICS` and `MULLIGAN_HEURISTIC` off by
   default, keeping only the skips allowed above. The heuristic skips are the quiet-window gate,
   "own spell on the stack", the interaction filter, and the cross-phase `react-sig` dedupe. Decide
   `PLAN_EXECUTE` and the fast tier the same way: both decide or downgrade for the model. Expect more
   model calls per game, and state the cost change.

## Priority 2: land the work already built

Merge one at a time, with `eval/harness_acceptance.sh` green before each, renumbering
`HARNESS_EPOCH` and the observation version where the branches collide.

8. `fix/protocol-defects`: `require_parameters` on tool rounds, the case-sensitive KEEP and MULLIGAN
   match, and the head-and-tail record of a failed reply.
9. `test/golden-checks`: AskContract plus golden checks for 42 decision types, and 11 harness
   defects fixed.
10. `feat/scoring-axes`: Bradley-Terry rating, panels, sanity arms, and the benchmark version.
11. The issue fixes: #117 (`fix/plan-execute-tier-fast-conditions-v2`), #118, #113, #109, #110,
    #108.
12. `feat/no-model-limits`: the limits audit (`docs/harness/MODEL-LIMITS.md`). Review it before
    merging.
13. `feat/puzzle-suite`: `eval/PUZZLES.md` and `puzzle_score.py`. Review it before merging.
14. Commit `runs/combo/` (8 Haiku 5.5 combo games and `REPORT.md`).

## Priority 3: later

15. The first real scoring-panel run (cost stated first, small block).
16. Compound actions: the model states a sequence once and it is carried out step by step.
17. Choose the headline score.
18. Bradley-Terry deck-matchup term; automatic re-run of crashed games; handling of DSML and text
    call markup from models that write calls as text.
