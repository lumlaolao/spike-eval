# Prompt craft: rules for every prompt and tool the seat sees (2026-10-15)

Work plan item 1 (`docs/harness/WORK-PLAN.md`). Each rule below is taken from a vendor guide, and
each is checked against what the harness does now. The audit (item 2) and the rewrite (item 4) apply
these rules; a change that breaks one of them needs a measured reason.

Sources, read 2026-10-15:
- [A] Anthropic, *Prompting best practices*:
  platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- [H] Anthropic, *Prompting Claude Haiku 5.5*: …/prompt-engineering/prompting-claude-haiku-5-5
- [C] Anthropic, *Prompt caching*: platform.claude.com/docs/en/build-with-claude/prompt-caching
- [T] Anthropic, *Define tools*: platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- [S] Anthropic, *Strict tool use*: platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use
- [W] Anthropic Engineering, *Writing tools for agents*:
  anthropic.com/engineering/writing-tools-for-agents
- [X] Anthropic Engineering, *Effective context engineering for AI agents*:
  anthropic.com/engineering/effective-context-engineering-for-ai-agents
- [K] Anthropic Engineering, *Equipping agents for the real world with Agent Skills*
- [O] OpenAI, *Function calling*: developers.openai.com/api/docs/guides/function-calling
- [R] OpenRouter, *Prompt caching* and *Reasoning tokens*: openrouter.ai/docs

## 1. What the harness does now (measured)

- **Request shape.** One system message (the `framing()` preamble: role, deck plan, protocol, tool
  paragraph, PRINCIPLES, FORMAT, and on the smart tier `STATIC_REFERENCE`). Then the chat history
  (ONE_CHAT) and the decision as a user message, with tools (`tool_choice` "auto", or "none" once the
  budget is spent). The request is built in `OpenRouterBridge.send`, `LlmSpikeGui.java`.
- **Size.** Haiku 5.5, Sneak and Show, seed 81 (`runs/combo/`):
  - 41 calls, averaging 10,187 prompt tokens each (smallest 7,326, largest 10,932); 418K prompt
    tokens in total, against 31K completion tokens;
  - the decision text itself is small: the mulligan question is 735 characters.
  - So nearly all input is the same preamble, the same tool definitions and an append-only history,
    which is what caching pays for.
- **No caching.** No `cache_control` is sent, so Anthropic models through OpenRouter pay full input
  price on every call. DeepSeek and OpenAI cache on their own [R], but we never read
  `prompt_tokens_details.cached_tokens`, so `cost_report.py` overstates their cost and we cannot tell
  whether the cache hits.
- **The answer is text.** The decision comes back as a final line (`CHOICE: n`, `PICKS: …`,
  `BLOCKS: …`, `KEEP`) scraped from prose. `STATIC_REFERENCE` spends a whole section (ANSWER
  CONTRACT) teaching that grammar, and threatens "a malformed answer costs you the decision". Only
  lookups are native tool calls.
- **Reasoning is dropped across tool rounds.** We never send `reasoning_details` back with the
  assistant turn that made a tool call. OpenRouter says the reasoning of the tool-request turn
  should return with the tool result, unmodified [R]. Today the model answers after a lookup without
  its own earlier reasoning.
- **Coaching is in the system prompt.** It is mixed with the protocol:
  - PRINCIPLES ("CAST IT — repeatedly passing while holding cantrips is how combo decks lose");
  - FORMAT ("at most two short sentences of reasoning");
  - advice inside decision texts ("Keeping a workable hand almost always beats shrinking it").

## 2. The rules

### Answers and tools

1. **Every decision is answered by a tool call whose schema allows only legal answers.** Use an enum
   of the option numbers (or option ids), and min/max item counts for subsets. Invalid states should
   be unrepresentable [O "use enums… to prevent invalid states"]. With `strict: true` the input is
   guaranteed to match the schema by constrained sampling, where the provider supports it [S]. Then
   the ANSWER CONTRACT section, the re-ask on a malformed line, and the "decline for you" threat can
   all go.
2. **Keep `tool_choice` "auto" and say in the user turn which tool answers.**
   - Forcing a tool skips thinking on every model that accepts it.
   - Opus 5.5 and Sonnet 5.5 reject forced tool use with a 400 error. Use "auto" with strict tools
     instead, plus an instruction in the user message [T].
   - Changing `tool_choice` between calls also invalidates the cached messages [C, T]. So keep it
     the same on every call: do not switch to "none" when the budget is spent. Refuse the extra
     call in the tool result instead (rule 6).
