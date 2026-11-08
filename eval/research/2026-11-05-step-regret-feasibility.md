# Step regret: feasibility study (read-only)

Read on `origin/main` at 06a4eac (harness-epoch-82). `S=forge-overlay/forge-gui-desktop/src/main/java/forge/spike`.
Line numbers are from `git show origin/main:<path>`. Nothing was built, run or committed.

## The idea, restated

For each decision `k` of a recorded puzzle attempt, set `W(k)` = "the position after decision k can still be won".
A **regret step** is a `k` with `W(k-1)` true and `W(k)` false. The **floor** at `k` is the share of the legal options
at `k` that keep `W` true, which is what a uniform random player would score at that decision. Forge judges every leaf.

---

## 1. How the search arm enumerates a choice (Step = seat, bound, value)

**The seam.** Every zero-model arm is just a `java.util.Random` placed in the seat's field `rnd`
(`PuzzleArms.java:12-29`). The seat builds it once, when the seat is constructed:
`this.rnd = PuzzleArms.isArm(mode) ? PuzzleArms.forMode(mode, MyRandom.getRandom().nextLong(), seat) : null`
(`LlmSpikeGui.java:1329`; the field is `private final`, `:1284`). The `search` arm is `PuzzleArms.Search`
(`PuzzleArms.java:327-348`). It sends every `nextInt` / `nextBoolean` / `nextLong` to the one shared
`PuzzleArms.Line` (`:262-324`).

**A Step.** `Step(seat, bound, value)` (`PuzzleArms.java:250-258`). `Line.draw(seat, bound)` (`:293-312`) has two parts:
- It replays `prefix[at]` while the prefix lasts. If the prefix step has a different seat or bound, it records
  **DRIFT** (`:299-302`).
- After that it draws `completion.nextInt(bound)`, and it records every draw in `taken`.

A draw with `bound <= 1` makes no Step (`Search.nextInt`, `:339`). Draws stop being recorded after `maxDraws`
(3000, `PZL_SEARCH_MAX_DRAWS`, `:314-323`), and the runner then ends the game unsolved (`LlmSpikePuzzle.java:638-642`).

**The search.** `PuzzleSearch` sees only `Game.play(prefix, completionSeed) -> Rollout(steps, solved, drift, actions)`
(`PuzzleSearch.java:31-44`).
- `prove()` (`:114-133`) runs UCT `findWin` (`:250-274`, select `:296-322`). Solver nodes maximise the win rate and
  opponent nodes minimise it (`:312-318`). It then re-proves each other opponent value on the winning line
  (`:219-235`).
- `exhausted(path)` (`:283-292`) is true when every line under a prefix was played. Only then does NOT_FOUND mean
  "no line wins" (`Result.exhausted`, `:57`, `:130`). Otherwise NOT_FOUND means only "not found" (`:23-24`).
- With `CONTROLLER_B=ai` only the solver draws from the Line. The opponent is Forge's AI, which draws from the engine's
  `MyRandom`, so the proof has no opponent branches (`LlmSpikePuzzle.java:63-64`, `:381`; PUZZLES.md:130).

**The runner.** `LlmSpikePuzzle.search` (`:419-553`) proves on one engine seed. Each rollout re-seeds `MyRandom` with
that seed and replays the whole game from the start position (`replay`, `:555-566`, `MyRandom.setRandom` at `:559`).
The proof's main line is replayed twice more and must give the same game log (`:474-483`).

**Decision hooks the search arm covers.** These are the arm branches `rnd != null` in `LlmSpikeGui.java`:

