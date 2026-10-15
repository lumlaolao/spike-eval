# How the seat's actions and tool calls are read, and whether it is right

Date: 2026-10-15. Scope: `NativeTools.java`, `ContextMemory.java` (`ask`, `askNative`), `LlmSpikeGui.java`
(`OpenRouterBridge`, `decideOne`/`decideMany`, `parseTagNumber`, `lastSegmentAny`, blockers, play),
`ChatSessionBridge.java`, `eval/tool_audit.py`, `eval/match_review.py`, and the recorded traffic in
`runs/compare/{one-tier,two-tier}` (12 games, deepseek-v4.1-flash, harness-epoch-4) and
`runs/frontier/{opus,luna}` (2 games, epoch-5).

Labels: **[code]** read from the source, **[data]** counted from the jsonl, **[src]** from a cited
external source, **[inf]** my inference.

## 1. How it works today

### Lookups: native function calls (correct in shape)

[code] On `openrouter:` seats with `MEMORY_TOOLS=1`, `askNative` sends one `user` message (decision +
evidence brief) after a system preamble, plus `tools` (inspect, memory_search/read/note, rules_lookup,
simulate, report_unclear_decision) and `tool_choice: "auto"`. Each `tool_calls` turn is echoed back
verbatim (`msg.toString()`, so any `reasoning`/`reasoning_details` fields go back unmodified), each
call is mapped to the canonical text command, run by `ContextMemory.execute`, and answered with a
`role:"tool"` message carrying `tool_call_id`. Budget: 2 calls per ask, 64 per game, at most 3 model
rounds plus one "tools are spent, answer now" round. On the last round the tools are still sent, with
`tool_choice: "none"`. A call made anyway is refused; call markup written into text (deepseek's
`<｜DSML｜ invoke ...>`) is refused and the model is told once to answer.

### The decision itself: a text tag parsed out of free prose

[code] The answer is never structured. The preamble tells the model to write "at most two short
sentences of reasoning, then a final line in EXACTLY the format I request", and the harness parses:

- `CHOICE: n` with `parseTagNumber`: markdown stripped, colon optional for CHOICE/PICKS/BLOCKS,
  `ANSWER:` accepted as a synonym, up to 6 punctuation characters and the word "option" allowed
  before the number, the last matching line wins.
- `PICKS: 1,3` / `PICKS: NONE`: `lastSegment` then every `\d+` in the segment; out-of-range numbers
  dropped silently, deduped, truncated to `max`.
- `BLOCKS: blocker->attacker, ...`: pairs matched to cards by **name** (`matchCard`), not by number.
- `KEEP`/`MULLIGAN`, `CARD: <name>`, and side channels parsed from the same text: `PLAN:` (replaces
  the seat's plan on any smart reply), `STEPS:` (auto-executed cast list), `YIELD:`, `PROFILE:`, and on
  the text path `SIMULATE`.
- On no parse, one re-ask that resends the whole prompt plus "(Your previous reply had no parseable
  CHOICE line...)"; then the caller's deterministic default (`play(unparsed)->PASS`,
  `blockers(unparsed)->none`, ...), which `match_review.py` counts as **LOST** via the fallback regex
  `bridge-down|unparsed|no-reply|budget-exhausted|\(err\)|parse`, plus a check that the last
  `CHOICE:\s*(\d+)` in the reply equals the recorded pick.

So the repository has a split protocol: lookups are structured, the action is regex-scraped prose.
That is exactly the pattern CLAUDE.md banned for tools ("do not parse a tool request out of prose")
and it still applies to the one output that matters most.

### What the recorded games show [data]

| arm | final answer rounds | rounds with no answer tag (first try) | parse re-asks (`first_failed_reply`) | LOST (match_review) | truncations (`TRUNCATED->RETRY`) |
|---|---|---|---|---|---|
| one-tier (6 games, smart only) | 456 | 0 | 0 | 1 | 5 |
| two-tier (6 games) | 390 | 12 (10 fast, 2 smart; 1 was DSML call markup) | 12 | 0 | 4 |
| Opus 5.5 (1 game) | 74 | 0 | 0 | 0 | 0 |
| GPT-6 Luna (1 game) | 28 | 0 | 0 | 0 | 0 |

- Final recorded replies: 931 of 936 (99.5%) end with a clean tag line (optionally followed by
  PLAN/STEPS/YIELD lines). 5 needed lenient parsing or failed: `CHOICE: 2.`, a reply ending in prose
  after the tag, one with no tag, and the degenerate one-tier seed-82 T11 reply whose later
  "Final Answer: ... NCAA: 3" overrode its `CHOICE: 4` (the one LOST; fixed in epoch-5).
