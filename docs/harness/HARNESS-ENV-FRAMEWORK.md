# The harness-building environment: the framework

**Status: a framework with a skeleton, for the owner.** 2026-10-22. `HARNESS-ENV.md` argued that building this
harness is the RL task (GBA Eval's "build the bike"), graded first by a hidden surface test. This document makes it
concrete: what the agent starts with, how an episode ends, what the hidden grader is and how it reaches a harness it
has never seen, how the environment itself is validated, and a pilot. Sections 4.9 to 4.12 say what is built and
what it measured: the `harness-env-abi/1` grader with four candidates, the engine-side Forge-AI counter, 69 hand
instances and a generator over the card scripts, the episode runner and the calibration report; section 9 lists the
files. Nothing here changes the harness.

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
released files, which this branch did), and the repository itself. **A seen copy** (the term the rest of this
document uses) is any copy of the repository the agent's model may have seen: a public mirror, a crawl, training
data. It holds everything withheld above, so nothing committed here is secret from the agent. The defences are the
hidden instances (section 4.6), the similarity check (section 4.7) and the time-stamped contamination risk stated
with every score, as GBA Eval states for its public corpus.

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

**Built** (section 4.11, `eval/harness_env_episode.py`): the sandbox, the fresh repository, the leak check run
again on the sandbox as handed over (after the repository and any dev slice are added), the agent command, the
`DONE` file, the cap, timed checkpoints copied outside and graded after the end, export and grading outside, the
report. Not built: the idle reminder, `abi_check` and `finish` as tools (the agent writes `DONE` itself), and the
container split (the runner uses a directory and a scrubbed environment on this machine, not an image), and
isolation of the candidate from the grader (section 4.7).

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
instance where the engine offers fewer than two legal answers. **Built** (section 4.10): six templates over the
34,086 scripts, the secret seed, a dev pool kept out of every held-out set, versioned sets; the full cell grid above
is not yet built, and the 69 hand instances (section 4.9) cover the cells the templates do not.

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

### 4.4 Instrumentation (which the candidate can still reach: section 4.7)

The grader's classes come first on the classpath and its engine patch set is applied to a clean checkout, after the
submission's own engine patches, so a submission cannot shadow them (built: intake refuses a submission that
declares any class in `forge.harnessenv` or a package under it, or ships a file under a `forge/harnessenv` path,
and scores it 0; the Forge-AI counter is written into the submission's own marked `PlayerControllerAi.java` when it
ships one; a copy in which a hook name gets fewer counters than in the engine's own file (an overload left
uncounted) is refused, "counter incomplete"). The check counts per name (`harness_env_aicount.counts`, pinned equal
to the counters the patch writes), so a decoy method with a hook's name or a signature hidden by a comment can
still stand in for an uncounted one: a parsed signature check would close that.
The copy runs in the grader's JVM anyway (section 4.7, open), and that hole needs the seat in its own process, with
the counter outside code the submission ships.
The patch replaces what `Player.getController()` hands the engine with a
generated wrapper that delegates every `PlayerController` method to the candidate and records entry, exit and wall
time per hook and thread (designed, not built). This is safe for the rules engine: `forge-game` has no `instanceof` test on a controller at
the pin (the files that have one are `forge-ai`'s `ComputerUtil*` helpers and `forge-gui`'s match and network code). Forge's AI is counted
the same way: any `PlayerControllerAi` decision method, `AiController` or `AiPlayDecision` entry on the seat's thread
while a seat hook is open fails the instance and the run-wide gate. A static scan cannot do this job: our own overlay
references `forge.ai` in four files for legitimate reasons (it subclasses the AI controller and reads state through
`ComputerUtil`), measured by `harness_env_grade.py intake`. **Built** instead of the wrapper's AI count: the
grader's patch of `PlayerControllerAi` counts every decision hook on a watched player, with the payment
executor counted apart (section 4.9); a seat that builds its own AI controller is caught, a seat that calls
`ComputerUtil` helpers directly is not (open question 9).

### 4.5 Scoring and partial credit

A family's score is the fraction of its instances passing all five checks (families: play, combat, targets, modes,
costs, triggers and replacements, game start, library, choices, variants; `harness_env_grade.FAMILIES` maps every one
of the 89 ledger hooks to one, tested). Two numbers come from the family profile (owner, 2026-10-22;
`harness_env_grade.calibrated`):