| hook | search-arm encoding | line |
|---|---|---|
| play / priority menu (`chooseSpellAbilityToPlay`) | one draw, bound = `passIdx` (spells + lands + PASS, PASS last) | 4356 |
| every `decideOne` ask: target, binary, confirm, color, zone-change, phyrexian, entity, ... | one draw, bound = options + 1 if there is a decline option | 2088-2096 |
| every `decideMany` ask: sacrifice, attackers, cards-for-effect, splice, mana-sources, costs, ... | one include-or-skip bit per option, skipping the bits `lo`/`hi` force (`PuzzleArms.subset`, `:95-105`) | 2159-2172 |
| order / trigger-order asks | a permutation, bounds n, n-1, ... 2 (`PuzzleArms.permutation`, `:115-121`) | 2161-2164 |
| repeatable modes (`decideModesRepeat`) | always `min(num, 8)` picks with replacement | 2226-2236 |
| divided damage / allocation | one draw per amount except the last | 2295-2307 |
| blockers | one draw over the engine's legal block sets (`legalBlockSets`) | 3901-3910 |
| number over a range wider than 12 | `nextLong(min, max+1)`; ranges of 12 or less go through `decideOne` | 6372-6383 |
| card name | one draw over `faces` | 6600-6601, 6652-6658 |
| mulligan | no draw: always keep | 2947 |

The search arm and the model seat build the same play menu (`faithfulMenu`, `:9128-9130`; used at `:4115`). Both go
through the same `disambiguate`d option lists in `decideOne` / `decideMany` (`:2085`, `:2155`). So for index-encoded
asks, the menu the model answered and the menu the arm draws from are built by the same code.

## 2. What a recorded attempt keeps per decision

**The decision row** (`emitDecisionRow`, `LlmSpikeGui.java:11494-11555`), one `t:"decision"` JSONL row per decision:
- `seat`, `turn`, `phase`, `type`, `n_options`, `choice`, `tier`, `options` (the exact disambiguated labels, `:8576`).
- `picks` (0-based indices) on multi-pick rows only.
- `prompt` and `raw_reply` (the tool answer rendered to text, e.g. `...CHOICE: 7\nPROFILE: ...`), `answer_via`, tokens,
  `shadow_choice`.

The row does **not** keep:
- the `min`/`max` of a `decideMany` ask;
- the legal block sets;
- the answer's side fields as structured data: `keep_untapped` (`:4422-4423`), which changes which lands are tapped,
  and `yield` (`:4537`), which skips later windows.

These are present only inside `raw_reply`, or exactly in the HTTP transcript.

**Encoding differs by row type:**
- On a `play` row, `n_options` = `passIdx` (PASS is included in `options`), and PASS is written as `choice: -1`.
- On a `decideOne` row, `n_options` = `options.size()` without the decline, and a decline is `choice: -1` (`:2093`,
  `:2115`).
- On a multi-pick row, `choice` is the number of picks (`:2172`), so `picks` is required.

**Skipped windows** are `t:"window"` rows with a `reason` and a `castable` count (`skipWindow`, `:8524`). The skips are:
- `nothing-usable` (`:4128-4129`), which applies to every arm;
- `last-sig` (`:4215`), `react-sig` and the `SKIP_HEURISTICS` gates (`:4092`, `:4099`, `:4157`), which apply to the
  model seat only (`rnd == null`). `SKIP_HEURISTICS` is off by default (`:884`);
- `yield:*` (`:4147-4153`), set only by a model answer.

Rows written by the harness (`tier:"auto"`, `n_options:-1`, e.g. `splice(none payable)`, forced phyrexian) come from
code both seat kinds run (`auto`, `:8475`; `:3356`, `:5607`).

**The puzzle row** (`LlmSpikePuzzle.writeRow`, `:915-955`) keeps:
- `seed` (`:930`), `opponent_arm` (`:932`; `puzzle_solve.py:275-277` always uses `CONTROLLER_B=ai`,
  `PZL_SEEDS=1..k`);
- `pzl_sha256`, `harness_epoch`, `solver_actions`, `log_sha256`;
- `transcript_engine_file` (the whole Forge game log, `Transcript.engineLog`, `Transcript.java:210`) and
  `transcript_http_files` (exact request and response bodies, `Transcript.java:24-33`, with TRANSCRIPTS=1).

So **the engine seed is recorded**, and the transcript holds the exact tool-call arguments of every answer.