- First-attempt failure on the fast tier (reasoning disabled, 1024 tokens): 10/128 = 7.8%; smart
  tier 2/716 = 0.3%. Each failure costs a full resend of the state (prompt tokens doubled for that
  decision) and up to 150 s (`zone-change: 3 attempts, 150510ms wasted`).
- The epoch-4 games recorded an unparsed play as a real PASS with no fallback (fixed in epoch-5 per
  `runs/compare/REPORT.md`), so LOST in epoch-4 data is a lower bound.
- Tool calls: 87 native tool turns, 19 of them with two parallel calls; outcomes over both compare
  arms: served 92, FAILED 8 (lookahead preview crashes), empty 4, bad-args 2, REFUSED 1.

Reading: with deepseek-v4 and frontier models the text-tag protocol mostly works. Its cost is
concentrated in the fast tier and in rare degenerate replies, and the lenient parser now carries
real logic (synonyms, last-wins, distance rules) that has already picked a wrong number once. The
risk grows with weaker models, which a broad benchmark must admit.

## 2. Should the decision be a structured answer?

### What the providers support

- **Anthropic** [src: platform.claude.com define-tools]: `tool_choice` is `auto | any | tool | none`.
  Forced tool use (`any`/`tool`) "skips thinking on every model that accepts forced tool use", is an
  error with manual extended thinking, and **returns 400 on Claude Opus 5.5 and Sonnet 5.5**, where
  the docs recommend "`auto` with strict tool use to guarantee schema-valid tool inputs". They also
  warn against a parameter that asks for step-by-step reasoning (it can trigger a
  `reasoning_extraction` refusal): ask for a short explanation, keep reasoning in thinking.
- **OpenAI** [src: developers.openai.com function-calling]: `auto | required | {function} |
  allowed_tools | none`; strict mode needs `additionalProperties:false` and every property listed in
  `required`; for reasoning models, reasoning items returned with tool calls must be passed back.
- **OpenRouter** [src: openrouter.ai tool-calling, structured-outputs, provider-routing,
  reasoning-tokens]: tools must be sent on every request; `parallel_tool_calls` defaults to true;
  `response_format: json_schema` with `strict:true` is enforced only by providers with a native strict
  mode, and support is per endpoint, not per model. With the default `require_parameters:false`,
  providers "will ignore unknown parameters", and tool routing is "best effort". Reasoning details
  should be passed back unmodified with tool results; `max_tokens` covers reasoning plus visible output,
  and a budget spent on reasoning returns `finish_reason:"length"` with empty content.

### What other benchmarks do [src]

- Kaggle Game Arena (google-deepmind/game_arena README): free text, "Models are instructed to emit
  their move in a format e.g. 'Final Answer: X'", parsed by regex or a second LLM, soft-matched to the
  legal moves, with "rethinking" re-prompts on illegal moves.
- BALROG (arXiv 2411.13543): a natural-language action string; on invalid output the agent is told,
  a default action (do-nothing) runs, and the event is logged.
- TextArena (arXiv 2504.11442): string actions through a Gym-style interface.
- mini-swe-agent: bash-only, deliberately "does not use the LM's tool-calling interface" so any model
  works (74% SWE-bench Verified per a secondary listing).

So the current design (text answer, engine-validated menu, one re-ask, deterministic default, logged)
is the mainstream benchmark practice, not an error. The benchmarks that parse text do so to stay
model-agnostic; the cost is that format compliance becomes part of the score.

### Effect on reasoning

- "Let Me Speak Freely?" (Tam et al., EMNLP 2024, arXiv 2408.02442) [src]: stricter format
  constraints hurt reasoning, JSON-mode constrained decoding hurt most; classification sometimes
  improved; parse errors were not the main cause.
- "The Format Tax" (arXiv 2604.03616) [src, via search summary]: most of the loss comes from format
  instructions in the prompt, not the decoder; separating reasoning from formatting (free text first,
  or extended thinking) recovers much of it; recent closed models show little gap.
- JSONSchemaBench (arXiv 2501.10868) [src]: constrained decoding in principle only filters invalid
  tokens; earlier declines partly attributed to poor prompts and schemas.

[inf] The harm in these studies comes from forcing the reasoning *into* the schema (or answering
before reasoning). A selection-only answer tool called *after* free reasoning (thinking or visible
content) is the shape the evidence says is safe, and it must not be forced on Anthropic (forcing
skips thinking). That has to be measured here, not assumed.

