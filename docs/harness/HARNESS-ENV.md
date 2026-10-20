# Building the bike: the harness as an RL environment, in the shape of GBA Eval

**Status: a proposal for the owner, nothing in it is built.** Design note, 2026-10-20. The question: is building this
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

Sources: the task prompt and ABI in the public repository ([TASK.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/quickstart/task/TASK.md),
[ABI.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/spec/ABI.md), [README](https://github.com/mechanize-work/gba-eval),
[grader.yaml](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/corpus/grader.yaml), the corpus README),
[Epoch AI's listing](https://epoch.ai/benchmarks/gbaeval), the [gbaeval.com blog index](https://gbaeval.com/blog),
[Mechanize's announcement post](https://x.com/MechanizeWork/status/2061469838292496717), an
[AlphaSignal write-up](https://alphasignal.ai/news/anthropic-s-claude-fable-5-beats-the-hardest-coding-benchmark-in-under)
and the [rl-list vendor entry](https://www.rl-list.com/vendors/mechanize).

**What the agent is given.** A Docker container with Rust 1.87 and the `wasm32` target, clang, cmake, python with numpy
and scipy; `spec/ABI.md` (ten C-ABI exports the wasm must implement: init, ROM buffer and load, reset, `emu_set_keys`,
`emu_run_frame`, a 240x160 framebuffer, audio buffer, sample count and rate); a 16 KiB clean-room BIOS stub; `gbatek.htm`,
the standard GBA hardware reference; `dev-roms/`, example ROMs; and an `oracle` CLI, "a reference emulator, with no
source code available", a black-box Mesen2 that runs any ROM with any input sequence. The task text: build an emulator
"whose video and audio output matches a reference emulator across a suite of real games", cycle-accurate, handling
button presses so games progress "past title screens, through menus, and into gameplay"; "keep working indefinitely".

**What is withheld or forbidden.** "No internet access. The test corpus and grading are offline." "No copying existing
emulators." "Do not use the oracle's output as the implementation." The graded corpus is not the dev ROMs: the corpus
README lists `roms/test/` (the open accuracy suites: armwrestler, fuzzarm, jsmolka, destoer, tonc, nba-hw, mgba-suite),
`roms/homebrew/` (about sixteen) and `roms/commercial/` as "local-only", with replays recorded as `<frame> <keys_hex>`
input logs; reference frames and per-replay thresholds are precomputed. Mesen2's source is not readable from the
container.

**How grading works.** `overall = 0.60 x replay + 0.20 x procedural + 0.20 x audio`. Replays: "real gameplay with button
presses, end-to-end behavior under input", frames compared against Mesen2 with per-replay thresholds; "idle-frame
correctness alone is not sufficient". Procedural: "CPU, memory, timers, DMA, deterministic self-checking ROMs", weighted
inside the section (cpu 0.35, memory 0.15, comprehensive 0.20, timer 0.12, dma 0.08, halt_irq 0.10). Audio: "per-frame
log-mel spectral distance vs Mesen2", half PSG and half game audio. The config notes that adding ROMs to a subsystem
"improves measurement precision without shifting other subsystems' weights". The harness has a `lockstep/` component,
candidate and reference stepped together. The GBA is deterministic (no clock, no analog input), so one input log has
one correct output.

**Why they think it measures capability.** The README: "we hope it gives a concrete sense of what we mean when we talk
about environments and grading"; Mechanize's essays call for replication tasks where "either the generated
implementation behaves identically to the reference, or it doesn't" and say the hard part is that "writing effective,
comprehensive tests is hard" ([GPT-3 moment for RL](https://www.mechanize.work/blog/the-upcoming-gpt-3-moment-for-rl/)).
A multi-week human project, a spec everyone has, a reference nobody can copy, and a grade that is a profile (gameplay,
CPU, memory, timers, DMA, audio), not one bit.

**Episodes.** 24 hours a model, checkpoints every 15 minutes with score trajectories, tokens and wall clock recorded;
attempt repositories published with commit histories and chat transcripts. Scores: GPT-5.5 0.532; Claude Opus 4.8 0.709
(passed GPT-5.5's 24-hour score "in under an hour"); Claude Fable 5 0.745 (gameplay 91.5%, audio 44.5%; passed Opus 4.8
in under two hours); Claude Opus 5 0.796; several open-weight models near zero. Grok Build 0.1 "hard-codes the exact
example ROMs to fake passing in its CPU implementation" and scored 0.024: the dev ROMs are not the graded ROMs, so the
hack bought nothing.

## 2. The mapping, point by point

| GBA Eval | Ours | Holds? |
|---|---|---|
| the emulator the agent writes | the harness: `PlayerControllerLLM` plus observation, prompt, tools, bridges, eval layer (`forge-overlay/`, `eval/`) | yes |
| the CPU and hardware spec (GBATEK, ABI.md, BIOS stub) | Forge's `PlayerController` interface (133 methods; 86 strategic, 15 payment in `controller-ownership.tsv`), the 34,086 card scripts, the comprehensive rules (`data/rules/MagicCompRules.txt`), and our S2 interface contract (CLI, rows, `[TALLY]`, wire protocol) | yes; our "GBATEK" is the engine's own source, which the agent may read, as GBATEK is read |
| the oracle: a black-box reference emulator with no source | an instrumented Forge plus our harness at a pinned epoch, reachable only as a service: "play this instance with this answer and tell me the end state" | yes, with the same rule: its output may be consulted, never used as the implementation |
| dev ROMs | the public development suite: the sweep's 553 named cards, the 28 gauntlet decks, the golden boards, h_0 and h_1 | yes |
| the hidden corpus: accuracy suites, homebrew, commercial local-only | the hidden surface instances (every decision kind x context, generated from card scripts with fresh seeds) and puzzle lines with `[solution]` | yes; Grok's lesson is why instances must be generated, not listed |
| procedural self-checking ROMs (cpu, memory, timer, dma, halt_irq), 20% | the per-surface deterministic checks: one hook in one context, asked / offered / shown / carried out / no parse; subsystem weights by hook family | yes, and this is our primary grader (section 3), weighted higher than theirs |
| gameplay replays under input, 60%, "idle-frame correctness is not sufficient" | full games through the candidate seat with a scripted or fake model (`random`, `first`, `pass` policies on the gauntlet decks and fresh seeds), compared in lockstep with the reference trace: every ask, every option list, every end state | yes; and the same warning: a seat that passes only on PASS is "idle-frame correct" |
| audio, 20%: a second output channel compared by a distance, not equality | the observation: what the seat is shown, compared by `AskContract` plus a fact check, not byte equality | partly; see where it breaks |
| per-replay thresholds, partial credit, a profile per subsystem | per-cell scores by hook and context, weighted by real-game frequency; the gates (`ai=0`, no silent drop, no parse) | yes |
| Mesen2 as the one reference | our harness at a pinned epoch as the one reference, with its known gaps in `MODEL-LIMITS.md`, `surface_gaps.txt` | yes, and the same risk: a reference bug becomes the standard |
| 24 hours, checkpoints every 15 minutes, transcripts published | days, not hours; milestone starts carved from our history; PR bodies as the transcript | the episode is longer (section 6) |

**Where the analogy breaks.**

1. **The harness has a second quality dimension the emulator lacks: what the model is shown.** An emulator's output is a
   frame; equality is the whole question. A seat's "output" to the engine is a legal object (checkable), but its output
   to the model is a prompt, and "showed what the decision turns on" is a judgement. GBA Eval's audio channel is the
   nearest analogue: graded by a distance with a threshold, not equality. Ours is `AskContract` (a lint: the DECISION
   line, the source's rules text, each option whose and at what cost, nothing cut, no engine id, numbered 1..n) plus a
   fact check (the objects the decision involves are named), and the hidden-state invariance test (two instances
   differing only in hidden state must produce identical requests). It is weaker than the other four checks and is
   weighted like audio, not like replays.
2. **Legality is the engine's, not the candidate's.** In GBA Eval the candidate can be wrong about the hardware; in
   ours the engine refuses illegal moves, so the candidate cannot play illegally. What it can do is hide a legal move,
   decide one itself, or hand one to Forge's AI. The surface test therefore checks the *menu* and the *asker*, where
   an emulator test checks the *result*.
3. **The reference is ours and is young.** Mesen2 is years of cycle-accuracy work; our reference is at epoch 69, with
   defects still being found weekly (three in g27, g28, g33 alone). A candidate that disagrees with the reference may be
   right. GBA Eval accepts this; we should keep the engine as the oracle of legality wherever possible, and use the
   reference seat only for the end-state diff.
4. **No single input log.** A GBA replay is one key sequence; a Magic game has two players and hidden information. The
   scripted answer must be chosen by engine identity, and the opponent must be a fixed arm.
5. **The spec is partly the engine's source.** GBATEK is a document; ours is the engine's Java, which the agent reads
   and which also tempts it to subclass Forge's AI controller (what we did). That is allowed; what is not is leaving a
   decision to it.

**What GBA Eval's choices imply for ours.**

- **Spec given, reference black-boxed, corpus withheld.** Give the spec (S0 to S2), give the engine, give a reference
  service, withhold the surface instances and the lines, and generate the hidden set fresh. Grok's 0.024 is the
  argument for generation over a list.
- **No copying.** GBA Eval forbids copying existing emulators and has nothing to copy from offline. We must withhold
  our own code and history (283 PRs with the fix for every defect in their bodies) and the public repository is the
  contamination risk for any model trained after today.
- **Partial credit as a profile.** Their leaderboard reads 91.5% gameplay against 44.5% audio for one model; ours
  should read per hook family and per context, so "maps combat but not costs" is a sentence the score can say.
- **Precision by adding cases without moving weights.** Their config note is the rule for our suite: more instances
  in a cell sharpen that cell; the weights come from real-game frequency and are versioned apart.
- **Checkpoints with trajectories.** A score every 15 minutes made "beat the previous best in under an hour" a
  statement. Our episodes should grade a sampled hundred surfaces at every commit (under a minute) and keep the curve.
- **Episode length.** 24 hours was enough for 0.8 of an emulator; our week was 490 commits. The task must be cut into
  milestone starts (section 6), or the first milestones only, to fit a day.

## 3. The primary grader: the surface test

**A surface** is one kind of engine decision in one context, instantiated as a minimal engine state from a card
script. The grader checks each instance deterministically with no human: the engine is the legality oracle, a scripted
model gives known answers, a reference seat gives the expected end state. This is GBA Eval's procedural suite, with
the weight of its replay suite.

### 3.1 Enumerating the space, from the engine

1. **Hooks**: `PlayerController` by reflection, classified by the ledger (133; 86 strategic, 15 payment). Proven today:
   the `surfaces` stage reaches 89 hooks with the model asked, `human_decisions.py` finds 0 `PlayerControllerHuman` asks
   unowned, `GoldenChecksTest.OWNED` names 77 seat decision types with one golden check each (159 tests) and the five
   `Surface*ChoicesTest` classes add 28.
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

- **Weights** from recorded play: this clone's `runs/` holds 79,691 decision rows (`play` 69%, `target` 7%, `attackers`
  4%, then `counter-type`, `replacement-order`, `sacrifice`, `mana-sources`, `choose-entity`, `cleanup-discard`,
  `damage-assign` at 1 to 2% each, a tail of forty-plus types under 1%); contexts weighted from the same rows
  (`play:alt:*`, `play:opt:*`, cost kinds). A floor weight keeps rare surfaces worth mapping; a weights file is
  versioned with the suite, and adding instances to a cell sharpens it without moving the others (GBA Eval's rule).
- **Score** = Σ weight x (five checks pass) / Σ weight over the hidden instances, reported per hook family and per
  context, **gated** by run-wide invariants: engine-side `ai=0`, no silent drop, no parse, no engine file changed
  unmarked.
- **Held-out**: the dev suite is public (553 sweep cards, 28 gauntlet decks, golden boards); the graded suite is drawn
  fresh per run (cards by feature, board parameters, seeds), ids unpublished, matched by engine identity.
- **Cost**: an instance is a bare game and one decision, milliseconds to a second; the sweep runs inside the
  `javatests` JVM today. A sampled hundred grades a commit in under a minute; thousands an hour in one JVM, against 15
  to 40 minutes for full acceptance plus 28 gauntlet games of up to five minutes.

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
  where cells compose (the states between plays the sweep cannot reach: combat, upkeep, death, end step). "Idle-frame
  correctness is not sufficient" reads here as: the `pass` policy proves nothing about plays, attacks or blocks, which
  the canary history already taught (`CLAUDE.md`: floor never reaches the play menu).
- **Lines**: `h_0`, `h_1` with `[solution]`, 534 manifest rows with prover `solution_actions` convertible to lines,
  Forge's puzzles. A line that plays exactly as written (no drift, every line played, `plays_not_made=0`) and reaches
  its `[acceptance]` state checks that surfaces compose and that the engine's ruling survived the harness. Today's
  puzzle runs reach 40 of 77 decision types and 453 of 526 rules puzzles are one or two actions; a secondary, cheap
  (seconds a line), already-proven suite.
- **The play benchmark** (STR-AI, PUZ with a real model, intervals beside floors) is the ride: scored last, only on a
  seat at the ceiling on surfaces, and reported as its own number.

## 5. The spec, the owner, the hacks

**Spec** (given): S0 the goal (`CLAUDE.md`), S1 the owner rules (`CLAUDE.md`, `STANDARDS.md` section 8's twenty rows),
S2 the interface contract (CLI environment, rows, `[TALLY]`, wire protocol, `.pzl` sections, what a seat may know,
tools, fallbacks: `ARCHITECTURE.md` with the "why" and the file names removed). **Withheld**: our code, 920 Java and 444
Python tests, the history, the game-loop log, acceptance records, canary pins, lines, generator seeds. **Reference**:
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

GBA Eval fits a day because an emulator has one spec and one output; our week was 490 commits on 64 merged PRs
(2026-10-15 to 2026-10-20) over three months of earlier work, and sessions of tens of millions of tokens against
Mechanize's 100k to 500k ([their own estimate](https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute)). So:

- **Milestone starts**, from our history (epochs 30 to 69 in nine days, ownership 13 to 0, `surfaces` 69 to 89): M0 a
  floor-vs-AI game writes rows; M1 an HTTP seat answers priority, attack, block and target surfaces and `garbage` never
  beats floor; M2 engine-side `ai=0` and no silent drop over the gauntlet; M3 tool-call answers only; M4 `AskContract`
  and invariance on every request; **M5 the surface suite**; M6 lookups and previews; M7 lines; M8 the eval layer; M9
  the paid play score. Each start is the commit that first passed gate k, tagged by epoch; each gate a frozen subset
  of the suite with a dense per-cell sub-score from the first commit (GBA Eval's 15-minute checkpoints).
- **PR replays** (SWE-Gym, [arXiv 2412.21139](https://arxiv.org/abs/2412.21139v1)): 283 candidates; 96 fix or feat
  commits touch the overlay and a test; tonight's #280, #282, #283 are one to six hours each. **Synthetic defects**
  (SWE-smith, [arXiv 2504.21798](https://arxiv.org/pdf/2504.21798)): mutate `PlayerControllerLLM`, `LlmCostDecision`,
  `AnswerTool`, keep the mutants a surface catches.
- **Pilot**: (1) build the generator and measure *our own* harness on a first suite of about 2,000 instances across the
  (hook, context) cells with corpus weights, inside the existing test JVM, about three days, free; the sweep's history
  says it will not score 1.0 (its first runs found an unpayable craft, a 999-times window, a StackOverflowError, Squad
  answered 1.2 billion). (2) Move the checks into a grader-side engine patch set and the script policy onto the wire;
  re-run through the wire. (3) Twenty PR replays graded by the suite. (4) One from-scratch M1 episode graded only by the
  instrumented engine and the suite's priority, attack, block and target cells: if M1 cannot be graded without our
  code, nothing later can. (5) Replays and lines as the end-to-end check, then about $0.30 of paid STR-AI games last.

## 7. What this says about our process now

- Build the surface generator and run it on ourselves first: it is a grader for an environment and a free, model-less
  gate for us; the sweep and the `surfaces` stage are two-thirds of it.
- Move the invariants into a grader-side engine patch set that does not trust the overlay it measures.
- Write the interface contract (S2) as its own document; it is the ABI.md of this bike.
- Keep a weights file from the corpus (`runs/` is uncommitted and partly gone), one decision file, the PR bodies'
  review rounds (the transcripts GBA Eval publishes), the acceptance `OUT=` directories (tonight's are in
  `/tmp/puzzle-scratch/acc/`), tokens and wall clock per PR (the checkpoint trajectory), and epoch tags (this clone is
  shallow, 490 commits since 2026-10-15).
- Withhold what will be withheld: generator seeds, the hidden suite, the lines and the private puzzle split
  (`BENCHMARK-V2.md` decision D) must leave the public repository before they are a grader.

## Sources

- GBA Eval: [github.com/mechanize-work/gba-eval](https://github.com/mechanize-work/gba-eval);
  [quickstart/task/TASK.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/quickstart/task/TASK.md);
  [spec/ABI.md](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/spec/ABI.md);
  [corpus/grader.yaml](https://raw.githubusercontent.com/mechanize-work/gba-eval/main/corpus/grader.yaml);
  [corpus](https://github.com/mechanize-work/gba-eval/tree/main/corpus), [harness](https://github.com/mechanize-work/gba-eval/tree/main/harness),
  [quickstart](https://github.com/mechanize-work/gba-eval/tree/main/quickstart); [gbaeval.com](https://gbaeval.com/) and its
  [blog index](https://gbaeval.com/blog) (posts: "Grok Build 0.1 (unsuccessfully) reward-hacks GBA Eval", "How gameplay
  replay scoring works", "What replay scores look like", "Iterating on the grading strategy", "How the environment is
  designed"; the posts render client-side and could not be fetched here); [Epoch AI listing](https://epoch.ai/benchmarks/gbaeval);
  [Mechanize announcement post](https://x.com/MechanizeWork/status/2061469838292496717) (via search; not fetched directly);
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
  `human_decisions.py`; `KeywordSurfaceSweepTest.java`, `GoldenChecksTest.OWNED`, `Surface*ChoicesTest`, `AskContract.java`,
  `ScriptBridge.java`, `LlmSpikeGui.SURFACE_REPORT`; `eval/puzzles/rules-pilot/manifest.jsonl`, `eval/puzzles/hard/`;
  PR bodies #270 to #283; `runs/` row counts; `forge-gui/res/cardsfolder` (34,086 scripts).
