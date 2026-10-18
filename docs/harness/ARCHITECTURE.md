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
 Forge rules engine (pinned commit; three marked patches, ENGINE-PATCHES.md)
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

**Forge, pinned (`FORGE_COMMIT`, `docs/harness/ENGINE-PIN.md`); the checkout is never edited by hand.**
- *Why Forge:* it implements the full comprehensive rules for a large card pool, with a headless mode.
  The engine decides legality, so the model can never make an illegal move, and the harness never has to
  judge one.
- *Why pinned:* results are only comparable within one engine version. The benchmark version
  (`eval/benchmark_version.py`) includes the commit, and every game line records it.
- *Why unmodified except for listed patches:* a forked rules engine could drift from the one it claims to
  be. Everything we add lives in `forge-overlay/` and is copied in by `install.sh`. The overlay is the source of truth; files in
  the Forge checkout get overwritten.
- *Engine patches (owner, 2026-10-15), only where the seat has no hook:* a patched copy of an engine file at
  its own path in the overlay, every change marked `HARNESS PATCH`, the upstream original's sha256 recorded
  in `docs/harness/ENGINE-PATCHES.md` and checked by `eval/test_engine_patches.py`, so a re-pin forces a
  re-diff. Why: "truly let agents have control" (owner) where no `PlayerController` call exists. Two so far:
  the colour an any-colour source makes for a generic shard (`ComputerUtilMana`), and a companion the seat
  declines is no companion rather than a crash (`Player.assignCompanion`).

## 3. The seat: `PlayerControllerLLM`

**A subclass of Forge's `PlayerControllerAi` that overrides each decision method.**
- *Why subclass the AI controller:* the engine has paths that only work for a non-human controller
  (headless play, payment, triggers). Subclassing keeps those paths and replaces the decisions.
- *Why that is dangerous, and how it is policed:* every method not overridden is decided by Forge's AI.
  `docs/harness/controller-ownership.tsv` lists every controller method, who decides it (model,
  deterministic, Forge AI) and how often the pool reaches it. The acceptance run prints the count of AI
  decisions still reachable in Legacy play, and `[TALLY] ... ai=0` in each game log is the invariant.
  Since harness-epoch-14 no reachable decision falls back to it (section 15). Since harness-epoch-40 no decision of
  any mechanic is left to it either: exert, enlist, splice onto Arcane, coin-flip calls, shield counters, dice
  (ignore, reroll, modify, swap; planar dice), stickers, contraptions, attractions, multiple starting hands and assist
  were Forge AI's ("not in eval decks") and are the seat's, each with a golden check. *Why it mattered:* an
  inherited AI method does not count on `[TALLY] ai=`, so a game that reached one showed ai=0.

**A legal action is never hidden (owner, 2026-10-17: "if it's a legal action we don't disallow it, that's a model
issue, not harness").** Whether something is offered is the engine's legality, never a judgment of what is wise:
- What can be paid is decided by `legallyPayable`: the engine's mana and non-mana tests. Forge AI's
  `ComputerUtilCost.canPayCost` adds its own taste on top. It refused, half the time at random, a planeswalker
  ability that spends all its loyalty, and refused ward costs and casualty creatures it would not pay.
- A cost may be paid with the ability's own target, as a human may (Goblin Bombardment sacrificing the creature it
  aims at).
- Who crews, and which creatures that share a type tap, are the seat's.
- Mana abilities are on the menu, one per card name and ability, so mana can be floated.
- What a choice offers is also only what can be paid: a keyword cost's count (conspire, multikicker, squad,
  replicate), escalate's modes and strive's targets stop at what the engine says the cast can pay, so no answer
  the seat may give fails the cast (harness-epoch-43).
