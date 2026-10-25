# Model limits: what the harness capped, what bound, what changed (2026-10-15)

The owner's goal: **the harness does not limit the model at all.** This is the audit of every place the
harness capped, truncated, budgeted or cut short what the seat can think, see, look up or do; whether
each limit bound in recorded games; and what was done about it. It landed with harness-epoch-7
(observation-faithful-v24, evidence-tools-v3, seat-tools-v7, lookahead-preview-v5, one-chat-v2).

The rule the decisions follow (docs/STANDARDS.md section 8): cost is not a reason to cap thinking, seeing
or looking up. What stays is a **loop guard** set far above anything a game needs (a model that loops
on one tool or one ability must still end), a **condition** that defines an arm (two-tier's fast tier),
or a **transport** retry. Each kept limit says which it is.

## Evidence

Fourteen recorded games, all `v4-faithful`, memory tools and lookahead on, seat A Death & Taxes
against Forge's AI on UR Delver:

| Run | Games | Model asks | Model | Notes |
|---|---|---|---|---|
| `runs/compare/one-tier` | 6 | 456 | deepseek-v4.1-flash | `ONE_TIER=1`, harness-epoch-4 |
| `runs/compare/two-tier` | 6 | 378 | deepseek-v4.1-flash | smart + fast tier, harness-epoch-4 |
| `runs/frontier/opus` | 1 | 74 | claude-opus-5.5 | `ONE_TIER=1`, harness-epoch-5 |
| `runs/frontier/luna` | 1 | 28 | gpt-6-luna | `ONE_TIER=1`, harness-epoch-5 |

"Asks" are decision rows the model answered (`tier` not `auto`), 936 in all. The counts were read from
the decision rows' `prompt` and `options`, the `t:"context_tool"` and `t:"context_call"` rows, and the
game logs (`[TRUNCATED->RETRY...]`, `play(no-budget)`, `play(turn-cap)`, `[BRIDGE ...] no reply`,
`wall-clock timeout`). Line numbers are at `acf6f2e` (the base of this change); `LSG` is
`forge-overlay/.../spike/LlmSpikeGui.java`, `SO` is `SeatObservation.java`, `CM` is `ContextMemory.java`.

## The table

### What the seat sees (every ask)

| Limit | Where | Bound | Decision | New value |
|---|---|---|---|---|
| An event's text cut at 90 characters (`oneLine` on capture), in the history AND in the memory archive built from it | `SO:682` | **219 of 936 asks** (23%; 19-31% per run) showed a cut event: block assignments ("ForgeAI assigned Murktide Regent @109 to block Thalia @25. ForgeAI..."), fetch searches, Force of Negation's target | removed | whole text (`flat`) |
| Events since the last decision, verbatim, at most 40 | `LSG:481` (`v4-faithful` recentEventsMax) | 11 asks (the gap spilled into the digest) | raised | 10000 |
| Earlier turns: a full digest for the last 3 turns only, older turns high-signal events only | `LSG:481` (digestTurns 3) | 290 asks showed a turn compacted to "(no high-signal events)"; every older turn lost its zone changes, combat and resolutions | raised | every turn gets a full digest (10000) |
| A digest line keeps 12 distinct entries, then "...(N more)" | `LSG:481` (digestPerTurn 12) | **1,431 cut digest lines** across the 936 asks | raised | 10000 |
| Reveals: latest 6 shown | `SO:902` | **452 of 936 asks** (48%); up to 54 reveals in a game | removed | every reveal, oldest first |
| A stack entry's text cut at 160 characters | `SO:1055` | 84 asks (Stoneforge Mystic's trigger, Ponder, Flickerwisp) | removed | whole |
| An opponent card's rules text cut at 320 characters | `SO:171` | 30 cut lines (Ragavan lost its Dash clause); 19 cards of the deck pool are longer than 320 | removed | whole |
| Opponent cards listed: latest 30 | `SO:170` | 0 (at most 12 seen) | removed (a long game against a varied deck reaches it) | every card seen |
| The seat's own deck text cut at 400 characters a card | `LSG:7613` | 0 in these games (Death & Taxes has no such card); 7 of the 332 cards in `eval/decks` (Animate Dead, Tamiyo, Ajani, Minsc & Boo, Ojer Kaslem, Tezzeret, Genesis of the Daleks) | removed | whole |
| Known-in-hidden-zones facts: 12, oldest dropped silently | `SO:258` | 0 (never populated in these games) | removed (bounded by the opponent's cards) | uncapped |
| An object's zone trail (INSPECT `history:`): 8 moves | `SO:261` | 0 (at most 4) | removed | uncapped |
| The model's own reason carried into the next step of a play ("What you wrote when you chose it"), cut at 800 | `LSG:4960` | 14 carried intents | removed | whole |
| An optional trigger's text cut at 320 (faithful confirm-trigger ask) | `LSG:3933` | 0 of 64 | removed | whole |
| Engine text in asks cut at 90 (`oneLine`): a card-name ask's message and "must match", colour asks, a sacrifice cost's spell and message, a prevent-effect cost, optional-cost labels, mode labels, a reveal's prefix | `LSG:1191, 3249, 3298, 4135, 4157, 4231, 4313-4314, 4353, 4702, 4707` | not measurable from the rows (the cut text is gone); modes and optional costs are option labels the seat must choose between | removed under the faithful profile (`said()`); the legacy profiles keep their bytes | whole |
| Trigger-order labels used the legacy `saLabel` (160-character cut, engine card ids such as "Palace Jailer (8)") under the faithful profile | `LSG:3675` | 0 asked (all 37 trigger-order rows were forced) | fixed | the faithful label (`labelOf`) |
| `LINES:` ask says "up to 3" | `LSG:814` | not countable | removed | "as many as you see" |

### What the seat may look up (tools)

| Limit | Where | Bound | Decision | New value |
|---|---|---|---|---|
| Tool calls per ask: 2 | `CM:18` | **23 of the 83 asks that used a tool** reached it (6 of 27 one-tier, 17 of 56 two-tier); one game made 25 inspects in 20 asks | raised | 16 |
| Tool calls per game: 64 | `CM:19` | 0 (most in one game: 30) | raised with the per-ask budget, or 16 an ask would reach it in 4 asks | 1000 |
| One preview (`simulate`) per decision | `CM:246`, text path `LSG:2744` | 1 refusal here ("ERROR: one preview per decision."); `GAME-LOOP-LOG.md` records it refusing a corrected retry | removed | as many as the ask has calls (16) |
| A previewed line: at most 3 plays (schema `maxItems: 2`) | `PlayerLookahead.java:166`, `NativeTools.java:136` | a 3-play line was asked once; longer lines could not be expressed | removed | any length; each option at most once |
| `memory_read` page: 2000 characters | `CM:20` | 2 reads needed a next page (an observation is up to 17,608 characters) | raised | 20000 (one page holds any recorded observation) |
| `memory_search` page: 10 results | `CM:335` | 8 searches had more pages (up to 41 matches) | raised | 50 |
| A note: 500 characters; a command line: 1000 | `CM:303, 272`; `NativeTools.java:173` | 0 notes written | raised (a plan worth noting can be longer) | 2000; 2100 |
| Notebook shown back: latest 4 notes | `CM:125` | 0 (no seat wrote a note) | removed | every note |
| Reports per game: 10, one per decision | `CM:27` | 0 reports | **kept**: a complaint channel to the maintainers, not a decision input; 10 a game is far above any use seen | 10 |
| Historical reveals in the memory brief: latest 4, 240-character snippets | `CM:117, 359` | n/a | **kept**: duplicates the observation's REVEALS block, which now lists every reveal whole; snippets point at `memory_read` | 4 / 240 |
| RULES tool: 1200-character pages, 150-character snippets, 12 cross-references | `RulesTool.java:43-45` | the tool was off in every game | **kept**: paginated (the model pages with `next=`), and unmeasured | unchanged |

### How long and how often the seat may think or act

| Limit | Where | Bound | Decision | New value |
|---|---|---|---|---|
| Smart-tier `max_tokens` (the thinking budget) | `ModelCfg.buildTable` | at 16000: 7 of 865 smart calls truncated, all deepseek hard main phases | raised to 32000 by `fix/thinking-cap` (`acf6f2e`); **kept** at 32000 here: providers reject a `max_tokens` above their own output limit, which would turn every call into an error | 32000 |
| Per-call timeout | `ModelCfg.buildTable` | at 150s: 2 calls timed out | 300s by `acf6f2e`; kept (it couples to the post-cap grace and run_batch.py's `GRACE_S = 1000`) | 300s |
| Truncation retry: a reasoning row retries at 4096 tokens with reasoning off | the OpenRouter bridge's truncation retry (`LSG`) | fired 7 times (smart) | **kept for the smart tier**: it fires only after the model spent the whole 32000-token budget, and the alternative is the deterministic fallback, not more thought. **Fixed for the fast tier**: its reasoning is already off, so the retry asked again at the same 1024 tokens; it now doubles the budget | smart unchanged; fast 2x |
| Fast tier: 1024 tokens, 45s, reasoning `none` | `LSG:6601, 6616` | 2 of 170 fast calls truncated | tokens and time raised; reasoning **kept** `none`: that is what the two-tier arm is (a condition, `one_tier` on the game line). `ONE_TIER=1` uses the smart row everywhere and is the no-limit arm the game-loop skill already plays | 8192 tokens, 120s |
| `SMART_BUDGET` / `FAST_BUDGET` calls a game (`play(no-budget)` once spent) | `LSG:607-608` | 0 llm seats (most: 137 smart calls in a 17-turn game; every `no-budget` line in `runs/` is a floor seat) | raised to loop guards; a 40-turn one-tier game reaches 320 | 10000 / 10000 |
| Play decisions per turn, faithful: 80 (`play(turn-cap)`) | `LSG:606` | 0 (most in one turn: 15) | raised: a storm turn counts both players' windows | 500 (loop guard) |
| Play decisions per turn, legacy profiles: 25 | `LSG:603` | not in use (every game is faithful) | **kept**: the legacy profiles are frozen measurement definitions | 25 |
| `ONE_CHAT_TURNS`: the last 20 decisions kept in the conversation | `ChatSessionBridge.java:30` | one-chat was off in every game; Opus's 74 asks would have dropped 54 | raised | 10000 (whole game). A model whose context window cannot hold a long game can be given `ONE_CHAT_TURNS=N` |
| Claude CLI lane deadline: 90s | `LSG:6719` | the lane was not used | raised to the API lane's timeout | 300s |
| Claude CLI session reset every 40 asks | `LSG:6723` | the lane was not used | **kept**: CLI lane only (no new features there), and the snapshot carries the state across the reset | 40 |
| PROFILE line read only on the ask that requests it (every 5 turns); LINES only on its first 2 asks | `LSG:816, 809`, parse at `LSG:2720-2735` | not countable (a volunteered line left no trace) | the asks keep their cadence; **a PROFILE or LINES line in any reply now replaces that note** (`adoptNotes`), as the legend always claimed | any decision |
| Playable lands offered: first 4 distinct names | `LSG:5629` | 0 of 560 play asks | removed for the faithful seat (a five-land-type hand had no way to play its fifth); legacy and teacher menus keep it so the zero-model canaries do not move | every land |
| Parse re-ask: a reply with no answer line is asked once more, then `LOST` | `LSG:2660` and the decideOne/decideMany loops | 0 LOST (match_review) | **kept**: a loop guard; the re-ask carries the whole decision | 2 attempts |
| Bridge retries: 3 attempts with 0.5s / 2s backoff | `LSG:4733` | 4 second attempts, 0 third | **kept**: transport, and the post-cap grace is three timeouts | 3 |
| Degenerate-repetition collapse | `collapseDegenerateRepetition` | n/a | **kept**: a guard against a reply that repeats itself thousands of times | unchanged |
| `first_failed_reply` cut at 500 in the jsonl | `LSG:4881` | n/a | **kept**: a diagnostic field, never read by the model | 500 |
| Legacy-profile caps (hand list 40, graveyard/exile 30, cast history 25, INTEL 16, RECENT EVENTS 10, `saLabel` 160) | `LSG:472, 1939, 5412` | not in use | **kept**: `v1-compat`..`v3-compact` are frozen measurement definitions (ContextProfileTest); the faithful profile is the one with no limits | unchanged |

### A decision model's seat (`jev:`, `DecisionsBridge`, 2026-10-24)

A decision model answers only typed questions over a closed list of keys (`docs/harness/ARCHITECTURE.md` section 9,
"The Decisions bridge"). Where a decision cannot be put that way it is not sent: the bridge gives no answer, logs
`[JEV-NO-ANSWER]` with the reason, and the seat takes its fixed legal option. "Bound" counts the asks of the three
puzzles of the first Jev run, read from Luna's rows (`runs/puzzles/hard-gen2-luna/<id>/game-*.jsonl`, 116 asks);
the first Jev game (`runs/puzzles/jev-test-1`, 2026-10-24) is not counted here. `DB` is `DecisionsBridge.java`.

| Limit | Where | Bound | Decision | Value |
|---|---|---|---|---|
| A number from a range of more than `MAX_LISTED` values is not asked | `DB.compose`, `NUMBER` | 0 of 116 | **kept**: a choice lists every value as a key; `chooseWideKeywordCost` asks for 0 through 2^31-1 (Squad, Multikicker, Replicate), which cannot be listed | 40 values |
| A division with more than `MAX_LISTED` splits is not asked | `DB.compose`, `ALLOC` | 0 of 116 | **kept**: the same reason; a split is one listed key | 40 splits |
| A repeatable mode picked up to more than `MAX_LISTED - 1` times is not asked | `DB.compose`, `MANY_REPEAT` | 0 of 116 | **kept**: each mode's count is a choice over 0 through the maximum | 39 picks |
| Naming a card is not asked | `DB.compose`, `CARD` | 0 of 116 | **kept**: there is no closed list of names to put as keys | never asked |
| A decision whose options have no labels is not asked | `DB.compose` (`ONE`, `MANY`, `MANY_REPEAT`, `ALLOC`) | 0 of 116 | **kept**: a key with no text would be a blind question; every current site labels its options | never asked |
| Several picks are assembled from one yes/no per option, answered independently; an ordered pick list is ordered by probability | `DB.compose` (`MANY`), `DB.topPicks`; blockers that may block several attackers the same way | 6 of 116 (cards-for-effect 1, trigger-order 5) | **kept**: the API answers the questions of one request in parallel and has no "choose a set" primitive; listing every subset as a choice grows as 2^n | the decision's own min and max |
| No lookups, no `simulate`, no plan, no yield, no reason | `LLMBridgeFactory.decisions` (`refuseConditions`), `run_batch.py`, `puzzle_solve.py` | every ask | **kept**, a condition that defines the arm (STANDARDS section 8): see `docs/harness/ARCHITECTURE.md` section 9, "What Jev cannot do" | refused at launch |

### A Claude CLI seat (`claude-cli:`, `ClaudeCliBridge`, 2026-10-24)

The seat answers through Claude Code's structured output (`docs/harness/ARCHITECTURE.md` section 9, "The Claude CLI
answer bridge"); it is a development lane, never a benchmark score. One puzzle has been played on it, at
claude-cli-bridge-v1: `runs/puzzles/opus-cli-smoke` (Opus 5.5, solved, 60 decisions, $2.18 by the CLI's reported
cost). No limit below was reached there.

| Limit | Where | Bound | Decision | Value |
|---|---|---|---|---|
| No lookups, no `simulate`, no kept conversation (ONE_CHAT, TURN_CHAT) | `LLMBridgeFactory.claudeCli` (`refuseAnswerOnly`), `run_batch.py`, `puzzle_solve.py` | every ask | **kept for phase 1**, a condition that defines the arm (STANDARDS section 8); the lookups need an MCP server the CLI can call (phase 2) | refused at launch |
| A plain text ask (no `answer` offered) is not sent | `ClaudeCliBridge.ask` | 0: every decision site offers `answer` to a tool seat | **kept**: there is no schema to answer it with, and text is never parsed | never asked |
| A CLI call that runs past its deadline is killed and gives no answer | `ClaudeCliBridge.run`, `CLAUDE_DEADLINE_MS` or `ModelCfg` | 0 in `runs/puzzles/opus-cli-smoke` | **kept**: the model's `ModelCfg` timeout; the seat's bridge retry and then its fixed legal option follow | `ModelCfg.timeoutMs`, 300 s, on both tiers |

### Wall clock

| Limit | Where | Bound | Decision | New value |
|---|---|---|---|---|
| 45-minute game cap in the game-loop skill (`--cap-minutes 45`; a capped game is scored `invalid`) | `.claude/skills/game-loop/SKILL.md` | 0 of 14 games capped; the longest ran 37.2 minutes (83% of the cap) with 16000-token thinking and 2 tools an ask | raised: with 32000-token thinking at up to 300s a call and 16 tool calls a decision, a 45-minute cap would end games while the model thinks | 120 minutes |
| run_batch.py's derived default cap | `eval/run_batch.py` `derive_cap_minutes` | n/a | kept: 240 minutes for any non-Haiku model, 120 for Haiku | unchanged |
| Post-cap grace / backstop (`GRACE_S = 1000`) | `eval/run_batch.py` `GRACE_S` | n/a | kept: the largest timeout is still 300s (fast is 120s), so `maxDecisionMs()` is unchanged at about 902s | unchanged |

## What it costs

Nothing here caps cost, so prompts grow with the game. The longest recorded prompt was 17,608
characters. With whole events, every turn digested and every reveal listed, a 30-turn game's late
prompts are estimated at 40-70K characters (about 10-18K tokens): roughly 300 events, and reveals that
include whole searched libraries. That is inside every model's context; `run_batch.py --budget-usd`
still stops a batch at its budget.

## Checks

- `ContextMemoryTest.theToolBudgetsAreLoopGuardsNotDecisionLimits`, `everyNoteIsShownBack`
- `ContextProfileTest.theFaithfulHistoryIsWhole`, `theFastTierAnswerIsNotCutShort`,
  `theCallBudgetsAndTheTurnCapAreLoopGuards`
- `SeatObservationTest.nothingTheObservationShowsIsCut`
- `PlanAnytimeTest.aProfileOrLinesLineAtAnyDecisionReplacesThatNote`
- `NativeToolsTest.simulateIsOfferedOnlyWithAHandlerAndRunsForEveryCall` and the budget tests, now
  written against `ContextMemory.TOOLS_PER_ASK`; `LookaheadProbeTest` (no line-length cap)
- `eval/test_tool_use_suite.py` keeps the suite's mirror of the executor in step (16 calls, 20000-character
  pages, 50 results, 2100-character command lines)

Not determined here: this audit has not been repeated since harness-epoch-7, so late-game prompt size, tool calls
an ask above 2, and calls reaching the 32000-token cap in the games since are not counted on this page. The games
since are read in [GAME-LOOP-LOG.md](GAME-LOOP-LOG.md).
