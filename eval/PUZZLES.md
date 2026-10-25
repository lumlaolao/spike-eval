# PUZ: the puzzle suite (how it is built, run and scored)

PUZ is the third headline score (`CLAUDE.md`). A puzzle is a hand-built position with a goal and a turn
limit; the solver either meets the goal or does not. **The rules engine decides, not us:** Forge's own
puzzle mode (`Puzzle.addGoalEnforcement`, unmodified at the pinned engine) puts a hidden "Puzzle Goal"
card in the solver's command zone that makes the solver lose at the limit unless the goal is met. No
answer key, rollout or scoring code of ours decides an attempt. `LlmSpikePuzzle` only reads the outcome.

This replaces the rollout-graded design in `EVAL-DESIGN.md` §3.3 for the suites built so far. Positions
mined from our own games (§3.1–3.2) join the same format later as `source.kind: "mined"`, with a goal
Forge enforces ("Win" or "Survive" by turn N).

## Files

| File | What it does |
|---|---|
| `eval/puzzle_format.py` | the record and suite format, validation, the split, `forge-candidates`, `freeze`, `check` |
| `eval/puzzle_baselines.py` | the free arms (random, pass, first, floor, Forge AI), reference lines, tiers, `verify` |
| `eval/puzzle_solve.py` | runs one model on one split of a frozen suite, k attempts per puzzle, with `--budget-usd` |
| `eval/puzzle_score.py` | scores one solver on one split, every number beside its floors |
| `eval/puzzle_watch.py` | reads a `puzzle_solve.py` run while it plays or after: per-puzzle state, cost and flags |
| `eval/test_puzzle_suite.py` | Python tests (no JVM) |
| `LlmSpikePuzzle.java` | runs one puzzle headless, many seeds per JVM (`PZL_SEEDS`), one result row each |
| `PuzzleArms.java` (+ `PuzzleArmsTest`) | the zero-model arms and the draw tape that makes a random rollout replayable |
| `eval/suites/puzzles-*.json` | frozen suites |

## A puzzle record (`puzzle.v1`)

- `id`, `split` and `status`:
  - the split is `sha256(id)`: about 30% are held out, and no one picks which;
  - the status is `active` (scored), `dropped` (measured and excluded, with `flags` saying why) or
    `candidate` (not yet measured, or not proven solvable).
- `goal`: Forge's goal and turn limit, read from the `.pzl`.
  - A goal Forge has no case for is refused, since Forge would silently enforce "win" instead.
  - So is a turn limit that can never fire.
- `pzl` and `pzl_sha256`: the position.
  - `forge:NAME.pzl` is a puzzle Forge ships (`forge-gui/res/puzzle` in the pinned checkout).
  - Most of those are third-party works (Possibility Storm, Gathering Magic, Pauper Puzzles and
    others), so they are read where Forge distributes them and not copied here.
  - The hash catches a checkout whose file differs.
- `baselines`: solves and attempts for each free arm: `random` (uniform over the same legal menus the
  model sees), `pass`, `first`, `floor`, `ai` (Forge's AI as the solver) and, when measured (`--arms greedy`; not in
  the frozen v1 suites, and not a default arm), `greedy` (the one-move-deep preview floor; `GreedyBridge`, see
  docs/harness/ARCHITECTURE.md), stored with the condition it ran under (`condition`: its env and the harness
  epoch; the record is refused without it). A puzzle `greedy` solves is one move deep.
- `reference_line`: the proof that the position is solvable in this engine. It is one of:
  - a random rollout's draw tape and seed, replayed by the `script` arm, which must not drift and must
    reach the goal;
  - when random never solves the puzzle, a Forge AI solve's seed and game-log hash, replayed by seed,
    which must reproduce the same log.
  - a rules puzzle's search proof (`arm: search`, below);
  - a generated board's own `[solution]` (`arm: written`, with the step count it was gated at), replayed as
    written against Forge's AI, which must play every line and solve (the hard suite, below).
- `verification`: the result of that replay, with the engine commit.
- `tier`:
  - 1: Forge's AI solves at least half its attempts;
  - 2: it does not, and the line takes three or fewer solver actions;
  - 3: four or more.

