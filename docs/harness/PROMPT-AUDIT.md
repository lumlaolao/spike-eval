# Prompt audit: every text the seat sees, against the prompt-craft rules (2026-10-15)

A dated audit: it records the prompts as they were on 2026-10-15, and the last section says where each finding was
closed. Each finding names the rule it breaks in [PROMPT-CRAFT.md](PROMPT-CRAFT.md). Every
quote below is from the code, `framing()` and `STATIC_REFERENCE` in `LlmSpikeGui.java`, or from a
recorded prompt: the Haiku 5.5 combo games in `runs/combo/`, faithful profile, observation v23.
Findings are numbered so the rewrite could close them one by one.

## What one request holds

| Part | Where | Size (smart tier) |
|---|---|---|
| Tool definitions (7 tools) | `NativeTools.definitions` | 24–130 words each |
| System: role, deck plan, decklist, every card's rules text | `framing()` + `planA/planB` | most of the ~10K tokens |
| System: PROTOCOL, TOOLS paragraph, PRINCIPLES, FORMAT | `framing()` | ~400 tokens |
| System: STATIC_REFERENCE (legend, answer contract, worked examples, glossary) | `framing()` | ~2,000 tokens |
| Chat history (ONE_CHAT) | `ChatSessionBridge` | grows through the game |
| User: state (LEGEND line, both players, stack, events, FACTS) | `SeatObservation` | 3–12K characters |
| User: the decision, its options, the answer format, extra asks | each decision method | 0.3–2K characters |
| User: archive line, brief, tool count | `ContextMemory.askNative` | ~250 characters |

## Findings

### A. Two legends that disagree (rules 9, 13)

**A1. The snapshot legend in the system prompt describes an older observation format.**
`STATIC_REFERENCE` explains fields the faithful v23 snapshot no longer prints, and misses the
fields it does print:
- explained but no longer printed: `YOU [seat] life L | energy E, poison P/10`, `(castable)` tags,
  `OPP CAST THIS GAME`, `RECENT EVENTS`, `INTEL:`;
- printed but not explained: `CAST THIS GAME`, `SINCE YOUR LAST DECISION`, `KNOWN IN HIDDEN
  ZONES`, `FACTS`, `NOT VISIBLE`, `(need 1 more B)`.

Every user message also carries its own LEGEND line, which is correct for v23. The model gets two
legends that disagree, one in the system prompt and one in every message.
- Fix: one legend for the format actually sent, generated from or tested against
  `SeatObservation`, in the system prompt only (it is fixed text, so it caches).
- Remove the per-message LEGEND line. It is about 200 tokens a call, outside the cache.

### B. The answer is a text grammar (rules 1, 2, 14)

**B1. Every decision ends in an exact final line** (`CHOICE:`, `PICKS:`, `BLOCKS:`, `CARD:`,
`KEEP`/`MULLIGAN`, plus `PLAN:`, `LINES:`, `YIELD:` and `PROFILE` lines piggybacked on some asks).
The system prompt spends the ANSWER CONTRACT section on it, the worked examples teach it, and it
threatens: "a malformed answer costs you the decision".
- Seen in games: a model writing `CHOICE` and `PICKS` together (`runs/combo/REPORT.md`).
- Fix: the answer tool (item 3). Then the contract, the threat and the format half of the examples
  all go.

**B2. One play decision carries up to four extra asks** in prose, each with its own line format:
the plan, the opponent profile, `LINES:` (how your cards combine) and `YIELD:`. With native tools,
each is a separate optional field or tool:
- `plan` and `yield` are optional fields of the answer tool;
- `LINES` is a field of the plan.
That removes four parsers.

### C. Coaching mixed into protocol (rules 13, 15)

**C1. PRINCIPLES is opinion**, for example:
- "At instant speed with nothing to gain, PASS";
- "CAST IT — repeatedly passing while holding cantrips is how combo decks lose";
- "Life, cards, and tempo are resources".

It is the harness's strategy, given to every model, and it changes what is being measured. Remove
it (the `NO_COACHING` arm already measures without it).

**C2. FORMAT caps reasoning**: "at most two short sentences of reasoning". That fights the model's
own thinking, and on Haiku 5.5 prompt text does not control thinking; effort does (rule 15). Remove
it. With an answer tool, the visible text is free.

**C3. Coaching inside decision texts:**
- mulligan: "Keeping a workable hand almost always beats shrinking it";
- attackers: "attacking with 0 is legal and sometimes correct — weigh their untapped mana and
  possible blocks", and "attack with everything needed and WIN NOW";
- card naming: "Use everything you've seen this game … to choose the name".

Fix: keep the facts (the lethal count, the card counts); drop the advice.

