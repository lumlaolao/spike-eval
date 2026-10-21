# Explaining it: the tech and the stack, for a presenter

Read this before presenting the project or the demo. It says what the system is, how one decision flows through it,
what each layer is built from and why, how the benchmark works and why every score comes with a floor, which numbers
to quote and where each comes from, and the questions an audience asks. The detail behind every line is in
`ARCHITECTURE.md`; the demo runbook is `DEMO.md`. Every number here names its source; quote it with the source.

## In one breath

A language model plays full games of Magic: The Gathering inside Forge, the open-source rules engine. Every
decision the engine would ask a human player (89 kinds of decision, and 44 ways to play a card) goes to the model
as a function call. The engine produces every option, so the model cannot make an illegal move, and the harness
never decides for it. The games and puzzles are scored as a benchmark: each score with its confidence interval and
the trivial baseline it has to beat.

The principle behind every design choice (owner, 2026-10-15): **only the model limits play quality.** The harness
shows the game faithfully, asks every decision, carries out the answer, and decides nothing itself.

## Magic in 60 seconds (for people who know LLMs)

- Two players, 20 life each, a deck of their own cards. Reduce the opponent to 0 life. Each player's hand and the
  order of their deck (the library) are hidden; the battlefield, graveyards and what has been cast are public.
- Lands make mana; mana pays for spells. Most turns you play one land and cast what you can afford.
- A turn has phases: untap, upkeep, draw, main, combat, a second main, end. Combat is two decisions, declare
  attackers then declare blockers, and then damage.
- Spells and abilities go on **the stack** and resolve last in, first out. Before anything resolves, each player may
  respond. The rules say who may act when (**priority**), so a game is a long series of windows in which a player
  may do something or pass.
- A **decision** here is any point at which the engine asks a player something: what to play, what to attack
  with, what to target, which card to discard, whether to keep the opening hand (the mulligan), how to split
  damage, which colour to make, and so on. Forge asks a player through 89 methods; a game asks hundreds of times
  or more (the seed-42 zero-model canaries record 365 and 1,828 decision rows).

## LLM tooling in 60 seconds (for Magic players)

- A **function call** (also "tool call") is a structured reply: instead of prose, the model returns the name of a
  function it was offered and JSON arguments for it. The harness runs the function and returns the result, in the
  same exchange, and the model may call again or answer.
- A **system prompt** is the fixed instruction block at the top of every request. The **prompt cache** is the
  provider's discount for a prefix identical to a recent request; the harness keeps that prefix stable so most of
  each request is read from cache.
- **Reasoning** is the model's own working before it answers. The provider returns it, and the harness records it
  with every call, which is what the demo viewer shows.

## The stack, layer by layer

```
 Forge rules engine (Java 17, pinned commit, 4 patched files)            the rules, legality, the opponent (Forge AI)
   │  "player A: choose" (one of 89 decision hooks)
   ▼
 forge-overlay: PlayerControllerLLM (Java)                               the seat: one per model player
   ├─ SeatObservation        what that player may know, each fact asked of the engine
   ├─ prompt                 fixed system message + this decision, in cache-friendly order
   ├─ NativeTools            inspect, memory_search/read/note, rules_lookup, simulate, report_unclear_decision
   ├─ AnswerTool             one `answer` function (and `set_plan`), validated against the decision
   └─ OpenRouterBridge       HTTPS to openrouter.ai: function calling, prompt cache, latency routing
   ▼
 GPT-6 Luna on OpenRouter                                                the player being measured
   ▼
 EvalLog → game-*.jsonl (every decision, call, tool result, cost)        the record everything else reads
   │
   ├─ eval/ (Python 3, stdlib only)   run_batch, scoring (panel_score, puzzle_score), review sheets, acceptance
   └─ demo  (stdlib HTTP server + plain HTML/CSS/JS)   the viewer, the side by side, puzzle mode, the eval page
```

