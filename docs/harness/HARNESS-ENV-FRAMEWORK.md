# The harness-building environment: the framework

**Status: a framework with a skeleton, for the owner.** 2026-10-22. `HARNESS-ENV.md` argued that building this
harness is the RL task (GBA Eval's "build the bike"), graded first by a hidden surface test. This document makes it
concrete: what the agent starts with, how an episode ends, what the hidden grader is and how it reaches a harness it
has never seen, how the environment itself is validated, and a pilot. Section 9 lists what is built today
(`eval/harness_env*.py`, `eval/harness_env/`, and the instance grader in the overlay's test tree, section 4.9) and
what it measured. Nothing here changes the harness.

The owner's frame (2026-10-22): the agent builds the engine-to-model bridge; it may run anything for as long as it
likes; it may never check its work against the grading criteria (what "the perfect harness" does); the episode ends
only when the agent says it is finished, and then the hidden grader runs.

## 1. What comparable environments do, and the rule each gives us

| Environment | Agent's sandbox | Grader | Length, ending | In-episode signal | Rule for us |
|---|---|---|---|---|---|
| GBA Eval (`quickstart/`, `harness/`, commit 92b585e) | `task` container: toolchain, `spec/ABI.md`, GBATEK, five dev ROMs, `TASK.md`, a fresh git repo whose hooks dir is root-owned and empty; the reference emulator only as an HTTP sidecar ("no way to ptrace or process_vm_readv") | a separate grader image run on the wasm copied out (`grade.sh --from-container`); fuel budgets per frame | 24 h; "continue working towards this goal indefinitely"; checkpoints every 15 min to a private repo (internal, not shipped) | none from the grader; the oracle's output is open | the artifact leaves the box, the grader never enters it; an ABI is the only coupling; budgets stop a hang |
| SWE-bench | the repository at the base commit plus the issue | hidden FAIL_TO_PASS and PASS_TO_PASS tests applied to the patch in a fresh container | one patch | none | history leaks the answer: agents ran `git log --all` to read the future fix ([SWE-bench issue 465](https://github.com/SWE-bench/SWE-bench/issues/465)); ship a repository with no history |
| SWE-Gym, SWE-smith | executable repositories; SWE-smith mutates working code and keeps the mutants a test suite catches | the repo's own tests | per task | tests visible (training) | mutants of a good solution are free, graded instances of the task, and a test of the grader |
| PaperBench ([arXiv 2504.01848](https://arxiv.org/html/2504.01848v2)) | paper plus addendum, internet with the authors' code blacklisted (10 of 646 runs used a blacklisted resource and were zeroed) | `reproduce.sh` run "in a fresh VM" so "replication outputs can be distinguished from any results hard-coded by the candidate at task-time"; rubric leaves judged by a model, the judge itself validated (JudgeEval, F1 0.83) | 12 h; models "frequently finished early"; IterativeAgent removes the ability to end early | none | rebuild and run the submission outside the box; validate the grader against labelled cases; expect early finishes |
| RE-Bench ([arXiv 2411.15114](https://arxiv.org/html/2411.15114)) | 1 to 6 H100s, a scoring function the agent may run "at any time" | the best logged score | 8 h wall, pauses not counted | the score itself, which invited "impressive and unexpected loopholes" | an in-episode score is a target to hack; the owner's frame forbids it |
| MLE-bench ([arXiv 2410.07095](https://arxiv.org/html/2410.07095)) | data and a test set without labels | private labels; plagiarism check against public notebooks (Dolos, over 60% similarity disqualifies) | 24 h (100 h in a later run) | a validation server that "does not provide a score", only "an error message in the case of invalid submissions" | an in-episode check of *format* is safe; a check of *score* is not; compare submissions to public solutions |

## 2. The starting state

**Built by `eval/harness_env_sandbox.py build` from the episode spec `eval/harness_env/episode-v0.json`
(format `harness-env-episode/0`, checked by `eval/harness_env_spec.py`).** An allowlist, never a denylist: only
what a `copy` entry names enters the sandbox, each with its reason.

| Path in the sandbox | From | Why |
|---|---|---|
| `TASK.md` | `eval/harness_env/TASK.md` | the task statement |
| `spec/SPEC.md` | `eval/harness_env/SPEC.md` | the goal, the owner's rules (section 2 there), the interface the grader drives (section 3 there: `harness-env-abi/1`) |
| `spec/MagicCompRules.txt` | `data/rules/MagicCompRules.txt` | the comprehensive rules, as GBATEK is given |
| `spec/FORGE_COMMIT` | `FORGE_COMMIT` | the engine pin |
| `dev/decks/*.txt` | `eval/decks/*.txt` minus `hook-*` | decks for the agent's own games; the 20 hook probe decks are out because their names and plans say which hooks are hard |
| `overlay/`, `notes/` | made empty | the submission and the agent's own notes |
| `/task/forge` (mount) | a fresh clone of Forge at the pin, built once with the offline Maven repository, read-only | the engine and its source, which is the spec of what a controller is asked |

The released set today: 56 files, 52 of them decks (`build` on this branch). **Withheld**, by the spec's `withheld` globs: `.git`,
`forge-overlay/`, `eval/`, `docs/`, `.claude/`, `scripts/`, `runs/`, `datasets/`, `puzzles/`, `data/cards/`,
`CLAUDE.md`, `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `install.sh`, the launchers. That is our solution (the
overlay, its 42 test files, its four engine patches), the grading truth (the ownership ledger, the gap files, the
golden-check inventory, the acceptance stages and pins), the history (716 commits and 131 PR bodies since
2026-10-15, `HARNESS-ENV.md` section 6, which narrate every fix), and every document that describes the design.

**How "withheld" is proven** (`leak_problems`, five nets, each caught by a planted leak in
`eval/test_harness_env_sandbox.py`):
1. every file is in the manifest with the same bytes, and the agent's directories start empty;
2. no file has the bytes of any withheld repository file (a withheld file under another name);
3. no file holds a forbidden token: our class names (`PlayerControllerLLM`, `SeatObservation`, `AnswerTool`, ...),
   our markers, the repository's name (`mtg-forge-llm-agent`, which would point an agent with tools at the answer);
4. no path holds a forbidden fragment (`forge/spike`, the gap files) and nothing is a symlink;
5. a `.git`, if present, has one commit, no remote, no stash and no unreachable objects (a history squashed but not
   pruned still answers `git cat-file`).
`engine_problems` checks the engine: HEAD at the pin, `git status` clean, none of our overlay installed in a module's
`src` or `target`. Run on this machine's working checkout (`/root/forge`) it reports 12 problems: the four patched
engine files, the overlay's avatar sprite, `data/`, both launchers, and `forge/spike` under `src/main` and `src/test`. The
working checkout can never be the agent's engine; it must be a fresh clone built clean.

**Not provable by a scan:** a paraphrase of our design in a released document (the third net is reading the five
released files, which this branch did), and the public repository itself: any model trained after today may recall
it. The defences are the hidden instances (section 4.6), the similarity check (section 4.7) and the time-stamped
contamination risk stated with every score, as GBA Eval states for its public corpus.

**Tools, compute, network.** Shell, editor, JDK 17, Maven offline, python3, git (the fresh repository, hooks dir
root-owned and empty as in GBA Eval's `task/Dockerfile`). 8 CPUs, 32 GB, 60 GB disk (one game JVM is about 1 GB,
`.claude/rules/eval.md`). Network: loopback only. No provider key is in the box, so the agent cannot buy model calls
or leak state to `openrouter.ai`; its seat talks to a fake or scripted model it writes itself. Whether to give it a
generic loopback model is open question 3.

## 3. The episode: running, finishing, checkpoints

- **The end is the agent's.** It calls `finish` or writes `/task/DONE` with one line naming the commit it submits.
  The runner then stops the agent, snapshots `/task` at that commit, and nothing written after counts. There is no
  time pressure in the prompt (TASK.md: "Work as long as you want").
- **A cap that is a safety net, not a clock**: 72 hours wall (`caps.wall_hours`), three times GBA Eval's day, against
  our own week of 716 commits. At the cap the last checkpoint is graded (`done.on_cap: grade-last-checkpoint`) and the
  result is marked `ended_by=cap`, reported apart from `ended_by=agent`, never pooled.
- **An agent that never finishes**: idle 180 minutes (no tool call) gets one reminder that the episode ends only when
  it says so; idle again ends it as at the cap. A loop that keeps calling tools runs to the cap.
- **An agent that finishes early**: PaperBench's models "frequently finished early". The owner's frame keeps the
  choice the agent's; the runner records wall time and tokens used, and the finish is final. Asking "are you sure"
  once, with no information, is open question 4.
- **Checkpoints for dense reward without leaking the grader**: every 30 minutes (`caps.checkpoint_minutes`) the
  runner commits `/task` into a private mirror outside the box. The grader scores the checkpoints **after** the
  episode ends, so the curve (GBA Eval's score trajectory) and an RL dense reward (score deltas between checkpoints)
  exist, and the agent never sees one of them during the episode. The only in-episode check is MLE-bench's kind:
  `abi_check` (to build) answers "compiles over the engine, the factory loads, `create` returns a controller, one
  request reached the endpoint", or the first error; never a score, never an instance.

## 4. The hidden grader

### 4.1 The interface contract (`harness-env-abi/1`, `eval/harness_env/SPEC.md` section 3)

The agent implements `forge.harnessenv.SeatFactory.create(game, player, lobbyPlayer, ModelEndpoint)` returning a
`PlayerController`, named in `overlay/harness-env.properties`. Its seat speaks OpenAI-compatible chat completions to
`ModelEndpoint.baseUrl`, and every decision request offers a function `answer` with the field that decision needs
(`option`, `options`, `blocks`, `amounts`, `number`, `keep`, `card`; the same fields as our own fixed answer schema,
`AnswerTool.fixedDefinition`). That is all the grader assumes: no class names, no prompt format, no log format.
The grader builds games itself, installs the controller with `Player.dangerouslySetController` (as our tests do in
`LineFindingProbeTest.seat`), and serves the endpoint from inside the grading JVM.

### 4.2 Instances, generated from the card pool

An instance is (hook, context, card, zone, moment, board parameters, seed); its id is the sha256 of that tuple in
canonical JSON, so it regenerates anywhere. Hooks: `PlayerController` by reflection (112 `public abstract` methods at the pin)
intersected with the ledger's decision kinds (89 today, `surface_coverage.decision_hooks`). Contexts and situations
as `HARNESS-ENV.md` sections 3.1 and 3.2 enumerate them (the 44 ways to play, the cost parts, the asking `ApiType`s,
combat shapes, trigger and replacement counts, the sweep's four moments and five zones), boards from
`KeywordSurfaceSweepTest.board(Moment)` parameterised by the card's own cost. Each grading run draws a stratified
sample per (family, context) cell with a secret seed (`HARNESS_ENV_SEED`, never in the repository), discarding an
instance where the engine offers fewer than two legal answers.

### 4.3 Driving an unknown harness: enumerate and replay

The grader cannot read the candidate's option labels, and must not try (that would be the grader parsing text). It
needs none: for an instance whose engine-legal set is L, it runs the instance once per answer, `option = 1 .. |L|+1`
plus 0, each from the same seed, and records the engine outcome of each (the object chosen, then a digest of zones,
tapped state, counters, life, stack, pool and stack targets). Multi-answer hooks are sampled: the empty set, each
singleton, the full set, a random subset; blocks: none, each single pair, one full assignment; numbers: the bounds,
every value up to ten, out-of-range on both sides. Then the five checks, all by engine identity:

| Check | Pass when | Measured by |
|---|---|---|
| asked | `|L| >= 2` and at least one request reached the endpoint during the hook call; with `|L| = 1` none is required | the grader's endpoint, keyed to the hook on the stack (4.4) |
| offered | the set of outcomes of the in-range answers equals L, one answer per legal outcome; an out-of-range answer yields a correction then the fixed fallback, never a new outcome | the engine's own legality (`getAllPossibleAbilities`, `CombatUtil`, `CostPayment.canPayAdditionalCosts`), as the sweep computes it |
| carried out | the same answer twice gives the same outcome, and the outcome of `option = i` is the one whose card or target names the request text for `i` mentions | outcome digests; a name match, not a parse of a format |
| shown | the requests contain the source card's name and rules text, every legal option's card and target names, no `CARDNAME`, no engine id pattern, no filter syntax; and are byte-identical when only hidden state (the opponent's hand, the library order) changes | string presence over the request JSON; paired instances |
| no text parsing | replies of prose only ("I choose option 2"), empty, and garbage all give the same fixed fallback outcome; a call naming option 1 beside prose naming 2 gives option 1's outcome | outcomes |

Cost (estimate, not measured): an instance is a bare game and one decision, about 0.1 to 1 s; with ten replays a
2,000-instance run is about 20,000 replays, one to six hours in one JVM, under an hour on eight.

### 4.4 Instrumentation the candidate cannot touch

The grader's classes come first on the classpath and its engine patch set is applied to a clean checkout, after the
submission's own engine patches, so a submission cannot shadow them (one that ships a class in `forge.harnessenv` or
a grader package is rejected at intake). The patch replaces what `Player.getController()` hands the engine with a
generated wrapper that delegates every `PlayerController` method to the candidate and records entry, exit and wall
time per hook and thread. This is safe for the rules engine: `forge-game` has no `instanceof` test on a controller at
the pin (the files that have one are `forge-ai`'s `ComputerUtil*` helpers and `forge-gui`'s match and network code). Forge's AI is counted
the same way: any `PlayerControllerAi` decision method, `AiController` or `AiPlayDecision` entry on the seat's thread
while a seat hook is open fails the instance and the run-wide gate. A static scan cannot do this job: our own overlay
references `forge.ai` in four files for legitimate reasons (it subclasses the AI controller and reads state through
`ComputerUtil`), measured by `harness_env_grade.py intake`.

### 4.5 Scoring and partial credit

A family's score is the fraction of its instances passing all five checks (families: play, combat, targets, modes,
costs, triggers and replacements, game start, library, choices, variants; `harness_env_grade.FAMILIES` maps every one
of the 89 ledger hooks to one, tested). Two numbers come from the family profile (owner, 2026-10-22;
`harness_env_grade.calibrated`):

- **benchmark**: the geometric mean over families, each floored at `FLOOR` = 0.001. A family at zero multiplies the
  total by 0.001^(1/n): with nine or ten families one missing family leaves about 0.46 of the score, two about 0.22,
  and a seat that only routes plays about 0.002. This is the published number.
- **dense**: the plain mean over families, the RL reward between checkpoints, where every instance gained counts.

Both are gated to 0 by any Forge-AI decision, a hang past the per-decision budget (10 s beyond model latency,
`SPEC.md` section 3.3), an unmarked engine patch, or a rejected intake. Families are weighted equally until weights
from recorded play exist (`HARNESS-ENV.md` section 3.4), versioned apart from the instances. The per-family profile
is printed beside both numbers, as GBA Eval reports gameplay apart from audio.

### 4.6 Determinism, versions, held-out

- Same submission, same seed: the same score, bit for bit (the engine is seeded, the scripted model is a function of
  the instance and the answer index, no wall clock enters an outcome). A pilot gate (section 7).
- Three versions travel with every score: the grader (`surface-v0`), the instance generator, the weights; plus the
  engine pin. Scores across any of them are not compared.
- Held-out: the agent sees no instance (no dev split by default, open question 2). The generator's seed is secret
  and fresh per run; a reported score names its seed's hash. A fixed private split is kept for comparing models.

### 4.7 Reward hacks and their defences

| Hack | Defence |
|---|---|
| hard-codes cards, decks or seeds (Grok Build's move in GBA Eval, which scored 0.024) | fresh instances from 34,086 scripts; paired instances with a renamed copy of the card (a script with a new name, same abilities): an outcome that differs is special-casing and fails both |
| delegates to Forge's AI | the wrapper's count on the seat's thread (4.4); a run-wide gate |
| parses prose | the no-parse check on every instance |
| hides an option, or offers one the engine refuses | the offered check |
| skips the model with a heuristic | the asked check; a skip is allowed only where `|L| = 1` |
| hangs, or loops on the model | per-decision budget, per-run cap; a hang fails the instance |
| fabricates its own log or surface record | nothing the candidate writes is read; asks are counted at the grader's endpoint and wrapper |
| detects the grader (thread names, classpath) and behaves differently | the secondary graders (section 5) run the seat in whole games through the same factory; a seat that scores on surfaces and fails games is flagged for reading |
| copies our public repository from memory | similarity of the submission to our overlay (token-level, MLE-bench's Dolos rule), reported, and above a threshold the episode is read by a person |
| edits the engine | intake rejects an unmarked engine patch; the grader's patches apply after the submission's |
| reads files outside the overlay at grading time | the grader runs with no network and a read-only filesystem outside its scratch directory |

### 4.8 What is built: `surface-v0`, a validation grader

`eval/harness_env_grade.py` takes a submitted overlay in, assembles a grading workspace outside it (the grader's own
acceptance machinery, ledger, empty gap files, the submission's `src/main` only, and the hidden tests: the sweep, the
golden checks, the ten `Surface*Test` classes, `ControllerOwnershipTest`, `PromptFidelityTest`, `PlanAnytimeTest` and
the helpers they reference, 22 files), runs the acceptance stages `preflight classpath compile javatests surfaces`
there (opt-in `--run`, refused while a `--busy` marker exists), and scores the evidence: 0.5 hooks asked (mean over
families), 0.2 ways to play played, 0.3 hidden test classes passed (mean over classes), gated on a passing compile and
a proven zero count of silent Forge-AI hooks. The submission's own tests are dropped.

It is a validation grader only, for three reasons. Its hidden tests call our classes and read our prompt format, so
they compile only against our overlay, its mutants and its history; a `harness-env-abi/1` submission gets a null score
with that reason, not a 0. Its record of which hooks were asked comes from the candidate's own recorder
(`LlmSpikeGui.SURFACE_REPORT`). And our harness defines its tests, so ours scores 1 by construction (section 6).
`surface-v1` is sections 4.1 to 4.5.

### 4.9 What is built: the instance grader (`EnvGrader`), every surface, any seat

`forge-overlay/forge-gui-desktop/src/test/java/forge/spike/EnvGrader.java`, `EnvInstances.java` and
`EnvGraderSuite.java` are sections 4.3 and 4.5 run inside one JVM: the grader drives a seat only through
`PlayerController` and a scripted wire, so it grades ours, a naive bridge and ablations alike. It lives in the test
tree (never production code), named `Suite` so acceptance compiles it but does not run it; `harness_env_grade.py
instances` runs it (about ten minutes on this machine for 50 instances and four seats) and `table` reads its report.

- **The wire** (`EnvGrader.Wire`, an `LLMBridge` with function calling): a decision is a conversation; decision k is
  answered with the k-th scripted `answer` call, a correction round in the same conversation with the same one. One
  call may fill several fields (`option`, `options`, `number`, `blocks`, `amounts`, `keep`), and each decision reads
  its own; this is how a fixed answer grid reaches a seat whose labels the grader never reads (`SPEC.md` 3.2 says so).
  Prose (which carries `CHOICE: 2` and `PICKS: 2` lines), an empty reply and garbage are the no-parse replies. A replay
  that asks more than 60 times or runs 30 s ends with an `Error`, which a seat's `catch (Exception)` cannot swallow.
- **Answer grids**: every option 1..n plus one out of range; every subset; every order; every split of an amount;
  every set of (blocker, attacker) pairs; numbers from one below to one above the range; per-decision sequences for a
  play that is followed by its cost, mode, X or target.
- **Outcomes by engine identity**: the attack (`attacker->defender` pairs from the `Combat`), the blocks, the damage
  map, the returned card, number, colour or yes/no, or a digest of every zone, life, tapped state, power, toughness,
  damage, pool and the stack with its targets. **Legal sets from the engine**: attacks enumerated over
  `CombatUtil.getAllPossibleDefenders` and kept by `AttackConstraints.countViolations` against the best attack (what
  `CombatUtil.validateAttackers` tests), blocks added through `CombatUtil.canBlock` and kept by
  `CombatUtil.validateBlocks`; elsewhere the set the hook is handed, or the rule (lethal before trample, 702.19c). A
  play followed by more decisions states how many legal outcomes its decision has (`minDistinct`). A legal outcome
  written `?x` may be reached and need not be (cancelling a cast not yet committed).
- **The engine's own loops are mirrored**: an attack `validateAttackers` refuses is asked again (three tries, where
  `PhaseHandler` asks forever); blocks are not, because the engine checks a non-GUI controller's blocks only in its
  GUI (the comment on `GoldenChecksTest.aMenaceAttackerBlockedByOneCreatureIsSentBack`), so an illegal block stands
  in a game and fails here. Damage steps run as `PhaseHandler.onPhaseBegin` runs them: first strike, state-based
  actions, regular damage.
- **The seats**: `ours` (`PlayerControllerLLM` on the answer tool, faithful profile); `default`, a naive bridge
  (`PlayerControllerAi` with only `chooseSpellAbilityToPlay` routed to the wire: every ability it can pay for, and
  Forge's AI fills in targets as its cast path needs); `ours-minus-combat` and `ours-minus-costs`, ours with the
  combat hooks (attack, block, damage assignment, damage order, attack cost) or the cost hooks (cost decision maker,
  optional costs, unless costs, payments, delve, mana) handed to a `PlayerControllerAi`. `empty` (no harness) is
  intake's 0.

**The catalog (50 instances, `EnvInstances.all`)**, by the owner's grouping:

| Family | Instances |
|---|---|
| combat (20) | attack: choose among two creatures, a planeswalker, a battle, must attack (Bloodrock Cyclops), cannot attack (defender, summoning sick), the Ghostly Prison tax through the engine's declare step; block: choose blockers, menace, flying against reach, cannot block (Goblin Raider, tapped), must block (Prized Unicorn), one blocker on two attackers (Palace Guard); damage: order of blockers, a trampler's split, lethal by the rule on an indestructible blocker, free division among two blockers, double strike asked in both damage steps; a combat trick in the declare-blockers step; a prevention shield split; which of two damage replacements applies |
| play (1) | land, creature, pass or float mana |
| targets (2) | any target (players, creatures, cancel), a target through a real cast |
| modes (1) | Kolaghan's Command, two of four modes, through a cast |
| costs (6) | sacrifice as a cost, delve, kicker, an unless cost, pay life, X (Green Sun's Zenith, through the cast and the search) |
| triggers and replacements (3) | order of two simultaneous triggers, an optional trigger, an optional replacement |
| game start (3) | mulligan, the card put on the bottom, play or draw |
| library (4) | scry 2, surveil 2, a tutor's search, a put-back order |
| choices (9) | edict sacrifice, discard two of three, cleanup discard, a number, tap or untap, a colour, yes or no, a creature to choose, reveal from hand |
| variants (1) | a coin-flip call |

**Measured** (2026-10-22, `docs/harness/evidence/2026-10-22-env-grade.tsv`, read by `harness_env_grade.py table`):

| family (by hook) | instances | ours | default | empty | ours-minus-combat | ours-minus-costs |
|---|---|---|---|---|---|---|
| combat | 17 | 16/17 | 0/17 | 0/17 | 0/17 | 16/17 |
| costs | 6 | 5/6 | 0/6 | 0/6 | 5/6 | 1/6 |
| targets | 3 | 3/3 | 0/3 | 0/3 | 3/3 | 3/3 |
| choices (modes included) | 11 | 11/11 | 0/11 | 0/11 | 11/11 | 11/11 |
| triggers and replacements | 5 | 5/5 | 0/5 | 0/5 | 5/5 | 5/5 |
| game start | 3 | 3/3 | 0/3 | 0/3 | 3/3 | 3/3 |
| library | 2 | 2/2 | 0/2 | 0/2 | 2/2 | 2/2 |
| play | 2 | 2/2 | 1/2 | 0/2 | 2/2 | 2/2 |
| variants | 1 | 1/1 | 0/1 | 0/1 | 1/1 | 1/1 |
| **dense** (mean) | | 0.975 | 0.056 | 0 | 0.870 | 0.901 |
| **benchmark** (gated) | | **0.973** | **0.002** | **0** | **0.455** | **0.814** |

By the owner's surface grouping the same run reads combat 19/20 for ours, 0/20 default, 3/20 minus-combat (the trick,
the shield split and the replacement choice use no combat hook), costs 5/6, 0/6, 5/6, 1/6, benchmark 0.977, 0.002,
0, 0.812, 0.832. The default bridge earns its credit only where it routes: one of the two play instances (a land, a
creature or a pass; it loses the cast whose target Forge's AI picks). Ours earns combat (16 instances the default
fails), costs (5), choices and modes (11), triggers and replacements (5), targets (3), game start (3), library (2),
variants (1). `ours-minus-combat` loses exactly its 17 combat-hook instances and keeps the rest; `ours-minus-costs`
keeps one cost instance, the sacrifice, because our seat pays that cost inside its own play path
(`LlmCostDecision`), which overriding a `PlayerController` hook cannot reach: the cost ablation is partial, and an
external grader cannot ablate further.

**What it found in ours** (the two instances ours fails; both reproduce with
`harness_env_grade.py instances ... --only <id>`):
1. `combat.block.must-block`: against Prized Unicorn ("all creatures able to block it do so"), a model that answers
   "no blocks" twice gets "no blocks": the seat sends the illegal set back once with the reason, then takes its fixed
   fallback, which is no blocks, and the engine does not validate it. The fallback should be a legal block set.
2. `costs.x`: Green Sun's Zenith with X = 1, a model that declines the search and then answers no to the engine's
   "Cancel Search? Up to 1 more card(s) can be selected." is shown the search again, without end: the seat asked 61
   times in under a second. No loop guard bounds that pair (`MODEL-LIMITS.md` lists the kept guards).
Neither is fixed on this branch (a harness change, out of scope here); both are candidates for `defect-hunt`.

## 5. Secondary graders, and how the three combine

- **Lines** (the puzzle-gate's written solutions, `eval/puzzle_gen.py`, `scripts/puzzle-gate/`): each step of a
  written line is an engine-identity pick; the grader finds the candidate's answer index for it by the same
  enumerate-and-replay on a copy of the game, plays the line through the seat, and requires the `[acceptance]` state
  with every step played. Checks that the surfaces compose; seconds a line.
- **Short games against Forge's AI** with scripted policies over the wire (`first`, `random` by seed, `pass`,
  `empty`, `garbage`) on the released decks and fresh seeds: invariants, not lockstep (the game finishes, no
  crash or hang, engine-counted Forge-AI decisions 0, requests at least the engine's asks with two or more options,
  and `empty` and `garbage` never beat the `pass` floor, as the acceptance `gaming` stage requires of ours).
- **Combination**: overall = 0.70 surfaces + 0.15 lines + 0.15 games, each in [0, 1], under the shared gates.
  Surfaces carry the weight because each instance isolates one cell (GBA Eval weights its procedural suite 0.20; here
  it is the primary). The paid play score (STR-AI, PUZ with a real model) is the ride, never part of this number.

## 6. Validating the environment

Each row is a test of the grader: it must hold before a model's score means anything.

| Candidate | Expected | Today |
|---|---|---|
| our overlay at main | near the ceiling | instance grader: 0.973 benchmark, 48 of 50 instances (section 4.9; the two it fails are harness findings). `surface-v0`: 1.0 on four recorded green acceptance runs (`/tmp/puzzle-scratch/acc-1eb9718b0`, `acc-fe8d609e3`, `acc-53c290a97`, `acc-goalfix-build`, 2026-10-20), scored by `harness_env_grade.py score` |
| our overlay with a real defect | below ours | `surface-v0`: 0.9994 benchmark (0.9998 dense) on `acc-9dad7b479` (2 of 853 failed: `everyOwnedDecisionTypeIsExercisedHere`, a decision type the golden list lacked); strictly below, but barely |
| a seat that never asks one family | far below ours | instance grader, `ours-minus-combat`: 0.455 (dense 0.870). `surface-v0`, `acc-1eb9718b0` with the combat (9 hooks) or cost (15 hooks) asks removed: 0.464 benchmark, 0.944 dense. Before the per-family gate both read 0.944 |
| a seat missing part of a family | below ours, above the zero-family case | `ours-minus-costs`: 0.814 (cost family 1/6) |
| the default bridge: priority to the model, everything else Forge's AI | near 0, credit only in play | 0.002 benchmark, 0.056 dense; 1 of 50 instances (a play) |
| an empty overlay | 0 | 0.0 with "no Java in the submission", no JVM (`grade` on a built sandbox's `overlay/`) |
| a compile that dies | 0 | 0.0 on `acc-e8792a4b4` (javac killed, rc=143) |
| a text-parsing seat, a first-option-only seat | low on no-parse, offered | not built; the no-parse check passes every seat that is never asked (one outcome), so it only bites with the asked check beside it |
| SWE-smith mutants of our overlay (remove a menu kind, call `super` in a hook, swap the executed option, cut an option label, read a reply's prose) | each scores below ours; a survivor is equivalent or a grader gap, which becomes an instance family | `surface-v0` can run them (pilot step 1) |
| our history at epoch tags 4 to 70 | the score rises with the epochs that `surfaces` 69 to 89 records | `surface-v0` on each tag's overlay |
| the same submission twice, and on two seeds | identical; within the interval | `surface-v1` |

## 7. Pilot

All free: no model call, no egress. Each step says what makes it fail.

1. **`surface-v0` end to end on our main and three hand mutants** (`harness_env_grade.py grade forge-overlay <out>
   --forge <clean checkout> --run --busy /tmp/puzzle-scratch/ACCEPTANCE_RUNNING`): about 15 to 25 minutes a run
   (javatests ran 3m47s in `acc-1eb9718b0`, compile and classpath on top). Fails if ours scores below 0.99, if any mutant
   scores 1.0, or if a second run of ours differs. Not run on this branch: the machine was measuring timing.
2. **`surface-v1`**. Done in one JVM (section 4.9): enumerate-and-replay, the scripted wire, 50 hand-chosen instances
   over every family, four seats; ours 0.973, the default bridge 0.002, minus-combat 0.455. Left: the same through
   `SeatFactory` and an HTTP endpoint (the candidate's own bridge, not our `LLMBridge`); the delegating wrapper that
   counts Forge-AI calls engine-side (here a seat that hands a decision to the AI fails only because it is never
   asked); a text-parsing toy seat to prove the no-parse check bites; several instances per family (game start,
   library, variants and modes have one to four). About three days.
3. **The generator**: instances from card-script features, 2,000 a run, the secret seed, weights from a corpus; the
   mutants and the history of section 6 graded. About a week.
4. **The containers**: task, grader and runner split (GBA Eval's compose shape), the fresh engine clone, `abi_check`,
   the checkpoint mirror, `finish`. Fails if a planted leak in the image passes `leak_problems`.
5. **One episode**: an agent model the owner names builds from scratch for up to 72 hours. This is the first paid
   step; its cost is the agent model's tokens (GBA Eval's graded runs used 8.71M to 2.35B tokens,
   `HARNESS-ENV.md` section 6). Nothing here prices it until the model is named.

## 8. Open questions for the owner

1. **Which agent model and budget for episodes?** The game-model rule (Luna, `CLAUDE.md`) names the seat's player;
   the builder is a different role and, unless the owner says otherwise, needs its own decision.
2. **A dev split?** GBA Eval gives five dev ROMs that are not graded. A public dev slice of the surface test would
   let the agent check method, which the frame forbids as written. Default here: none.
3. **A generic loopback model in the box?** It would save the agent writing one and fix the wire it targets; it also
   hints at what the grader sends. Default here: none, the wire is specified in `SPEC.md`.
4. **One "are you sure" turn at `finish`?** It counters early finishes (PaperBench) without information; it also
   nudges. Default here: no.
5. **Calibration, done provisionally; two parameters are the owner's**: the benchmark is now a geometric mean over
   families with a floor (section 4.5), the additive mean is kept as the dense reward. A seat that never asks combat
   reads 0.455 instead of 0.944. Open: the floor (0.001 makes one missing family cost about half the score; 0.01
   would cost 40%), and whether families stay equal or take corpus weights (play 69% of recorded rows, combat about
   5%, which would make a missing combat family cheap again unless the floor is per family).
   Also small families: with one instance, one failure zeroes the family (the first run's modes instance did); each
   family needs several instances before its gate is fair.
6. **How much of the interface to fix.** The `answer` field names in `SPEC.md` are ours; fixing them makes grading
   possible without parsing, and also tells the agent one design choice we made. The alternative (the candidate
   declares its answer schema and the grader fills it) is more open and harder to validate.
7. **Owner decisions as a file** (`HARNESS-ENV.md` section 5, `DECISIONS.jsonl` behind `ask_owner`): in the box or not?
   This framework gives the rules in `SPEC.md` section 2 instead.
8. **The public repository.** Any later model may have read it. Keep the hidden instances and the weights out of it
   from now (`HARNESS-ENV.md` section 7), and decide whether a score from a model trained after today is reported
   with a contamination flag.

## 9. Files

| File | What |
|---|---|
| `eval/harness_env/episode-v0.json` | the episode spec: released files with reasons, withheld globs, forbidden tokens and path fragments, engine, agent, caps, done, submission, grader |
| `eval/harness_env/TASK.md`, `eval/harness_env/SPEC.md` | what the agent reads (released into the sandbox; scanned by the leak check) |
| `eval/harness_env_spec.py` | spec parser and consistency check (`test_harness_env_spec.py`, 14 tests) |
| `eval/harness_env_sandbox.py` | `build`, `check` (the five nets), `engine` (`test_harness_env_sandbox.py`, 18 tests, each leak planted) |
| `eval/harness_env_grade.py` | `intake`, `score` (any acceptance OUT), `grade` (assemble, run, score) for `surface-v0`; `instances` (run the instance grader) and `table` (its per-family table, both groupings, calibrated scores); `calibrated` (`test_harness_env_grade.py`, 34 tests) |
| `forge-overlay/.../test/java/forge/spike/EnvGrader.java`, `EnvInstances.java`, `EnvGraderSuite.java` | the instance grader: wire, seats, checks, answer grids, 50 instances; compiled by acceptance, run on demand |
| `docs/harness/evidence/2026-10-22-env-grade.tsv` | the measured report of section 4.9 (200 rows: 50 instances x 4 seats) |

Reading list behind section 1: GBA Eval at commit 92b585e (`quickstart/README.md`, `quickstart/docker-compose.yml`,
`quickstart/task/Dockerfile`, `quickstart/services/oracle-server.py`, `quickstart/task/TASK.md`, `corpus/grader.yaml`,
`harness/grader/src/wasm_candidate.rs`); the papers and the SWE-bench issue linked in the table.