**Real rows read.**
- `runs/tvs/{A-low-k1,B-max-k1,C-low-k3}/hard-{storm,splice}-*/game-*.jsonl`: 15 attempts, all at
  seed 1..3, `opponent_arm: ai`, epoch 80, solver `claude-cli:` Haiku 5.5 (dev lane). The build is kept:
  `build.json` -> `runs/harness-acceptance/tvs-9baac04` (classes present). Transcripts are present.
  - Decision types are **only `play` and `target`** (model tier), plus `auto` splice, phyrexian and trigger-order rows.
  - `keep_untapped` appears in at most 2 replies (B-max storm 88f9).
  - There are no yield, last-sig or react-sig windows; only `nothing-usable`.
  - The splice board `hard-splice-06dad20af5b3` failed in all 4 scored attempts (A seed 1, C seeds 1-3): "life p1 <= 0
    (was 4)", 16-19 decisions each. The storm attempts all solved (11).
- `runs/puzzles`: 1941 game files with model decisions.
  - Across the corpus: play 30183, target 5713, sacrifice 3743, trigger-order 1722, confirm-trigger 1640, scry 1251,
    attackers 527, ...
  - Windows: `nothing-usable` 12911, `yield:end-of-turn` 961, `yield:my-next-turn` 22.
  - Luna failed attempts: hard-gen-luna-check 48 of 68 (epoch 70, no transcripts), hard-gen2-luna 141 of 156,
    gen3-luna 41 of 44, bench-v1 30 of 290.

## 3. Can the model's choices be mapped to Step indices deterministically?

**Partly. The menus and their order are the same, but the decision points and the RNG are not.**

**What maps exactly.** Index-encoded asks: `play`, and every `decideOne` (target, binary, confirm, color, ...).
- The option list is built by the same code, in the same order.
- The arm's bound is the menu size: `passIdx` for play, `options + decline` for `decideOne`.
- Mapping: on a play row, `value = choice < 0 ? n_options - 1 : choice` with `bound = n_options`. On a `decideOne` row,
  `bound = n_options + (decline ? 1 : 0)` and `value = choice < 0 ? n_options : choice`. The row does not say whether a
  decline existed. Recover it from `prompt`, or from whether `choice == -1` was possible.
- For the tvs splice and storm attempts, every model decision is of this kind.

**What does not map from the rows:**
1. **Decision points differ.**
   - The model seat skips windows the search arm draws at: `yield:*` (983 in the corpus), `last-sig`, `react-sig`, and
     the forced-PASS menu (`:4209`, which draws nothing in Search anyway).
   - A yield window would become a PASS Step, but its bound is not on the row: `castable` counts spells, not lands
     (`:4128`, `:4215`).
   - Mana-source asks retry for the model (`retryNote`) but break for an arm (`:5725`).
2. **Encodings differ.**
   - Subsets are bits, and which bits draw depends on `lo`/`hi`, which are not logged.
   - Blocks are an index into `legalBlockSets`, which is not logged.
   - Repeatable modes always take `min(num, 8)` picks, so a model that chose fewer has no encoding.
3. **Side effects the Step cannot carry.** A play answer's `keep_untapped` changes which lands are tapped, and the arm
   has no equivalent. The game state then differs **silently**, because `Line` checks only seat and bound
   (`PuzzleArms.java:299-302`).
