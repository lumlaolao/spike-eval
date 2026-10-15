# Harness architecture: every part, and why it is built that way

**Keep it true:** a change that alters how the harness works updates this document in the same PR
(`docs/STANDARDS.md` section 7).

This is the map of the harness that lets a language model play full games of Magic: The Gathering inside
the Forge rules engine, and of the benchmark built on it. Each section says what the part does and the
reason for the choice. Deeper records are linked; this document is the overview.

**The principle everything follows (owner, 2026-10-15): only the model limits play quality.** The harness
shows the model the game faithfully, asks it every decision, carries out what it says, and decides
nothing for it. A change that lets the harness decide, hide, cut or coach is a defect, unless it is
measured and opted into as a separate condition.

## 1. The shape of it

```
 Forge rules engine (pinned commit, unmodified)
   │  asks the seat's PlayerController for every decision
   ▼
 PlayerControllerLLM  (forge-overlay/.../spike/LlmSpikeGui.java)
   │  1. builds what the seat may see        SeatObservation (faithful profile)
   │  2. builds the ask                      system prompt (fixed) + archive + state + decision
   │  3. sends it, with the tools            OpenRouterBridge (function calling, prompt cache)
   │  4. reads the answer                    AnswerTool: one `answer` call, validated
   │  5. carries it out                      returns the engine's own legal object
   ▼
 Engine applies it; illegal moves cannot happen: every option shown was produced by the engine
```

Around the seat: the eval layer (`eval/`) launches games, pins their conditions, prices them, scores
them and refuses to pool what is not comparable.

## 2. The engine

**Forge, pinned (`FORGE_COMMIT`, `docs/harness/ENGINE-PIN.md`), never edited.**
- *Why Forge:* it implements the full comprehensive rules for a large card pool, with a headless mode.
  The engine decides legality, so the model can never make an illegal move, and the harness never has to
  judge one.
- *Why pinned:* results are only comparable within one engine version. The benchmark version
  (`eval/benchmark_version.py`) includes the commit, and every game line records it.
- *Why unmodified:* a forked rules engine could drift from the one it claims to be. Everything we add
  lives in `forge-overlay/` and is copied in by `install.sh`. The overlay is the source of truth; files in
  the Forge checkout get overwritten.

## 3. The seat: `PlayerControllerLLM`

**A subclass of Forge's `PlayerControllerAi` that overrides each decision method.**
- *Why subclass the AI controller:* the engine has paths that only work for a non-human controller
  (headless play, payment, triggers). Subclassing keeps those paths and replaces the decisions.
- *Why that is dangerous, and how it is policed:* every method not overridden is decided by Forge's AI.
  `docs/harness/controller-ownership.tsv` lists every controller method, who decides it (model,
  deterministic, Forge AI) and how often the pool reaches it. The acceptance run prints the count of AI
  decisions still reachable in Legacy play, and `[TALLY] ... ai=0` in each game log is the invariant.
  Since harness-epoch-14 no reachable decision falls back to it (section 15).

**What the harness may decide without asking (owner rule):**
- a choice with exactly one legal option (forced);
- the priority menu is PASS only (nothing castable, activatable or playable), or this exact spot (turn,
  phase, stack, board, menu) was already passed.

Anything new asks again: an opponent's spell or ability, every attack and block, a trigger, a new option.
- *Why:* skipping a window the model could act in, keeping a hand for it, or picking its play is a
  heuristic standing in for the model. The old shortcuts (mulligan heuristic, skip heuristics, a cheap
  fast tier) remain only as opt-in measurement switches (`MULLIGAN_HEURISTIC=1`, `SKIP_HEURISTICS=1`,
  `ONE_TIER=0`).

**When the model gives no usable answer:** a fixed legal option (the first, or decline), recorded on the
decision row with why. Never Forge's AI.
- *Why fixed and recorded:* a fallback that chooses well would hide a broken ask; a fixed one keeps the
  defect visible in the numbers (`eval/defect_analyzer.py` counts them).

## 4. What the model sees: the observation

**`SeatObservation` (faithful profile, `CONTEXT_PROFILE=v4-faithful`).**
- The state as the seat's player could know it:
  - its own hand;
  - every public zone;
  - the opponent's hand count, plus cards revealed to it;
  - what was cast and happened since its last decision;
  - mana available, and what each hand card needs.
- Objects are named by handles (`@12`), never by engine ids.
- *Why faithful:* hidden information must stay hidden, or the benchmark measures something else.
  `SeatObservationTest` renders two games that differ only in hidden state and requires identical
  observations.
- *Why handles:* a stable, short name for "this copy", usable in tools and answers. Engine ids leak
  internals and change between games.
