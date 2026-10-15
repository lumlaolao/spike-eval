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
- **Every game model is GPT-6 Luna** (`openrouter:openai/gpt-6-luna`, owner 2026-10-15: cheap and good), for both
  tiers and both seats unless the owner names another. No Opus. Paid games always carry `--budget-usd`.
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
3. **An answer tool for every decision and every note.** Built on `feat/answer-tool`:
   - one fixed `answer` schema, validated per decision;
   - corrections in the same conversation;
   - `set_plan` and `simulate` declared on every ask, so the tool list never breaks the cache;
   - documented in `CLAUDE.md` (standard) and `PLAYER-HARNESS.md` ("Answer tool").

   **Merged 2026-10-15** (PR #168, acceptance green). Tool-use suite, Haiku 5.5, 3 trials, smart tier
   (`runs/tooluse/answer-v2-haiku`, about $0.20):
   - text answers: reach 57%, correct 67%;
   - answer tool: reach 60%, correct 67%, 69 of 69 answers through the tool, none lost;
   - restraint and ceiling were 100% in both arms.

   Left: one cheap Luna game read end to end.
4. **Rewrite the prompts from the audit.** First pass done 2026-10-15 (`harness-epoch-8`, observation v24,
   seat-tools-v8), closing these `PROMPT-AUDIT.md` findings:
   - A1: one correct state guide, in the system prompt only;
   - C1, C2, C5: no PRINCIPLES, reasoning cap or worked examples in the faithful system prompt, which
     now has tagged sections (`<your_deck>`, `<how_this_works>`, `<reading_the_state>`,
     `<legacy_reference>`);
   - C3: the advice is gone from the mulligan and attackers texts;
   - C4: the Legacy reference describes the archetypes without advice;
   - E1: the archive block comes first and the decision last;
   - F1, F2: no internal ids, version tags or double budgets in the message;
   - G1: the four short tool descriptions are rewritten;
   - H1 (#166): the attack outlook names who could block each attacker.

   Still open:
   - D1: capitals and "EXACTLY" in individual decision texts (the answer tool already replaces their
     answer lines);
   - C3: advice in the card-naming text;
   - record the model's reasoning (Luna writes no visible text beside its calls, so a row holds only
     the answer): store `message.reasoning` where the provider returns it.
5. **Measure the new prompts and tools.** First block done 2026-10-15 (`runs/prompt-ab/REPORT.md`): 4 Luna
   games per arm, the prompt the only difference.
   - The new prompt is about 30% smaller per call (6.6K against 9.4K tokens).
   - Full re-asks fell from 9 to 2, and nothing was taken or lost in either arm.
   - Wins were 2 of 4 against 0 of 4: not evidence at this size (p = 0.43).

   Next: a larger block, once the remaining prompt work (D1, card naming) is in.
6. **Mana payment: auto-tap vs manual (#111).**
   - Today: before a payment where the untapped sources outnumber what is needed and are not all
     alike, the seat is asked a separate `mana-sources` question (which sources stay untapped);
     Forge's payment taps the rest. So the model already owns the choice when it matters, at the
     cost of an extra call per such spell (3 of 20 decisions in the first Luna game).
   - Design (2026-10-15):
     - the play decision's `answer` takes an optional `keep_untapped` (source handles);
     - when the model gives it while choosing the spell, the follow-up question is skipped;
     - when it does not and there is a real choice, the follow-up is asked as today. The model owns
       the choice either way, and a model that plans ahead saves a round trip.
   - #111 (no card in the pool triggers it): the tap part of a cost is checked after the mana part,
     against what is actually still untapped, instead of against the test payment.
   - **Built 2026-10-15** (answer-tool-v5): `keep_untapped` on the play answer. #111 is still open; no
     card in the pool triggers it.
7. ~~Heuristic skips off by default.~~ **Done 2026-10-15** (`harness-epoch-8`):
   - the mulligan heuristic, the skip heuristics and the fast tier are off unless `MULLIGAN_HEURISTIC=1`,
     `SKIP_HEURISTICS=1` or `ONE_TIER=0` (`run_batch.py --two-tier`);
   - the skips left are the engine's (PASS is the only legal action) and an identical spot already
     passed;
   - `PLAN_EXECUTE` was already off by default.

   Also done: `answer` takes an optional `why` (one sentence for the record), and readable reasoning
   is kept on context_call rows (Luna's is encrypted, so `why` is its only trace); answer-tool-v3.

## Priority 2: land the work already built

Done 2026-10-15:
- 8 `fix/protocol-defects` (#165);
- 9 golden checks (#173);
- 10 scoring axes (#172);
- 11 the issue fixes #117, #118, #113, #109, #110, #108 (one integration PR, harness-epoch-10);
- 12 the model-limits audit (`docs/harness/MODEL-LIMITS.md`), merged with the issue batch. Uncut
  history, reveals and card text; 16 tool calls an ask; any number of previews; budgets are loop
  guards only.

Left:

13. `feat/puzzle-suite` is unfinished. It has `puzzle_score.py`, `puzzle_baselines.py`,
    `puzzle_format.py`, `PuzzleArms.java` and `PuzzleArmsTest`. It still needs `eval/PUZZLES.md`,
    Python tests, a frozen suite and a merge with main. Moved to priority 3.
14. Commit `runs/combo/` (8 Haiku 5.5 combo games and `REPORT.md`).

## Priority 3: later

- Finish the puzzle suite (see 13).

15. The first real scoring-panel run (cost stated first, small block).
16. Compound actions: the model states a sequence once and it is carried out step by step.
17. Choose the headline score.
18. Bradley-Terry deck-matchup term; automatic re-run of crashed games; handling of DSML and text
    call markup from models that write calls as text.
