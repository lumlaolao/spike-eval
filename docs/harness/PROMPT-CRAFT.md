# Prompt craft: rules for every prompt and tool the seat sees (2026-10-15)

Each rule below is taken from a vendor guide. The audit ([PROMPT-AUDIT.md](PROMPT-AUDIT.md)) and the rewrite
applied them; a change that breaks one of them needs a measured reason.

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

## 1. Where the harness stands against these rules

The rules were written on 2026-10-15 against the harness of that day (a text answer scraped from prose, no cache
marks, coaching in the system prompt), and the audit ([PROMPT-AUDIT.md](PROMPT-AUDIT.md)) and the rewrite applied
them. What holds now, with its home in [ARCHITECTURE.md](ARCHITECTURE.md):

- **The answer is a tool call** (rules 1-2, 5-7): one fixed `answer` schema validated against the menu, and
  `set_plan` for the plan (section 6). `tool_choice` is "required" for OpenAI models and "auto" elsewhere
  (`AnswerTool.toolChoiceFor`); a reply that calls nothing is reminded `AnswerTool.MAX_NUDGES` times, then lost to a
  fixed legal option.
- **The system prompt is fixed for the game and holds facts only** (rules 9-15, 17): what was removed and where is
  PROMPT-AUDIT.md, "Where the findings were closed".
- **Caching** (rules 17-20): `cache_control` is sent for Anthropic models (`cachesPrompt`); cached and cache-write
  tokens are recorded per call and priced by `eval/cost_report.py`.
- **Reasoning across tool rounds** (rule 8): the served assistant message goes back whole
  (`NativeTools.Turn.messageJson`, pinned by `NativeToolsTest.theBridgeSendsToolsAndParsesAToolCallTurn`).
- **The size that made caching matter** (2026-10-15, Haiku 5.5, Sneak and Show, seed 81, `runs/combo/`): 41 calls
  averaging 10,187 prompt tokens against a 735-character mulligan question; nearly all input was the same preamble,
  tool definitions and history.

## 2. The rules

### Answers and tools

1. **Every decision is answered by a tool call whose schema allows only legal answers.** Use an enum
   of the option numbers (or option ids), and min/max item counts for subsets. Invalid states should
   be unrepresentable [O "use enums… to prevent invalid states"]. With `strict: true` the input is
   guaranteed to match the schema by constrained sampling, where the provider supports it [S]. Then
   the ANSWER CONTRACT section, the re-ask on a malformed line, and the "decline for you" threat can
   all go.
2. **Force the call where forcing keeps the reasoning; otherwise "auto" with the ask naming the tool.**
   Researched and measured 2026-10-15.
   - **The three levels:**
     - "auto" lets the model answer in text;
     - "required" (OpenAI) or "any" (Anthropic) demands some tool call;
     - naming one function demands that function [O, T].
   - **Anthropic** [T]:
     - Opus 5.5, Sonnet 5.5 and Fable 5.1 reject "any" and a named tool with a 400 error.
     - Manual extended thinking rejects them too.
     - On the models that accept forcing, a forced call skips thinking: the response starts with the
       tool call.
     - Anthropic's alternative is "auto" plus strict tools, with the instruction in the user turn.
   - **OpenAI, GPT-6 Luna** (probe of one decision, two calls per setting,
     a one-off probe script not kept in the repository, under $0.01):
     - "auto": called `answer`, 23–24 reasoning tokens;
     - "required": called `answer`, 18–66 reasoning tokens, so the reasoning is kept;
     - the function named: called `answer`, with 0 reasoning tokens, so naming the tool skips the
       reasoning, as on Anthropic.
   - **So the harness sends "required" to OpenAI models and "auto" elsewhere**
     (`AnswerTool.toolChoiceFor`; `ANSWER_TOOL_CHOICE` overrides it for measurement,
     from a direct JVM launch only: every launcher pins the default).
     - With "required", a text-only reply is impossible at the API.
     - With "auto", a reply that calls nothing gets `AnswerTool.MAX_NUDGES` reminders and is then lost.
     - It never names the function.
   - **Constant per model:** changing `tool_choice` between calls invalidates the cached messages [C, T].
     The lookup-only text protocol still sends "none" on its last round; answer asks never change it.
   - **Fill-in defaults** (found by the same probe): Luna fills every optional field with a default,
     including `"yield":"end-of-turn"` (the first enum value) and `"keep_untapped":[]`.
     - In the four recorded Luna games, 175 of 189 answers yielded to end of turn this way, so 24 of
       48 passes gave up the rest of the turn's windows.
     - Rule: an optional field's first enum value and its empty value must mean "nothing"
       (answer-tool-v8 puts "none" first; an empty list is "not said").
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
   Already done: the served assistant message is echoed back whole.

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
    [O]. The legacy profiles' WORKED EXAMPLES taught the text answer format; with an answer tool that job
    is gone. Keep an example only if a measurement shows it helps.
15. **Do not try to cap reasoning in the prompt.** On Haiku 5.5, telling the model to answer
    directly did not stop it thinking; effort is the control [H]. A line like the legacy profiles'
    "at most two short sentences of reasoning" competes with the model's own thinking.
16. **Give the date and the model's knowledge limits only where they matter.** For us that means
    Oracle text (the rules tool), not the date. No change needed beyond tool descriptions.

### Caching (cost and latency)

17. **Order every request as tools, then system, then messages, with nothing volatile early** [C].
    The cache is an exact prefix match. One changed byte in the system prompt invalidates
    everything after it. So the system prompt must not hold the turn, the life totals, the plan
    statement or a tool budget count. The system prompt holds the deck plan, which is fixed per game;
    anything per-decision belongs in the user turn.
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
    - Fixed answer tool plus validation keeps the cache; a per-decision enum gives constrained sampling.
      The harness uses the first (section 3).
20. **Record cache use**: cached and cache-write tokens per call on the game line, priced in
    `cost_report.py`.

### Skills and MCP

21. **Skills are progressive disclosure**: a name and description always loaded, the body loaded when
    relevant, linked files loaded deeper [K]. For the seat, the matching pattern is: keep the system
    prompt short, and offer deep reference (card Oracle text, rules, the full snapshot legend) as
    lookups. We already do this with the rules tool. Agent Skills themselves are a Claude Code and
    Agent SDK feature, not something an OpenRouter call can load, so the seat does not use them.
22. **MCP stays out of the seat**, as decided earlier (`PLAYER-HARNESS.md`, "Memory across
    decisions"). MCP is a transport for tools; the seat's tools are already native function
    definitions in the request, and adding a server would add latency without new capability.

## 3. Open questions the rules leave

- **Fixed answer tool or per-decision enum** (rule 19): the harness uses one fixed `answer` tool and validates the
  option against the menu, which keeps the cache; a per-decision enum would give constrained sampling. The two have
  not been measured against each other on the tool-use suite (`python3 eval/tool_use_suite.py`).
- **Examples** (rule 14): none are sent under the faithful profile; whether any example helps has not been measured.
