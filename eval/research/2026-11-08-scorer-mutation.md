# Mutation-testing the scorers: do the tests notice a one-site change? (2026-11-08)

Experiment E5 of the 2026-11-08 research pass. Free: no model call, no JVM, no `OPENROUTER_ALLOW_EGRESS`; the scorers'
behaviour is unchanged (this PR adds tests and one script).

## Question

The three headline scores come from a few Python files: `eval/aggregate.py`, `eval/panel_score.py`,
`eval/puzzles/puzzle_score.py`, with `eval/sanity_arms.py`, `eval/rating.py`, `eval/sprt.py`, `eval/seed_policy.py` and
`eval/rl/eval_only.py` beside them. Their test files pass. Do those tests fail when one site of the code is changed on
purpose (a swapped inequality, a dropped guard, a constant off by one)? A site no test notices is an "88% lesson" waiting
to happen: the score would move and nothing would go red.

## Sources

Read for the design in the 2026-11-08 research pass (`research-2026-11-08.md`, experiment E5); not re-fetched here.

- SWE-smith, Yang et al., arXiv 2504.21798 (April 2025): mutate working code and keep the mutants a test suite catches.
  https://arxiv.org/abs/2504.21798
- Anthropic, "Demystifying evals for AI agents", 2026-01-09: a 0% pass rate over many trials usually means a broken task
  or grader. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Prime Intellect, "Learnings from Auditing Public Evaluations", 2026-11-03: graders that reject correct answers.
  https://www.primeintellect.ai/blog/auditing-public-evaluations
- Mechanize, "Cheap RL tasks will waste compute", 2025-08-18: the bottleneck is writing comprehensive tests.
  https://www.mechanize.work/blog/cheap-rl-tasks-will-waste-compute

## Method

`scripts/mutate_scorers.py` (stdlib `ast`; tested by `eval/tests/test_mutate_scorers.py`).

- **Mutants.** Every single-site change of eleven kinds: comparison flip (`<`/`<=`, `>`/`>=`, `==`/`!=`, `is`/`is not`,
  `in`/`not in`), `and`/`or`, `+`/`-`, `*`/`/`, a numeric constant (0 and 1 swap, else +1), `True`/`False`, `not x` to
  `x`, `return v` to `return None`, an `if` body to `pass` (not an `if __name__` guard, not a body that is already
  `pass`), `continue` to `pass`. Docstrings and strings are never touched.
- **Sample.** Up to 40 per file, drawn with `random.Random(0)` over the file's sites (`eval/seed_policy.py` has 31 sites,
  all run). The same 311 mutants were run on both trees below.
- **Run.** One temp copy of `eval/`, `docs/`, `scripts/`, `forge-overlay/` and the root files (about 35 MB), under a
  `mktemp -d` in the session scratchpad, deleted at the end. For each mutant the module is overwritten with the mutated
  source and only `eval/tests/test_<module>.py` runs, with `python3 -I`, a 60 s timeout, `PYTHONHASHSEED=0`,
  and its own `TMPDIR`. **Killed** = the test file exits non-zero; **survived** = exits 0; **error** = timeout or
  death by signal (not counted). Every verdict is run twice and must repeat, or it is an error (see "What the first
  runs taught").
- **Trees.** *Before* = `origin/main` at `0ec3593` exported to a directory (its tests). *After* = this branch (its tests
  plus the new ones). Scorer code is identical in both.
- **Judging a survivor.** By hand, from the source line: equivalent (no behaviour change), covered by another test file
  (`--recheck` reruns each survivor against the other test files that name the module; only files that pass on the
  unmutated copy count), or a test gap.

## Baseline: the tool's own planted mutants

The tool refuses to report unless these come out right (`self_check`, run first; also in the unit test):

| Planted mutant (toy `clamp`) | Required | Got |
|---|---|---|
| unmutated | survives | survives |
| `return x` to `return x + 0` (equivalent) | survives | survives |
| `return hi` to `return lo` (a bug) | killed | killed |
| `return x` to an endless loop | error, not a kill | error |

The unmutated copy of every target also passes its own test file before any mutant runs (otherwise every mutant would
look killed). In the toy, the generator's own mutants split as expected: all killed except `x < lo` to `x <= lo` and
`x > hi` to `x >= hi`, which return the same value at the boundary (the unit test pins that pair).

## Result

Mutation score = killed / (killed + survived). The interval is Wilson 95% over the sampled mutants, treated as
independent draws from the file's sites (they are not quite: sites in one function share a test, so the true interval is
a little wider).