### Proposed design [inf]

One more native function, offered on every model decision: `answer` (or per kind: `choose`,
`pick`, `block`), with a per-decision schema:

- single pick: `{"option": {"type":"integer","enum":[1..n]}}`
- subset: `{"options": {"type":"array","items":{"enum":[1..n]},"uniqueItems":true,"minItems":min,"maxItems":max}}`
- blocks: `{"blocks": [{"blocker": enum of blocker numbers, "attacker": enum of attacker numbers}]}`
  (numbers, not names: removes `matchCard` name matching)
- optional `plan` (string, maxLength 300) and `steps` (array of option numbers) so PLAN/STEPS stop
  being scraped from prose; no `reasoning` field.
- `strict: true` where the endpoint supports it; otherwise validate in `NativeTools.command` and answer
  an invalid call with a `tool` error message in the *same* conversation (no full-state resend).

`tool_choice` stays `"auto"`: required/forced is rejected by Opus/Sonnet 5.5 and skips thinking on
the Claude models that accept it. The prompt says "answer by calling `answer`". A text reply with a
valid tag is still accepted that round (the existing parser becomes the fallback, counted as
`answered_via: text`), so a model that ignores the tool loses nothing. Lookups and the answer share
one loop: the answer call ends the ask; on the last round the tool list is narrowed to `answer` (a
`tools` list of one, `tool_choice:"auto"`) instead of sending everything with `"none"`.

Pros: invalid answers impossible under strict, out-of-range numbers caught as a typed error rather
than silently dropped; one protocol for lookups and action; no synonym/last-wins heuristics; blocks
by number; cheaper retries; works the same for weak models that mangle tags. Cons: per-decision
schemas change the tool list each call (prompt-cache prefix lost; measure cost); strict support
varies per OpenRouter endpoint (needs `provider.require_parameters` or validation); a possible
reasoning tax for some models; Claude CLI lane keeps text only, so native and text answers are
different conditions (stamp `answer_protocol` on the game line, never pool, as CLAUDE.md already
requires for tools).

## 3. Other incorrect or questionable things found

1. **No provider pinning for tool parameters** [code + src]. No request sets
   `provider.require_parameters`, so per OpenRouter's docs an endpoint may ignore `tool_choice` (the
   v5 "provider ignoring tool_choice none" defect is this) or `response_format`. Set
   `require_parameters:true` on native requests, or record the served provider per call.
2. **`looksLikeDecisionTag` matches `KEEP` anywhere** [code]: `u.contains("KEEP")` is true for
   "keep mana open", so a length-truncated reply whose reasoning says "keep" skips the truncation retry
   and falls to the controller's full re-ask (or PASS). Restrict KEEP/MULLIGAN to mulligan prompts or
   to a line start.
