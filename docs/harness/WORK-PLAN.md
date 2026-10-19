# Work plan: what is left (updated 2026-10-19)

The living to-do list for the "the model is the only bottleneck" work. Only open work is listed here;
what is done is in git history, `docs/harness/GAME-LOOP-LOG.md` and the run reports. Update this file
when an item is finished or a new one is agreed, so the next session can pick up from it.

## The plan (owner, 2026-10-19; this comes before every list below)

The deliverable is the benchmark's PUZ score. The 996 rules questions are all sorted
(`eval/puzzles/rules-pilot/manifest.jsonl`): 204 converted, 10 Forge disagreements, 8 multiple-review,
774 dropped with a reason. Going back over the drops yields less each pass (batches 7-9: 14, 4, 3; batch 12: 5;
batch 13: 6 of 8 gated drafts), so more passes are not the route to a larger suite. In order:

1. ~~**Land PR #268**~~ (done 2026-10-19, merge e68c47d: batches 1-12, epochs 52-65, full acceptance 18/18;
   the review's deferred LOWs are issue #269).
2. **A first PUZ score** on the converted puzzles: build the rules suite (`eval/PUZZLES.md`, "The rules suite":
   `rules-candidates`, baselines in 20-puzzle chunks, freeze), then `eval/puzzle_solve.py` with GPT-6 Luna and
   `eval/puzzle_score.py`: its confidence interval and the trivial baseline it beats. Paid: state the cost first.
   First chunk: random play solves 3 of the first 8 rules puzzles more than 20% of the time (RP_001 17/50,
   RP_004 24/50), so they drop; the rest are active.
3. **Decide whether the suite is big enough.** If not, find a new source of puzzles (for example the PUZZLE
   candidates in `GAME-LOOP-LOG.md`), not another pass over the dropped rules questions. The 11 ungated
   batch-13 drafts (`batch13-drafts/`) are finished only if cheap.
4. **Games, the built-in prover and harness fixes only when they serve 1-3**: a harness defect that makes a
   puzzle mis-adjudicate is fixed (as epoch 63 was for RP_207); open-ended game-loop play waits.

Revise this section when a step finishes or the owner changes the order.

## Handoff (2026-10-18)

State of `main`: harness-epoch-51 (observation-faithful-v41, seat-tools-v11, lookahead-preview-v12;
three engine patches, `ENGINE-PATCHES.md`). Acceptance and the gauntlets were last run on epoch 46 (the owner asked to skip them on 2026-10-18 night). A new machine reinstalls and rebuilds Forge first (`install.sh`, then the `mvn` build); on the Mac,
set `JAVA_HOME` to JDK 17.

Next steps, in order:
1. **The benchmark plan is approved: [eval/BENCHMARK-V1.md](../../eval/BENCHMARK-V1.md)** (2026-10-18). Puzzles are
   the headline (private mined split, tool condition C1), games against the Forge AI the validity check; human
   timing skipped for now. Next is its phase 2 (the puzzle verifier, free). It answers review row E6.
2. **The game loop continues at g12.** g11 was clean (the first of three in a row with nothing new). Play
   one game at a time on decks not used this round (the `game-loop` skill) until three in a row add nothing.
3. **After any harness change, and once now for epoch 50**, run the three gauntlets with new seeds, each into its own
   directory, and check each with `python3 eval/gauntlet_scan.py <dir>` (it must say 0 defect lines):
   - `FORGE_HOME=<checkout> bash eval/keyword_gauntlet.sh <dir-first> 6`
   - `FORGE_HOME=<checkout> GAUNTLET_POLICY=random GAUNTLET_SEED=<salt> bash eval/keyword_gauntlet.sh <dir-random> 6`
   - `FORGE_HOME=<checkout> GAUNTLET_POLICY=random GAUNTLET_SEED=<salt> GAUNTLET_TOOLS=1 bash eval/keyword_gauntlet.sh <dir-tools> 6`
4. Owner decisions still open: E4, E5, then E7 (paid, cost first); which models to compare (BENCHMARK-V1 decision
   4); C3.

Merging: the owner asked for the full gate on the epoch-46 PR (`pr_format.py check-branch` and `check-pr`, the three
review skills on the PR head, a recorded acceptance run, then `scripts/pr_merge.sh`). Use it unless the owner says
otherwise.