- `difficulty`, `tier_v2` and `skills` (additive, `docs/harness/BENCHMARK-V2.md` section 4), computed from the
  proof, never typed in (`puzzle_format.difficulty`, `tier_v2`, `skill_tags`; `puzzle_baselines.py retier`):
  - `L` solver actions; `D` the solver's search draws with two or more values and `B` the product of their sizes
    (capped at 10^12), so `B` is the size of the answer space the proof searched (an order over n items is n-1
    draws, a subset ask one draw per option), not the menus a player sees; `O` opponent choice points, counted
    only for a proof against the search opponent (null against Forge's AI); `T` turns played, from the puzzle's
    start turn (the next one when it starts in CLEANUP) and capped at its `Turns:` limit, null without the
    pzl's `Turns:` header or a real end turn; `board_objects` the cards in both players' battlefield, hand,
    graveyard and exile;
  - `tier_v2`: 3 when `L >= 6` or `O >= 2`; else 2 when `L >= 3`, `O >= 1` or `T >= 2`; else 1 when
    Forge's AI solves at least half, 2 when it does not; null without a proof, its turns, its opponent count or
    (for a short line) the AI's rate. Difficulty, `tier_v2` and `skills` are all null when the pzl cannot be read or
    is not the file `pzl_sha256` names, and `prove`, `run` and `retier` refuse such a record before anything runs.
    `B` is recorded but is not a tier-3 trigger until it counts the menus the seat is shown, not the search's
    draws (RP_035, one cast Forge's AI solves 4/4, read `B = 92160` from `puzzle_baselines.py prove --suite
    eval/suites/greedy-check-v0.json --ids rules-RP_035 --budget 600 --unique-budget 0 --write <copy>`, then
    `run --arms ai --baselines-only` and `retier` on the copy). The AI's rate that splits a short line between
    tiers 1 and 2 rests on its default seeds `1..4`, so 2/4 and 1/4 land in different tiers within one wide
    interval. The thresholds are the proposed ones, to be refit after the first full run;
  - `skills`: the tags that can be read off the source and the proof (rules application, sequencing, combat
    arithmetic, long lines, adversarial, multi-turn, restraint). Hidden information is not tagged.

An active puzzle needs the three floor baselines (random, pass, first), a verified line and a tier.
Random must solve it at most 20% of the time; anything easier measures luck and is dropped.

## Building a suite

```bash
STAGES="preflight classpath compile" bash eval/harness_acceptance.sh   # an isolated build
python3 eval/puzzle_format.py forge-candidates --suite forge-builtin --out runs/puzzles/candidates.json
python3 eval/puzzle_baselines.py run --suite runs/puzzles/candidates.json --rollouts 200 -j 4 --write runs/puzzles/measured.json
python3 eval/puzzle_format.py freeze runs/puzzles/measured.json --out eval/suites/puzzles-forge-builtin-v1.json
python3 eval/puzzle_format.py check eval/suites/puzzles-forge-builtin-v1.json
```

- Everything here is free: the baseline JVMs point the model at a closed loopback port and carry no
  API key.
- The compile stage writes `overlay-sha.txt` beside `classes/`: the hash of the overlay sources it compiled
  (`benchmark_version.overlay_sha_of_checkout`, the `overlay_sha` run_batch.py stamps), the same before and after
  javac or the stage fails. `puzzle_baselines.py` and `puzzle_solve.py` run only a build whose hash is this
  checkout's: `--build DIR` is refused when it is another or missing, and with no `--build` the newest matching
  build under `runs/harness-acceptance/` (by compile time, not name) is used, or none and the command to build one
  is printed. `--allow-stale-build` runs a named old build anyway (`ALLOW_STALE_BUILD=1` for
  `scripts/puzzle-gate/run_one.py`). The build is recorded in `build.json` in each JVM's run directory (build dir,
  its hash, the checkout's, whether it was stale) and by `puzzle_solve.py` in `puzzle-run.json` and each puzzle's
  `batch-manifest.json`, only there: a puzzle result row carries no overlay hash. `scripts/puzzle-gate/gen_gate.py`
  checks its `--build` the same way before playing any board and passes `--allow-stale-build` on.
- `run` can be split into stages with `--arms` and `--ids`, since baselines accumulate in the record.
- `run` writes the manifest after every puzzle. `--resume` with the same `--out-dir` and `--build`
  reuses each arm whose rows that build already wrote for exactly the requested seeds, so a killed run
  continues where it stopped. Rows from another build (another classes dir, or a `build.json` naming another
  compiled overlay or none) or a partial seed set are run again; `gen_gate.py --resume` reuses a line on the same
  terms.
- A frozen suite carries `suite_sha256`, computed over everything that defines a score: ids, file
  hashes, goals, splits, statuses, tiers, the random rate and the engine commit. Editing a frozen
  suite breaks the hash, and every tool then refuses it.
- `puzzle_baselines.py verify --suite S` replays every stored line. Run it after an engine bump: a
  puzzle whose line no longer reaches the goal is retired.

## Proving a puzzle against every opponent reply

```bash
python3 eval/puzzle_baselines.py prove --suite S.json [--ids a,b] --budget 1500 -j 4 --write S2.json
```

- Runs `CONTROLLER_A=search CONTROLLER_B=search` (`PuzzleSearch.java`): both seats choose from the legal
  menus through one shared line of choices, and the search finds a winning line, then beats every other
  opponent choice on it, recursively. The result is the record's `proof`.
- `proven`: a strategy that wins against every opponent choice the engine offers, each leaf a Forge-scored
  solve; the main line replays twice to the same game log. `not_found`: the budget ran out, never "unsolvable".
  `--opponent ai` proves against the Forge AI's replies only.
- A rollout stops after `PZL_SEARCH_MAX_DRAWS` choices (default 3000), outside its prefix only, and counts as
  not solved: a random completion can loop (an ability activated forever) until the wall-clock cap or the heap
  ends it, and is not drift (prove-v1 called PS_ELD1, PS_DOM0 and PS_M204 drift for this).
- A subtree whose every line has been played is never replayed (rollouts are deterministic given their line),
  and its solves are remembered.
- Not yet branched on: the engine seed (one seed per proof). The search arm branches over every split of combat
  damage and of a divided spell ("distribute 5 damage"); the other arms split evenly.
- Why: `BENCHMARK-V1.md`, "What a puzzle must pass before it is frozen".

## Proving a written line

```bash
python3 eval/puzzle_baselines.py line --pzl eval/puzzles/hard/h_1.pzl [--script L.txt] [--seeds 1..3]
```

- A puzzle's `[solution]` section (or `--script`) holds one line per decision in the words the seat is shown:
  `cast Lava Spike`, `opponent` (a target), `Desperate Ritual` (a splice pick), `Mountain; Mountain` (two picks),
  `Grizzly Bears -> Hill Giant` (a block), `none`, a number. A line names the option whose label starts with it;
  ` ... ` skips ahead in the label (`activate Tundra ... Add {U}`); `at [T2] PHASE: line` waits for that step.
  `untap: Mountain; Swamp` names the permanents to keep untapped when a payment asks; a handle after the label
  (`activate Smuggler's Copter @27`) tells twins apart; `0 6` (space-separated numbers) answers a damage or
  amount split; `yes` / `no` answer a confirm (`ScriptBridge.java` holds the full grammar).
  The section is in the file the solver never reads; it is a public answer, so a held-out puzzle keeps its line out
  of the public repository. The generated hard suites are the exception while the repository is private (owner,
  2026-10-21): their held-out boards carry their `[solution]` and `Rule:` lines, which must be stripped (and kept
  outside the repository) before it is made public.
- It is played by the model seat with `SMART_MODEL_A=script:<file>` (`ScriptBridge.java`) under the condition a
  paid run pins with the tools off, so the line meets the menus a model meets. A priority menu the next line does
  not fit is passed (the line waits); every other decision it does not fit is a drift, recorded on the row
  (`script_drift`), and the rest of the game is answered by the do-nothing option.
- Exit 0 only when every seed played every line (`script_played`) and solved. A drift can still solve (the
  do-nothing answers can stumble into the acceptance state), so the solve alone is never read. The verdict also
  refuses a row where a play the line chose did not happen (`plays_not_made`, counted by the seat; the list is on
  `PlayerControllerLLM.playsNotMade`); a cancel the line wrote is its own choice and is not counted.
- Forge's AI is the opponent by default: the line is then proven against its replies only. `--opponent search`
  runs the prover with the written line as the solver (`CONTROLLER_A=llm` on a `script:` spec, `CONTROLLER_B=search`):
  every draw on the search line is the opponent's, so `proven` means this one fixed line beats every reply the
  engine offers, and `not_found` names the reply that beats it (`refutation_line`, RP_141 with one attacker: the
  Persecutor blocks it; the verdict says whether the line lost or drifted under it). A reply under which the line
  drifts counts as beaten; one under which it wins before its last line does not. Uniqueness is not searched (the
  line has no solver choice on the search line to vary).
- `--minimality` replays the line with each action removed in turn and exits 0 only when every removal fails. An
  action is the line that opened it (a priority play, an attack, a block; the row's `script_actions`) and the lines
  after it; removing a line's only action plays the pass arm. A removal is replayed relaxed (`script:relaxed:<file>`):
  a decision the next line does not fit takes the do-nothing answer and the line keeps its place, so the rest is
  played as far as it goes. Only a game played out and lost counts as "fails"; a solve is "played" (the action is
  not needed), and a drift, the cap, a deck-out, a lost play or a line that stalled with lines unplayed (a play
  that can never fit again, as a source the removed action would have untapped) is "inconclusive". Against Forge's
  AI only, on the first seed. The record is `minimality.json` in the output directory, each removal with its verdict
  and its skipped and unplayed counts.

## Rules puzzles: an acceptance state and one solution

A rules puzzle (made from a question in `puzzles/modern/puzzles.jsonl`; triage in `eval/puzzles/rules/`) adds
an `[acceptance]` section to its `.pzl`: one condition per line on the final engine state, all of which must hold.
Forge ignores the section; `PuzzleAcceptance.java` reads it, and the puzzle is solved when the conditions hold
when the game ends (the Forge goal, usually `Survive`, still bounds the turns), whoever won.

```
[acceptance]
life p1 = 20                                       p0 is the solver, p1 the opponent
count p1 Graveyard "Grizzly Bears" = 1             exact card name, or * for any card
tokens p0 Battlefield "Goblin Token" = 2           tokens only (Forge names them "Goblin Token")
counters p0 Battlefield "Walking Ballista" P1P1 >= 3
winner p0                                          p0, p1 or none
```

A line it cannot read stops the run before any game. Result rows carry `acceptance` (accepted or rejected) and
`acceptance_failed` (each failed condition with what was there).

Two things the state does not say. When a player loses, Forge turns face up every face-down card that player owns
(`Game.onPlayerLost`, CR 707.9), so a name condition on a face-down permanent reads its real name whenever the solver
lost: condition on a counter or on `*` instead (RP_199). And setup counter names are case-sensitive with no warning:
`Counters:FLYING=1` makes a custom counter that grants nothing, where `Flying` is the keyword counter (RP_205).

**Uniqueness.** After a proof, a puzzle with an acceptance state (or any puzzle with `PZL_UNIQUE_BUDGET=N`) is
searched for a second solution: every other option at every solver choice on the main line, looking for a line
whose solver actions (its game-log actions) or solver choices the log does not show (a mode, a clone pick, a
number, a card named; card handles dropped) differ, and which is itself proven against every opponent reply. The
choices that make no second solution on their own are not compared either (`UNIQUENESS_NEUTRAL`: an order, a
scry or clash, a confirm, which source paid, ...), and a choice's label drops the object's current size, negative
sizes included (a target shown as "(5/5)" before Mystic Subdual and "(-1/1)" after is one choice), and so does a
planeswalker's current loyalty. The final state is deliberately not compared: the opponent's replies change it too
(RP_045's first check called a line where the opponent's Scavenging Ooze ate a card a second solution).
`uniqueness_difference` lists what differed.
`PZL_SEARCH_LINES` (default 32; 4 gave up on RP_034) bounds the lines tried per choice point.
`uniqueness`: `unique` (none found) or `multiple` (`uniqueness_second_line` and `uniqueness_second_actions`
show it); `uniqueness_exhausted` of `uniqueness_deviations` deviations were played out completely, and when all
were, "unique" is exhaustive rather than "not found within the budget". `uniqueness_unproven_alternatives`
counts other solving lines an opponent reply beat (not solutions). Smoke checks: `eval/puzzles/rules-pilot/
RP_SMOKE.pzl` (unique) and `RP_SMOKE2.pzl` (multiple).

### The rules suite

The converted rules puzzles (`eval/puzzles/rules-pilot/manifest.jsonl`, `result: converted`) are built the same way:

```bash
python3 eval/puzzle_format.py rules-candidates --suite rules --out runs/puzzles/rules-candidates.json
python3 eval/puzzle_baselines.py run --suite runs/puzzles/rules-candidates.json --rollouts 50 -j 2 --write runs/puzzles/rules-measured.json
python3 eval/puzzle_format.py freeze runs/puzzles/rules-measured.json --out eval/suites/puzzles-rules-v1.json
```

The full rules suite is not frozen yet. The frozen one is a 36-puzzle sample,
`eval/suites/puzzles-rules-sample-v1.json` (29 active, all on the open split; how it was built:
`eval/puzzles/rules-pilot/benchmark-v1/REPORT.md`).

A rules puzzle is solved when its acceptance state holds, so the arms and the scorer need nothing new. Its reference
line may also be its search proof (`arm: search`): when neither a random rollout nor Forge's AI solves it, the search
proves it again at the gate's budget (`SEARCH_LINE_BUDGET`, opponent `search`, no uniqueness search), and the
verification proves it once more, which must come out proven and accepted (on any line: a rollout capped under load
steers the search elsewhere).

### The generated hard suite

`eval/suites/puzzles-hard-gen-v1.json` holds boards drawn by `eval/puzzle_gen.py` (families and research:
`docs/harness/history/2026-10-20-generated-hard-puzzles.md`), each one turn long with a written line of 10 to 15 steps
and the opponent's life set to exactly what that line deals. `scripts/puzzle-gate/gen_gate.py` replays each line and
measures the floors; `scripts/puzzle-gate/gen_suite.py` freezes the result. A board is active only when its line
played as written and solved, and random (0 of 20), PASS, first option and greedy (0 of 4 each) all failed it and
Forge's AI solved it at most once in 4. Every other board the gate measured stays in the file as dropped, with
its reason.

```bash
FORGE_HOME=/root/forge python3 scripts/puzzle-gate/gen_gate.py --build B --forge /root/forge \
  --boards eval/puzzles/hard/gen --out runs/puzzles/hard-gen-gate -j 3
python3 scripts/puzzle-gate/gen_suite.py --gate runs/puzzles/hard-gen-gate --boards eval/puzzles/hard/gen \
  --out eval/suites/puzzles-hard-gen-v1.json
```

### The second hard suite

`eval/suites/puzzles-hard-gen2-v1.json` (236 boards in `eval/puzzles/hard/gen2/`; 156 open, 80 held out) raises the
bar: a line of 13 to 15 steps, the opponent's life the exact maximum of an exhaustive search over a model of the
board, no mana left unused at the end of the best line where the model can check it, and a trap: a natural plain
plan, searched to its own best, falls 1 to 3 short. Families and their traps:

| Family | Boards | Trap (the plain plan that falls short) |
|---|---|---|
| splice2 | 77 | never splice, or splice every payable card onto each Arcane spell |
| hoofdrain | 51 | sacrifice only the elves cast this turn |
| copy | 35 | copy only burn, never the ritual on the stack |
| grave | 35 | cast every mana spell first |
| drain2 | 21 | play the land first; cast the drainers last; sacrifice as soon as a death drains |
| atog | 15 | Atog eats every artifact; the Ironworks makes no mana |
| elf2 | 2 | cast Craterhoof as soon as payable; tap the mana creatures first |

Every board passed the gate (random 0 of 20; PASS, first, greedy and Forge's AI 0 of 4 each), each family on a build
of main 96caa9b's overlay (unchanged by this suite) with its family's generator, at harness-epoch-70; the gate's rows
are in `runs/puzzles/hard-gen2-gate`. "Maximum" means the maximum within the generator's model of the board: the gate
replays only the written line, so a line the engine allows and the model leaves out is not caught.
`eval/test_puzzle_gen.py` re-derives each committed splice2, drain2, elf2 and hoofdrain board's life, steps and trap
from its .pzl; copy, grave and atog are tested on freshly drawn boards only. Notes:
`docs/harness/history/2026-10-21-harder-splice.md`, `docs/harness/history/2026-10-22-harder-engines.md`,
`docs/harness/history/2026-10-20-generated-hard-puzzles.md` ("second set").

```bash
python3 scripts/puzzle-gate/gen_suite.py --name hard-gen2 --gate G1 G2 ... --boards eval/puzzles/hard/gen2 \
  --out eval/suites/puzzles-hard-gen2-v1.json
```

### The third hard suite

`eval/suites/puzzles-hard-gen3-v1.json` (68 boards in `eval/puzzles/hard/gen3/`; 44 open, 24 held out) adds four
families mechanically unlike the earlier ones, to the second suite's bar (13 to 15 steps, the goal the exact searched
maximum, no mana unused, the best plain plan 1 to 3 short):

| Family | Boards | Goal | Trap (the plain plans that fall short) |
|---|---|---|---|
| tokens | 24 | life | sacrifice a token whenever there is one; cast the payoffs as soon as the lands pay for them |
| landfall | 17 | life | play a land before anything else; cast each landfall creature as soon as it can be paid |
| equip | 15 | life, in one combat with no blockers | everything on the creature with the most power; cast every Equipment before equipping; Embercleave in the main phase; equip each Equipment at once |
| infect | 12 | the opponent's tenth poison counter (`p1counters=POISON=N`, `Goal:Win`, `winner p0`: acceptance has no poison condition) | Become Immense as soon as it can be paid; pump only in combat; proliferate only after combat; never proliferate; Inkmoth Nexus always attacks; the land first |

Every board passed the gate (random 0 of 20; PASS, first, greedy and Forge's AI 0 of 4 each, against a gate bar of
at most 1 of 4 for the AI); the rows are in `runs/puzzles/hard-gen3-gate` (six gate runs). Five tokens boards whose
written line did not play were left out of the folder before freezing. `eval/test_puzzle_gen.py` re-derives every
committed board from its `# search:` and `# trap:` lines, the written line included. Searches, line rules and limits:
`docs/harness/history/2026-10-22-infect-equip.md`, `docs/harness/history/2026-10-22-landfall-tokens.md`.

```bash
G=runs/puzzles/hard-gen3-gate
python3 scripts/puzzle-gate/gen_suite.py --name hard-gen3 --gate $G/gen3-gate-A $G/gen3-gate-B $G/gate-lf-a \
  $G/gate-b $G/gate-c $G/gate-d --boards eval/puzzles/hard/gen3 --out eval/suites/puzzles-hard-gen3-v1.json
```

## Running a model

```bash
python3 eval/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open \
    --model openrouter:openai/gpt-6-luna --k 4 --budget-usd 1.00 --out runs/puzzles/luna-open
python3 eval/puzzle_score.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open runs/puzzles/luna-open
```

- Each attempt is a fresh game on seeds 1..k: the same seeds for every solver, so attempts differ only
  in the opponent AI's and the engine's random choices.
- A run stopped part way (a container restart) continues with the same command plus `--resume`: a puzzle whose
  k attempts on this suite are already in `--out` is skipped; one cut off part way is played again from seed 1,
  its rows so far and its JVM log renamed `puzzle-results.partial<n>.jsonl` and `jvm.partial<n>.log` (never
  scored, still priced). A resume into a directory made under another model, suite or seat condition, or on
  another build (`puzzle_solve.resume_conflicts` lists what counts), is refused.
- The seat is the one the games use:
  - the faithful profile, with the answer tool;
  - no lookup tools unless `--memory-tools` is given, and the rules lookup only with `--memory-tools --rules-tool`
    (`--rules-tool` alone is refused: the seat would not offer it);
  - no harness shortcuts;
  - every setting pinned explicitly and written to each puzzle's `batch-manifest.json`.
- The system prompt states the puzzle's brief (`LlmSpikePuzzle.seatBrief`: goal, turn limit and anything the
  solver is told, such as the top cards of the library; never the name or difficulty label), in place of "win the
  game". The eval pool's archetype reference is left out.
- Each result row carries `harness_epoch` and, for a model seat, what the attempt spent: `model_calls`,
  `prompt_tokens` and `completion_tokens` as the provider reported them, `model_ms`, and `lookups`.
- An `openrouter:` model sends each position to openrouter.ai. That needs the owner's egress opt-in
  (`OPENROUTER_ALLOW_EGRESS=1` in `secrets.env`), which no tool here sets.
- `--max-price-at-list` (an `openrouter:` model only) sends every request a price ceiling at the model's list
  price in `eval/pricing/prices.json` (OpenRouter's `provider.max_price`, USD per million tokens), so latency
  routing never lands on a dearer priority tier (owner, 2026-10-25). OpenRouter refuses a request no endpoint at
  or under it can serve, so before a run check each model has an endpoint that takes tools at list
  (`/api/v1/models/<id>/endpoints`; the frontier run script on `docs/writeup-notes` does this). Each puzzle's
  `batch-manifest.json` records it (`record.openrouter_max_price`, null without the flag) and `--resume` refuses
  another ceiling. A row with requests refused under the ceiling is not scored, is flagged on the progress line and
  by `puzzle_watch.py`, and is replayed on `--resume` (`docs/harness/ARCHITECTURE.md` section 9). The ceiling is
  the base list price, so it also rules out a long-context tier (above 272K prompt tokens on GPT-6.1 Sol, 200K on
  Grok, 100K on Haiku 5.5; the largest prompt recorded is 69K, per the `prices.json` comment). `--budget-usd` is
  still checked at the dearest endpoint.
- A `jev:` model (a decision model on OpenRouter's Decisions API, `docs/harness/ARCHITECTURE.md` section 9) needs
  the same opt-in, and runs without `--memory-tools`: it cannot call a lookup.
- A `claude-cli:` model (Claude Code in print mode, `docs/harness/ARCHITECTURE.md` section 9) is a development lane
  on the owner's subscription: it runs only with `CLAUDE_CLI_SEAT=1`, its rows are stamped `dev_lane`, and
  `puzzle_score.py` heads its report "DEVELOPMENT LANE", never a PUZ score. It sends each position to Anthropic under
  the CLI's own login and needs no OpenRouter opt-in. It runs without `--memory-tools`, with `-j 1`;
  `--claude-cli-effort` and `--claude-cli-thinking-tokens` pin its CLI settings (the row's `claude_cli_effort`,
  `claude_cli_thinking_tokens`); its spend is the CLI's reported cost.

- Every run keeps its full record (owner, 2026-10-25): see "Keeping a run".

### Keeping a run

Every `puzzle_solve.py` run records everything needed to reconstruct each model conversation and each game, in the
run directory, with nothing cut:

| File | Holds |
|---|---|
| `suite.json` (run root) | the suite the run was played on, byte for byte (`puzzle-run.json` names it) |
| `<puzzle>/<name>.pzl` | the position the JVM read, byte for byte; `batch-manifest.json` `record` gives its sha256 |
| `<puzzle>/transcripts/http-<jvm>-<n>.jsonl` | every HTTP round-trip to the model, retries, refusals, timeouts and connection errors included, as two `t:"http"` rows of one `seq`: a `phase:"sent"` row written before the request goes out, with the request body exactly as sent (system prompt, every message, tools, `tool_choice`, reasoning and token settings, the `provider` object, the model), then a `phase:"response"` row with the response body exactly as received (content, tool calls, `reasoning` and `reasoning_details`, finish reasons, provider, generation id, usage and cost), or the `stop` or `error` that ended the call and the status and headers if they came. Each body has its sha256; rows name the seat (`label`) and the game file. `<n>` starts at 1 and moves only when an append fails. |
| `<puzzle>/transcripts/engine-<id>-seed<s>-r<n>-<ms>.jsonl` | the game's whole Forge game log, one row per entry in the engine's order (a capped game's log is read again when written) |
| `<puzzle>/game-*.jsonl`, `jvm.log`, `puzzle-results.jsonl`, `batch-manifest.json`, `build.json`, `java.args` | as before: the decision and call rows, the JVM's log, the result rows, the condition, the build |

Each result row names its record (`transcript_version`, `transcript_http_files`, `transcript_engine_file`) and counts
the rows that could not be written (`transcript_errors`, also printed as `[TRANSCRIPT] FAILED`). Each row is one
append of its whole bytes; an append that fails ends that file (a part-written row there is a gap `archive_run.py`
names) and the next row starts the next file. A recording failure never reaches the bridge, so it never costs a
reply. No Authorization, cookie or API-key header is written; a row that would hold the key is refused. The record
changes no byte the model is sent (`TranscriptTest.recordingChangesNoByteSent`), so it is not a condition. It is the
`Transcript` class (`TRANSCRIPTS=1` with `EVAL_OUT`), which `puzzle_solve.py` always turns on; a build without
`Transcript` compiled in is refused unless `--allow-no-record`, and the manifest's `record.transcript_version` is then
null. Not covered: the Claude CLI lane (no HTTP of its own), the body of a response cut off by its deadline, and a
call in flight when the JVM died, which has its sent row and no response row (`archive_run.py` lists it as
UNANSWERED).

Size: not measured yet; no run has the record. `python3 eval/archive_run.py --check <run>` prints it (`http_bytes`)
for any run. Compression, measured on a run from before the record: `python3 eval/archive_run.py
runs/puzzles/opus-cli-smoke --out <scratch>/opus-cli-smoke.tar.gz` packed 6 files, 801,012 bytes, into 35,946.

To keep a finished run, pack it and keep the archive somewhere outside the checkout:

```bash
python3 eval/archive_run.py runs/puzzles/frontier-1 --out /keep/frontier-1.tar.gz   # pack, then re-verify
python3 eval/archive_run.py --verify /keep/frontier-1.tar.gz                       # later, anywhere
```

`archive_run.py` refuses a run still writing, or a directory inside one (`finished` false in a `puzzle-run.json` at
or above it); a gap in the record (`--allow-gaps` packs it and lists them in the manifest): a result row without the
record its manifest asked for, a row that billed calls but names no http file, fewer answered calls in a game than
replies on its `context_call` rows, a missing transcript or `.pzl`, a body whose sha256 differs, a cut row, a row
that could not be written; and any file holding an OpenRouter key. A call sent and never answered is listed in the
manifest, not refused. Beside the archive it writes `<out>.manifest.sha256` (`sha256sum -c` format, one line per
file) and `<out>.sha256`; then it re-reads every member against the manifest inside, and `--verify` also compares the
manifest beside it with the one inside. `runs/` is never
committed. A run that should live with the repository goes on a data-only branch that is never merged, as the
`demo-recordings` branch holds `puzzles/hard-gen2-luna` (a README with what was run and its scores, then the run's
files); a run too large for git keeps only its archive's `.sha256` and manifest there, with where the archive is.

### Watching a run

```bash
python3 eval/puzzle_watch.py runs/puzzles/luna-open             # one row per puzzle, then the totals
python3 eval/puzzle_watch.py runs/puzzles/luna-open --follow    # one line per event until the run is over
```

What each column, flag and exit code means: `python3 eval/puzzle_watch.py --help`.

## Scoring

With c solves in k attempts:
- **pass@1** = c/k;
- **pass^j** = C(c,j)/C(k,j), for j = 2 and k: every one of j attempts solves, a reliability measure;
- **solved** = c ≥ k/2;
- **margin** = pass@1 − the random rate.

The headline is the mean margin over the split's active puzzles. Doing nothing scores about zero, and a
puzzle a coin flip solves earns almost nothing. Every aggregate is printed with:
- its floors (the same number for the random, pass and first arms);
- a 95% bootstrap CI over puzzles, and beside each solved count (and pass@1 at k=1) a Wilson interval: the
  bootstrap reports 0 of 21 solved as [0, 0], a certainty it does not have (Wilson: 0 to 3.2);
- per tier, the solved count beside the count each trivial arm would be expected to solve;
- where the AI baseline exists, a normalized score (random = 0, Forge AI = 1).

Two runs on the same puzzles (two solvers, or two conditions) are compared puzzle by puzzle with
`--paired-with RESULTS`: the mean per-puzzle pass@1 difference, its bootstrap interval over puzzles (ids sorted,
seed 7) and its clustered interval. `--against C D` gives the difference of differences, (B - A) - (D - C).
Runs of other puzzles, suites, splits or k are refused. Lookups per condition: `eval/puzzle_tool_use.py`.

`puzzle_score.py` refuses (exit 2) to:
- pool the open and held-out splits;
- score an unfrozen or edited suite;
- mix suite or puzzle versions;
- mix solvers;
- score a puzzle with fewer than k attempts, or with a seed repeated;
- score an unscoreable row (one where the goal was not enforced);
- pool attempts from two harness epochs (rows carry `harness_epoch` since epoch 69; an unstamped row beside a
  stamped one is refused too).

Beside the score (BENCHMARK-V2 phase 2): the clustered 95% interval of the headline and of pass@1 (the standard
error of the per-puzzle means, Miller 2024), the smallest pass@1 difference two independent runs of this size
detect at 80% power, the solved count by `tier_v2` when the records carry it, the budget per attempt (median and
total model calls, tokens, model time and lookups, from rows that record them), and the integrity counts at weight
0 (capped attempts, deck-out solves).

Some attempts count as failures, so a failure cannot vanish from the count:
- a capped game;
- a solve decided by an empty library.

## Limits (stated with every PUZ number)

- **Forge's built-in puzzles are public, and their solutions are published.** A model may have seen a
  solution. The held-out split does not change that; mined puzzles from the reserved seed band
  20000–29999 are the uncontaminated part, once they exist.
- A puzzle that neither random nor Forge's AI solves has no proof of solvability, so it stays a
  candidate. The active suite therefore leaves out the hardest puzzles: those beyond Forge's AI where
  random is lucky less than once in its rollouts.
- The opponent is Forge's AI, as in the puzzle's own mode. A puzzle whose author assumed an opponent
  making particular choices is played against whatever the AI does.
