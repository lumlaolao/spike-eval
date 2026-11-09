# Which proven-against-replies puzzles are not unique against Forge's AI

Zero-model, no network, no `OPENROUTER_ALLOW_EGRESS`. Generalises issue #447 (RP_034: a second solution that reaches the
acceptance state only because Forge's AI never activates the Welding Jar).

## Question

Of the 526 `result: converted` rows of `eval/puzzles/rules-pilot/manifest.jsonl`, the 56 with
`proof.opponent_branches_beaten > 0` are proven against every opponent reply. The suite is scored against Forge's AI.
For how many does a solver line that is not the proof also reach the acceptance state against the AI?

## Result

* **Flagged by the method: 2 of 56 (3.6%, Wilson 95% interval 1.0 to 12.1%)**: RP_034 and RP_295.
* **Confirmed as AI-blindness: 1 of 56 (1.8%, Wilson 0.3 to 9.4%)**: RP_034 (the known case). RP_295's second line is a
  variant of the proof's own plan and the search-opponent run did not refute it (below), so it is not shown to be AI-blind.
* **Baseline:** the same uniqueness search against the search opponent flags 0 of 56 (the manifest's verdict is `unique`
  for all 56; reproduced here, every `search_control` row is `proven` and `accepted`; Wilson 0 to 6.4%). A flag is
  therefore a difference between the two opponents, not noise in the search.