4. **The RNG is one draw off.**
   - A search seat draws one `MyRandom.nextLong()` at construction (`LlmSpikeGui.java:1329`). A model seat draws none.
   - So in a search rollout the engine stream (Forge AI's `percentTrue`, shuffles, coin flips) is shifted by one long
     against the model's game.
   - That is harmless on boards with an empty opponent and no randomness, but wrong in general.
   - The seed itself is fine. It is recorded, and both runners seed `MyRandom(seed)` before every game
     (`LlmSpikePuzzle.java:399`, `:559`; `applySeed`, `LlmSpikeGui.java:519-528`).
   - Lookahead does not touch the live RNG (`PlayerLookahead.java:24-26`, `:91-109`). The shadow does not either
     (`LlmSpikeGui.java:~8660`).
5. **The opponent.**
   - The recorded opponent is plain `ai`. It logs nothing and cannot be forced (`LlmSpikeFork.java:75-77`).
   - So its prefix choices cannot be replayed as Steps for `CONTROLLER_B=search`.
   - With `CONTROLLER_B=ai`, after a faithful prefix the AI's replies are a deterministic function of
     (seed, line). "Winnable" then means "winnable against this Forge AI on this seed", which is the condition the
     PUZ score is measured under.

**The change that removes all five: a new replay arm that hands over.** Replay the model's own answers through the
model-seat code. Every skip, encoding, side effect and RNG draw is then exactly as in the recorded game. Then hand the
same seat to the search arm at decision K.

- **`ReplayBridge`** (new, `S/ReplayBridge.java`, modelled on `ScriptBridge.java:41-120`), spec `replay:<game.jsonl>`.
  - It answers each `answer` call with the recorded call: the exact tool arguments from `transcript_http_files`
    (choice, picks, `keep_untapped`, `yield`, pairs).
  - Where there is no transcript, it falls back to the row's `choice`/`picks` and refuses any row whose `raw_reply`
    carries `keep_untapped`, `yield` or a block.
  - It is keyed by decision ordinal plus `(type, options)`. Any mismatch is DRIFT, never a guess.
- **Handover in `PlayerControllerLLM`.**
  - `rnd` becomes non-final (`:1284`).
  - After the seat's K-th model decision row (`evalDec`, `:8569`), a replay seat sets
    `rnd = new PuzzleArms.Search(0L, seat)` without drawing from `MyRandom`. Search ignores its own seed: every draw goes
    to `searchLine`.
  - All later hooks then take the arm branches. The first Step of the Line is decision K+1 exactly.
  - The menus stay the same, because `observer != null` keeps `faithfulMenu` true.
- **Runner.** A mode `CONTROLLER_A=replay-search` in `LlmSpikePuzzle.search` (`:381`) builds the replay seat for every
  rollout. The rest (`PuzzleSearch`, the twice-replayed main line, drift, `exhausted`) is unchanged.

The repository already contains the pieces this needs:
- `LlmSpikeFork` already replays a game by decision index and forces an option at one index (`FORK_FORCER`,
  `LlmSpikeGui.java:468-481`; the play hook at `:4512`). It refuses `llm` seats only because it cannot replay them
  (`LlmSpikeFork.java:38-42`, `:113-136`). The ReplayBridge removes that reason.
- `ScriptBridge` already proves "a written line against the search opponent" (`LlmSpikePuzzle.java:378-382`).

## 4. Cost

- **Per rollout.** 166 search results in `runs/puzzles/**` logs (`[PZL] search ...: proven|not_found after N rollouts
  (Ts)`):
  - median 0.70 s per rollout;
  - proven: 61, mean 99 rollouts / 75 s;
  - not_found: 105, mean 283 rollouts / 211 s;
  - median 115 s, p90 334 s per search.
  - Add about 10 s of JVM start per launch (`LlmSpikePuzzle.java:387`).
- **Budgets.** The default `PZL_SEARCH_BUDGET` is 2000 and `PZL_SEARCH_LINES` is 32 (`LlmSpikePuzzle.java:424-427`).
  The gate uses 600 (`scripts/puzzle-gate/gate10.sh:30`), the prove runs 200 (`prove-all-e6x-b200`), PUZZLES.md:122
  uses 1500. A rollout caps at 3000 draws, and the gate caps each game at 60 s (`gate10.sh:30`).
- **The hard limit.** The prover found 4 of 25 Forge puzzles at budget 200 and not a 7-action line at 600. "A 12-action
  line with three options a step is one line in five hundred thousand" (`docs/harness/HARD-PUZZLES.md:130-133`).
  Early positions of the hard-gen boards (13-15 steps, PUZZLES.md:263) **will come back not_found**. Late positions
  are small trees and usually come back exhausted, which is an exact answer.
- **The estimate.** Take an attempt of M index-encoded decisions with n_k options each, budget B, and 0.7 s per rollout.
  The replay prefix costs nothing extra, since every rollout replays from the start anyway.
  - **Full floors** (one search per option at every decision): sum_k n_k searches. For splice (M ~ 17, sum n ~ 55) at
    B = 600 that is at most 55 x 7 min ~ 6.4 h per attempt on one core. It is far less in practice: late positions
    exhaust in seconds, and proven positions average about 100 rollouts.
  - **Regret only.** Against a deterministic opponent, W is monotone: once false it stays false. So locate the step by
    scanning backward from the end (cheap, exact) or by bisection, about log2 M + 2 searches. Then take the floor only
    at that step (n_k* searches). That is about 10 searches, at most 70 min per attempt, about 18 min at `-j 4`.
  - **Solved attempts cost about one rollout.** The model's own line is the witness that every position on it is
    winnable, so the search finds nothing new there. That one replay is the fidelity check.
  - **Money:** $0. The runs are zero-model and loopback.

## 5. Minimal design

**Files**
- `S/ReplayBridge.java` (new): the recorded-answer bridge from section 3, with `drift`, `exhausted`, `report()` in the
  same shape as `ScriptBridge.report()` (`ScriptBridge.java:104-120`).
- `S/LlmSpikeGui.java`:
  - make `rnd` non-final;
  - add the handover in `evalDec` once a `ReplayBridge` seat reaches `REPLAY_HANDOVER=K`, constructing `Search` with
    no `MyRandom` draw;
  - nothing else changes on the model path.
- `S/LlmSpikePuzzle.java`: `CONTROLLER_A=replay-search` with `REPLAY_GAME=<game.jsonl>`, `REPLAY_HTTP=<transcripts>`,
  `REPLAY_HANDOVER=K` and an optional `REPLAY_FORCE=o`. `REPLAY_FORCE` becomes a one-Step Line prefix
  `[Solver:n_K:o]`, and the runner checks that `n_K` equals the recorded row's bound before searching.
- `eval/puzzles/step_regret.py` (new):
  - reads an attempt's rows;
  - picks the positions (backward scan, then the floor at the found step);
  - launches the runner (several K in one JVM if `search()` is looped, to avoid the 10 s start);
  - writes the output.
- Tests: `ReplayBridgeTest` (a recorded fake-model game replays to the same `log_sha256`; one changed label
  is DRIFT). Also extend `PuzzleArmsTest` for a handover Line starting at K.

**Output** (additive JSONL, one row per searched position, `t:"step_regret"`, beside `puzzle-results.jsonl`)
- Identity: `id`, `pzl_sha256`, `seed`, `attempt_rollout`, `source_game`, `build_overlay_sha`, `harness_epoch`.
- Position: `k` (decision ordinal), `type`, `options`, `model_value`, `forced` (null or o).
- Result: `verdict` (`proven` | `exhausted_unwinnable` | `not_found` | `drift`), `replays`, `budget`,
  `capped_rollouts`, `line` (the witness).
- Per-attempt summary row:
  - `regret_k`: the first k with W(k-1) proven and W(k) `exhausted_unwinnable`, else null;
  - `regret_bracket` [i, j]: the last proven and the first exhausted position, when they are not adjacent;
  - `floor_k`: the share of options at regret_k whose forced search is proven, defined only when every option is
    proven or exhausted;
  - `undetermined_options`.

**The floor**
- `floor_k = |{o : W(after forcing o at k) = proven}| / n_k`, over the same menu the model saw (the play menu
  includes PASS).
- It is exact only when every option is `proven` or `exhausted_unwinnable`. Otherwise record the bracket
  `[proven/n_k, (n_k - exhausted)/n_k]`.
- The benchmark line: at **critical** decisions (W(k-1) true and floor_k < 1), the model's keep rate against the mean
  floor_k. That mean is the trivial baseline, per the "a score without its floor" rule.
- v1 covers index-encoded decisions only (play and decideOne types), as fork v1 limits itself to play and mulligan
  (`LlmSpikeFork.java:72-74`). Multi-pick, order, block, allocation and number positions are replayed but not scored.

**First free experiment**
1. **Fidelity.** Use the 11 solved tvs storm attempts and the 4 failed splice attempts:
   `runs/tvs/*/hard-{storm,splice}-*`. Their build is kept (tvs-9baac04), they have transcripts,
   their seeds are 1-3, and they are play and target only.
   - Run `replay-search` with K = all decisions and budget 0.
   - Each must reproduce its own `solved` / `acceptance_failed` and match `transcript_engine_file` line for line.
   - This is 15 rollouts, a few minutes. A mismatch here stops the project.
2. **Regret.** On the 4 failed splice attempts (`hard-splice-06dad20af5b3`, 16-19 decisions, "life p1 <= 0 (was 4)"),
   scan backward at B = 600 and take the floor at the found step.
   - Expect about 10 searches per attempt, under 5 h on one core in total, about 1.2 h at `-j 4`. $0.
   - Cross-check winnable positions against the board's exhaustive model (`eval/puzzles/puzzle_gen.py:261`
     `splice_best`), only as a check on the search budget, never as the verdict.