3. **Retries restart the native ask** [code]. `decideOne`'s re-ask calls `ask` again, which calls
   `askNative` again: the decision is archived a second time (`observe`), the tool exchanges of the
   first attempt are dropped, and a fresh 2-call budget is granted (PLAYER-HARNESS P1 "per-decision
   budgets across retries" is still open). A correction should be one more user message in the same
   message list.
4. **Truncation retry turns reasoning off** [code]: the hardest decisions (those that overran 32000
   tokens) are re-answered with `reasoning:{enabled:false}`. The code says so and logs it; it is still
   a condition change inside one game. Prefer an explicit supported `reasoning.effort` /
   `reasoning.max_tokens` cap so the answer has guaranteed room (OpenRouter docs: Anthropic needs
   `max_tokens` strictly above the reasoning budget).
5. **"Tool calls available this ask: N"** is written only in the first message and never updated; the
   model learns its budget from errors.
6. **Last round still sends all tools with `"none"`** (required by OpenRouter for histories holding
   tool calls), but a provider may ignore `"none"`; narrowing to a single `answer` tool removes the
   conflict entirely.
7. **`first_failed_reply` keeps the first 500 characters** (`cap`), i.e. the reasoning, not the end
   where the missing tag would be. Keep the last 500 for diagnosis.
8. **LOST check is narrower than the parser**: `match_review` compares `CHOICE:\s*(\d+)` while the
   parser accepts colon-less, markdown, `ANSWER:` and "option 3"; PICKS/BLOCKS/attackers are not
   checked at all; picks dropped as out of range are not counted.
9. **`isNonAnswer`** treats any content containing "RATE LIMIT" as bridge-down; harmless today, but it
   inspects model text, not HTTP status.
10. **Text path extracts `content` by string search** (`extractJsonString(body, "\"content\":\"")`)
    while the native path parses JSON; the first `"content":"` in the body wins.
11. **One-chat history** stores assistant answers as plain text and drops tool turns. Fine, but with an
    answer tool the history must store the answer as text (or synthetic call + result) consistently.
12. Correct as is: tools resent every round, `tool_call_id` labelling, raw assistant message echoed
    back (preserves reasoning_details), malformed calls not charged, parallel calls handled, the
    egress gate.

## 4. Recommendation and migration plan

Keep the text-tag protocol as the documented fallback; add the structured answer tool behind a
condition flag; decide by measurement. Expected effect on deepseek/frontier games is small (failure
is already 0.3% smart, 7.8% fast first-try), so the main payoffs are robustness, model breadth and
removing parser heuristics, not win rate.

1. **Fix the cheap defects first** (items 2, 7, 8, 1). Verify: unit tests in `LlmSpikeTest`
   (truncated "keep mana open" reply triggers retry), `match_review` re-run on `runs/compare` gives the
   same LOST count plus newly visible PICKS mismatches, acceptance suite green.
2. **Same-conversation corrections** (item 3): the re-ask appends a user message to the existing
   message list; budget shared across attempts. Verify: a ContextMemoryTest where a first answer is
   unparseable, assert one `observe`, tool count not reset, prompt tokens of the re-ask lower; seed-42
   floor canary unchanged (108 turns).
3. **Add `answer` to `NativeTools` and `eval/tooluse/tools.json`** (`seat-tools-v7`,
   `ANSWER_TOOL=1`, stamped `answer_protocol`). Per-decision enum schema, optional `plan`/`steps`,
   numeric blocks, `strict:true` + `require_parameters:true`, `tool_choice:"auto"`, last round narrowed
   to `answer`, text tag accepted as fallback and counted. Verify: `NativeToolsTest` definitions match;
   loopback fake-model game in `harness_acceptance.sh` exercising call, invalid call, text fallback.
4. **Offline A/B in the tool-use suite**: add arm `native+answer`, plus a no-lookup answer-only
   scenario set (the puzzle suite can supply positions). Metrics next to baselines: reach, correct,
   restraint, `answered_via_tool` rate, invalid-answer rate (baseline: today's 0.3%/7.8%), completion
   tokens. Run deepseek-v4.1-flash (both tiers), Haiku 5.5, and one weak model (llama-3.1-8b) where
   format failures are frequent. Verify: correct on the answer arm not lower than the text arm on the
   same scenarios (sign test per scenario, as the suite already does).
5. **Reasoning check on PUZ**: solve rate and time-to-solve with answer tool vs text, same model,
   same puzzles. This is the teacher-independent test for the "format tax". Stop if solve rate drops
   beyond noise.
6. **Games, small blocks** (per the cost rule, `--budget-usd`): 6 games per arm, deepseek, seeds
   81-83 both seat orders, one tier. Primary metric: LOST + re-asks per 100 model decisions (target 0),
   secondary: tokens/cost per game, `answered_via` text share, win rate (reported with Wilson CI, not
   expected to separate). Verify with `match_review.py`, `tool_audit.py`, `defect_analyzer.py`,
   `cost_report.py`.
7. **Decide the default**: if 4-6 show no loss on correct/PUZ and fewer failures, make the answer tool
   the default on `openrouter:` seats (opt-out flag like `NATIVE_TOOLS=0`), keep text tags for the CLI
   lane, update CLAUDE.md's tool standard to include the answer, and never pool `answer_protocol`
   conditions in reports.

## Sources

- Anthropic, Define tools / forcing tool use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- OpenAI, Function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenRouter, Tool calling: https://openrouter.ai/docs/guides/features/tool-calling
- OpenRouter, Structured outputs: https://openrouter.ai/docs/features/structured-outputs
- OpenRouter, Provider routing: https://openrouter.ai/docs/features/provider-routing
- OpenRouter, Reasoning tokens: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- Tam et al., Let Me Speak Freely?: https://arxiv.org/abs/2408.02442
- The Format Tax: https://arxiv.org/pdf/2604.03616
- JSONSchemaBench: https://arxiv.org/pdf/2501.10868
- Kaggle Game Arena harness: https://github.com/google-deepmind/game_arena
- BALROG: https://arxiv.org/abs/2411.13543
- TextArena: https://arxiv.org/abs/2504.11442
- mini-swe-agent: https://pypi.org/project/mini-swe-agent/