## Now (2026-10-17): real games on the finished harness, then the eval

- **Phase 1 (the review's R1-R7) and the surface work (S1-S6) are done.** Every decision hook and way to play is proven
  by a test (89/89, 44/44), every Forge keyword is swept, and three free gauntlets check the cards in full games:
  `eval/keyword_gauntlet.sh` with the `first` policy, `GAUNTLET_POLICY=random`, and `GAUNTLET_TOOLS=1` (lookups and
  previews). They found 15 defects on 2026-10-17, all fixed (the last three on harness-epoch-45).
- **Now:** Luna games one at a time through the game loop (`docs/harness/GAME-LOOP-LOG.md`, g01 onward on
  harness-epoch-43 to 45). A game costs $0.02 to $0.05; the time (22 to 91 minutes, about half of it provider waits) is
  on the sheet's TIME line.
- **Phase 2, the eval ([REVIEW-2026-10-16.md](REVIEW-2026-10-16.md) E1-E9):** E1 settled; E2, E3, E8, E9 done (the panel's
  pilot condition is pinned in `panels-v1.json`). Waiting on the owner: E4 (the headline score's handling of
  unfinished games), E6 (STR-HELDOUT: held-out decks or private puzzles), E5 (freeze an epoch), then E7 (the first
  paid block, cost stated first). Also waiting: the Luna puzzle run and the C3 comparison (cost first).
- The items below stay as the longer backlog.

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

## Priority 0 (owner, 2026-10-15 evening): do these first, in this order

**0. Every decision goes through a tool call: no text alternative anywhere.** **Done 2026-10-15 (#182,
answer-tool-v7, harness-epoch-13).** A reply that calls nothing is reminded twice, then lost; prose is never
read. `[TEXT-FORMAT-LEAK]` counts any text format left in a tool ask; `GoldenChecksToolTest` runs every golden
check on the tool protocol.

**A. No engine fallback.** "I would like no engine fallback if able" (owner). **Done 2026-10-15
(#184, #185, #187, #188, harness-epoch-17).** Since #187 and #188 the seat also owns retargeting of
every kind (divided spells keep their portions), redirection, convoke and improvise, mana from the
pool, effects played without the stack (their targets and their cost decision), and since #189 how
many cards delve exiles, and through the first engine patch the colour an any-colour source makes for a
generic shard (silent count 0). Left:
- the mechanics-only rows (dice, stickers, contraptions, attractions: no deck in the pool reaches them).

First Luna game on answer-tool-v8 (`runs/luna-v8/g1`, $0.018): 24 of 24 decisions by call, 0 Forge-AI
decisions. It found an unless cost the seat could not pay offered as a choice, and each such answer
recorded twice; both fixed in #187.

**0b. Force the call where it keeps the reasoning (2026-10-15, owner: "the tool call should be the only way
of interacting").** Researched and measured (`PROMPT-CRAFT.md` rule 2): `tool_choice` "required" for OpenAI
models (Luna keeps its reasoning), "auto" with reminders elsewhere (Anthropic 5.5 refuses forcing; forcing
skips thinking). The same probe found Luna filling every optional field with defaults, which yielded 24 of
48 passes to end of turn in the recorded games: fixed in answer-tool-v8 ("none" first, empty means not
said). Confirmed in the Luna games since (`docs/harness/GAME-LOOP-LOG.md`, g1-g13).

Not engine fallback, and kept: a choice with exactly one legal option is taken without asking (forced),
and a seat that has no model (floor, random arms) takes a fixed legal option. A model that fails to
answer twice loses the decision to a fixed legal option (the first, or decline), never to Forge's AI.

**C. Every observed fact from the engine, every block labelled by its source (owner, 2026-10-16).**
"It wasnt realizing lands can make more than 1 mana ... look into the root cause ... how the forge default ai
can do these"; "be clear what comes from the forge log ... shouldnt confuse it with like agent output".
- Root cause: the harness re-derived facts the engine already knows, from card types and log wording, so the
  seat was told less than Forge's AI computes. The audit (ARCHITECTURE section 4 has the table):
  - mana available: **done (#202)**, now the engine's mana per source, restricted mana apart;
  - the mana-source keep question's sources, the Ponder shuffle note: **done (#202)**;
  - the opponent's casts and land drops (CAST THIS GAME, LANDS PLAYED): **done (provenance PR)**, from
    `GameEventSpellAbilityCast` / `GameEventLandPlayed`, not split out of log sentences;
  - already engine calls, checked: castable, X up to N, legal attackers and blockers, unblocked damage,
    lethal damage in assignments, [sick], [tapped];
  - left: "(need 1 more B)" compares pips with the per-colour mana (shown only after the engine said no);
    the legacy profiles' `castHistory` still reads the log (kept for their bytes).
- Provenance: **done (provenance PR)**. Each block's header names its source (engine, Forge game log, card
  database, harness record, the seat's own notes), and the guide says so in one paragraph.

**B. `docs/harness/ARCHITECTURE.md`:** **Done 2026-10-15 (#183).** the whole harness in one document, each part with the reason it is
built that way.

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
    - Settled: `tool_choice` is "auto" on every round of an answer-tool ask (`AnswerTool.java`); "none"
      survives only on the text protocol. Measured on the 4 new-prompt Luna games
      (`runs/prompt-ab/new`): an ask's first round reads 54% of its prompt tokens from the cache, a
      follow-up round 92%.
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

   Read end to end in the Luna games since (`docs/harness/GAME-LOOP-LOG.md`).
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

   Second pass done 2026-10-15 (harness-epoch-11): the faithful mulligan and card-naming asks state
   facts in plain sentences (D1, C3). Still open: capitals in a few legacy-profile decision texts, which
   the faithful profile does not send.

5. **Measure the new prompts and tools.** First block done 2026-10-15 (`runs/prompt-ab/REPORT.md`): 4 Luna
   games per arm, the prompt the only difference.
   - The new prompt is about 30% smaller per call (6.6K against 9.4K tokens).
   - Full re-asks fell from 9 to 2, and nothing was taken or lost in either arm.
   - Wins were 2 of 4 against 0 of 4: not evidence at this size (p = 0.43).

   **Confounded (found 2026-10-15, `runs/luna-v8/g3`):** a yield passed the seat's own main phases until
   answer-tool-v9, so both arms lost land drops and plays to it (`runs/prompt-ab/*` up to 17 main phases a
   game, `runs/compare/*` up to 27; `match_review.py` now counts them as SKIP). This block, and the
   one-tier against two-tier comparison, must be re-run on the frozen measurement epoch (review E5) before
   any conclusion.

   Next: a larger block. The remaining prompt work (D1, card naming) is in (epoch 11). At about $0.02
   a Luna game, 20 games per arm is about $0.80; waiting on the owner's go-ahead to spend it.
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
   - **Built 2026-10-15** (answer-tool-v5): `keep_untapped` on the play answer.
   - **#111 fixed 2026-10-15** (#177): a tap part with exactly as many candidates as it needs takes them
     (Forge's own pick read a stale reserve and tapped the mana creature the payment then used), and
     whatever a tap part names is held while that ability's mana is paid.
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

13. The puzzle suite: landed (`feat/puzzle-suite-finish`, `eval/PUZZLES.md`,
    `eval/test_puzzle_suite.py`, `puzzle_solve.py` for a model run with `--budget-usd`).
    - Source: Forge's 368 built-in puzzles whose goal Forge enforces, read where Forge ships them
      (most are third-party works, so they are not copied).
    - Free baselines are running: random first (40 rollouts each), then Forge's AI, then the full
      arms on what either solves. Random solved 3 of the first 77 at least once, so the active suite
      will be small; a Forge AI solve is also accepted as the proof of solvability.
    - Found and fixed on the way: the baseline JVMs loaded stale classes (relative paths); the model
      seat was never told the puzzle's goal.
    - The first random pass hit the two-hour background limit at 285 of 368 and wrote nothing;
      `run` now saves after every puzzle and `--resume` continues a killed run.
    - Frozen 2026-10-16: `eval/suites/puzzles-forge-builtin-v1.json`, 34 puzzles of which 26 are active
      (the free arms pass, first, floor, random and ai were run on the 30 verified puzzles; 8 dropped).
    - Left: a small Luna run on the open split (cost first).
14. ~~Commit `runs/combo/`.~~ Done.

## Engine patches where the seat has no hook (owner approved 2026-10-15; built, `docs/harness/ENGINE-PATCHES.md`)

Owner: "can we think about editing the forge engine if needed to truly let agents have control". It can be
done without editing the Forge checkout: `install.sh` copies `forge-overlay/` over it, and the isolated
acceptance build puts the overlay's classes first, so a patched copy of an engine file at its own path in
the overlay replaces the original on both paths. The rules it would follow:
- only where a decision the seat should own has no controller hook, or an engine bug distorts play;
- the whole upstream file copied, the change marked `// HARNESS PATCH:`, kept minimal;
- `docs/harness/ENGINE-PATCHES.md` records each file with the sha256 of its upstream original at
  `FORGE_COMMIT`; a test fails when the pinned checkout's file differs, so a re-pin forces a re-diff;
- a golden check per patch, acceptance and the canaries, as for any change.

What it would be used for today (audited 2026-10-15): one decision. The colour an any-colour source (Lotus
Petal, LED) makes for a generic shard is set inside Forge's AI payment (`ComputerUtilMana`, forge-ai) before
the seat's colour question; it matters only to converge, sunburst and spent-colour checks. The engine's
`isAI()` branches were checked: none takes a decision from the seat (Spire colours are asked; a library
search picks one card at a time; the priority loop guard). Built with that one patch after the owner's
approval: `ComputerUtilMana`, golden check `theColourAnAnyColourSourceMakesForGenericIsTheSeats`.

## Formats beyond Legacy (owner, 2026-10-15: "start using modern standard and pauper decks")

Built: 36 real MTGO Challenge top-8 lists (7 Standard, 6 Modern, 6 Pauper, and since 2026-10-16 7 Legacy, 5 Pioneer,
5 Vintage, `eval/decks/SOURCES.md`), each legal by the pinned engine's format data and each played once on the random
arm (all load 60+15, finish, analyzer PASS). The Vintage decks found an engine crash: a declined companion (engine
patch, `docs/harness/ENGINE-PATCHES.md`).
Luna games on them through the game loop: g11-g13 so far (`GAME-LOOP-LOG.md`). **Owner decision** (review E1): the legality check finds three decks of the
Legacy eval pool illegal under the pinned engine's Legacy list: Miracles (Sensei's Divining Top), Painter (The
Fantasticar), UR Delver (Ragavan, Nimble Pilferer). Replacing them changes the pool's results (deck_sha).

## Priority 3: later

15. The first real scoring-panel run (cost stated first, small block).
16. ~~Compound actions (`PLAN_EXECUTE`).~~ **Settled 2026-10-16 (owner): off; it stays an opt-in measurement
    switch.** "The plan shouldnt be a set in stone set of decisions, obviously we must reevaluate at each part."
    Every vendor guide describes the same loop, in which the model sees the environment's result before each step:
    - Anthropic, *Building effective agents*: agents must "gain ground truth from the environment at each step";
      predefined paths (workflows) are for "well-defined tasks";
    - OpenAI, *A practical guide to building agents*: a run is "a loop that lets agents operate until an exit
      condition is reached", for "nuanced judgment, exceptions, or context-sensitive decisions";
    - Google, Gemini function calling: calls run "in sequence", each result sent back to the model;
    - xAI, tools overview: the model "decides what to do next", "processes results and continues".

    `PLAN_EXECUTE` casts the stated steps without asking again, and the harness decides when the plan is broken: a
    workflow, in a game of hidden information and an opponent acting in between. The plan stays a note (`set_plan`),
    shown at every decision and worded since answer-tool-v10 as "a note, not a commitment".
17. Choose the headline score.
18. Done 2026-10-15:
    - the Bradley-Terry deck-matchup term (#178);
    - a crashed game is played once more, its failed attempt set aside under `crashed/`
      (`run_batch.py --retries`);
    - DSML and other call markup written as text: not parsed, by the standard (no tool request is
      read out of prose). An answer-tool reply that calls nothing gets one reminder to call `answer`,
      and only one recorded game (DeepSeek) ever wrote such markup.