- A creature that can block more than one attacker may: its label says how many, and the `answer` schema allows it.
- The one exception: a window whose only option is floating mana that changes nothing passes unasked, as an empty
  one: a land's ability costing only {T} and mana that only adds mana, into a pool that empties without burn, with no
  trigger on a tap or on mana added. Floating that does change the game (a creature tapped and unable to block,
  Manabarbs, Upwelling's pool, mana burn) is a legal play and is asked (`floatChangesNothing`, harness-epoch-42).

**What the harness may decide without asking (owner rule):**
- a choice with exactly one legal option (forced). A cost that takes exactly as many cards as there are
  candidates is paid with those, by the harness (since harness-epoch-40): left to Forge's AI, the pick could be
  refused (it declined to exile Altar of the Wretched for a craft, so the payment failed);
- the priority menu is PASS only (nothing castable, activatable or playable), or this exact spot (turn,
  phase, stack, board, menu) was already passed;
- a play that failed to pay is not offered again in the same window (turn, phase, top of the stack), as Forge's
  AI skips a failed ability: the seat was offered the same unpayable play 999 times.

Anything new asks again: an opponent's spell or ability, every attack and block, a trigger, a new option.
- *Why:* skipping a window the model could act in, keeping a hand for it, or picking its play is a
  heuristic standing in for the model. The old shortcuts (mulligan heuristic and five-card floor, skip heuristics, a cheap
  fast tier) remain only as opt-in measurement switches (`MULLIGAN_HEURISTIC=1`, `SKIP_HEURISTICS=1`,
  `ONE_TIER=0`).

**Combo lines (2026-10-16, harness-epoch-32):** every step of a combo turn is the seat's and can be taken when the rules
allow: a mana ability that costs more than tapping (Lion's Eye Diamond, a Spirit Guide, Lotus Petal) is on the play menu,
so LED can be cracked in response to the seat's own Infernal Tutor; Doomsday's five cards and their order, Ad Nauseam's
every repeat, and each storm copy's target are asked. Golden checks play each line out (`GoldenChecksTest`:
`doomsdaysPileAndItsOrderAreTheSeats`, `adNauseamRepeatsAsOftenAsTheSeatSays`,
`tendrilsStormCopiesResolveAndTheirTargetsAreTheSeats`, `lionsEyeDiamondCanBeCrackedInResponseToInfernalTutor`,
`ritualThenPetalThenAdNauseamIsPaid`).
The stack says what each item is and does: a spell or ability "not yet resolved", a copy effect with the number of
copies the engine will make ("trigger of Tendrils of Agony: Storm (...) -> makes 9 copies"), and none of Forge's debug
notes. Identical items (a batch of storm copies, whose targets come after) have no order to choose and are not asked
(harness-epoch-33). *Why:* Luna read "1 pending" as a choice it was owed, and was asked to order ten storm copies
labelled like the card in hand (C3 ANT).

**Targets on the stack and announced values (2026-10-16, harness-epoch-34):** when the stack is a spell's only target
zone ("counter target spell", Stifle), its choices are the spells and abilities on the stack themselves, as the human
path offers them (`targetCandidates`); an ability is named "ability of <card>: <what it does>". Values a cast announces
are asked as the engine's human path asks them (`announceX`, after `PlaySpellAbility.announceValuesLikeX`): each
`Announce$` variable (a Shoal's X), then an X in any cost part (Toxic Deluge's life), mana X up to what the seat can
pay. A cast at another cost than the card's basic spell (a pitch spell) is labelled with that cost. *Why:* the surface
checks found Luna's Force of Will "countering" a Dark Ritual that then resolved (the target was the card, and Forge
counters only targeted spell abilities), Disrupting Shoal's pitch failing at X = 0, and the pitch labelled with the
mana cost it replaces.

**Ways to play a card (2026-10-16, harness-epoch-35):** every way the engine lists (Forge's `AlternativeCost` and
`OptionalCost`) is offered and paid as the human path does it. Optional costs (kicker, multikicker, buyback, entwine,
bargain) are asked after the seat picks the play (`withOptionalCosts`, CR 601.2b), from the menu and from effects
(cascade, madness), and only when the whole cast stays payable, the picked set included (re-asked once with the reason, then declined). A spell playable only with one of them (retrace and
jump-start from the graveyard, an offering that lowers a cost the seat cannot otherwise pay) is offered with it, and
the hand tag says so. Emerge and offering sacrifices are the seat's, offering only creatures that leave the rest
payable. A spell or trigger with several targets ("any number of target creatures", divided damage) is asked for the
set on every path (`chooseManyTargets`). A creature spell's option shows its rules text. *Why:* Forge's AI asks
optional costs while listing its options, a step this menu does not use, so no kicker, buyback or entwine was ever paid;
Fury's trigger could take one target and the default took Fury itself; Elder Deep-Fiend's emerge was never offered.
Since harness-epoch-39 all 44 are played by a golden check (`eval/play_gaps.txt` is empty):
- **Pricing never touches the spell.** A probe copy gets its own pips-to-reduce list (`withOptional`): a Forge copy
  shares that list with the original, so pricing a Defiler's "pay 2 life, {W} less" left {W} off the real spell and
  the plain Savannah Lions was offered and cast with no land and no life paid.