3. **Only then** on an epoch-82 Luna run with transcripts. The older Luna runs (hard-gen-luna-check, epoch 70, no
   transcripts, build gone) will probably drift, because the menus have changed since.

**Main risks**
- **Budget-limited not_found is not "unwinnable".** It is the whole risk on long boards. Mitigations:
  - label W false only on `exhausted`;
  - report the bracket otherwise;
  - use the monotonicity of W as a consistency check: an earlier position not_found but a later one proven means the
    budget was too small at the earlier one;
  - prove winnable cheaply with a witness line (any continuation the script arm or the search plays to a solve).
    Unwinnable is the expensive side.
- **`exhausted` is a bit generous.**
  - A rollout stopped at `maxDraws`, or ended by the wall-clock cap (`invalid`), is counted unsolved and terminal
    (`PuzzleSearch.java:265-267`; `LlmSpikePuzzle.java:449-451`).
  - "Exhausted" therefore treats a capped line as a loss. Record `capped_rollouts` and do not call a position
    unwinnable if any of its rollouts was capped.
  - Cap rollouts by draws, not by wall clock: the gate warns that a rollout capped under load steers the search
    elsewhere (PUZZLES.md:240-241).
- **Drift** (build or epoch). The menus must be byte-identical to the recorded ones. Run on the recorded build
  (`build.json`) or refuse on the first `(type, options)` mismatch.
  - Silent divergence through side fields (`keep_untapped`, `yield`) is why the replay must use the transcript's exact
    tool arguments, and why the engine-log comparison runs.