- **benchmark**: the geometric mean over families, each floored at `FLOOR` = 0.001. A family at zero multiplies the
  total by 0.001^(1/n): with nine or ten families one missing family leaves about 0.46 of the score, two about 0.22,
  and a seat that only routes plays about 0.002. This is the published number.
- **dense**: the plain mean over families, the RL reward between checkpoints, where every instance gained counts.

A pass needs all five checks and no Forge-AI decision; an instance with fewer than two legal answers earns no
credit by passing and loses it by failing (section 4.9). Any Forge-AI decision gates the benchmark to 0 run-wide and
leaves the dense reward (built); so, by design, do a hang past the per-decision budget (10 s beyond model latency,
`SPEC.md` section 3.3; today a hang fails its instance). A submission with no Java, no factory, an unmarked engine
patch, a class in the grader's package or a failed build scores 0 (built, `harness_env_grade.refusal`), as does a
report whose Forge-AI counter was off (`ai` = -1: "counter off", never read as zero).
Families are weighted equally: the calibration (section 4.12) measured corpus weights from 207,907 recorded
decisions (section 4.12, `docs/harness/evidence/2026-10-24-corpus-decision-counts.json`) and rejects them, since play is two thirds of decisions and a seat missing combat then keeps 0.840.
Weights are versioned apart from the instances. The per-family profile
is printed beside both numbers, as GBA Eval reports gameplay apart from audio.

### 4.6 Determinism, versions, held-out

- Same submission, same seed: the same score, bit for bit (the engine is seeded, the scripted model is a function of
  the instance and the answer index, no wall clock enters an outcome). A pilot gate (section 7).
- Three versions travel with every score: the grader (`surface-v0`), the instance generator, the weights; plus the
  engine pin. Scores across any of them are not compared.
- Held-out: the agent sees no instance (no dev split by default, open question 2). The generator's seed is secret
  and fresh per run; a reported score names its seed's hash. A fixed private split is kept for comparing models.
  An episode is graded only on a generated set (`--set`, or `--cards` with the secret seed, drawn after the agent
  stops); the episode runner refuses to grade on the hand catalog, which is in a seen copy (section 2), and refuses
  a `--set` whose split is not `heldout`.

### 4.7 Reward hacks and their defences