| File | Sites | Sampled | Before: killed / survived | Before score (95%) | After: killed / survived | After score (95%) | Error |
|---|---|---|---|---|---|---|---|
| `eval/aggregate.py` | 829 | 40 | 17 / 23 | 42% (29 to 58) | 25 / 15 | 62% (47 to 76) | 0 |
| `eval/panel_score.py` | 320 | 40 | 18 / 22 | 45% (31 to 60) | 26 / 14 | 65% (50 to 78) | 0 |
| `eval/puzzles/puzzle_score.py` | 404 | 40 | 24 / 16 | 60% (45 to 74) | 35 / 5 | 88% (74 to 95) | 0 |
| `eval/sanity_arms.py` | 97 | 40 | 32 / 6 | 84% (70 to 93) | 36 / 2 | 95% (83 to 99) | 2 |
| `eval/rating.py` | 487 | 40 | 24 / 16 | 60% (45 to 74) | 28 / 12 | 70% (55 to 82) | 0 |
| `eval/sprt.py` | 200 | 40 | 29 / 11 | 72% (57 to 84) | 37 / 3 | 92% (80 to 97) | 0 |
| `eval/seed_policy.py` | 31 | 31 | 30 / 1 | 97% (84 to 99) | 30 / 1 | 97% (84 to 99) | 0 |
| `eval/rl/eval_only.py` | 95 | 40 | 35 / 5 | 88% (74 to 95) | 40 / 0 | 100% (91 to 100) | 0 |
| **all** | 2463 | 311 | **209 / 100** | **68% (62 to 73)** | **257 / 52** | **83% (79 to 87)** | 2 |

All eight listed files exist. The two
errors are `sanity_arms.py` mutants that make the fake-model server never report its port: the test waits for it and the
60 s limit ends the run. Excluding the 8 survivors judged equivalent below, the score is 209/301 = 69% before and
257/301 = 85% after.

The floor is the planted pair above: a test file that checks nothing scores 0% (every mutant survives), one that fails on
any change scores 100%, and the planted bug and the planted equivalent came out as required. Where the tests were already
strong (`seed_policy`, `eval_only`, `sanity_arms`) the score began high; the scoring core (`aggregate`, `panel_score`)
began under 50%.

## Survivors after the new tests (52), judged

