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
  model sees), `pass`, `first`, `floor` and `ai` (Forge's AI as the solver).
- `reference_line`: the proof that the position is solvable in this engine. It is one of:
  - a random rollout's draw tape and seed, replayed by the `script` arm, which must not drift and must
    reach the goal;
  - when random never solves the puzzle, a Forge AI solve's seed and game-log hash, replayed by seed,
    which must reproduce the same log.
- `verification`: the result of that replay, with the engine commit.
- `tier`:
  - 1: Forge's AI solves at least half its attempts;
  - 2: it does not, and the line takes three or fewer solver actions;
  - 3: four or more.

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
- `run` can be split into stages with `--arms` and `--ids`, since baselines accumulate in the record.
- `run` writes the manifest after every puzzle. `--resume` with the same `--out-dir` and `--build`
  reuses each arm whose rows that build already wrote for exactly the requested seeds, so a killed run
  continues where it stopped. Rows from another build or a partial seed set are run again.
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
  ends it, and those rollouts used to be reported as drift (prove-v1: PS_ELD1, PS_DOM0, PS_M204).
- A subtree whose every line has been played is never replayed (rollouts are deterministic given their line),
  and its solves are remembered.
- Not yet branched on: the engine seed (one seed per proof). Since epoch 56 the search arm branches over every split
  of combat damage and of a divided spell ("distribute 5 damage"); the other arms still split evenly.
- Why: `BENCHMARK-V1.md`, "What a puzzle must pass before it is frozen".

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
final state is deliberately not compared: the opponent's replies change it too (RP_045's first check called a line
where the opponent's Scavenging Ooze ate a card a second solution). `uniqueness_difference` lists what differed.
`PZL_SEARCH_LINES` (default 32; it was 4, which gave up on RP_034) bounds the lines tried per choice point.
`uniqueness`: `unique` (none found) or `multiple` (`uniqueness_second_line` and `uniqueness_second_actions`
show it); `uniqueness_exhausted` of `uniqueness_deviations` deviations were played out completely, and when all
were, "unique" is exhaustive rather than "not found within the budget". `uniqueness_unproven_alternatives`
counts other solving lines an opponent reply beat (not solutions). Smoke checks: `eval/puzzles/rules-pilot/
RP_SMOKE.pzl` (unique) and `RP_SMOKE2.pzl` (multiple).

## Running a model

```bash
python3 eval/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open \
    --model openrouter:openai/gpt-6-luna --k 4 --budget-usd 1.00 --out runs/puzzles/luna-open
python3 eval/puzzle_score.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open runs/puzzles/luna-open
```

- Each attempt is a fresh game on seeds 1..k: the same seeds for every solver, so attempts differ only
  in the opponent AI's and the engine's random choices.
- The seat is the one the games use:
  - the faithful profile, with the answer tool;
  - no lookup tools unless `--memory-tools` or `--rules-tool` is given;
  - no harness shortcuts;
  - every setting pinned explicitly and written to each puzzle's `batch-manifest.json`.
- The system prompt states the puzzle as its author wrote it (goal, turn limit and anything the solver
  is told, such as the top cards of the library), in place of "win the game". The eval pool's
  archetype reference is left out.
- An `openrouter:` model sends each position to openrouter.ai. That needs the owner's egress opt-in
  (`OPENROUTER_ALLOW_EGRESS=1` in `secrets.env`), which no tool here sets.

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

`puzzle_score.py` refuses (exit 2) to:
- pool the open and held-out splits;
- score an unfrozen or edited suite;
- mix suite or puzzle versions;
- mix solvers;
- score a puzzle with fewer than k attempts, or with a seed repeated;
- score an unscoreable row (one where the goal was not enforced).

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