- **Harmonize is priced with its tap.** The menu checks each power the seat could tap (`harmonizePowers`, with the
  card's cast spell set as the engine sets it), and the seat picks the power from the payable ones only.
- **An option says what the card does.** A cast at another cost (prowl, surge, spectacle, overload, a pitch) shows
  the text of the face being cast; the engine's description is only the cost and its reminder. The cost shown is the
  cost paid (a Defiler's reduction taken off), and alternative costs keep their printed names (Web-slinging).

**When the model gives no usable answer:** a fixed legal option (the first, or decline; no attack for
attackers), recorded on the decision row with why. Never Forge's AI, and never an option read out of the
model's plan text (the "plan-named" fallbacks went in harness-epoch-30, review M2).
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
- **What a player keeps track of** (as MTGA shows it): the opponent's cards cast and lands played this
  game, the rules text of every opponent card seen, hidden cards revealed to the seat, cards seen going
  into an opponent's hidden zone (KNOWN IN HIDDEN ZONES), and the top of the seat's own library that it
  put or saw there (scry, surveil, Ponder, Brainstorm), top first.
  - *Why the library top is checked on every render:* it is the seat's knowledge, not engine truth, so
    it may never claim more than the seat knows. Drawn cards drop off, a shuffle (the controller's
    `cheatShuffle` hook, delegated unchanged) forgets it, and anything that stops matching is forgotten.
- Objects are named by handles (`@12`), never by engine ids.
- **Every fact in it is asked of the engine, the way Forge's own AI asks, never re-derived by the
  harness.** Re-deriving is how the seat came to be told less than Forge AI knows.
  - *Why:* the mana line used to count untapped sources per colour from card types. The three Tron
    lands read as 3 mana instead of 7, Ancient Tomb as 1, and a summoning-sick Llanowar Elves as 1.
    The seat was told it was short of mana that Forge's payment code knew it had (g7, Kozilek's Command).
  - Where each fact comes from:

    | Fact | Engine source |
    |---|---|
    | mana available, per source and in total | each mana ability's `canPlay()` and `amountOfManaGenerated`, as in `ComputerUtilMana.getAvailableManaEstimate`; plus the mana pool; mana with a spend restriction (`AbilityManaPart.getManaRestrictions`) listed apart |
    | "(castable)" | `ComputerUtilMana.canPayManaCost`, and the play menu's own offer check |
    | X up to N | `canPayManaCost` at each X |
    | which sources are spare in a payment | `ComputerUtilMana.getManaSourcesToPayCost`, and `canPlay()` on each source |
    | legal attackers and blockers, unblocked damage | `CombatUtil.canAttack` / `canBlock`, `ComputerUtilCombat.sumDamageIfUnblocked` |
    | lethal damage in a damage assignment | `ComputerUtilCombat.getEnoughDamageToKill` |
    | [sick], [tapped] | `Card.isSick()` / `isTapped()` |
    | the shuffle after a Ponder order | the ability (`MayShuffle`, or a Shuffle effect), not the message wording |
    | X in the mode and target questions | the announced `getXManaCostPaid` of the spell |
    | a card's name in a question | the engine's name, with an effect's engine id removed (one function, `safeName`) |
    | a filter in a cost or a name ask ("another blue card", "three black creatures", "a nonland card") | the engine's filter (`Card.Blue+Other`) said in words (`filterParts`, `plainFilters`, `nameConstraint`), in every cost the seat reads and in the state's stack and history lines (`SeatObservation.redact`), under the faithful profile (legacy profiles keep the engine's text). Control and ownership ("creatures an opponent controls"), a chosen or shared name, and a bare filter (a plural) are said too. Any other condition is left out of a counted phrase ("a card"), reads "the creatures the card describes" in a bare one, and leaves a name ask to the card's own text. A creature type Forge pluralised with an "s" takes its own plural from Forge's type list (`typePlurals`: "Elfs" reads "Elves", g10) |
    | CAST THIS GAME, LANDS PLAYED | the engine's `GameEventSpellAbilityCast` / `GameEventLandPlayed` (a copy is not a cast), not the log line's wording |

  - The one hint still worked out by the harness: "(need 1 more B)" compares the cost's coloured pips
    with the per-colour mana. It is shown only when the engine has already said the card cannot be
    paid, and only for plain costs.
  - Tested in `SeatObservationTest` (Tron 7, Ancient Tomb 2, a sick dork 0, a shortfall named in mana).
- *Why faithful:* hidden information must stay hidden, or the benchmark measures something else.
  `SeatObservationTest` renders two games that differ only in hidden state and requires identical
  observations.
- *Why handles:* a stable, short name for "this copy", usable in tools and answers. Engine ids leak
  internals and change between games.
- *Why uncut:* the harness no longer truncates event text, card text, reveals or history
  (`docs/harness/MODEL-LIMITS.md`). Cuts were a harness limit on the model.

**Every block says where it comes from.**
- The header of each block names its source:
  - engine state: YOU, OPP, `STACK (engine, ...)`, COMBAT, `FACTS (engine estimates ...)`;
  - Forge's game log: `GAME HISTORY (Forge game log, ...)`, `SINCE YOUR LAST DECISION (Forge game log, ...)`,
    REVEALS TO YOU;
  - the card database: OPPONENT CARDS YOU HAVE SEEN;
  - the harness's record of what the seat saw: `KNOWN IN HIDDEN ZONES (harness record: ...)`;
  - the seat's own words: YOUR PLAN, YOUR OPPONENT PROFILE, YOUR CARD INTERACTIONS.
- The guide repeats it in one paragraph, and says that no other model's words appear anywhere.
- *Why:* the log is in the engine's words and names players. A line like "Rival scries 1", or one naming
  the seat's own player, could be read as a message to the seat or as its own earlier output. The owner
  asked that what comes from the Forge log never be confused with agent output.
- Legacy profiles keep their bytes; only the faithful profile carries the labels (`GameHistoryTest`).

**Every public facet of the state is shown (2026-10-16, observation-faithful-v35).** Probed facet by facet against the
engine's state (`SeatObservationTest.everyPublicStateFacetIsShown`, `whatAPermanentRemembersIsShown`,
`aSuspendedCardShowsItsTimeCounters`): counters, damage, attachments, loyalty, tapped, summoning sickness, face-down and
phased-out permanents (a phased-out one used to vanish), poison and other player counters, the monarch, the initiative,
the city's blessing, day or night; what a permanent remembers (Pithing Needle's named card, Cavern of Souls' type, a
chosen colour, player or number, Chrome Mox's imprint, a token), also on lands; counters and waiting states off the
battlefield (a suspended card's time counters, an adventure, foretold, plotted). *Why:* a Needle naming Jace read
"Pithing Needle [untapped]", and a suspended Ancestral Vision showed no countdown.

**How to read the state is explained once, in the system prompt (`SeatObservation.GUIDE`).**
- *Why:* the per-message legend cost about 200 tokens a call outside the cache. It also disagreed with an
  older one in the system prompt (`docs/harness/PROMPT-AUDIT.md`, A1).

## 5. The prompt

**System message, fixed for the whole game** (`seatFraming`):
- the role;
- `<your_deck>`: the deck's plan and list;
- `<how_this_works>`: the engine, the tools, answering by call;
- `<reading_the_state>`: the guide;
- `<reference>`: terms and keywords, the same in every format and in a puzzle. It named the Legacy eval pool's
  archetypes until harness-epoch-30, which told only a Legacy seat what it could face (review 2026-10-16).

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
- *Why no versions in the prompt:* the model gains nothing from them, and a changing prefix would break the cache.
  Every stamp is on the game line instead (`docs/harness/VERSIONS.md`, generated from the code).
- The guide describes every section and hand tag the state can show; since observation-faithful-v37 it lists the
  alternative-cost, optional-cost and timing tags and says a mana ability's ACTIVATE only fills the pool.

## 6. The answer: a tool call, nothing else

**`AnswerTool`: one fixed `answer` function for every decision.** All its fields are optional:
- the answer fields: `option`, `options`, `blocks`, `amounts`, `keep`, `number`, `card`;
- the play extras: `steps` under `PLAN_EXECUTE`, `yield`, `profile`, `lines`, `keep_untapped`;
- `why`, one sentence for the record.

Each call is validated against the decision it answers. An illegal call gets the reason and one more
round in the same conversation; a second illegal call loses the decision.

A tool ask's decision text comes from the question builders the text lane and the ai-teacher seat share.
`AnswerTool.rephrase` removes their text answer line ("Reason briefly, then end with EXACTLY: ..."), and the
ask's one answer line is the `ANSWER:` line appended after it; no reasoning cap is left in (answer-tool-v11).
A text format that survives is logged as `[TEXT-FORMAT-LEAK]` and fails acceptance.
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
- **Zero text parsing on a tool ask:** a decision reads the typed `AnswerTool.Answer`, never the reply.
  The text parsers are reached through a guard that refuses on any ask that offered `answer`, logs
  `[TEXT-PARSE]` and counts it (`textParses=` on `[TALLY]`); the answertool acceptance stage and
  `GoldenChecksToolTest` fail on one. The row's `raw_reply` is the answer rendered for the record only.
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
| `simulate` | the lookahead: what an option resolves to, on a copy of the game (`LOOKAHEAD-DESIGN.md`). The copy shuffles every library and re-deals each opponent's hand from its hand and library, so a preview reveals no hidden card (lookahead-preview-v6, `LookaheadProbeTest.hiddenCardsDoNotChangeThePreview`). While the seat aims a play it is making, `option` is a target's number and the play is previewed at it (lookahead-preview-v10, seat-tools-v11; g03 asked there and was refused). A preview restores the targets of every ability of the chain it aimed (`aPreviewLeavesNoTargetOnAnyAbilityOfTheChain`: Arc Trail's second target stayed set, and the real cast skipped asking for it). A mana ability is a step like any other and the report shows the mana pool (lookahead-preview-v12; g10's line "cast Heritage Druid twice, then tap Gaea's Cradle" was refused whole) |
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
- **The plan:** `set_plan` (goal and next steps), shown back on later asks as "a note, not a commitment".
  - *Why a note:* every decision is re-made on the state then. The agent guides of Anthropic, OpenAI, Google and
    xAI all describe a loop in which the model sees the environment's result before each step. A plan carried out
    without asking again (`PLAN_EXECUTE`, opt-in for measurement only) is a predefined workflow, and Magic has
    hidden information and an opponent acting in between (WORK-PLAN item 16).
- **The opponent read and the card lines:** fields of the play answer.
- *Why an archive and not a growing transcript:* a full transcript grows without bound and drowns the
  current state. The archive keeps everything, retrievable, while the prompt stays current
  (`docs/harness/MATCH-MODEL.md`).
- *Why a fresh state per decision and not one long conversation (as Claude Code runs):* the board changes at
  every step and the engine holds the true state, so each decision gets it rendered fresh. Models are about
  39% worse in multi-turn conversations than single-turn (Laban et al., ICLR 2026) and less reliable as the
  input grows (Chroma, context rot). The research is in `docs/harness/REVIEW-2026-10-16.md`, phase 1b.
- **Conversations as conditions:** `TURN_CHAT=1` keeps this turn's earlier decisions in the conversation
  (a multi-step line in one train of thought) and starts over each turn; `ONE_CHAT=1` keeps the whole game.
  Both replay a decision as it happened: its DECISION part, the model's assistant messages unchanged with
  their tool calls and `reasoning_details`, and a result for every call (OpenRouter's reasoning guide: pass
  reasoning back unchanged and in order). Acceptance stage `turnchat` checks every request is a valid
  tool-call sequence. Opt-in until a measured block shows one keeps lines more coherent (review C3).

## 9. Talking to models: bridges

- **`OpenRouterBridge`** (`openrouter:<model>` specs) is the default. It handles:
  - function calling;
  - `cache_control` for Anthropic models (OpenAI and DeepSeek models cache on their own);
  - reasoning passed back unchanged across tool rounds;
  - per-model settings (`ModelCfg`): token caps and timeouts;
  - readable reasoning, kept on the call row where the provider returns it;
  - routing by latency (`provider.sort`, `OPENROUTER_PROVIDER_SORT`, default `latency`), and the provider
    that served each call on its row.
  - *Why latency routing:* Luna calls stalled for up to 6 minutes while answering in ordinary length (646
    output tokens in 352 s, against a median of 253 in 9 s). The time was the endpoint's, not the model's
    thinking. OpenRouter's default routing weighs price; the model is the same on every endpoint.
  - a stalled connection is retried (`HttpDeadline`, `OPENROUTER_IDLE_MS`, default 90 s, 0 off): no headers, or
    bytes and then silence, for that long, inside the ask's deadline.
  - *Why:* in the C3 combo games some connections sent nothing for minutes and held the ask to its full
    timeout, 15-25 minutes a game; a fresh connection answers in seconds.
- **`ClaudeBridge`** (the Claude CLI lane): text only, kept for the CLI.
- **Egress boundary:** an `openrouter:` model receives the game state. Nothing starts unless
  `OPENROUTER_ALLOW_EGRESS=1` is set by the owner, and no tool sets it.
- **The game model:** GPT-6 Luna (`openrouter:openai/gpt-6-luna`) for every game unless the owner names
  another, at about $0.06–0.07 a game (one tier, every decision reasoning).
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
| `search` | the puzzle prover: both seats draw from one shared line of choices (`PuzzleArms.Search`), and `PuzzleSearch` replays prefixes to find a winning line and beat every opponent reply on it |

- *Why the prover:* Forge's puzzle mode tests one opponent line, its AI's, so a solve can come from the AI
  blocking badly. A proof (`eval/BENCHMARK-V1.md`, phase 2) beats every opponent choice the engine offers, and
  every leaf of it is a solve Forge scored. A decideMany subset is drawn through `PuzzleArms.subset`, which
  makes the old draws for every other arm (no canary moves) and one include-or-not per option for search.

- The seed-42 floor and random mirrors are pinned canaries, at A/108/1824 and A/45/368.
- *Why:* a score is meaningless without the trivial baseline it beats (the "88% lesson" in `CLAUDE.md`).
  A canary that cannot move cannot catch a regression, so any change to an automatic path re-pins it
  with evidence.

## 12. The eval layer

**Formats and decks.** The Legacy pool (16 decks) is the original benchmark. Beside it, real MTGO Challenge top-8
lists for six formats (Legacy, Modern, Pioneer, Standard, Pauper, Vintage; `eval/decks/SOURCES.md`), named
`<format>-<archetype>.txt` (`deck_legality.FORMAT_PREFIXES`), run with `run_batch.py --format <f>`.
- The engine plays any deck, so the format is what the seat is told (`GAME_FORMAT`, in the system prompt)
  and what the game line records (`game_format`, an experiment condition: formats never pool).
- `eval/deck_legality.py` checks a deck against the pinned engine's own format data (legal sets, ban lists,
  Pauper's rarities); `--format` refuses a deck that is not legal.
- A deck with no `.plan` gets no plan text, only its decklist and card texts. *Why:* the fallback used to be
  the default decks' plan, so a Standard deck was told it was Grixis Reanimator.

- **`run_batch.py`** launches games:
  - it pins every condition explicitly (ambient environment is scrubbed) and records provenance;
  - provenance: `overlay_sha` hashes every overlay source (`forge-overlay/*/src/main/**`: the harness
    classes and the engine patches), and cited mode refuses when any installed file differs from the
    checkout (`benchmark_version.overlay_tree_sha`; it covered two files until 2026-10-16, review H3);
  - unset `--context-profile` is the faithful seat (`v4-faithful`), as in the play scripts; the legacy
    `v1-compat` (coaching, a reasoning cap, a 25-decision turn cap) runs only as a named arm;
  - it resumes;
  - it plays a crashed game once more and sets the failed attempt aside (`--retries`);
  - it enforces the budget;
  - it refuses an llm seat that does not name both of its models, in every mode: a seat with no controller
    is an llm seat, and an unset model fell through to the harness default (Opus), so a Luna mirror
    launched with seat B unset played Luna against Opus 5.5 with every stamp healthy (issue #234);
  - cited mode forces `--jobs 1` only when a seat runs on the local claude CLI (parallel JVMs starve it);
    `openrouter:` games wait on the network and may run in parallel (review E3).
  - *Why pinned:* a baseline arm that silently inherited a setting would be pooled with a different
    measurement.
- **`aggregate.py`** reports win rates with confidence intervals, kept apart per condition.
  **`defect_analyzer.py`** is the run oracle: `ai_tier_count=0` and no silent drops, or the run fails.
- **Seed governance (`eval/seed_policy.py`):** some seed blocks are reserved for evaluation, and no
  training data may come from them.
- **The three headline scores:**
  - **STR-AI:** win rate against Forge's AI over fixed panels (`eval/SCORING.md`). The panels file pins
    the pilot condition (profile, tiers, answer tool, tools, lookahead, chat windows, plans): `run_batch.py
    --panel` launches and `panel_score.py` scores no other. *Why:* a score is a property of one seat; two
    settings under one name would be two benchmarks.
  - **STR-HELDOUT:** the same on a held-out seed band; to become the private mined puzzle split
    (`eval/BENCHMARK-V1.md`, owner decision 2026-10-18).
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
   - loopback fake-model games for tools, answers, the faithful profile, lookahead and rules;
   - `scoring`: a puzzle and a panel cell played through the launchers a paid run uses and scored by
     `puzzle_score.py` and `panel_score.py` (since 2026-10-16, review M4).
   - It never touches the Forge checkout.
2. **Golden checks:** every decision type the seat owns, through the live controller, in both the text
   and the tool protocol.
   - Every prompt a golden check asks is linted (`AskContract`): no CARDNAME, no engine card id, no engine filter
     syntax (`Card.nonLand`), no Forge debugging note on a trigger (the label words come from the engine's own
     localizer), no option cut short. `match_review.py` flags the same in games as LEAK.
   - Acceptance stage `surfaces` (2026-10-16): the Java tests record, for each decision row, the engine hooks
     on its call stack (`-Dharness.surface.report`, off in play). Every hook the ledger marks as a decision
     the seat answers (`controller-ownership.tsv`, kind strategic or payment) must have been reached with the
     model asked. The ones not yet proven are listed in `eval/surface_gaps.txt`, which may only shrink.
   - The same stage checks the ways to play a card: each play the seat makes records how it was played
     (`play:alt:Evoke`, `play:opt:Kicker1`, from which zone, which face), and every kind the engine lists must be
     played by a golden check or listed in `eval/play_gaps.txt` (may only shrink).
   - **Every mechanic (`KeywordSurfaceSweepTest`, 2026-10-17):** for each of Forge's keywords, three real cards
     that have it, in each zone and turn moment a card can be played from (own main phase, own combat with an
     unblocked attacker, own upkeep, the opponent's turn). Parity: what the engine lets a human do with the card
     (`Card.getAllPossibleAbilities`, the list the GUI offers on a click, kept when payable with a legal target)
     must be on the seat's menu, and the menu must offer nothing else. Playthrough: each such play is made by the
     seat with a fake model that answers every ask in its own format, resolves, hands nothing to Forge's AI,
     reads no answer it cannot parse and keeps `AskContract` on every ask. Any gap fails the test. The five
     keywords with no card to sweep are pinned (no card uses Absorb, Frenzy or Poisonous; the agendas are
     Conspiracy draft only).
   - **The keyword gauntlet (`eval/keyword_gauntlet.sh`, on demand, no model cost):** the sweep's 553 cards in 28
     decks (`eval/gauntlet/`), each deck's LLM seat on the loopback fake model (`--policy first --answer-tool`:
     the first option of every ask) against the next deck's Forge AI, in full games. Triggers, combat, upkeep and
     end-step mechanics reach the seat as a game brings them. `eval/gauntlet_scan.py` fails on a choice Forge's
     AI made, an unreadable answer, a play that failed to pay, a crash or an unfinished game. *Why both:* the
     sweep proves each play is reachable and plays once; only games bring the states in between. The first run
     found a craft the seat could never pay (with one candidate, the exile was left to Forge's AI, which declined
     to exile Altar of the Wretched) and chose 999 times in one window, and a StackOverflowError (floating mana
     asked while the hand was priced). Its second run, with failed payments counted, found three more: an
     ability offered at X=1 with no counter to remove (Arcee: the menu now checks the costs beyond mana at the
     least X, and X is capped by what can pay it), a spree mode chosen that the mana could not cover (modes are
     offered only when payable, and checked together), and a convoke payment one short (creatures that may
     convoke are not counted as mana when a land is kept untapped; a convoke that leaves the rest unpayable is
     asked again with the reason).
     `GAUNTLET_POLICY=random` answers every ask with a uniformly random legal answer instead (seeded per deck, so a
     game replays): the targets, picks, splits, numbers, blocks and mulligans `first` never gives. Its first run
     found four more: a cost the cast could not pay offered as a count to pay (conspire with no creatures to tap;
     escalate's second mode; Squad and Replicate "any number of times" answered 1.2 billion, which hung the payment
     and ran out of heap). Keyword costs, escalate modes and strive targets are now offered only as many times as
     the engine says the cast can pay (`payableKeywordTimes`, `escalateCost`, `keywordCost`). Its second run found
     a CANCEL answer to a tap cost paid anyway: writing the decision row cleared the cancel before it was returned,
     only with the game log on, which no golden check had; `GoldenChecksToolTest` now runs with the log on.
     `GAUNTLET_TOOLS=1` gives the seat its lookups, and the random policy previews, inspects and searches at random.
     Its first run found a StackOverflowError: a hand tag priced inside another pricing switched the guard off when
     done, so a pool-mana choice reached from the next tag asked the seat, whose prompt priced the hand again. The
     guard is now restored, never cleared, and building any prompt counts as pricing from start to end, so no hook a
     prompt build reaches can ask (`renderSnapshot`; `pricingInsidePricingKeepsTheGuardOn`).
   - *Why measured, not listed by hand:* a hand-kept map says which test should reach a hook; the record says
     which test did. A per-type list let a hook that only ever took forced picks look covered.
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
- **Instructions for agents** (2026-10-17, from Anthropic's Claude Code guidance: CLAUDE.md is loaded in every
  session, so keep it under 200 lines and move the rest where it loads on demand): `CLAUDE.md` holds what applies
  everywhere; `.claude/rules/` holds path-scoped rules that load when matching files are touched (the overlay, the
  eval, the seat's prompts and tools); `.claude/skills/` holds the workflows; `docs/harness/INDEX.md` marks each
  document current or historical. `eval/test_docs_index.py` keeps CLAUDE.md under 200 lines and the index complete.

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
    and which; an affordability test counts every card, unasked. So are an "up to" cost reduction (Mana
    Matrix: the whole reduction while pricing; asked inside the menu it re-entered the snapshot and overflowed,
    #225) and an emerge or offering sacrifice. The least delve stated is the engine's own test payment of the rest
    (an estimate let Become Immense delve none and fail to pay; harness-epoch-45).
  - A damage assignment order already declared (legacy ordering, `overrideOrder` false) is followed, not asked
    again (#224); a free split is the seat's.
  - Convoke and improvise, retargeting, redirection and mana from the pool are the seat's too. A convoke
    choice asked from an affordability test is answered as "every helper could pay" without asking; the
    engine does not say which calls are tests, so the seat's real payment marks its own window.
  - An effect played without the stack (an opening-hand ability, a replacement effect, a trigger that
    skips the stack) gets the seat's modes and targets, and its cost is paid with the seat's cost
    decision: the engine routine hard-codes Forge's AI there, so the harness runs a copy of it.
  - A spell or copy with several targets is retargeted as a set: the seat picks up to as many as it had,
    and none, or a set the spell cannot have, keeps the old one. A divided spell keeps its portions, as
    the engine does for a human: exactly as many new targets, each taking an old portion the seat assigns.
  - The colour an any-colour source makes for a generic shard during payment is the seat's through the
    first engine patch (`ComputerUtilMana`); a coloured shard forces its colour.
  - Whether to reveal a companion is the seat's (an optional choice); a declined companion is no companion, through
    the second engine patch (`Player.assignCompanion` crashed on it).
  - The resolve line of a state-dependent pump (+X where X is the target's power, e.g. Berserk) reads the
    target's power before the effect applies, through the third engine patch (MagicStack built the GAME
    HISTORY's resolve line after the pump and made it contradict the stack preview the seat was shown: g06
    seed 4106 T7 showed "+5/+0" and "+10/+0" in the same turn's consecutive prompts).
  - **Mechanics no deck in the pool reaches:** dice, stickers, contraptions.
- **Mana payment:** the engine maps the chosen sources to the cost. The seat decides which sources stay
  untapped when it matters (`keep_untapped`, or the mana-sources ask). The planner can be more hopeful than the
  real payment: when the engine cannot pay around the kept sources, the hold is released and the cost paid again,
  recorded as an auto row (harness-epoch-45). A cancelled modal spell drops its modes, so the next cast asks again.
- **The Claude CLI lane** answers in text, because it has no function calling.