| Judgment | Count | Which |
|---|---|---|
| Equivalent | 8 | `rating.py` 249 (`x >= 0` to `>`: both branches give 0.5 at 0), 376 (`>` to `>=` at an exact tie of a float plus tolerance), 455 (the `1e-300` pivot guard is unreachable on any fit the tests make); `sprt.py` 214 and `seed_policy.py` 96 (`return 0` to `return None`: `sys.exit(None)` exits 0), `sprt.py` 104 (`z0 == 0 or z1 == 0`: both are zero together, only when `p0 == p1`, which `binom_sprt` already refuses); `puzzle_score.py` 524 (`sorted(rates[0])` to `rates[1]`: the two runs were checked to cover the same puzzles); `panel_score.py` 523 (JSON indent 2 to 3) |
| Covered by another test file (a gap in the module's own file only) | 7 | `aggregate.py` 1195 (`is` to `is not`), 1235, 1237, 1272, 1273, 1309 (all `test_tool_accounting.py`), 1821 (`test_sanity_arms.py`) |
| Test gap, score-relevant, left | 10 | below |
| Test gap, report text or defaults, left | 27 | below |

**Gaps left, score-relevant (10).** None changes a win count or an interval by itself; each changes a value the score
or its refusal rests on:

1. `rating.py` 572: the Elo-to-logit conversion of the anchor prior (`prior_sd_elo / ELO_PER_LOGIT`, `/` to `*`).
2. `rating.py` 373: the default first-player prior sd, 1.0 to 0.
3. `rating.py` 501: the bootstrap's stratum key (`rec[4]` to `rec[5]`).
4. `rating.py` 582: the per-player game count (`+= 1` to `+= 0`).
5. `panel_score.py` 97: the check that every cell names a deck in the pool (`or []` to `and []` skips it).
6. `panel_score.py` 459: the "MIXED CONDITIONS ALLOWED BY FLAG" banner.
7. `puzzle_score.py` 439: the report's `pilot` flag (`or` to `and`).
8. `sprt.py` 107: the Wald expected-n formula (`alpha * upper` to `alpha / upper`); its test allows plus or minus 5.
9. `aggregate.py` 1490: `n_pairs_used` of the refused pooled summary (`test_experiments.py` pins it at 0, but it
   could not run in the minimal copy, so the recheck did not see it).
10. `aggregate.py` 1200: the "mixed" merge of a deck's controller.

**Gaps left, report text or defaults (27).** A changed message, a count shown, or a default: `aggregate.py` 554, 1195
(the guard's body), 1526, 1615, 1654, 1658; `panel_score.py` 114, 235 (the percentile index clamp, wrong only below
40 resamples), 452, 465 (twice), 471, 478 (twice), 483, 484, 498; `puzzle_score.py` 178, 638 and 652 (the default
bootstrap sizes 2000 and 10000); `sanity_arms.py` 73, 86; `rating.py` 112, 176, 649, 660, 670. Line numbers are the
original file's; the per-mutant rows are in the run's JSON (not kept; rerun the tool).

## Gaps found and fixed (48 mutants newly killed, 33 tests)

New tests, each named for the mutant it kills; every one was run against its mutant (the "After" column above is that
run):

- `test_aggregate.py`: the fallback gate passes exactly at its ceiling (1086 `>` to `>=`); the CLI exits 2 on the fallback
  gate, 4 on the harness-decided gate and 6 on a missing sanity arm, and 0 below (1836, 1857, 1861); the smart and fast
  tiers count toward the gate (1082); a rerun is the same outcome only when winner and turns both match (476); a lone
  experiment reads "(single condition)" (505).
- `test_panel_score.py` (class `TestScorerArithmetic`): the strict score weighs a loss 0 (202); `str_norm` is undefined
  when the AI anchor is not above the random anchor (261); it needs a random arm in every cell, not one (392); the
  margin's standard error, interval and bootstrap use the Laplace-smoothed variance of both arms and model minus control
  (377, 383, 387); an empty pool has no score (221); at most eight stray seeds are named (293).
- `test_puzzle_score.py` (class `MutationPinsTest`): `pass_hat` accepts all attempts and names a negative count (89); the
  binomial tail of the policy floor (103); a puzzle's margin is its rate minus the random rate (319); the default
  resamples (342); a suite edited after freezing is refused (216); rows stamped with another suite are refused and with
  this one score (228); an edited puzzle is told apart from an unknown one (259); the normalized score needs a Forge-AI
  rate on every puzzle (385); same-k pairs carry no k floor (531, 537); `--json` writes the report (690).
- `test_rating.py`: the stable softplus on both sides (365); `goodness_of_fit` predicts from the rating gap, the
  first-player term and the matchup term, and its variance is `p(1-p)` (401, 412).
- `test_sprt.py`: zero counts and the end points of the score range are valid (142, 147); a bad alpha or beta is named,
  not a math error (63); the CLI's required arguments exit 2 (258, 266, 278).
- `test_sanity_arms.py`: a missing floor is incomplete and a floor that drew every game reads as zero (163, 164); the
  violation message states the margin in points (167).
- `test_eval_only.py`: at most five seeds per reason, only the doubled seeds are named, the cache key of a missing file
  (65, 108, 115, 181).
- `test_mutate_scorers.py`: the tool's own generator, runner and planted mutants.

No mutant revealed a bug in a scorer, so no issue was filed: every surviving non-equivalent mutant is a test the
module lacked, not behaviour that is wrong today.

## What the first runs taught about the tool

The first full runs of the same 311 mutants disagreed by up to 3 kills per file, so the tool was hardened until two
runs agreed (before: identical counts on two final runs; after: within 1). The causes found:

- `python3 -I` drops the test's own folder from `sys.path`, which broke tests that import a sibling test; the runner puts
  that folder back.
- Hash randomisation changes `rating.py`'s test outcomes (set order picks the anchor): `PYTHONHASHSEED=0`.
- The tests leak temp directories (6,267 in 3 runs, 540 MB) and leave their fake model server running when they crash;
  each run now has its own `TMPDIR` and its process group is killed after it, and the output goes to a file, not a pipe
  a leftover server would hold open.
- One mutant that crashes the module at import was reported "survived" in one full run and killed in 60 of 60 isolated
  repeats, cause not found: a verdict now has to repeat, or it is an error. The final runs had no such error beyond the
  2 timeouts.
- A test that cannot run in the minimal copy (`test_experiments.py` reads other overlay Java files) cannot recheck a
  survivor; the copy now includes `forge-overlay/`.

A number from a single unrepeated run of this kind of tool should not be trusted to better than plus or minus 3 mutants
per file.

## What would change a decision

- The scoring core (`aggregate`, `panel_score`) is at 62% and 65% after the pins, not "mutation-checked"; STANDARDS should
  not say so yet. The 10 score-relevant gaps are the next tests to write before the next scored run; the first four are
  `rating.py` (the prior and bootstrap strata), the ones most likely to move a STR-AI rating without a red test.
- `scripts/mutate_scorers.py` is cheap (about 15 minutes for the 311 mutants, about 35 with `--recheck`); rerun it when a scorer
  changes: `python3 scripts/mutate_scorers.py eval/<file>.py --json out.json`.

## Not done

- Only 40 of 829 sites in `aggregate.py` (5%) and 40 of 320 in `panel_score.py` (12%) were sampled. The rates have the
  intervals above; the unsampled sites were not looked at.
- Only the module's own test file decides a kill. A survivor killed by another file is counted a survivor here and named
  above where the recheck could run.
- Mutants are single-site and syntactic. Missing behaviour (a check that is not in the code at all) is invisible to this
  method; `docs/eval/` still has to say what each scorer must refuse.
- `eval/rl/eval_only.py` and `eval/rating.py` were mutated in the Python only; the Java side of the scores
  (`LlmSpikeHeadless` game lines) is out of scope.