- *Why uncut:* the harness no longer truncates event text, card text, reveals or history
  (`docs/harness/MODEL-LIMITS.md`). Cuts were a harness limit on the model.

**How to read the state is explained once, in the system prompt (`SeatObservation.GUIDE`).**
- *Why:* the per-message legend cost about 200 tokens a call outside the cache. It also disagreed with an
  older one in the system prompt (`docs/harness/PROMPT-AUDIT.md`, A1).

## 5. The prompt

**System message, fixed for the whole game** (`seatFraming`):
- the role;
- `<your_deck>`: the deck's plan and list;
- `<how_this_works>`: the engine, the tools, answering by call;
- `<reading_the_state>`: the guide;
- `<legacy_reference>`: what the archetypes in the pool are, as information, not advice.

A puzzle replaces the role with `<puzzle>`: the author's goal and what the solver is told.

**User message per decision:**
- the `<archive>` brief (what older evidence exists and how to search it);
- the state;
- the decision with its numbered options, last.

- *Why this order:* the prompt-cache prefix is exact. Tools, then the system prompt, then the history must
  not change between calls, so nothing per-decision goes early (`docs/harness/PROMPT-CRAFT.md` rules
  17–19). The question goes last because queries at the end of long inputs are answered better (rule 12).
- *Why facts, not coaching:* advice in the prompt is the harness's strategy, and it changes what is being
  measured. Principles, reasoning caps, worked examples and decision-text advice were removed (audit
  C1–C5). The deck's own plan stays: it is information a player has.
- *Why plain language:* capitals and "you MUST" cause over-triggering on current models (rule 10).

## 6. The answer: a tool call, nothing else

**`AnswerTool`: one fixed `answer` function for every decision.** All its fields are optional:
- the answer fields: `option`, `options`, `blocks`, `amounts`, `keep`, `number`, `card`;
- the play extras: `steps` under `PLAN_EXECUTE`, `yield`, `profile`, `lines`, `keep_untapped`;
- `why`, one sentence for the record.

Each call is validated against the decision it answers. An illegal call gets the reason and one more
round in the same conversation; a second illegal call loses the decision.
- *Why a call:* text parsing is where most harness defects came from. Models wrote `CHOICE` and `PICKS`
  together, put the answer mid-prose, or wrote tool markup as text. A schema makes the answer structured,
  and validation makes a wrong one explainable.
- *Why one fixed schema instead of one per decision:* tool definitions open the cache prefix. A schema
  that changed every decision discarded the whole cache on every call. A per-decision enum is kept as a
  measurement arm (`ANSWER_SCHEMA=per-decision`).
- *Why `tool_choice` is per model* (`AnswerTool.toolChoiceFor`, `PROMPT-CRAFT.md` rule 2):
  - **OpenAI models get "required":** measured on Luna, it keeps the reasoning, and a text-only reply
    becomes impossible.
  - **Anthropic models get "auto":** Opus 5.5, Sonnet 5.5 and Fable 5.1 refuse forcing, and a forced call
    skips thinking on the rest.
  - **Never the function by name:** that skips the reasoning on both.
- *Why every optional field's default means nothing:* a model may fill every field with its first or
  empty value. Luna did, and the old first `yield` value took it out of its own windows. `yield` starts
  with "none", and an empty `keep_untapped` is "not said" (answer-tool-v8).
- *Why a yield never passes the seat's own main phase:* a yield passes the windows with nothing of the
  opponent's on the stack until the turn it names. Given at the seat's own upkeep ("keep mana open"), it
  also passed that turn's main phases, so the seat lost its land drop and every sorcery-speed play
  (`runs/luna-v8/g3`, two turns). The main phases with an empty stack always ask (answer-tool-v9).

**No text alternative (answer-tool-v7).**
- A reply that calls nothing is reminded up to twice in the same conversation, then the decision is lost.
  Prose is never parsed as an answer.
- A tool ask that still carries a text answer format is a `[TEXT-FORMAT-LEAK]`.
- `GoldenChecksToolTest` runs every golden check with the seat answering by call, and fails on a leak.
- *Why:* if a prompt offers another way to answer, a model will use it. The text grammar remains only
  for a backend with no function calling (the Claude CLI lane) and for the `ANSWER_TOOL=0` measurement
  arm.

**The tool list never changes within a game:** `answer`, `set_plan`, the lookups, and `simulate` on a
lookahead seat, declared on every ask. A call that does not apply gets an error saying why.
- *Why:* the same cache reason. A tool that comes and goes resets the prefix.

## 7. What the model can look up or do: native tools