| Hack | Defence |
|---|---|
| hard-codes cards, decks or seeds (Grok Build's move in GBA Eval, which scored 0.024) | fresh instances from 34,086 scripts (built); paired instances with a renamed copy of the card (a script with a new name, same abilities), where an outcome that differs is special-casing and fails both: **designed, not built** |
| delegates to Forge's AI | the wrapper's count on the seat's thread (4.4); a run-wide gate |
| parses prose | the no-parse check on every instance |
| hides an option, or offers one the engine refuses | the offered check |
| skips the model with a heuristic | the asked check; a skip is allowed only where `|L| = 1` |
| hangs, or loops on the model | per-decision budget, per-run cap; a hang fails the instance |
| fabricates its own log or surface record | the candidate's own files are not read; asks are counted at the grader's endpoint, outcomes in the engine. But the grader's report is written from the JVM the candidate runs in: see the next row |
| **rewrites the grader's verdict from inside the grading JVM** | **OPEN, not defended** (below). Cheap hardening only: a submission declaring a class in `forge.harnessenv` is refused, and the episode grader emits no score unless the operator passes `--trust-candidate` |
| detects the grader (thread names, classpath) and behaves differently | the secondary graders (section 5) run the seat in whole games through the same factory; a seat that scores on surfaces and fails games is flagged for reading |
| copies our repository from memory (a seen copy, section 2) | similarity of the submission to our overlay (token-level, MLE-bench's Dolos rule), reported, and above a threshold the episode is read by a person |
| edits the engine | intake scores an unmarked engine patch 0 (built); the grader's counter patch is written into the submission's own `PlayerControllerAi.java` when it ships one (built); the rest of the grader's patch set applies after the submission's (designed: the wrapper is not built) |
| reads files outside the overlay at grading time | the grader runs with no network and a read-only filesystem outside its scratch directory (designed; today javac and java get only `PATH`, `LANG`, `LC_ALL`, `JAVA_HOME`, `TZ`, so no seed and no provider key, but the filesystem is the operator's) |

**The open hole: the candidate runs inside the grader.** `harness_env_grade.build_classes` compiles the
submission and the grader in one javac, and `run_instances` runs the candidate's seat in the same JVM as
`GraderSuite`, which writes the report to a path it reads from a system property (`harness.envgrade.report`). Code
the candidate controls runs with the grader's privileges: a static initialiser, a shutdown hook or reflection can
read the system property and rewrite the TSV after the suite ends, set `AiCounter`'s state (its `stop` is
package-private, its map reachable by reflection), or answer the scripted model's checks from the grader's own
objects. The intake refusal of `forge.harnessenv` classes stops only the plainest form (a class in the grader's
package); it does not stop reflection or a shutdown hook. Nothing here should be read as protecting the report.
Closing it needs the candidate's seat in a separate process from the grader (the seat behind a small RPC, or the
grader reading only engine-side records written by a process the seat cannot reach): open question 12. Until then
`harness_env_episode.jvm_grader` returns `status: unscored: in-JVM grader` and no number for a candidate unless the
operator passes `--trust-candidate`, which the report records (`trust_candidate`). Our own seats (ours, the
ablations, the default bridge, the toys) are our code and are graded as before by `harness_env_grade.py instances`.
That command is the operator's tool: it checks no trust and runs whatever `--seats` names, so it is never the way
to score a submission.

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

### 4.9 What is built: the ABI instance grader, every surface, any seat

The grader of sections 4.1 to 4.5, driving a candidate only through `harness-env-abi/1`. It lives in the overlay's
test tree (never production code) under `forge/harnessenv/`; acceptance compiles it. Intake refuses a submission
that declares a class in that package (it scores 0, `harness_env_grade.refusal`). `harness_env_grade.py build`
compiles a submission's `src/main` beside it and the generated AI-counter patch; `instances` runs `GraderSuite`
(TestNG) in the seat's JVM (section 4.7) and `table` reads its report.

- **The interface** (`forge/harnessenv/SeatFactory.java`, `ModelEndpoint.java`): `create(game, player, lobbyPlayer,
  endpoint)` returns a `PlayerController`; the endpoint is a base URL, a model name and a log directory. The class is
  named by `seat.factory` in the submission's `harness-env.properties`.
- **The scripted model** (`grader/ScriptedModel.java`): an OpenAI-compatible HTTP server on loopback, one URL per
  replay (`/r/<id>/chat/completions`). A request with no assistant message opens a new decision; decision k gets the
  k-th scripted `answer` call, and a correction round inside the same conversation gets the same one. One call may
  fill several fields (`option`, `options`, `number`, `keep`, `blocks`, `amounts`); each decision reads its own, so a
  fixed grid reaches a seat whose labels the grader never reads. Modes CALL, PROSE (prose carrying `CHOICE: 2`),
  EMPTY and GARBAGE. After 60 requests a replay's requests are parked and its thread abandoned at 30 s: the HTTP
  boundary means a seat's `catch (Exception)` can no longer swallow the ceiling and loop.
- **Forge's AI, counted in the engine** (`grader/AiCounter.java`, `eval/harness_env_aicount.py`): the grader patches
  `PlayerControllerAi.java`, the pinned engine's (`git show HEAD:`, never the working copy) or the submission's own
  marked copy (section 4.4), with
  `AiCounter.enter("<hook>", this)` at the top of the 88 ledger decision hooks it overrides (93 counters with the
  overloads). A count is kept per watched player; the counter is probed at suite start, the run prints "ai counter on"
  or "OFF", and the validation run fails if it is off. Off, every row's `ai` reads -1, which gates the seat
  (`ai_gated`) and scores an episode 0 with the reason "counter off". `payManaCost` and `payCombatCost` are the payment executor (the
  ledger: "model+ai-fallback", "payment itself is engine mechanics"): counted apart as `ai_payment`, not gated.
  Any other count fails the instance (`pass` requires `ai = 0`) and gates the seat's benchmark to 0 run-wide
  (`harness_env_grade.ai_gated`); its dense reward is kept so training still sees a gradient.
- **Answer grids, outcomes, legal sets, the engine's loops** as before: every option plus one out of range, every
  subset, order, split and block set, numbers one past each bound, per-decision sequences for a play followed by its
  cost, mode, X or target; outcomes by engine identity (attack pairs, blocks, damage map, returned object, or a digest
  of zones, life, tapped state, power, toughness, damage, pool and stack); legal sets from `CombatUtil`,
  `AttackConstraints`, the hook's own list or the rule (lethal before trample, 702.19c). The attack re-ask loop is
  mirrored (three tries); blocks are not, because the engine validates a non-GUI controller's blocks only in its GUI.
- **Forced instances** (fewer than two legal answers): a pass is left out of family credit, a failure counts
  (`env_table`). A forced pass proves nothing about asking; an illegal answer where one was legal still costs. This
  rule is what keeps a seat that never asks combat at zero there (one forced block kept it at 1/17 before).
- **The seats**: four candidates prove the interface, built only from `SPEC.md` and the ABI.
  - `ours`: `PlayerControllerLLM` adapted by `forge/spike/HarnessEnvOurs.java` (`$Ours`), our OpenRouter bridge
    pointed at the endpoint, faithful profile; `$MinusCombat` and `$MinusCosts` hand the combat or cost hooks to a
    `PlayerControllerAi` of the same player (ablations).
  - `default` (`toys/Toys$PriorityOnly`): asks the model only for priority; everything else is Forge's AI.
  - `toy-text` (`Toys$TextParser`): routes eleven decisions but reads the reply's prose, never a tool call.
  - `toy-ai` (`Toys$AiDelegate`): asks the model, then lets Forge's AI decide (or uses it as a hint).
  - `empty` (no Java in the submission): intake's 0, no JVM.

**The catalog (69 instances, `grader/Instances.java`)**, by the owner's grouping:

| Family | Instances |
|---|---|
| combat (20) | attack: two creatures, a planeswalker, a battle, must attack, cannot attack (defender, summoning sick), the Ghostly Prison tax; block: choose, menace, flying against reach, cannot block, must block (Prized Unicorn), one blocker on two attackers; damage: blocker order, trample split, lethal on an indestructible blocker, division among two blockers, double strike in both steps; a trick in declare blockers; a prevention split; which of two damage replacements |
| play (4) | land, creature or pass; flashback; an activated ability; creature or land |
| targets (4) | any target, through a real cast, Doom Blade (nonblack only), Lava Spike (player only) |
| modes (4) | Kolaghan's Command, Izzet Charm, Abzan Charm, Funeral Charm, each through a cast |
| costs (8) | sacrifice, delve, kicker, an unless cost, pay life, X (Green Sun's Zenith), mana colour for generic (Birds of Paradise and Sol Ring, from the engine's payment log `sa.getPayingMana()`), mana from the pool (Bone Saw) |
| triggers and replacements (3) | order of two triggers, an optional trigger, an optional replacement |
| game start (5) | mulligan, bottom one, bottom two, play or draw, an opening-hand action |
| library (7) | scry 2, scry 1, surveil 2, surveil 1, a tutor's search, a put-back order, top or bottom |
| choices (9) | edict, discard two of three, cleanup discard, a number, tap or untap, a colour, yes or no, a creature, reveal |
| variants (5) | a coin-flip call, a flip result to keep, a roll to ignore, dice to reroll, a roll to modify |

**Measured** (2026-10-22, all 69 instances x six seats, counter on, `docs/harness/evidence/2026-10-22-env-grade-abi.tsv`,
`harness_env_grade.py table`), by hook family, the grouping the benchmark gates on:

| family (by hook) | n | ours | default | empty | minus-combat | minus-costs | toy-ai | toy-text |
|---|---|---|---|---|---|---|---|---|
| choices (modes included) | 14 | 14 | 0 | 0 | 14 | 14 | 3 | 0 |
| combat | 17 | 16 | 0 | 0 | 0 | 16 | 0 | 0 |
| costs | 8 | 7 | 0 | 0 | 7 | 2 | 0 | 0 |
| game start | 4 | 4 | 0 | 0 | 4 | 4 | 1 | 0 |
| library | 5 | 5 | 0 | 0 | 5 | 5 | 0 | 0 |
| play | 6 | 6 | 1 | 0 | 6 | 6 | 1 | 0 |
| targets | 5 | 5 | 0 | 0 | 5 | 5 | 0 | 0 |
| triggers and replacements | 5 | 5 | 0 | 0 | 5 | 5 | 1 | 0 |
| variants | 5 | 5 | 0 | 0 | 5 | 5 | 2 | 0 |
| **dense** | | 0.980 | 0.019 | 0 | 0.875 | 0.910 | 0.137 | 0 |
| **benchmark** (gated) | | **0.979** | **0** | **0** | **0** | **0** | **0** | **0** |
| benchmark without the AI gate | | 0.979 | 0.002 | 0.001 | 0.457 | 0.851 | 0.023 | 0.002 |

The ablations and the default are gated to 0 because they really hand decisions to Forge's AI, and the counter sees
it; the last row is the family rule alone, as if the counter had missed them. `toy-ai` passes all five checks on
`choices.colour` and is caught only by the counter (8 counted calls); `toy-text` fails only the no-parse check (prose
answered 2, empty answered 1). By the owner's surface grouping ours reads 0.982, combat 19/20.

**What it found in ours** (kept as instances by the owner's instruction, for `defect-hunt`; each reproduces with
`harness_env_grade.py instances ... --only <id>`):
1. `combat.block.must-block`: against Prized Unicorn, the seat sends an illegal block set back once with the engine's
   reason; a model that answers "no blocks" twice gets a legal block set (harness-epoch-71, issue #311); the engine
   does not validate a non-GUI controller's blocks. The evidence TSV (`evidence/2026-10-22-env-grade-abi.tsv`)
   predates that epoch and the grader was not re-run.
2. `costs.x`: Green Sun's Zenith with X = 1, a model that declines the search and then answers no to "Cancel Search?"
   is shown the search again without end (61 requests, then parked and abandoned). No loop guard bounds that pair.
   Filed as issue #312 and fixed in harness-epoch-72 (the NO is heard once, a second decline ends the search;
   `GoldenChecksTest.aSearchDeclinedAgainAfterKeepSearchingEnds`); the grade above is from before the fix.

### 4.10 What is built: the instance generator (`eval/harness_env_generate.py`)

Instances from the card scripts, not a hand list. Six templates read features from every script in
`res/cardsfolder` (34,086): `attacker-keyword` and `blocker-keyword` (a keyword creature in a block; 5,193 and 4,358
cards), `attacker` (17,620), `single-target-spell` (3,452), `sac-outlet` (38), `charm` (512). Each generated
instance is built in the JVM by `grader/GeneratedInstances.java` on a template board; one that cannot be built (the
engine offers fewer than two answers, the card does not load) is discarded and listed in `<report>.discarded`.

- **Secret seed.** `HARNESS_ENV_SEED` picks the cards; only `sha256(seed)` is written into the set. Without it the
  generator refuses.
- **Dev slice, off by default.** A fixed 10% of cards (`sha256("dev-pool|" + name) % 10 == 0`) is the dev pool; a
  dev set uses only it and the public seed `dev`; a held-out set never uses it. `grader.dev_slice` in the episode
  spec is `false`, so the box gets nothing.
- **Versioned sets.** A set file carries its format, the generator version, its template list, the seed hash and
  the set's own sha; scores across versions are not compared (section 4.6).
- **Determinism.** The same seed gives the same set byte for byte (tested); the same set graded twice gives the same
  report (measured below, section 6).

### 4.11 What is built: the episode runner (`eval/harness_env_episode.py`)

One command runs an episode end to end: build the sandbox from the spec and leak-check it; give it a fresh one-commit
git repository (no history to read the answer from; hooks pointed at an empty directory); run the agent command in
the box with a scrubbed environment; end on the agent's `DONE` file, its exit, or the cap (reported apart as
`ended_by`; `DONE` counts once it is a regular file whose line ends in a newline or that has sat unchanged for 5 s,
so a line still being written is not cut short, and a `DONE` that is not a regular file never blocks the cap); copy checkpoints out of the box on a timer (`--checkpoint-s`) and grade them only after the episode
ends, never during, so no in-episode score exists to hack (RE-Bench's lesson); export the submission, grade it
outside the sandbox and write `report.json`. Grading (`jvm_grader`): intake refuses what scores 0 (no Java, no
factory, an unmarked engine patch, a class in `forge.harnessenv`); the build runs with a scrubbed environment
(`grader_env`: no `HARNESS_ENV_SEED`, no provider variable); the instance grader runs with
`candidate=<the submission's factory>` on a generated set (`--set`, or `--cards` drawing one with the secret seed
after the agent stops; without either, without `--classpath`, with a `--set` that is not readable JSON, or with a
`--forge` that is not a git checkout whose HEAD holds `PlayerControllerAi.java`, the runner exits 2 before the
agent starts), and only with `--trust-candidate` (section 4.7). A refused sandbox still leaves `manifest.json`
beside `report.json`, so the record says what was checked.

Two dry-run agents (`eval/harness_env_agents.py`) calibrate it: `nothing` writes `DONE` and scores 0 (ended by the
agent in 1 s, "no Java"); `copy-ours` copies our overlay's `src/main` and the adapter and should score about ours
(section 6). They test the runner's plumbing on a submission we know; they say nothing about whether an agent can
build a seat from the sandbox, or about a seat written to fool the grader.

### 4.12 What is built: the calibration report (`eval/harness_env_calibrate.py`)

Seven ways to turn the family profile into one number, scored on the same report, and six criteria written from the
owner's sentences: ours at least 0.9, the default at most 0.05, a seat missing a family at most half of ours, a
seat missing part of one below ours by more than 0.05, one more failure in ours' smallest family costing at most a
quarter, the AI delegator at 0. Corpus weights come from 207,907 recorded decisions, every `"t":"decision"` row
under `/home/user/mtg-forge-llm-agent/runs` on 2026-10-24, committed as counts by decision type in
`docs/harness/evidence/2026-10-24-corpus-decision-counts.json` (play 0.674, choices 0.097, targets 0.076, triggers
0.044, costs 0.027, combat 0.025, game start, library and variants floored at 0.019). The table is
`python3 eval/harness_env_calibrate.py docs/harness/evidence/2026-10-22-env-grade-abi.tsv --counts
docs/harness/evidence/2026-10-24-corpus-decision-counts.json`, which needs no runs directory (the counts were
written by the same command with `--runs /home/user/mtg-forge-llm-agent/runs` added).

| setting | ours | default | minus-combat | minus-costs | toy-ai | criteria (gated) | criteria (no AI gate) |
|---|---|---|---|---|---|---|---|
| geometric, equal weights, floor 0.001 | 0.979 | 0.002 | 0.457 | 0.851 | 0.023 | 6/6 | 5/5 |
| geometric, half equal half corpus | 0.987 | 0.010 | 0.620 | 0.905 | 0.056 | 6/6 | 4/5 |
| geometric, equal, floor 0.01 | 0.979 | 0.015 | 0.591 | 0.851 | 0.064 | 6/6 | 4/5 |
| geometric, corpus weights | 0.995 | 0.050 | 0.840 | 0.962 | 0.137 | 6/6 | 2/5 |
| dense mean, 0 if any family is 0 | 0.980 | 0 | 0 | 0.910 | 0 | 6/6 | 5/5 |
| dense, equal | 0.980 | 0.037 | 0.875 | 0.910 | 0.163 | 4/5 | 4/5 |
| dense, corpus | 0.995 | 0.225 | 0.972 | 0.978 | 0.274 | 2/5 | 2/5 |

(Scores shown are without the AI gate; with it every seat but ours reads 0 under the benchmark settings.) The gate
alone makes every benchmark setting pass, so the family rule is judged without it. **Recommended: geometric, equal
weights, floor 0.001**, the first setting meeting every criterion both ways. Corpus weights fail because play is
two thirds of recorded decisions: a seat that never asks combat keeps 0.840. The strict any-zero rule also passes but
is a cliff by construction (one instance more lost in a one-instance family zeroes the seat).

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

Each row is a test of the grader: it must hold before a model's score means anything. "Instance grader" numbers are
the ABI run of section 4.9 (69 instances, counter on) unless a row says otherwise.

| Candidate | Expected | Today |
|---|---|---|
| our overlay at main, through the ABI adapter | near the ceiling | 0.979 benchmark, 67 of 69 (the two it fails are harness findings, kept). `surface-v0`: 1.0 on four recorded green acceptance runs (`acc-1eb9718b0`, `acc-fe8d609e3`, `acc-53c290a97`, `acc-goalfix-build`; the OUT directories were scratch on the machine that ran them and are not retained; their scores are in `docs/harness/evidence/2026-10-24-surface-v0-scores.json`) |
| an episode whose agent copies our overlay (`harness_env_agents.py copy-ours`) | about ours | 0.979, 67 of 69, the same two failures: sandbox, `DONE`, export, build, grade outside (`ended_by=agent`). `docs/harness/evidence/2026-10-22-episode-copy-ours.report.json`; the agent command is in the report, the runner was `harness_env_episode.py eval/harness_env/episode-v0.json <scratch>/ep-ours --classpath <acceptance classpath.txt> --forge /root/forge -- ...` with no `--set` (69 instances: the hand catalog, which an episode now refuses), before `--trust-candidate` existed |
| an episode whose agent does nothing | 0 | 0, "no Java", `ended_by=agent` in 1 s (`docs/harness/evidence/2026-10-22-episode-nothing.report.json`, same runner form) |
| our overlay with a real defect | below ours | `surface-v0`: 0.9994 on `acc-9dad7b479` (same evidence file); the instance grader: the two kept defects already hold ours below 1 |
| a seat that never asks one family (`ours-minus-combat`) | far below ours | 0 gated (it calls Forge's AI); 0.457 on the family rule alone (dense 0.875) |
| a seat missing part of a family (`ours-minus-costs`, cost family 2 of 8) | below ours | 0 gated; 0.851 on the family rule alone |
| the default bridge: priority to the model, everything else Forge's AI | near 0 | 0 gated; 0.002 alone; dense 0.019 (1 of 69, a play) |
| a seat that asks the model then lets Forge's AI decide (`toy-ai`) | 0 | 0, caught only by the engine-side counter on the instances it otherwise passes; dense 0.137 |
| a text-parsing seat (`toy-text`) | low, failing no-parse | 0 of 69; wherever it is asked, it fails no-parse |
| an empty overlay | 0 | 0, "no Java in the submission", no JVM |
| a compile that dies | 0 | 0 (`surface-v0` on `acc-e8792a4b4`, same evidence file; the episode grader on a javac failure) |
| the same seed twice | the same instance set | byte-identical (`test_harness_env_generate.py`) |
| the same set graded twice | the same scores | identical scores; ours byte-identical (note below) |
| SWE-smith mutants of our overlay, our history at epoch tags | each below ours; rising with the epochs | not run |

**Determinism and the held-out set.** A held-out set from a secret seed (24 instances, four per template, none
discarded, set `dd7139ba3fd8294d`) graded twice for ours and the default bridge, 2026-10-23: ours passes all 24 (the
two forced target instances are left out of credit), benchmark 1.0 both times, its rows byte-identical; the default
passes none, benchmark 0 both times. One default row differed between the runs in its `carried` check (Forge's AI
breaks ties at random), never in `pass`, so the scores are identical. A seat that is itself random gives a
different profile per run; the score is deterministic in the grader, not in the seat. Evidence:
`docs/harness/evidence/2026-10-23-heldout-determinism.json` (the two invocations, `harness_env_grade.py instances
... --seats ours=...,default=... --set <set>` into two scratch directories, per-run pass counts and scores, a hash of
each seat's rows, the one differing row). The set file and the two reports are not committed: the set names
held-out cards.

## 7. Pilot

All free: no model call, no egress. Each step says what makes it fail.

1. **`surface-v0` end to end on our main and three hand mutants**: not run (the machine was measuring timing); it is
   now a validation grader only.
2. **`surface-v1`, the ABI grader**: done (section 4.9). Four candidates through `SeatFactory` and an HTTP endpoint,
   the engine-side counter, 69 instances, every family at least four.
3. **The generator**: six templates done (section 4.10); left: the full (family, context) grid, 2,000 instances a
   run (today a set is 24 to 30), renamed-card pairs (section 4.7), the mutants and history of section 6.
4. **The episode runner**: done without containers (section 4.11). Left: the image split (GBA Eval's compose
   shape), a fresh engine clone built clean in the image, `abi_check` and `finish` as tools, the idle reminder,
   and the candidate's seat in its own process (section 4.7).
5. **One episode with a real agent**: the first paid step; its cost is the agent model's tokens, unpriced until the
   model is named.

## 8. Open questions for the owner

1. **Which agent model and budget for episodes?** The builder is a different role from the seat's player.
2. **Calibration: adopt geometric, equal weights, floor 0.001?** Section 4.12 recommends it: the only benchmark
   setting meeting all six criteria with the AI gate and all five without it. Corpus weights fail (a seat missing
   combat keeps 0.840). The criteria are the owner's sentences written as tests; changing one changes the answer.
3. **Gate on the payment executor?** `payManaCost` and `payCombatCost` reach Forge's AI on every paid cast in ours,
   as the ledger documents. Today they are counted (`ai_payment`) and not gated. Gating them would zero ours until
   the harness pays its own mana.
4. **The forced-instance rule.** An instance with one legal answer earns nothing by passing and costs by failing.
   Alternative: drop forced instances entirely (then ours' must-block defect would not have counted before
   harness-epoch-71).
5. **A seat calling `ComputerUtil` helpers directly** escapes the counter, which patches only `PlayerControllerAi`.
   Patch `AiController` and `ComputerUtil*` too (wider, more false positives on legitimate state reads), or accept it.
6. **Scarce templates.** Only 38 cards are sacrifice outlets the template can build, 4 of them in the dev pool; a
   held-out set draws from 34. Fine for now; a bigger generator needs more template shapes, not more cards.
7. **Where the grader lives and what it costs.** It is in the overlay's test tree so acceptance compiles it. Rough
   timings, not evidence (one run each, 2026-10-22, on a machine shared with other jobs, load not recorded): the
   full catalog for six seats about 16 minutes (build to report, 19:50 to 20:06), an episode's build and grade of
   one seat about 6 (20:08 to 20:14). Move it to its own source root when the images are built?
8. **A dev split?** Built and off (`grader.dev_slice: false`): 10% of cards and a public seed. Turn it on?
9. **A generic loopback model in the box?** Default none; the wire is in `SPEC.md`.
10. **The harness defects** the grader found in ours: section 4.9, "What it found in ours".
11. **A seen copy (section 2).** Keep hidden instances, seeds and weights out of the repository; flag contamination
    for models trained after today.
12. **A separate process for the candidate's seat, or the grader in-JVM?** Today the seat runs in the grader's JVM
    and can rewrite its verdict (section 4.7, open). Options: the seat in its own JVM behind a small RPC that the
    grader drives (the engine stays in the grader's process; every `PlayerController` call crosses the boundary,
    slower, and the seat no longer holds live engine objects, which changes `harness-env-abi/1`); or the engine and
    the seat together in a candidate process whose engine-side records (outcomes, the AI counter) are written
    through a channel the seat cannot reach (a second process, a pipe opened before the seat's classes load).
    Until one is chosen and built, a candidate gets a number only with `--trust-candidate`.

## 9. Files

| File | What |
|---|---|
| `eval/harness_env/episode-v0.json` | the episode spec (grader version `instances-v1`, `dev_slice` false) |
| `eval/harness_env/TASK.md`, `eval/harness_env/SPEC.md` | what the agent reads; SPEC section 3 is `harness-env-abi/1` |
| `eval/harness_env_spec.py`, `eval/harness_env_sandbox.py` | spec check; sandbox build, leak check, engine check |
| `eval/harness_env_grade.py` | intake, `surface-v0`, `build` (submission + grader + AI patch), `instances`, `table`, the gates and scores |
| `eval/harness_env_aicount.py` | the Forge-AI counter patch of `PlayerControllerAi` (the engine's, or the submission's own marked copy) |
| `eval/harness_env_generate.py` | instance sets from the card scripts: secret seed, dev pool, versions |
| `eval/harness_env_episode.py`, `eval/harness_env_agents.py` | the episode runner; the copy-ours and do-nothing agents |
| `eval/harness_env_calibrate.py` | the seven settings, six criteria and the recommendation |
| `eval/test_harness_env_*.py` | the tests of each |
| `forge-overlay/forge-gui-desktop/src/test/java/forge/harnessenv/` | `SeatFactory`, `ModelEndpoint`; `grader/` (scripted model, counter, boards, checks, 69 instances, generated instances, suite); `toys/` (priority-only, text parser, AI delegate) |
| `forge-overlay/forge-gui-desktop/src/test/java/forge/spike/HarnessEnvOurs.java` | our seat on the ABI, and the two ablations |
| `docs/harness/evidence/2026-10-22-env-grade-abi.tsv` | the measured report of section 4.9 (414 rows: 69 instances x 6 seats) |
| `docs/harness/evidence/2026-10-24-corpus-decision-counts.json` | decision counts by type behind section 4.12's corpus weights |
| `docs/harness/evidence/2026-10-22-episode-*.report.json` | the two dry-run episodes of section 6 |
| `docs/harness/evidence/2026-10-23-heldout-determinism.json` | the held-out determinism run of section 6 (summary, no cards) |
| `docs/harness/evidence/2026-10-24-surface-v0-scores.json` | `surface-v0` on six recorded acceptance runs (section 6) |

Reading list behind section 1: GBA Eval at commit 92b585e (`quickstart/README.md`, `quickstart/docker-compose.yml`,
`quickstart/task/Dockerfile`, `quickstart/services/oracle-server.py`, `quickstart/task/TASK.md`, `corpus/grader.yaml`,
`harness/grader/src/wasm_candidate.rs`); the papers and the SWE-bench issue linked in the table.