3. **Tool descriptions are the main lever: at least 3–4 sentences each** [T "by far the most important
   factor"]. Say what the tool does, when to use it and when not to, what each parameter means, and
   what it does not return. Write it as if onboarding a new hire [W].
4. **Few tools, no overlap.** Fewer than about 20 at the start of a turn [O]. If an engineer cannot say
   which tool fits, the model cannot either [X]. Group related operations under one tool with an
   action parameter [T].
5. **Do not make the model fill in what the harness knows** [O]. Ids, the seat, the turn and the
   decision kind are filled in by the harness, not asked for.
6. **Tool results and errors are actionable text.** No codes or stack traces. Say what went wrong
   and show a correct call [W]. Return only high-signal fields, with readable names instead of
   internal references [T, W].
7. **Ask for an explanation, not reasoning, in a tool parameter.** A field that asks for "your
   step-by-step reasoning" can draw a `reasoning_extraction` refusal [T]. A short `why` field is
   fine; thinking stays in thinking.
8. **Pass reasoning back unchanged across tool rounds** (`reasoning_details` through OpenRouter) [R].

### The prompt text

9. **Be clear and direct, and explain why.** A reason lets the model generalise; a bare rule in
   capitals does not [A]. Apply the golden rule: a colleague with no context should be able to
   follow the prompt [A].
10. **Normal language, not shouting.** Current models are responsive to the system prompt, and
    "CRITICAL: You MUST" causes over-triggering. Use "Use this tool when…" [A]. Blanket instructions
    ("if in doubt, use X", "search for any present-day fact") made Haiku 5.5 over-use the tool with
    no gain in accuracy [A, H].
11. **Structure with XML tags or headers**, one tag per kind of content: deck plan, rules of the
    interface, game state, the question [A, X]. Use the same tag names everywhere.
12. **Long data first, the question last.** Queries at the end improved quality by up to 30% on
    long inputs [A]. For us: the game state above, then the decision and its options at the very end.
13. **The right altitude** [X]: neither hard-coded if-then rules nor vague advice. State facts and
    the goal; leave strategy to the model. This is the test for the coaching cut:
    - information stays (how to read the snapshot, what a tag means, the deck's stated plan);
    - opinion goes (PRINCIPLES, "keeping almost always beats shrinking", "at most two sentences of
      reasoning").
14. **Examples: few, diverse, canonical, in `<example>` tags** [A]. They may hurt reasoning models
    [O]. Our WORKED EXAMPLES teach the text answer format; with an answer tool that job is gone.
    Keep an example only if a measurement shows it helps.
15. **Do not try to cap reasoning in the prompt.** On Haiku 5.5, telling the model to answer
    directly did not stop it thinking; effort is the control [H]. Our "at most two short sentences
    of reasoning" competes with the model's own thinking. Remove it, and set effort per tier
    instead.
16. **Give the date and the model's knowledge limits only where they matter.** For us that means
    Oracle text (the rules tool), not the date. No change needed beyond tool descriptions.

### Caching (cost and latency)

17. **Order every request as tools, then system, then messages, with nothing volatile early** [C].
    The cache is an exact prefix match. One changed byte in the system prompt invalidates
    everything after it. So the system prompt must not hold the turn, the life totals, the plan
    statement or a tool budget count. Today `framing()` holds the deck plan, which is fixed per
    game, so it is fine. Anything else per-decision belongs in the user turn.
18. **Mark breakpoints** for Anthropic models through OpenRouter [R, C]:
    - top-level `"cache_control":{"type":"ephemeral"}` (automatic caching, which moves with the
      conversation); or
    - an explicit breakpoint on the system message.
    - Haiku 5.5 caches from 512 tokens [C]; our preamble is far above that.
    - Reads cost 0.1× input, and 0.05× on Opus 5.5 and Sonnet 5.5. A write costs 1.25×, with a
      5-minute TTL refreshed on each hit [C]. Our calls come seconds apart, so the 5-minute TTL is
      enough.
19. **Keep the cache-invalidating settings fixed within a game**: tool definitions, `tool_choice`
    (rule 2), effort and thinking settings [C, H].
    - The fast tier is a separate model call with its own prefix, so it does not break the smart
      tier's cache. But a change of effort on the same conversation does.
    - Tool definitions must not change mid-game. A per-decision answer schema therefore cannot be a
      changing tool definition: put the legal options in an enum built for that decision only if it
      sits after the breakpoint, or keep one fixed answer tool and validate the option against the
      menu in the tool result.
    - This trade-off is the main design question for item 3. Fixed answer tool plus validation keeps
      the cache; a per-decision enum gives constrained sampling. Measure both.
20. **Record cache use**: `cache_read` and `cache_write` tokens per call in the jsonl (additive
    fields), and price them in `cost_report.py`.

### Skills and MCP

21. **Skills are progressive disclosure**: a name and description always loaded, the body loaded when
    relevant, linked files loaded deeper [K]. For the seat, the matching pattern is: keep the system
    prompt short, and offer deep reference (card Oracle text, rules, the full snapshot legend) as
    lookups. We already do this with the rules tool. Agent Skills themselves are a Claude Code and
    Agent SDK feature, not something an OpenRouter call can load, so the seat does not use them.
22. **MCP stays out of the seat**, as decided earlier (`PLAYER-HARNESS.md`, "Memory across
    decisions"). MCP is a transport for tools; the seat's tools are already native function
    definitions in the request, and adding a server would add latency without new capability.

## 3. What follows for the work plan

- **Item 3 (answer tool):** rules 1, 2, 5, 6, 7 and 19. Open question: a fixed tool plus validation,
  or a per-decision enum. Decide by measuring cache hit rate and the illegal-answer rate on the
  tool-use suite.
- **Item 4 (prompt rewrite):** rules 9–15 and 17.
  - The system prompt holds only stable text: role, deck plan, how the interface works, how to read
    the state.
  - The user turn holds the state, in tags, with the question last.
  - Drop PRINCIPLES, FORMAT and the ANSWER CONTRACT; move the snapshot legend into the rules tool,
    or keep it if it measures better.
- **New, small, do first:**
  - send `cache_control` for `anthropic/` models (rule 18);
  - record `cached_tokens` (rule 20);
  - keep `tool_choice` fixed (rule 2);
  - pass `reasoning_details` back across tool rounds (rule 8).
  - Each is a contained change in `OpenRouterBridge`, measurable on one cheap Haiku 5.5 game.