* **Validation on RP_034:** flagged `multiple` against `--opponent ai` (11 s, 3 rollouts): the main line destroys the
  Golem and the Jar; the second line `Solver:3:1 Solver:3:0 Solver:3:1 Solver:2:0 Solver:2:1 Solver:2:0` is the
  2 damage + 2 damage + destroy mode set (issue #447's line (a) family); the difference lists the modes. Against the search
  opponent the manifest says `unique`. The method flags the known case, so the sweep was run.

## Flagged puzzles

| Puzzle | Main line (AI run) | Accepted second line against the AI | What differs | Search-opponent evidence |
|---|---|---|---|---|
| RP_034 | `Solver:3:0 Solver:3:1 Solver:2:1 Solver:2:0 Solver:2:0` (Fiery Confluence: 2 damage, destroy, destroy) | `Solver:3:1 Solver:3:0 Solver:3:1 Solver:2:0 Solver:2:1 Solver:2:0` (2 damage, 2 damage, destroy) | mode set; the main line's second destroy picks the Stone Golem | Beaten by the Welding Jar reply per #447 (both lines `rejected` with `Opponent:2:0 Opponent:2:0`, `Stone Golem in Graveyard`). Not re-run here. |
| RP_295 | `Solver:2:1 Solver:16:15 Solver:2:0 Solver:7:6 Solver:2:0 ...` (block with the four creatures, convoke one Raging Goblin for Stoke the Flames) | same prefix with `Solver:2:1` in place of `Solver:2:0` at the convoke choice (two Raging Goblins tapped for convoke, `Solver:4:3 Solver:3:1` at the end) | `convoke: [Goblin]` vs `convoke: [Goblin, Goblin]` | **Not refuted.** Uniqueness against the search opponent at 4000 rollouts (`rp295-search-unique4000.json`): `unique`, 32 deviations, only 1 played out, 0 `unproven_alternatives`. The second line is not shown to lose to any reply. A probe of it against the search opponent drifted (the line's opponent steps do not fit it). |

RP_295 is the 4-blocker convoke puzzle (CR 506.4b). Both lines are the rule's plan, differing by how many blockers are
tapped for convoke, which the rule does not constrain. The tool calls them two solutions against the AI; whether the
extra Goblin changes the outcome under some opponent reply was not determined.

## Method

1. Build: worktree of `origin/main` at `db313769c324`, `STAGES="preflight classpath compile"` under the shared lock,
   OUT a scratch build dir (`BUILD` below); overlay sha **`edaca55afdf6`** (the same as the #447 evidence), engine pin as in
   `FORGE_COMMIT`. `.pzl` files and the manifest were not edited.
2. Main arm, per puzzle (`drive.py <id> ai 600`): the search runner as `puzzle_baselines.prove_puzzle` runs it
   (`CONTROLLER_A=search`, seed 1, `PZL_SEARCH_BUDGET=600`, `GAME_CAP_MS=60000`) with `CONTROLLER_B=ai` instead of
   `search`, and the uniqueness search on (`PZL_UNIQUE_BUDGET=600`, the gate's budget; `PZL_SEARCH_LINES` default 32).
   The runner proves a line against the AI, then tries every other option at every solver choice on it for a second
   solving line (`docs/eval/PUZZLES.md`, "Uniqueness"). `multiple` against the AI is the flag.
   The tooling does run uniqueness against `ai`; no fallback was needed. (`prove`'s CLI takes a suite file, not the
   JSONL manifest, so `drive.py` calls `puzzle_baselines.run_arm` with the same environment.)
3. Control arm, per puzzle (`drive.py <id> search 0`): the proof against every reply, uniqueness off, to record the
   proof line and its `Opponent:` steps.
4. Cross-check (`probe.py <id>`): the proof's `Solver:` steps only (its `Opponent:` steps removed), played as
   `puzzle_baselines.py probe --opponent ai` with completion seeds 0, 1, 2. This is the issue's "cheap check"; it shows
   whether the proof's own plan, run against the AI, still reaches acceptance. It cannot flag a puzzle by itself (the
   proof line is the intended solution), so it is recorded in the rows but not counted.

```bash
BUILD=<acceptance OUT> FORGE_HOME=<forge> python3 drive.py RP_034 ai 600           # one puzzle, main arm (opponent ai, unique budget 600)
cat ids.txt | xargs -P3 -I{} sh -c 'python3 drive.py {} ai 600 > res-ai-{}.json'      # the 56, 25 min wall
cat ids.txt | xargs -P3 -I{} sh -c 'python3 drive.py {} search 0 > res-search-{}.json' # control, 12 min wall
cat ids.txt | xargs -P3 -I{} sh -c 'python3 probe.py {} > res-probe-{}.json'           # cross-check
python3 drive.py RP_295 search 4000                              # the RP_295 follow-up, 20 min
```

`ids.txt` is the 56 ids: `converted` with `proof.opponent_branches_beaten > 0`, in manifest order.
`drive.py` and `probe.py` are in this folder (they read the repository from their own location, the build from `BUILD` and write raw runs under `RUNS`, default `./raw`). The full 56 ran, no
subset, so there is no sampling seed. Raw run directories (6 MB) were deleted; `rows.jsonl` keeps the compact rows.

## Per-puzzle table

Columns: input sha256 prefix of the `.pzl` (full hashes in `rows.jsonl`; the same hash is in each result row);
`beaten` = `opponent_branches_beaten`, manifest / this sweep's control (equal for 51 of 56; the other five,
RP_007, RP_019, RP_034, RP_141 and RP_189, count differently (cause not investigated; the control ran with uniqueness off, the manifest at the gate's
settings); all 56 are still above 0 in the control); manifest uniqueness verdict (search opponent); AI-run `exhausted/deviations` and
verdict; `Opponent:` steps in the proof line; cross-check verdicts for completion seeds 0/1/2 (A accepted, R rejected,
D drifted).

| Puzzle | .pzl sha256 | beaten (man/ctl) | manifest | AI exh/dev | AI verdict | opp steps | probe |
|---|---|---|---|---|---|---|---|
| RP_007 | 8a5d7bd3316c | 1/6 | unique | 4/4 | unique | 1 | AAA |
| RP_019 | 14b5087811a2 | 2/6 | unique | 2/2 | unique | 1 | AAA |
| RP_022 | 65b50a66343c | 1/1 | unique | 7/27 | unique | 1 | AAA |
| RP_023 | 3fa139fe0dde | 1/1 | unique | 4/4 | unique | 1 | AAA |
| RP_031 | b4ebc2d361be | 1/1 | unique | 3/3 | unique | 1 | AAA |
| RP_034 | f640f9b7e89a | 28/13 | unique | 0/7 | multiple | 2 | AAA |
| RP_082 | b874ee2221e4 | 5/5 | unique | 6/6 | unique | 3 | AAA |
| RP_083 | 0bc30e933771 | 1/1 | unique | 4/5 | unique | 1 | AAA |
| RP_099 | a1d4d627d754 | 1/1 | unique | 1/1 | unique | 1 | AAA |
| RP_114 | 7f85675f5c0f | 3/3 | unique | 2/2 | unique | 1 | AAA |
| RP_141 | b8bb6d552a33 | 2/3 | unique | 2/2 | unique | 1 | AAA |
| RP_152 | d0294363ec7a | 2/2 | unique | 7/22 | unique | 0 | AAA |
| RP_156 | cb4013426cef | 1/1 | unique | 7/7 | unique | 1 | RAR |
| RP_157 | fd61dfa07007 | 1/1 | unique | 4/4 | unique | 1 | AAA |
| RP_158 | 0d683c1354b6 | 1/1 | unique | 7/7 | unique | 1 | RRR |
| RP_159 | 24e2e4b562d7 | 1/1 | unique | 1/1 | unique | 1 | AAA |
| RP_160 | 7c5ec56fdb97 | 1/1 | unique | 6/6 | unique | 1 | ARR |
| RP_166 | 0ff408dac4e9 | 1/1 | unique | 4/4 | unique | 1 | AAA |
| RP_169 | 9971a41b294e | 2/2 | unique | 3/3 | unique | 1 | AAA |
| RP_180 | 042b7ab242f7 | 1/1 | unique | 3/3 | unique | 1 | AAA |
| RP_189 | 3148398238ef | 8/4 | unique | 5/5 | unique | 4 | AAA |
| RP_190 | d82653a43e94 | 3/3 | unique | 11/11 | unique | 1 | AAA |
| RP_192 | 2d09e2f673b9 | 2/2 | unique | 8/8 | unique | 1 | AAR |
| RP_204 | 2b93a1259a8b | 5/5 | unique | 4/4 | unique | 0 | AAA |
| RP_205 | c15126b50dbc | 2/2 | unique | 6/7 | unique | 0 | AAA |
| RP_206 | 409673117d77 | 1/1 | unique | 4/4 | unique | 1 | AAA |
| RP_242 | 7871d8777de0 | 1/1 | unique | 3/3 | unique | 1 | AAA |
| RP_256 | 04e4f9b0d0c9 | 2/2 | unique | 8/9 | unique | 1 | AAA |
| RP_260 | 96b0d79f2967 | 5/5 | unique | 8/8 | unique | 2 | AAA |
| RP_273 | f04e3f57651a | 1/1 | unique | 7/7 | unique | 1 | AAA |
| RP_276 | 0886b1886341 | 2/2 | unique | 6/6 | unique | 1 | AAA |
| RP_283 | 91af8ef05a82 | 1/1 | unique | 4/4 | unique | 1 | AAA |
| RP_293 | b2828c3699a3 | 1/1 | unique | 8/8 | unique | 1 | AAA |
| RP_295 | ce63af4d0628 | 219/219 | unique | 0/37 | multiple | 2 | DDD |
| RP_297 | 4aa32dc8850e | 1/1 | unique | 9/9 | unique | 1 | AAA |
| RP_301 | 450df94a526c | 3/3 | unique | 10/21 | unique | 2 | AAA |
| RP_303 | fc982c848825 | 2/2 | unique | 3/3 | unique | 1 | AAA |
| RP_336 | c7ad8e0f08d0 | 1/1 | unique | 10/10 | unique | 1 | AAA |
| RP_345 | b1d955c20873 | 1/1 | unique | 2/2 | unique | 1 | AAA |
| RP_372 | e45f64d8fd57 | 1/1 | unique | 5/5 | unique | 0 | AAA |
| RP_410 | 1a2af126ced2 | 1/1 | unique | 4/4 | unique | 0 | AAA |
| RP_429 | 75ba830f3feb | 1/1 | unique | 10/10 | unique | 1 | AAA |
| RP_437 | a67abd158052 | 5/5 | unique | 10/10 | unique | 0 | AAA |
| RP_449 | f06747dd1a9a | 3/3 | unique | 6/6 | unique | 0 | AAA |
| RP_457 | a8d943e492d1 | 1/1 | unique | 2/2 | unique | 0 | AAA |
| RP_476 | 0b8c0f815b6f | 6/6 | unique | 8/8 | unique | 0 | AAA |
| RP_477 | 4cc22b257fbd | 1/1 | unique | 3/3 | unique | 0 | AAA |
| RP_483 | 920f615e0fde | 3/3 | unique | 9/9 | unique | 1 | AAR |
| RP_484 | 4f29ee506bad | 30/30 | unique | 2/2 | unique | 2 | AAA |
| RP_503 | bc7e04510e03 | 10/10 | unique | 2/2 | unique | 2 | AAA |
| RP_506 | 38d7dd3a2b3c | 1/1 | unique | 9/9 | unique | 1 | AAA |
| RP_508 | f5153893657e | 1/1 | unique | 5/5 | unique | 1 | AAA |
| RP_510 | 6096ecaacb8c | 1/1 | unique | 3/3 | unique | 0 | AAA |
| RP_518 | da9d330c9a32 | 5/5 | unique | 24/24 | unique | 2 | AAA |
| RP_528 | 1681ad938cfe | 13/13 | unique | 8/8 | unique | 0 | AAA |
| RP_529 | 7f5b4ce20b32 | 3/3 | unique | 6/6 | unique | 0 | AAA |

Recomputing: the flag count is `sum(r["flag_ai_blind"])` over `rows.jsonl` (2 of 56); the Wilson interval is the
standard score interval at z = 1.96. `flag_ai_blind` is `ai_run.uniqueness == "multiple"`. The "confirmed 1 of 56" is a
judgment, not a field: RP_034 by issue #447's forced-Jar runs (not re-run here), RP_295 left unconfirmed.

## What this does and does not show

* A `unique` verdict from the search is exhaustive only when `exhausted == deviations`. In the AI run that holds for 48
  of 56; the other eight (RP_022, RP_034, RP_083, RP_152, RP_205, RP_256, RP_295, RP_301) had deviations the search did not
  play out within the budget, so a second AI-accepted line could hide there. RP_034 and RP_295 are two of the eight. So
  2 of 56 is a lower bound on the tool's flags for the puzzles it did not exhaust, not an upper bound on AI-blind puzzles.
* The search can only find a second line among the options at the solver's own choice points on its main line, and
  compares solutions by their game-log actions and unlogged choices (`UNIQUENESS_NEUTRAL` drops orders, scries, etc.).
  A second solution that differs only by a neutral choice, or one the 32-lines-per-point bound never reaches, is not seen.
* The AI run's main line is the first line the search found against the AI. For 14 puzzles it is not the proof's
  `Solver:` steps (the difference between
  `ai_run.line` and the `Solver:` steps of `search_control.line`); those are the puzzles where the AI let the search take another route.
  None of them was flagged `multiple` except RP_295. In 13 puzzles (RP_152, 204, 205, 372, 410, 437, 449, 457, 476, 477,
  510, 528, 529) the proof line has no `Opponent:` step at all: the opponent's replies did not change the line the
  prover returned, so the AI cannot be blind to a reply there.
* Cross-check: with the `Opponent:` steps removed, the proof's own plan still reaches acceptance against the AI under
  all three completion seeds in most puzzles. Mixed or rejected cells (RP_156, 158, 160, 192, 483) mean the
  one-completion random play past the line failed the acceptance state, which says nothing about a second solution;
  RP_295 drifted (its solver indices depend on the opponent steps). Not used to count.
* What was not determined: whether RP_295's two-Goblin convoke line loses to some opponent reply (no free tool here plays
  a fixed solver line against every reply: a probe with the search opponent needs the `Opponent:` steps and drifted);
  whether the AI's pass on the Jar is general or specific to RP_034's state; the eight non-exhaustive puzzles at a
  larger budget (RP_295 at 4000 on the search side only); puzzles outside the 56 (those with
  `opponent_branches_beaten == 0` have no opponent reply the proof depends on); the seed dependence of the AI's play
  (one search seed, 1).

## Files

* `rows.jsonl`: one row per puzzle (input hash, manifest verdict, AI run, search control, cross-check, flag).
* `rp295-search-unique4000.json`: the RP_295 search-opponent uniqueness run at 4000 rollouts.
* `drive.py`, `probe.py`: the two drivers (`puzzle_baselines.run_arm` with the environment above).