**Function definitions in `NativeTools`**, mirrored in `eval/tooluse/tools.json`:

| Tool | What it does |
|---|---|
| `inspect` | the full text and state of a visible object or a card by name |
| `memory_search`, `memory_read` | the evidence archive of this game, which the brief summarises |
| `memory_note` | the seat's own notebook |
| `rules_lookup` | the comprehensive rules, effective date stated (`docs/harness/RULES-TOOL.md`) |
| `simulate` | the lookahead: what an option resolves to, on a copy of the game (`LOOKAHEAD-DESIGN.md`) |
| `report_unclear_decision` | the model reports a confusing ask, which feeds the defect backlog |

Limits: 16 calls an ask, 1,000 a game. These are loop guards, not rationing.
- *Why native calls, not a text command grammar:* across 48 recorded games the text grammar refused 30
  of the model's 43 tool requests for their form. In the tool-use suite, the deciding fact came back
  from a tool in 87% of asks with native calls, against 13% with the grammar
  (`docs/harness/PLAYER-HARNESS.md`, "Tool protocol").
- *Why lookups instead of a bigger prompt:* progressive disclosure, as with skills. The prompt stays
  short and stable; depth (Oracle text, rules, old events) is fetched when wanted.
- *Why the rules come from a tool:* the model should look the rules up, not recall them; a model's
  memory of the comprehensive rules is unreliable at the edges.
- *Why no MCP in the seat:* MCP is a transport for tools. The tools are already native definitions in
  the request, so a server would add latency and give no new capability.

## 8. Memory across decisions

- **The archive (`ContextMemory`):** every event the seat saw, searchable with `memory_search`. Each ask
  starts with a short brief of what is there.
- **The plan:** `set_plan` (goal and next steps), shown back on later asks.
- **The opponent read and the card lines:** fields of the play answer.
- *Why an archive and not a growing transcript:* a full transcript grows without bound and drowns the
  current state. The archive keeps everything, retrievable, while the prompt stays current
  (`docs/harness/MATCH-MODEL.md`).

## 9. Talking to models: bridges

- **`OpenRouterBridge`** (`openrouter:<model>` specs) is the default. It handles:
  - function calling;
  - `cache_control` for Anthropic models (OpenAI and DeepSeek models cache on their own);
  - reasoning passed back unchanged across tool rounds;
  - per-model settings (`ModelCfg`): token caps and timeouts;
  - readable reasoning, kept on the call row where the provider returns it.
- **`ClaudeBridge`** (the Claude CLI lane): text only, kept for the CLI.
- **Egress boundary:** an `openrouter:` model receives the game state. Nothing starts unless
  `OPENROUTER_ALLOW_EGRESS=1` is set by the owner, and no tool sets it.
- **The game model:** GPT-6 Luna (`openrouter:openai/gpt-6-luna`) for every game unless the owner names
  another, at about $0.01–0.02 a game.
- *Why OpenRouter:* one API for many providers, so a model comparison changes one string.
- *Why the cache matters:* nearly all input is the same system prompt, tools and history. Recorded Luna
  asks read 54% of their prompt tokens from the cache on an ask's first round, and 92% on later rounds.

## 10. Costs

- Every call row carries:
  - prompt, completion, cached and cache-write tokens;
  - its price, from `eval/cost_report.py` with `eval/pricing/prices.json`.
- `run_batch.py --budget-usd` is required for paid games: it stops starting games at the budget.
- The owner rule is a small block first, with the expected cost stated before it starts.
- *Why:* small per-game costs add up unseen. 204 games in one day came to about $8.

## 11. Zero-model arms and canaries

| Arm | What it does |
|---|---|
| `floor` | always passes |
| `random` | uniform over the same legal menus |
| `ai` | Forge's AI, the opponent and a baseline |
| `pass`, `first`, `script` | puzzle arms |

- The seed-42 floor and random mirrors are pinned canaries, at A/108/1824 and A/45/368.
- *Why:* a score is meaningless without the trivial baseline it beats (the "88% lesson" in `CLAUDE.md`).
  A canary that cannot move cannot catch a regression, so any change to an automatic path re-pins it
  with evidence.

## 12. The eval layer

- **`run_batch.py`** launches games:
  - it pins every condition explicitly (ambient environment is scrubbed) and records provenance;
  - it resumes;
  - it plays a crashed game once more and sets the failed attempt aside (`--retries`);
  - it enforces the budget.
  - *Why pinned:* a baseline arm that silently inherited a setting would be pooled with a different
    measurement.