**C4. The glossary's "General rule for all of them…"** and the per-archetype advice ("do not waste
burn on blockers unless forced") are opinion. The archetype descriptions of the opponents' decks
are information, and legitimately so: a real player knows the format. Keep the descriptions and
drop the advice.

**C5. Worked example C is wrong as written**: "my only instant is a sorcery-speed threat". With
the format half gone (B1), drop the examples. Re-add only examples that a measurement shows help
(rule 14).

### D. Shouting and stacked emphasis (rule 10)

**D1.** Capitals and absolutes everywhere:
- "EXACTLY", "ONLY", "NEVER", "WIN NOW";
- "you are ON THE DRAW";
- "Keep or mulligan? End with EXACTLY one word".

Current models over-trigger on this. Use plain sentences and give the reason once.

### E. Order and caching (rules 12, 17, 18)

**E1. Per-call text before the question.** The user message is already state first, question
last, which is right. But three things come after the question:
- the archive line ("EVIDENCE ARCHIVE: 245 entries; MEMORY tools remaining this game: 64");
- the brief (historical reveals);
- the tool count.

The question should be the last thing the model reads. Move these above the DECISION block.

**E2. Nothing is marked for caching.** Fixed in item 1b (`cache_control`). The system prompt is
already free of per-decision state, as caching needs.

**E3. `tool_choice` flips to "none"** on an ask's last round. That is a measured decision (item 1b).

### F. Wording the model has to decode (rules 3, 9)

**F1. Internal jargon:**
- "CURRENT OBSERVATION: M245";
- "Brief is selective; older evidence remains searchable. Current state takes precedence.";
- "observation observation-faithful-v23" in the header.

A version tag is for the logs, not the model. Say what the model can do: "Older events are in your
archive; search it with `memory_search`."

**F2. Two budgets in one message** ("Tool calls available this ask: 2" and "MEMORY tools
remaining this game: 64"). Say it once, in the tool descriptions and the tool result when one is
spent, not in every message.

**F3. The mana question's "Pick none to let the engine choose"** is the only place the model learns
that auto-payment exists. The auto-tap vs manual design (item 6) should state it in the system
prompt once.

### G. Tools (rules 3, 4, 6)

**G1. Four of seven tool descriptions are under the 3–4 sentence guideline**:
- `rules_lookup`, 24 words;
- `memory_read`, 28 words;
- `memory_search`, 32 words;
- `memory_note`, 32 words.

None says when not to use it, and `memory_note` has no description on its `note` parameter.
`simulate` (130 words) and `report_unclear_decision` (73 words) are the model to follow.

**G2. The TOOLS paragraph in the system prompt repeats the descriptions.** That is fine. But it
ends "After your tool calls, answer in the requested format", which the answer tool makes "answer
by calling `answer`".

### H. Facts that are not quite facts (rules 9, 13)

**H1. "Every attacker can be blocked"** (lookahead OUTLOOK). It uses `CombatUtil.canBeBlocked`,
which asks whether the attacker can be blocked at all, not by the defender's actual creatures. In
the Sneak and Show game, Emrakul (flying) was "blockable" while the only untapped creature could not
block a flyer. Haiku saw through it; a weaker model might not.
- Fix: list, per attacker, which of the defender's untapped creatures could block it.
- Otherwise say "can be blocked by a creature with flying or reach", not "can be blocked".
- Filed as [SeanCThomas0/mtg-forge-llm-agent#166](https://github.com/SeanCThomas0/mtg-forge-llm-agent/issues/166).

## Where the findings were closed

The rewrite landed as the faithful system prompt (harness-epoch-8, `seatFraming`) and the `answer` tool; the current
prompt is described in [ARCHITECTURE.md](ARCHITECTURE.md) sections 5 to 7.

- **A1:** one guide, `SeatObservation.GUIDE`, in the system prompt; the user message carries no legend line.
- **B1, B2:** every decision is an `answer` call; the plan is `set_plan`, and the opponent profile, card lines and
  yield are fields of the play answer. No text parser runs on a tool ask (`[TEXT-PARSE]` fails acceptance).
- **C1 to C5:** the faithful profile sends no PRINCIPLES, no reasoning cap, no worked examples and no glossary advice;
  decision texts state facts only. The legacy profiles keep their bytes.
- **D1, F1, F2:** plain sentences; no version tags in the prompt (they are on the game line).
- **E1:** the archive brief and the state come first, the decision and its options last.
- **E2, E3:** `cache_control` is sent for Anthropic models; `tool_choice` is fixed per model for answer asks.
- **G1, G2:** the lookup tools' descriptions are full paragraphs (`NativeTools.definitions`, mirrored in
  `eval/tooluse/tools.json`).
- **H1:** the attack outlook names, per attacker, which of the defender's creatures could block it (#166).
- **F3** (where the seat learns that auto-payment exists) is not checked on this page.