| Layer | Built from | Why this |
|---|---|---|
| Rules engine | [Forge](https://github.com/Card-Forge/forge), Java, pinned by `FORGE_COMMIT` (`ENGINE-PIN.md`) | It implements the comprehensive rules for a large card pool and runs headless. Legality is the engine's job, so the harness never judges a move. Pinned because results compare only within one engine version; every game line records the commit. |
| Engine changes | `forge-overlay/`, copied over the checkout by `install.sh`; 4 patched engine files, each change marked `HARNESS PATCH` and listed with its upstream hash in `ENGINE-PATCHES.md` | The repository never redistributes Forge (GPL) and the checkout is never edited by hand, so any build is reproducible from the pin plus the overlay. A patch is allowed only where the seat has no hook or the engine is wrong, never to make a run pass; a re-pin forces a re-diff. |
| The seat | `PlayerControllerLLM` in `LlmSpikeGui.java`, a subclass of Forge's `PlayerControllerAi` that overrides every decision method | Forge asks each player through its `PlayerController`. Subclassing the AI controller keeps the engine paths that only work for a non-human player (headless play, payment, triggers) and replaces the decisions. The danger is an un-overridden method that Forge's AI would answer; `controller-ownership.tsv` lists every method and who decides it, and each of the 89 hooks is proven asked by a test (acceptance stage `surfaces`). |
| What the model sees | `SeatObservation.java`, the faithful profile | Only what that player could know: its hand, the public zones, the opponent's hand count, cards revealed to it, Forge's game log since its last decision, mana available and what each hand card needs. Every fact is asked of the engine the way Forge's own AI asks (mana, castability, legal attackers), never re-derived by the harness. Each block is labelled with its source. A test renders two games that differ only in hidden cards and requires identical prompts. |
| The answer | `AnswerTool.java`: one fixed `answer` function for every decision | Text parsing was where most harness defects came from (answers mid-prose, two formats at once). A schema makes the answer structured and a wrong one explainable. One fixed schema, rather than one per decision, keeps the prompt cache intact. Zero text parsing: a text parse on a tool ask fails acceptance. |
| Lookups | `NativeTools.java`, native function calls, mirrored in `eval/tooluse/tools.json` | Card text (`inspect`), the game's own archive, the comprehensive rules (`rules_lookup`), and `simulate`: resolve an option on a copy of the game with the hidden cards re-dealt, so a preview reveals nothing. Limits of 16 calls an ask and 1,000 a game are loop guards, not rationing. With native calls the deciding fact came back in 87% of asks, against 13% with a text command grammar. |
| Model access | `OpenRouterBridge`, `openrouter:<model>` | One API for many providers, so a model comparison changes one string. Routing by latency, because some endpoints stalled for minutes while the model's own answer took seconds. The game state leaves the machine only with the owner's `OPENROUTER_ALLOW_EGRESS=1`; nothing sets it for them. |
| Memory | `ContextMemory` archive, `set_plan` | Each decision gets a fresh render of the true state plus a searchable archive of everything the seat saw, not an ever-growing chat: models do worse as a conversation grows. The plan is shown back as "a note, not a commitment", because each decision is re-made on the state then. |
| The record | `EvalLog` → `game-<seed>-<ms>.jsonl` | Every decision row, model call (tokens, latency, provider, reasoning), tool call and result, plus the version stamps (`VERSIONS.md`). Every score, review sheet and demo pane is computed from these files. |
| Eval and scoring | `eval/*.py`, Python 3 stdlib only | `run_batch.py` pins a condition, prices it and stops at `--budget-usd`; `panel_score.py` and `puzzle_score.py` score with intervals and floors; `aggregate.py` refuses to pool results from different conditions or versions. Stdlib only, so it runs anywhere. |
| The gate | `eval/harness_acceptance.sh`: 19 stages, about 12 minutes | An isolated compile, 929 Java and 528 Python tests, two bit-reproducible zero-model canaries, and loopback fake-model games for every tool path. No model, no cost, no network, and it never touches the Forge checkout. |
| The demo | `demo_server.py` (stdlib HTTP), `eval/demo/web/` (plain HTML, CSS, JS) | It reads the JSONL and writes nothing into a run, so it can never change what the seat sees or decides, needs no engine and no model, and replays any recorded game offline. |

## One decision, end to end

Take "declare attackers" on the model's turn.

1. Forge's combat code calls `declareAttackers` on the player's controller, which is `PlayerControllerLLM`.
2. The seat builds the observation: both battlefields (handles like `@12` instead of engine ids), life totals, the
   stack, what happened since its last decision (Forge's log, labelled as such), and the legal attackers from the
   engine's own `CombatUtil.canAttack`.
3. The request goes out: the tools (fixed for the game), the system message (fixed for the game: role, deck plan,
   how answering works, how to read the state), then this decision's state and its numbered options last. The
   fixed prefix is what the provider caches: 54% of prompt tokens on an ask's first round and 92% on later rounds
   are read from the cache.
4. The model may call lookups first (`inspect @12`, `simulate` an attack) and each result comes back in the same
   exchange.
5. It answers by calling `answer` with the attackers and a one-sentence `why`. The call is validated against the
   decision. An illegal call gets the reason and one more round; a second illegal call loses the decision to a
   fixed legal option (here: no attack), recorded on the decision row as a defect. A reply that calls nothing is
   reminded twice (`AnswerTool.MAX_NUDGES`), then treated the same way. Forge's AI never chooses.
6. The seat hands the engine the engine's own objects for those attackers. The engine resolves combat.
7. `EvalLog` writes the decision row and every call row. `[TALLY] ... ai=0 textParses=0` at the end of the game
   is the proof that Forge's AI never chose for the seat and no text was parsed as an answer.

The same path serves every decision: the hook changes, the observation, the prompt order, the `answer` call and
the record do not.

## The benchmark, and why every score has a floor

Three headline scores, each with its 95% interval and the floor it has to beat (CLAUDE.md, "The benchmark"):

| Score | What | Floor | Status |
|---|---|---|---|
| STR-AI | win rate against Forge's AI on the frozen panels (`eval/suites/panels-v1.json`): deck d against Forge's AI on deck d, 16 cells, 50 seeds, both seat orders, 1,600 games | the `floor` (always pass) and `random` (uniform legal option) arms on the same cells; the Forge-AI mirror is 50% by symmetry | not run: $32 to $80 at CLAUDE.md's $0.02 to $0.05 a game; the owner's decision |
| STR-HELDOUT | the same on seeds blocked from every training corpus (`eval/seed_policy.py`) | as STR-AI | not run |
| PUZ | puzzles where Forge enforces the goal and the engine judges the solve; mean over puzzles of pass@1 minus the puzzle's random solve rate (`eval/puzzle_score.py`) | random play scores 0 by construction; always-pass and first-option alongside | recorded (below) |

Why floors: a score means something only next to what trivial play gets on the same items. The "88% lesson"
(`.claude/rules/eval.md`): a replay-match score once read 88.1% while that student won 10% of its games, because
78.2% of the answers were PASS and doing nothing already scored 78.2%. So every number here reports its trivial
baseline, metrics are chosen so that baseline is near zero, and `aggregate.py` refuses to pool results from different
conditions, epochs or engine versions, because two settings under one name would be two benchmarks.

The puzzles come in two kinds. The rules puzzles are 526 converted Forge puzzles, mostly one or two actions, each
testing a rule; a scored sample is frozen as a suite. The hard puzzles (`HARD-PUZZLES.md`) are generated boards of 10
to 15 actions, each with its own written solution line, kept only when every floor failed it, and fresh, so they
cannot be memorised. A written line is played through the model seat by a script arm, so the engine checks every
step of the reference solution too.

## Numbers to quote, and their sources

| Number | Source |
|---|---|
| 89 decision hooks, 44 ways to play a card, every one proven asked or played by a test | acceptance stage `surfaces`; `docs/harness/ACCEPTANCE.md`, the 2026-10-20 row for PR #287 |
| 929 Java tests, 528 Python tests, 19 acceptance stages PASS, about 12 minutes (11.5 on that run) | the same `ACCEPTANCE.md` row |
| canaries: seed 42 floor mirror wins A in 108 turns (1,828 decisions), random mirror A in 55 (365) | `eval/harness_acceptance.expected` (Linux pins); the same `ACCEPTANCE.md` row |
| 4 patched engine files, each with its upstream hash and a golden check | `docs/harness/ENGINE-PATCHES.md` |
| the rules lookup holds 3,163 rules and 741 terms, effective September 25, 2026 | acceptance stage `rules`; `eval/puzzles/rules-pilot/benchmark-v1/REPORT.md` |
| PUZ, rules sample, 29 puzzles, k=5, Luna: 0.81 (95% CI 0.71 to 0.89) at epoch 66 with lookups off and on alike; at epoch 67 (neutral brief) 0.73 (0.61 to 0.84) off and 0.79 (0.69 to 0.87) on | `eval/puzzles/rules-pilot/benchmark-v1/REPORT.md`; recomputed live by `python3 eval/demo_eval.py` and on `/eval.html` |
| on that sample pass@1 floors are random 0.088, PASS 0.000, first option 0.207; Forge's AI solves 0.302 | the same REPORT.md |
| Luna called `rules_lookup` 0 times in 145 attempts when it was offered (`inspect` 40 times) | the same REPORT.md |
| hard generated puzzles, pilot: 20 of 68 solved, one attempt each, pass@1 0.294 (95% CI 0.19 to 0.40), floors 0 | `DEMO.md`; `/eval.html` with `--hard-results`; a pilot, never pooled with PUZ |
| a Luna game costs $0.02 to $0.05 and takes 20 to 90 minutes (the planning figure) | CLAUDE.md; `python3 eval/cost_report.py <run>` prices any run |
| recorded single games have cost from $0.01 to $0.32 (g26, 28 turns); the walkthrough game g33 (a win on turn 14) cost $0.13 | `docs/harness/GAME-LOOP-LOG.md` |
| the paid side by side cost $0.32 + $0.14; the games part at decision 6, turn 1 (Mana Crypt against Tundra); the older harness won on turn 16, the current one lost on turn 15 | the `demo-recordings` README. One pair illustrates; it measures nothing. |
| 54% of prompt tokens read from cache on an ask's first round, 92% on later rounds | `ARCHITECTURE.md` section 9 |
| the deciding fact came back from a tool in 87% of asks with native calls, 13% with a text grammar; the grammar refused 30 of 43 tool requests across 48 games | `docs/harness/PLAYER-HARNESS.md`, "Tool protocol"; `ARCHITECTURE.md` section 7 |
| 16 tool calls an ask, 1,000 a game; `MAX_NUDGES` is 2 | `docs/harness/MODEL-LIMITS.md`; `AnswerTool.java` |

## Questions an audience asks

- **Can the model cheat, or make an illegal move?** No. Every option is produced by the engine, the answer must
  pick one of them, and the observation holds only what that player may know; a test proves that hidden cards do
  not change the prompt. The `simulate` preview re-deals the hidden cards on its copy, so it reveals none.
- **Is the harness secretly playing for it?** No. Forge's AI never decides for the seat (`ai=0` on every game), no
  heuristic picks a move, and the only skips are a priority menu that is PASS only, a spot already passed
  identically, or a choice with exactly one legal option. The prompt carries facts, not strategy advice: coaching
  would measure the harness's strategy, not the model's.
- **What happens when the model gives no usable answer?** A fixed legal option (the first, decline, or no attack),
  recorded on the row as a defect. A fallback that chose well would hide a broken ask; a fixed one keeps the defect
  visible in the numbers (`eval/defect_analyzer.py` counts them).
- **Why not parse the model's text?** Text parsing caused most early defects. One function call with a fixed
  schema removed that class of bug, and a text parse on a tool ask now fails acceptance.
- **Why one model?** The owner fixed GPT-6 Luna for every seat so results compare. The bridge takes any OpenRouter
  model; a comparison changes one string, and the scorers refuse to pool the two.
- **Why Forge and not MTG Arena?** Forge is open source, implements the comprehensive rules, runs headless and can
  be pinned to a commit. Arena has no API for an agent.
- **Does the model know the rules?** It can look them up (`rules_lookup`), because a model's memory of the
  comprehensive rules is unreliable at the edges. On the rules sample it never did. With the neutral brief
  (epoch 67) it solved 119 of 145 attempts with lookups off and 127 with them on, against a first-option floor of
  0.21 and Forge's AI at 0.30 (REPORT.md; the earlier 130 of 145 was helped by the puzzle's name).
- **What does it cost?** Cents a game; the full STR-AI panel is $32 to $80. Every paid run states its cost first
  and is capped by `--budget-usd`; small per-game costs add up (204 games in one day came to about $8).
- **How do you know a change did not break it?** The acceptance gate above, plus three review passes on every PR
  (`pr-review`, `simplify`, `standards`) recorded on the PR, and a merge script that refuses without them
  (`docs/STANDARDS.md` section 1). The rule earned by the canaries: before trusting a green gate, say what would
  have made it fail.
- **Why is the strength score not run?** It costs real money and the owner decides when. The puzzle score is
  recorded; the panel's cells, seeds and cost are fixed and shown on `/eval.html` as "not yet run".

## The demo, in stack terms

- **The viewer** (`/`): replays a `game-*.jsonl` decision by decision: the board as the seat's prompt showed it,
  what it looked up, the reasoning its provider returned, its answer and `why`, and the cards it named, framed by
  kind (chosen, looked up, planned, mentioned). `--live` follows a game still being written.
- **The audience feed** (`DEMO_FEED=1`): the one engine-side piece. It writes engine truth (both hands) to a
  separate file for the audience; the seat's prompts are byte-identical with it on and off (`DemoFeedTest`).
- **The side by side** (`demo_compare.py`): builds the current harness and an older commit on the same engine pin
  and plays both on one seed and deck pair; two panes, one play button, and **first divergence** jumps to the first
  decision the two harnesses answered differently. `--parallel` plays both at once to watch live.
- **Puzzle mode**: the puzzle's written line solving it, the model's attempt beside it, and behind a reveal the
  intended line the seat never saw.
- **The eval page** (`/eval.html`): the acceptance run's stages and the headline scores with floors, every number
  read from a scorer or a summary, never typed in.

The ten-minute walkthrough, with the decision numbers to open, is in `DEMO.md`.

## A talk track

1. The claim (one breath): a model plays real Magic in a real rules engine, and it is measured.
2. The stack diagram, top to bottom: engine, seat, model, record, eval, demo. One sentence per layer.
3. One decision end to end, on the viewer: open a decision, show the prompt's labelled blocks, a `simulate`
   call, the `answer` call, the `why`.
4. The three rules that make it a measurement: the engine owns legality, the seat owns every decision (`ai=0`),
   and the prompt carries facts only.
5. The benchmark: three scores, each with a floor; the 88% lesson; PUZ recorded, STR costed and waiting.
6. The gate: 19 stages in about 12 minutes with no model and no cost, and how a change gets in.
7. Close on the side by side's first divergence: two harnesses, one seed, where the games parted.

## Words

| Word | Meaning |
|---|---|
| seat | one player slot driven by a model (`PlayerControllerLLM`) |
| decision hook | one method on Forge's `PlayerController` that asks a player something; 89 are decisions the seat owns |
| handle | `@12`: the seat's stable name for one game object, never an engine id |
| faithful profile | the observation that shows exactly what the player may know, every block labelled by source |
| floor | the score a trivial policy (random, always pass, first option) gets on the same items |
| canary | a zero-model game whose winner and turn count are pinned; a moved pin fails the gate |
| epoch / version stamp | the constants that say which harness produced a result; results pool only within one (`VERSIONS.md`) |
| overlay | the repository's copy of every file it adds to or patches in Forge, installed over a pinned checkout |
| arm | a condition played for comparison: `floor`, `random`, `ai`, `script:<file>`, or a seat setting measured apart |
| written line | a puzzle's reference solution, one option per line, played through the model seat with no model |