- **`aggregate.py`** reports win rates with confidence intervals, kept apart per condition.
  **`defect_analyzer.py`** is the run oracle: `ai_tier_count=0` and no silent drops, or the run fails.
- **Seed governance (`eval/seed_policy.py`):** some seed blocks are reserved for evaluation, and no
  training data may come from them.
- **The three headline scores:**
  - **STR-AI:** win rate against Forge's AI over fixed panels (`eval/SCORING.md`).
  - **STR-HELDOUT:** the same on a held-out seed band.
  - **PUZ:** the puzzle suite (`eval/PUZZLES.md`), where Forge enforces the goal and a solve is judged
    by the engine.
- **Ratings (`rating.py`):** Bradley-Terry with the Forge AI pinned at 1500, a first-player term, a
  deck-matchup term and cluster-bootstrap CIs.
- **Benchmark version:** reports pool only when they share engine, overlay, decks, seeds, panels,
  puzzles and tools.
- *Why so many refusals:* each one stops a number from meaning something other than what it says.

## 13. How a change is checked

1. **`eval/harness_acceptance.sh`:**
   - an isolated compile;
   - every Java and Python test, with a declared-vs-run count;
   - the pinned canaries, with a class-load proof that the new build ran;
   - loopback fake-model games for tools, answers, the faithful profile, lookahead and rules.
   - It never touches the Forge checkout.
2. **Golden checks:** every decision type the seat owns, through the live controller, in both the text
   and the tool protocol.
3. **Optional live-model checks (`eval/test_live_answer_tool.py`, `LIVE_MODEL_TESTS=1`, about a cent):**
   - short real chats with the game model on fixed decisions, using the real `answer` function and the
     per-model `tool_choice`;
   - every reply must be a call with a legal option, a cast must not fill in a yield, forcing must keep the
     reasoning, and a free spell (Force of Will, no land) must be answerable;
   - skipped in every ordinary run, so the suite stays free.
4. **`eval/tool_use_suite.py`:** tool reach, correctness and restraint, each beside its trivial
   baseline, before any game is played.
5. **One game at a time** (the `game-loop` skill): play it, read it decision by decision, fix what the
   harness did wrong, then play the next. A batch is not how the harness is tested.

- *Why this order:* cheapest first. The rule the canary history earned: before trusting a green gate,
  say what would have made it fail.

## 14. Deliberately not done

| Not done | Why |
|---|---|
| Naming the answer function in `tool_choice` | it skips the reasoning (Anthropic and OpenAI alike); "required" is used where it keeps it |
| `strict: true` by default | not every provider supports it; `ANSWER_TOOL_STRICT=1` measures it |
| Agent Skills or MCP inside the seat | the native tools already provide them |
| Forge AI as a fallback | it would decide for the model |
| Coaching in the prompt | it would measure the harness's strategy |
| Cutting text to save tokens | the cache makes repeated text cheap, and a cut hides information |

## 15. Known gaps (see `docs/harness/WORK-PLAN.md`)

- **Forge's AI decides nothing the seat can decide** (priority 0, A, harness-epoch-14):
  - damage order, the mana keep, card naming, modes, targets, cost picks and empty choices no longer
    fall back to it;
  - nor do the twenty choices it used to make outright: Fact or Fiction piles, opening-hand abilities,
    colours, counter kinds, replacement order, edicts, clash, combat taxes and others.
  - Delve is asked the same way: in the real payment, how many cards (any number up to the generic part)
    and which; an affordability test counts every card, unasked.
  - Convoke and improvise, retargeting, redirection and mana from the pool are the seat's too. A convoke
    choice asked from an affordability test is answered as "every helper could pay" without asking; the
    engine does not say which calls are tests, so the seat's real payment marks its own window.
  - An effect played without the stack (an opening-hand ability, a replacement effect, a trigger that
    skips the stack) gets the seat's modes and targets, and its cost is paid with the seat's cost
    decision: the engine routine hard-codes Forge's AI there, so the harness runs a copy of it.
  - A spell or copy with several targets is retargeted as a set: the seat picks up to as many as it had,
    and none, or a set the spell cannot have, keeps the old one. A divided spell keeps its portions, as
    the engine does for a human: exactly as many new targets, each taking an old portion the seat assigns.
  - **No hook in the pinned engine:** the colour an any-colour source makes for a generic shard during
    payment. It matters only to converge, sunburst and spent-colour checks.
  - **Mechanics no deck in the pool reaches:** dice, stickers, contraptions.
- **Mana payment:** the engine maps the chosen sources to the cost. The seat decides which sources stay
  untapped when it matters (`keep_untapped`, or the mana-sources ask).
- **The Claude CLI lane** answers in text, because it has no function calling.
