# Building the bike: the harness as an RL environment, in the shape of GBA Eval

**Status: a proposal for the owner.** Design note, 2026-10-20; its counts are as of that date. The framework that
makes it concrete, and what is built of it (the sandbox, the grader, the episode runner), are in
`HARNESS-ENV-FRAMEWORK.md`. The question: is building this
harness itself the interesting RL environment, the way Mechanize means it? This note answers with Mechanize's own
public instance, GBA Eval, mapped point by point onto our repository, and keeps the deterministic **surface test** as
the primary grader.

## 0. The frame: riding a bike versus building one

A model that plays Magic (rides) can win without knowing how the engine works: it reads a menu, picks a number. A
model that **builds the harness** (the bike: `PlayerControllerLLM` bridging every one of Forge's decisions to a seat)
must know exactly what the engine asks, when, with what options, what each answer does, and what a player is allowed
to see. Building is the stronger demonstration of mastery, and a model that can build the bike can also ride it:
the seat it built is the one it would then play through. The week we just spent is that task, and the question is how
to grade it without a human.

GBA Eval is the same shape. Nobody is asked to play Pokémon well; agents are asked to write the machine that runs
Pokémon, and the grade is whether the machine behaves like the reference on fixed inputs. The emulator is the bike.

## 1. GBA Eval, read closely

Every source is listed under [Sources](#sources).

**What the agent is given.** A Docker container with Rust 1.87 and the `wasm32-unknown-unknown` target, clang 14 with
`wasm-ld`, cmake, python3 with numpy and scipy; `spec/ABI.md` (ten C-ABI exports the wasm must implement: init, ROM
buffer and load, reset, `emu_set_keys`, `emu_run_frame`, a 240x160 framebuffer, audio buffer, sample count and rate);
a 16 KiB clean-room BIOS stub; `gbatek.htm`, the standard GBA hardware reference; `dev-roms/`, example ROMs; and an
`oracle` tool, which TASK.md describes as "a GBA emulator. You do not have access to the source code." (the grader's
reference is Mesen2, per the README). The task text: "Build a GBA emulator that matches a reference emulator's output
— both video and audio — across a suite of real games", fully cycle-accurate, handling input "so that games progress
past title screens, through menus, and into actual gameplay"; "You must continue working towards this goal
indefinitely."

**What is withheld or forbidden.** TASK.md: "Do not attempt to access the internet — the test corpus and grading
happen offline against the bundled reference, and pulling code from external sources is not what's being evaluated."
It names no rule against copying an emulator or the oracle's output; with no network and no oracle source there is
nothing in the container to copy. The graded corpus is not the dev ROMs, and it is not secret: `corpus/README.md`
lists `roms/test/` (the open accuracy suites: armwrestler, fuzzarm, jsmolka, destoer, tonc, nba-hw, mgba-suite) and
`roms/homebrew/` (sixteen games, three of them the dev ROMs "shipped in the agent task container; not in the grader
corpus") as "Force-added to git", and only `roms/commercial/` as "Never checked in (copyrighted)". Replays are
`<frame> <keys_hex>` input logs; reference frames and per-replay thresholds are precomputed. GBA Eval withholds the
corpus at the container, not by secrecy: the graded ROMs sit in a public repository, but the agent's container holds
only the dev ROMs and no internet beyond an egress allowlist (quickstart/README.md).

**How grading works.** `overall = 0.60 x replay + 0.20 x procedural + 0.20 x audio`. Replays: "Real gameplay with
button presses — end-to-end behavior under input" (README), frames compared against Mesen2 with per-replay thresholds;
TASK.md adds "Rendering a correct idle frame is not enough". Procedural: "CPU, memory, timers, DMA — deterministic
self-checking ROMs", weighted inside the section (cpu 0.35, memory 0.15, comprehensive 0.20, timer 0.12, dma 0.08,
halt_irq 0.10). Audio: "Per-frame log-mel spectral distance vs Mesen2", half PSG and half game audio. The config notes
that adding ROMs to a subsystem "improves measurement precision without shifting other subsystems' weights". The
harness has a `lockstep/` component, candidate and reference stepped together. The GBA is deterministic (no clock, no
analog input), so the graded runs are deterministic: a fixed ROM and input log under a deterministic reference.

**Why they think it measures capability.** The README: "we hope it gives a concrete sense of what we mean when we talk
about environments and grading"; Mechanize's essays call for replication tasks where "either the generated
implementation behaves identically to the reference, or it doesn't" and name the hard part: "Writing effective and
comprehensive tests remains a non-trivial task and demands substantial engineering effort"
([GPT-3 moment for RL](https://www.mechanize.work/blog/the-upcoming-gpt-3-moment-for-rl/)).
A multi-week human project, a spec everyone has, a reference nobody can copy, and a grade that is a profile (gameplay,
CPU, memory, timers, DMA, audio), not one bit.

**Episodes.** 24 hours a model, checkpoints every 15 minutes with score trajectories, tokens and wall clock recorded;
attempt repositories published with commit histories and chat transcripts. Scores: GPT-5.5 0.532; Claude Opus 4.8 0.709
(passed GPT-5.5's 24-hour score "in under an hour"); Claude Fable 5 0.745 (gameplay 91.5%, audio 44.5%; passed Opus 4.8
in under two hours). From the gbaeval.com leaderboard (`results/leaderboard.json`): Claude Opus 5 0.796; several open-weight
models near zero; Grok Build 0.1 "hard-codes the exact example ROMs to fake passing in its CPU implementation" and
scored 0.024: the dev ROMs are not the graded ROMs, so the hack bought nothing.

## 2. The mapping, point by point

| GBA Eval | Ours | Holds? |
|---|---|---|
| the emulator the agent writes | the harness: `PlayerControllerLLM` plus observation, prompt, tools, bridges, eval layer (`forge-overlay/`, `eval/`) | yes |
| the CPU and hardware spec (GBATEK, ABI.md, BIOS stub) | Forge's `PlayerController` interface (hook counts in section 3.1), the 34,086 card scripts, the comprehensive rules (`data/rules/MagicCompRules.txt`), and the proposed interface contract, SPEC-2 (CLI, rows, `[TALLY]`, wire protocol; not yet written) | yes; our "GBATEK" is the engine's own source, which the agent may read, as GBATEK is read |
| the oracle: a black-box reference emulator with no source | an instrumented Forge plus our harness at a pinned epoch, reachable only as a service: "play this instance with this answer and tell me the end state" | yes, with the same rule: its output may be consulted, never used as the implementation |
| dev ROMs | the public development suite: the sweep's 553 named cards, the 29 gauntlet decks, the golden boards, h_0 and h_1 | yes |
| the graded corpus: accuracy suites and homebrew force-added to git, commercial never checked in; none of it in the agent's container | the hidden surface instances (every decision kind x context, generated from card scripts with fresh seeds) and puzzle lines with `[solution]` | yes, withheld the same way, at the container; Grok's lesson is why instances must be generated, not listed |
| procedural self-checking ROMs (cpu, memory, timer, dma, halt_irq), 20% | the per-surface deterministic checks: one hook in one context, asked / offered / shown / carried out / no parse; subsystem weights by hook family | yes, and this is our primary grader (section 3), weighted higher than theirs |
| gameplay replays under input, 60% | full games through the candidate seat with a scripted or fake model (`random`, `first`, `pass` policies on the gauntlet decks and fresh seeds), compared in lockstep with the reference trace: every ask, every option list, every end state | yes; the idle-frame warning carries over (section 4) |
| audio, 20%: a second output channel compared by a distance, not equality | the observation: what the seat is shown, compared by `AskContract` plus a fact check, not byte equality | partly; see where it breaks |
| per-replay thresholds, partial credit, a profile per subsystem | per-cell scores by hook and context, weighted by real-game frequency; the gates (`ai=0`, no silent drop, no parse) | yes |
| Mesen2 as the one reference | our harness at a pinned epoch as the one reference, with its known gaps in `MODEL-LIMITS.md`, `surface_gaps.txt` | yes, and the same risk: a reference bug becomes the standard |
| 24 hours, checkpoints every 15 minutes, transcripts published | days, not hours; milestone starts carved from our history; PR bodies as the transcript | the episode is longer (section 6) |

**Where the analogy breaks.**

1. **The harness has a second quality dimension the emulator lacks: what the model is shown.** An emulator's output is a
   frame; equality is the whole question. A seat's "output" to the engine is a legal object (checkable), but its output
   to the model is a prompt, and "showed what the decision turns on" is a judgement. GBA Eval's audio channel is the
   nearest analogue: graded by a distance with a threshold, not equality. Ours is the "shown" check of section 3.3.
   It is weaker than the other four checks and is weighted like audio, not like replays.
2. **Legality is the engine's, not the candidate's.** In GBA Eval the candidate can be wrong about the hardware; in
   ours the engine refuses illegal moves, so the candidate cannot play illegally. What it can do is hide a legal move,
   decide one itself, or hand one to Forge's AI. The surface test therefore checks the *menu* and the *asker*, where
   an emulator test checks the *result*.
3. **The reference is ours and is young.** Mesen2 is years of cycle-accuracy work; our reference is at epoch 70 (main,
   PR #284), with defects still being found weekly (three in g27, g28, g33 alone). A candidate that disagrees with the
   reference may be right. GBA Eval accepts this; we should keep the engine as the oracle of legality wherever
   possible, and use the reference seat only for the end-state diff.
4. **No single input log.** A GBA replay is one key sequence; a Magic game has two players and hidden information. The
   scripted answer must be chosen by engine identity, and the opponent must be a fixed arm.
5. **The spec is partly the engine's source.** GBATEK is a document; ours is the engine's Java, which the agent reads
   and which also tempts it to subclass Forge's AI controller (what we did). That is allowed; what is not is leaving a
   decision to it.

**What GBA Eval's choices imply for ours.**

- **Spec given, reference black-boxed, corpus withheld.** Give the spec (SPEC-0 to SPEC-2), give the engine, give a
  reference service, withhold the surface instances and the lines, and generate the hidden set fresh.
- **Nothing to copy inside the container.** GBA Eval keeps the agent off the internet and the oracle's source out of
  reach; its corpus is public and absent only from the container. We must keep our own code and history (249 merged
  PRs, 248 into main, their bodies describing the fixes) out of the container the same way, and the public repository
  stays the contamination risk for any model trained after today, as GBA Eval's public corpus is for its own.
- **Partial credit as a profile.** Their leaderboard reads 91.5% gameplay against 44.5% audio for one model; ours
  should read per hook family and per context, so "maps combat but not costs" is a sentence the score can say.
- **Precision by adding cases without moving weights.** Their config note is the rule for our suite: more instances
  in a cell sharpen that cell; the weights come from real-game frequency and are versioned apart.
- **Checkpoints with trajectories.** A score every 15 minutes made "beat the previous best in under an hour" a
  statement. Our episodes should grade a sample at every commit and keep the curve (cost: section 3.4).
- **Episode length.** 24 hours was enough for three-quarters of an emulator; our week was far more than a day's work
  (section 6). The task must be cut into milestone starts, or the first milestones only, to fit a day.

## 3. The primary grader: the surface test

**A surface** is one kind of engine decision in one context, instantiated as a minimal engine state from a card
script. The grader checks each instance deterministically with no human: the engine is the legality oracle, a scripted
model gives known answers, a reference seat gives the expected end state. This is GBA Eval's procedural suite, with
the weight of its replay suite.

### 3.1 Enumerating the space, from the engine

1. **Hooks**: `PlayerController` by reflection, classified by the ledger (133; 86 strategic, 15 payment). Proven today:
   the `surfaces` stage reaches 89 hooks with the model asked, `human_decisions.py` finds 0 `PlayerControllerHuman` asks
   unowned, `GoldenChecksTest.OWNED` names 78 seat decision types with one golden check each (170 `@Test` methods in
   the class) and `Surface*ChoicesTest` and `SurfaceCombatSurfacesTest` add 28.
2. **Contexts**, from the engine's enumerations: `AlternativeCost` and `OptionalCost` (the 44 ways to play a card,
   `play_gaps.txt` empty since epoch 39); the `CostPart` subclasses (sacrifice, exile, tap-type, discard, return, pay
   life, remove counters, Phyrexian, convoke, delve, keyword counts); asking `ApiType`s (charm modes, dig piles, choose
   card, number, colour, type; repeat); `Announce$` and X; target counts, divided damage, copies and new targets; one or
   several simultaneous triggers; one or several replacement effects; combat shapes (n attackers, m blockers, trample,
   deathtouch, menace, must-attack, must-block, free division); mulligan depth; the sweep's four moments and five
   zones; mana floating or not.
3. **Situations**: the 34,086 card scripts, indexed by feature (`K:` keywords; `A:` lines with `Cost$`, `Announce$`,
   `Charm`, `Dig`, `TgtPrompt`, `ValidTgts`, `SubAbility`; `AILogic$` hints such as `AIPhyrexianPayment`, which is how
   PR #277's defect would be generated rather than found). An instance is (hook, context, card, zone, moment, board
   parameters, seed); its id is the hash, so it regenerates anywhere.

### 3.2 Instantiating without a human

`KeywordSurfaceSweepTest.board(Moment)` is the prototype: a bare game at a phase, twenty basics, creatures (one tapped,
one attacking unblocked in the combat moment), an artifact, a graveyard, libraries, one opposing creature, and the
card under test in the zone for the moment. The generator parameterises it from the card's own cost (a `Sac<1/Creature>`
gets creatures, delve a graveyard, X the lands to range over, an offering its type), lets the engine say what is legal
(`Card.getAllPossibleAbilities`, `CombatUtil.canAttack` and `canBlock`, `validateBlocks`, `legallyPayable`), and
discards an instance with nothing legal. Context shapes are templates: n attackers against m blockers with random
stats and a trampler for the division ask; two or three simultaneous triggers; two replacement effects; mana in the
pool for the floating window; a seven-card hand for the mulligan (the generated families `HARD-PUZZLES.md` section 3
proposes, used as surfaces). Hooks only an effect reaches are set up as `SurfaceEffectChoicesTest` and
`SurfaceResolutionChoicesTest` do today, with a scripted opponent or a spell put on the stack and resolved.

### 3.3 The scripted answer and the five checks

A scripted policy picks, per instance and seed, one legal option by its **engine identity** (card, ability, targets,
amounts, order), never by the candidate's label, and records it; over the wire it is a `script` policy beside
`eval/fake_model_server.py`'s `pass`, `first`, `random`, `empty`, `garbage`.

| Check | Decided by | Oracle |
|---|---|---|
| asked | the controller invocation for this hook on the seat's player was answered by a wire request, or by the candidate's own fixed fallback recorded `tier=auto` with a reason only where the owner rules allow (forced; PASS-only; the same spot passed) | the grader's **instrumented Forge**: every `PlayerController` call and every `PlayerControllerAi` decision method logged per player, grader classes first on the classpath under the candidate's overlay |
| offered | the request's options equal the engine's legal list: none missing, none extra, each cost and target set legal | `getAllPossibleAbilities` and the combat and payment tests on the same game object |
| shown | the request names the source card with its rules text, the decision line, each option whose and at what cost, the resource a number spends, the play it follows; nothing cut, no engine id, no `CARDNAME`, no filter syntax; identical under hidden-state changes | `AskContract` over the wire request plus the fact check; paired instances |
| carried out | after the scripted answer the state equals the reference seat's from the same instance and answer: zones, tapped, counters, life, stack, pool, targets on the stack item | state diff; the reference is our seat at a pinned epoch, or Forge's human play path driven directly for simple plays (the `lockstep` idea) |
| no text parsing | `garbage` and `empty` on the same instance: the decision must not move | wire log and the engine's record of what was chosen |

### 3.4 Coverage, weights, held-out, cost

- **Weights** from recorded play: a local clone's uncommitted `runs/` (the author's, on
  2026-10-20) holds 79,691 decision rows, counted as every `"t":"decision"` row of every `*.jsonl` under it (`play`
  69%, `target` 7%, `attackers` 4%, then `counter-type`, `replacement-order`, `sacrifice`, `mana-sources`,
  `choose-entity`, `cleanup-discard`, `damage-assign` at 1 to 2% each, a tail of forty-plus types under 1%). The rows
  pool every epoch they were played at, and some carry type names that predate `OWNED` (including
  `choose-entity`, `replacement-order`, `announce-x`, `combat-cost`, `discard-cost`, `return-cost`, `static-order`,
  `exile-cost`) and must be mapped onto it first.
  Contexts are weighted from the same rows (`play:alt:*`, `play:opt:*`, cost kinds). A floor weight keeps rare
  surfaces worth mapping; weights and instances are versioned apart (section 2).
- **Score** = Σ weight x (five checks pass) / Σ weight over the hidden instances, reported per hook family and per
  context, **gated** by run-wide invariants: engine-side `ai=0`, no silent drop, no parse, no engine file changed
  unmarked.
- **Held-out**: the dev suite is public (553 sweep cards, 29 gauntlet decks, golden boards); the graded suite is drawn
  fresh per run (cards by feature, board parameters, seeds), ids unpublished, matched by engine identity.
- **Cost**: an instance is a bare game and one decision, milliseconds to a second; the sweep runs inside the
  `javatests` stage today. A sampled hundred grades a commit in under a minute; thousands an hour in one JVM, against 8 to 15
  minutes for full acceptance on 4 cores, depending on load (`docs/harness/ACCEPTANCE.md`) plus 29 gauntlet games of up to five minutes.

### 3.5 Have and missing

| Have | Where | Missing |
|---|---|---|
| parity and playthrough per keyword, four moments, five zones, `FormatAnswerer`, `AskContract` on every ask | `KeywordSurfaceSweepTest` | runs in our JVM against our classes; needs the instrumented engine and the wire |
| 89 hooks and 44 play ways recorded from the call stack | `-Dharness.surface.report`, the `surfaces` stage | the recorder lives in `LlmSpikeGui`; move it to the grader's engine patch set |
| hook census | `controller-ownership.tsv`, `human_decisions.py` | weights from the corpus, not `legacy_reach` |
| one golden check per type; the contract on every ask | `GoldenChecksTest`, `Surface*ChoicesTest`, `PromptFidelityTest`, `AskContract` | boards drawn from card scripts by feature |
| scripted answers, drift never guessed, `plays_not_made` | `ScriptBridge`, `puzzle_baselines.py line` | answers by engine identity, served over HTTP |
| fake-model policies | `eval/fake_model_server.py` | a `script` policy keyed by instance id |
| rows, `[TALLY]`, analyzers | `defect_analyzer.py`, `match_review.py` | counts from the engine, not the rows |
| engine-patch discipline | `HARNESS PATCH`, `test_engine_patches.py` | the grader's own patch set over a clean checkout |
| generated families (proposed) | `HARD-PUZZLES.md` section 3 | built as templates |

## 4. The replay suite and the puzzles (secondary)

- **Replays = full games through the candidate seat** with a scripted or fake model on the gauntlet decks and fresh
  seeds, in lockstep with the reference trace: the sequence of asks, each option list, each end state. This is GBA
  Eval's 60%; here it is second to the surface suite because the surfaces already isolate each cell, and a game is
  where cells compose (the states between plays the sweep cannot reach: combat, upkeep, death, end step). TASK.md's
  "Rendering a correct idle frame is not enough" reads here as: the `pass` policy proves nothing about plays, attacks
  or blocks, which the canary history already taught (`CLAUDE.md`: floor never reaches the play menu).
- **Lines**: `h_0`, `h_1` with `[solution]`, 520 converted puzzles with prover `solution_actions`
  (`eval/puzzles/rules-pilot/manifest.jsonl`), Forge's puzzles. A line that plays exactly as written (no drift, every
  line played, `plays_not_made=0`) and reaches its `[acceptance]` state checks that surfaces compose and that the
  engine's ruling survived the harness. The puzzle rows in the same local `runs/puzzles` reach 30 of the 78 `OWNED`
  types (six more under pre-`OWNED` names), and most rules puzzles are one or two actions (`BENCHMARK-V2.md` section
  1); a secondary, cheap (seconds a line), already-proven suite.
- **The play benchmark** (STR-AI, PUZ with a real model, intervals beside floors) is the ride: scored last, only on a
  seat at the ceiling on surfaces, and reported as its own number.

## 5. The spec, the owner, the hacks

**Spec** (given): SPEC-0 the goal (`CLAUDE.md`), SPEC-1 the owner rules (`CLAUDE.md`, `STANDARDS.md` section 8's
twenty rows), SPEC-2 the interface contract, proposed and not yet written (CLI environment, rows, `[TALLY]`, wire
protocol, `.pzl` sections, what a seat may know, tools, fallbacks: `ARCHITECTURE.md` with the "why" and the file names
removed). **Withheld**: our code, its Java tests (924 at PR #284, `ACCEPTANCE.md`) and Python tests, the
history, the game-loop log, acceptance records, canary pins, lines, generator seeds. **Reference**:
instrumented Forge plus our seat at a pinned epoch as a service, consulted not copied.

**The owner** becomes a decision file behind an `ask_owner` tool (`DECISIONS.jsonl`: date, question, answer, rejected
alternatives, reason; the week's: Luna never Opus, egress, budgets, `ONE_TIER`, "a legal action is never hidden",
"only the model limits play quality", no `PLAN_EXECUTE`, the `BENCHMARK-V2.md` A to H queue), with unmatched questions
answered "your call, record it" and the log scored lightly; egress and money as affordances through a capped proxy.

**Hacks and defences** (every overlay-side invariant rebuilt in the engine or on the wire, because the agent owns the
overlay):

| Hack | Repo today | Environment |
|---|---|---|
| falls back to Forge's AI | `ai=0`, ownership test, analyzer gate | counted by the instrumented engine |
| hides a legal option | "never hidden", sweep parity | the offered check on every instance |
| a heuristic decides, windows skipped | SKIP rows, the 2% gate | engine ask count against wire requests; floating-mana and own-trigger windows are surfaces |
| edits the engine | overlay only, `HARNESS PATCH` | installed on a fresh checkout; unmarked diffs reject; grader classes first |
| hard-codes known cards or seeds (Grok's move) | canaries re-pinned with reasons | hidden instances regenerated per run; engine-identity matching |
| tests that cannot fail | "say what would make it fail" | the agent's tests earn nothing; a small mutation term |
| fabricated rows | `silent_drop_total` | wire log against rows and invocations |
| reads hidden state | observation invariance, viewer-aware tools | paired hidden-state instances |
| retrieves our code or history | — | withheld |
| parses prose | `[TEXT-PARSE]` guard | `garbage` on every instance |

## 6. Episodes, milestones, cost, pilot

GBA Eval fits a day because an emulator has one spec and one output; our week was 716 commits on main and 131 merged
PRs, #139 to #284 (since 2026-10-15, counted from the GitHub REST API by paging `commits?since=` and closed `pulls`, at
main 5778367), over three months of earlier work, and sessions of tens of millions of tokens (estimated, not measured:
tokens per session are not recorded, section 7). That is inside the range of GBA Eval's graded runs (leaderboard
`tokens_used`: 8.71M to 2.35B among graded runs that record tokens; Opus 5 8.71M, Fable 5 41.7M, Opus 4.8 57.1M, GPT-5.5
240.9M) and far above the 100k to 500k Mechanize gives as a generic per-task average ([their own
estimate](https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute)). So:

- **Milestone starts**, from our history (epoch 4 on 2026-10-15T04:06Z to epoch 70 on 2026-10-20, GitHub REST;
  REVIEW-2026-10-16 S3's 13 unproven hooks to 0; `surfaces` 69 to 89): M0 a floor-vs-AI game writes rows; M1 an HTTP
  seat answers priority, attack, block and target surfaces and `garbage` never beats floor; M2 engine-side `ai=0` and no
  silent drop over the gauntlet; M3 tool-call answers only; M4 `AskContract` and invariance on every request; **M5 the
  surface suite**; M6 lookups and previews; M7 lines; M8 the eval layer; M9 the paid play score. Each start is the
  commit that first passed gate k, tagged by epoch; each gate a frozen subset of the suite with a dense per-cell
  sub-score from the first commit (GBA Eval's 15-minute checkpoints).
- **PR replays** (SWE-Gym, [arXiv 2412.21139](https://arxiv.org/abs/2412.21139v1)): 249 merged PRs as candidates;
  today's #280, #282, #283 and #284 took 40 minutes to just over five hours each, first commit to merge (GitHub
  REST). **Synthetic defects** (SWE-smith, [arXiv 2504.21798](https://arxiv.org/pdf/2504.21798)): mutate
  `PlayerControllerLLM`, `LlmCostDecision`, `AnswerTool`, keep the mutants a surface catches.
- **Pilot**: (1) build the generator and measure *our own* harness on a first suite of about 2,000 instances across
  the (hook, context) cells with corpus weights, inside the existing test JVM, about three days, free; it will not
  score 1.0 (see the keyword gauntlet's first-run defects in `ARCHITECTURE.md` section 13, the keyword gauntlet). (2)
  Move the checks into a grader-side engine patch set and the script policy onto the wire; re-run through the wire.
  (3) Twenty PR replays graded by the suite. (4) One from-scratch M1 episode graded only by the instrumented engine
  and the suite's priority, attack, block and target cells: if M1 cannot be graded without our code, nothing later
  can. (5) Replays and lines as the end-to-end check, then about $0.30 of paid STR-AI games last.

## 7. What this says about our process now

- Build the surface generator and run it on ourselves first: it is a grader for an environment and a free, model-less
  gate for us; the sweep and the `surfaces` stage are two-thirds of it.
- Move the invariants into a grader-side engine patch set that does not trust the overlay it measures.
- Write the interface contract (SPEC-2) as its own document; it is the ABI.md of this bike.
- Keep a weights file from the corpus (`runs/` is uncommitted and partly gone), one decision file, the PR bodies'
  review rounds (the transcripts GBA Eval publishes), the acceptance `OUT=` directories (today's sit in
  `/tmp/puzzle-scratch/acc/` and are not kept, which is the reason to archive them), tokens and wall clock per PR (the
  checkpoint trajectory), and epoch tags (this clone is shallow; history counts come from the GitHub API).
- Withhold what will be withheld: generator seeds, the hidden suite, the lines and the private puzzle split
  (`BENCHMARK-V2.md` decision D) must leave the public repository before they are a grader.

## Sources

- GBA Eval: [github.com/mechanize-work/gba-eval](https://github.com/mechanize-work/gba-eval);
  [quickstart/task/TASK.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/quickstart/task/TASK.md);
  [spec/ABI.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/spec/ABI.md);
  [corpus/grader.yaml](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/corpus/grader.yaml);
  [corpus/README.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/corpus/README.md);
  [corpus](https://github.com/mechanize-work/gba-eval/tree/main/corpus), [harness](https://github.com/mechanize-work/gba-eval/tree/main/harness),
  [quickstart](https://github.com/mechanize-work/gba-eval/tree/main/quickstart); [gbaeval.com](https://gbaeval.com/) and its
  [blog index](https://gbaeval.com/blog) (posts: "Grok Build 0.1 (unsuccessfully) reward-hacks GBA Eval", "How gameplay
  replay scoring works", "What replay scores look like", "Iterating on the grading strategy", "How the environment is
  designed"); [Epoch AI listing](https://epoch.ai/benchmarks/gbaeval);
  [Mechanize announcement post](https://x.com/MechanizeWork/status/2061469838292496717);
  [AlphaSignal write-up](https://alphasignal.ai/news/anthropic-s-claude-fable-5-beats-the-hardest-coding-benchmark-in-under);
  [rl-list vendor entry](https://www.rl-list.com/vendors/mechanize).
- Mechanize essays: [The upcoming GPT-3 moment for RL](https://www.mechanize.work/blog/the-upcoming-gpt-3-moment-for-rl/);
  [How to fully automate software engineering](https://www.mechanize.work/blog/how-to-fully-automate-software-engineering);
  [Cheap RL tasks will waste compute](https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute);
  [Announcing Mechanize](https://www.mechanize.work/announcing-mechanize-inc/); [mechanize.work](https://mechanize.work).
- Related: [SWE-Gym](https://arxiv.org/abs/2412.21139v1); [SWE-smith](https://arxiv.org/pdf/2504.21798);
  [PaperBench](https://arxiv.org/html/2504.01848v2); [The Verification Horizon](https://arxiv.org/pdf/2606.26300);
  [TRACE](https://arxiv.org/html/2601.20103v1); [Prime Intellect on reward hacking](https://primeintellect.ai/rl-glossary/reward-hacking).
- This repository: `CLAUDE.md`; `docs/STANDARDS.md` section 8; `docs/harness/ARCHITECTURE.md`, `ACCEPTANCE.md`,
  `BENCHMARK-V2.md`, `HARD-PUZZLES.md`, `GAME-LOOP-LOG.md`, `controller-ownership.tsv`; `eval/harness_acceptance.sh`
  (`surfaces`), `defect_analyzer.py`, `match_review.py`, `fake_model_server.py`, `puzzle_baselines.py`,
  `human_decisions.py`; `KeywordSurfaceSweepTest.java`, `GoldenChecksTest.OWNED`, `Surface*ChoicesTest`,
  `SurfaceCombatSurfacesTest`, `AskContract.java`, `ScriptBridge.java`, `LlmSpikeGui.SURFACE_REPORT`;
  `eval/puzzles/rules-pilot/manifest.jsonl`, `eval/puzzles/hard/`; `VERSIONS.md`; PR bodies #270 to #284; the GitHub
  REST API (commit and PR counts); `runs/` row counts; `forge-gui/res/cardsfolder` (34,086 scripts).