- **Opponent mismatch.**
  - v1 is `CONTROLLER_B=ai`, as recorded: "winnable against this Forge AI on this seed". That is the PUZ condition,
    but it is not game-theoretic. An AI blunder can make a position "winnable".
  - Adversarial W (`CONTROLLER_B=search`) needs the opponent's prefix choices. Plain `ai` does not log them. An
    `ai-log` opponent in new attempts would (`LlmSpikeFork.java:108-113`). On the hard-gen families the opponent
    controls nothing, so the two agree there.
- **One seed per position.** The search does not branch on the engine seed (PUZZLES.md:136). On a board with hidden
  randomness, W holds for that seed only.
- **Determinism of the model seat path.** The model path makes no `MyRandom` draws outside isolated lookahead and
  shadow (checked above). But only the fidelity step (experiment 1) proves this for a given build. A static carried
  across the `PZL_SEEDS` attempts of one JVM is the thing to watch.

## Verdict

**Feasible with one change. It is not feasible as the code stands.**

The prover, Forge adjudication and the engine seed are all in place, and the menus are built by the same code for the
model and the search arm. But the search arm's Steps cannot faithfully encode a model's recorded game:
- windows the model skipped (yield, last-sig);
- multi-pick, block and allocation encodings, and `keep_untapped`, which are not logged in a form a Step can carry;
- one extra `MyRandom` draw at the search seat's construction.

The change is a `ReplayBridge`, which replays the model's exact tool answers through the model-seat code, plus a
handover of the same seat to `PuzzleArms.Search` at decision K, with no RNG draw and `rnd` made non-final. That is about
three files plus a Python driver, and nothing changes on the scored path.

With that, the experiment is free and the time is small: a fidelity pass on the 15 kept tvs attempts in minutes, then
step regret on the 4 failed splice attempts in about 1-5 CPU-hours. The labels are exact where the search exhausts,
which means near the end of long boards. On early positions of 13-15-step boards expect not_found, and report a
bracket, not a step.

