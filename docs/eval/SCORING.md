# Scoring: panels, ratings, sanity arms and the benchmark version

This page says how a block of games becomes a score. The design and its reasons are in
[research/2026-10-15-scoring-design.md](../../eval/research/2026-10-15-scoring-design.md) (sections 1, 2, 4,
5); the synthesis is [BENCHMARK-RESEARCH.md](BENCHMARK-RESEARCH.md). Everything here is Python
standard library, and every piece has a `test_<name>.py`.

| Piece | File | Answers |
|---|---|---|
| Panels | `suites/panels-v1.json` | which matchups, which seeds, which anchors |
| Launch | `run_batch.py --panel`, `--sanity-arms` | playing a panel and its free arms under one budget |
| STR-AI score | `panel_score.py` | per-cell win rates, the equal-weight pool, the cross margin, STR_norm |
| Sanity arms | `sanity_arms.py`, `aggregate.py --sanity-arms` | can a seat that does not play score? |
| Rating | `rating.py` | Bradley-Terry ratings with the Forge AI pinned at 1500 |
| Version | `benchmark_version.py` | which reports may be pooled |

## The panels (`suites/panels-v1.json`)

The deck pool is the 16 archetype lists the panels file names in `deck_pool` (which lists, and why:
its `deck_pool_note`), each pinned by `sha12` (sha256[:12] of the file, the same hash `run_batch.py`
stamps as `deck_sha`).

- **Mirror panel** (the headline): 16 cells, the pilot on deck *d* against the Forge AI on deck *d*.
  Deck strength cancels and the Forge-AI mirror is 50% by symmetry.
- **Cross panel**: 16 cells of the cyclic Latin square *d* vs *d+5* (mod 16, pool sorted by name), so
  every deck appears once on each side. Each cell has an **AI-vs-AI control** on the same deck pair.

Seeds come from the `stage3-holdout` eval-only bank (`seed_registry.json`), above the 7000-7799 that
`stage3-v1.json` uses: the pilot plays `7800..7849` in both seat orders (100 games per cell), the
control `7800..7999`. They are not a `seed_policy.py` reserved block, so `seed_registry.py check` is
what keeps them out of training corpora; it also reconciles a panels file's seeds. Every pilot
plays the same list, so pilot-vs-pilot differences are seed-paired. A smaller block uses a subset.

The `anchors` block fixes the scale: Forge AI = 1500 Elo, the rating prior N(1500, 400²), the
first-player prior N(0, 1) in logits, WR_AImirror = 0.5, and the random arm's 5-point no-skill band.

## Launching (`run_batch.py`)

```bash
# cost first: what a block like this costs (CLAUDE.md, cost rule)
python3 eval/cost_report.py --plan 96 runs/compare/two-tier
# the mirror panel, a 3-seed block per cell (96 games), with its free sanity arms
python3 eval/run_batch.py --panel mirror --seeds 7800..7802 --sanity-arms --budget-usd 15 \
    --controller-a llm --smart-a openrouter:openai/gpt-6-luna \
    --fast-a openrouter:openai/gpt-6-luna --out runs/panel-mirror-luna
```

- The seat plays the faithful profile (`v4-faithful`) unless `--context-profile` names another; that
  is `run_batch.py`'s default (`DEFAULT_CONTEXT_PROFILE`). `v1-compat` is the historical baseline and
  runs only as a named arm.

- `--panel mirror|cross` plays every cell under `--out` (`--panel-cells a,b` for a subset, which is
  not scored as valid). Identity A is the pilot you give; identity B is the panel's opponent, so
  `--deck*`, `--controller-b` and `--smart-b/--fast-b` are refused. `--seeds` defaults to the
  panel's published list and must be a subset of it.
- `--budget-usd` is **required** with `--panel` and `--sanity-arms`, and with `--panel` it caps the
  spend of every directory under the root, not one cell. A sanity arm against the Forge AI costs
  nothing and still runs after the scored block used the budget up; one facing a paid identity B
  keeps the budget.
- The cross panel plays its control per cell on a prefix of the control seeds four times as long as
  the pilot's block (`--no-control` skips it). A cross cell is the one asymmetric matchup cited mode
  accepts: both decklists must still hash to the panel's pins. Its game lines carry
  `deck_sha = <sha a>+<sha b>`.
- `--sanity-arms` plays, after each scored block, the same seeds and decks with identity A as `floor`,
  `random`, and the LLM seat on the loopback fake model (`fake_model_server.py`) with
  `--policy empty` and `--policy garbage`. The fake model is priced at $0 in `pricing/prices.json`;
  the loopback endpoint needs no API key and no egress consent. Each arm records `fake_policy` in its
  manifest; the port is not recorded.

Directory layout of a panel run:

```
ROOT/<cell id>/                   the pilot's games (one experiment, one manifest)
ROOT/<cell id>__control/          cross panel: the AI-vs-AI control
ROOT/<cell id>__sanity-<arm>/     floor | random | empty | garbage
ROOT/<cell id>__sanity-<arm>.server/fake_requests.jsonl   what the fake model was asked
```

A plain block with `--sanity-arms` gets the same siblings: `<out>__sanity-<arm>`.

## STR-AI score (`panel_score.py`)

```bash
python3 eval/panel_score.py runs/panel-mirror-deepseek --panel mirror [--score strict] [--json]
```

| Number | Definition |
|---|---|
| cell score | wins / decisive games (`--score decisive`, the default and the `aggregate.py` convention), Wilson 95% CI; the strict score (wins + draws/2 over every started game, unfinished = 0) beside it; the seat-1 win share |
| pooled | mean of the cell scores, each cell weighted equally whatever its game count |
| pooled CI | stratified: Var = Σ p̃(1−p̃)/n / M², with p̃ = (w + ½)/(n + 1) so a 0/n cell does not claim zero variance; and a cluster bootstrap resampling seeds (both seat orders together) within each cell |
| spread | worst cell, best cell, number of cells whose Wilson CI lies wholly below 50% |
| margin (cross) | per cell WR(pilot on d vs AI on d') − WR(AI on d vs AI on d'), pooled the same way; its variance adds the control's |
| STR_norm | (WR − WR_random) / (WR_AImirror − WR_random), **uncapped**. WR_random is the pooled random arm over the same cells; WR_AImirror is 0.5 (mirror) or the pooled control (cross). Not computed without a random arm in every cell |

A report is `valid` only when every cell (and control) exists and holds one experiment, identity B
is the Forge AI, every game line carries the cell's pinned `deck_sha`, only published seeds were
played, one pilot with one set of settings played every cell, `aggregate.py`'s fallback and
harness-decided gates pass in every cell, no sanity arm beats the floor, and every game belongs to
one benchmark version (a sanity arm too may play only published seeds), and only when it is complete and has its
random baseline. The verdict is one `status` (additive in `--json`): `NOT VALID` for any of the faults above;
`INCOMPLETE` when a cell or control is missing (a cell the budget stopped before its first game, a manifest and no
game file, counts as missing), a played seed has one seat order only (`pairs`; a seat order is filled by a game line
or by a crash its log names, see "Started games"), the played cells or the controls do not hold the same seeds (the
equal-weight pool or the margin is then not seed-paired), or a played cell has no score (no decisive game); `NO
RANDOM BASELINE` when the random sanity arm is not on the pilot's own seeds in both orders in every cell, so STR_norm
has no baseline (a score without its floor is not a score; the `floor` arm is a sanity check, not this baseline);
else `VALID`. Every played cell holding the same subset of the published seeds in both orders is `VALID`, and the
verdict line says how many of the published seeds that is (`games_played`, `games_expected`, `seeds_per_cell` in
`--json`). Exit 0 valid, 1 any other status (still printed), 2 refused.

**Started games.** The strict score's denominator is the started games: one per (seed, seat order) launched, in
`aggregate.py` and `panel_score.py` alike (`aggregate.place_crashes`). A game line is one. A file with no game line (a
crash, a kill, a backstop timeout) is one only when its slot holds no game line, so a crash left beside its replay is
not a second game. Its seat order is the one whose `run_batch.py` log (`game-<seed>-<0|1>.log`) names the file. A file
no log names is charged only when its seed has no game line in either order, at most as many per seed as the experiment
has seat orders (two when any game line or named crash is swapped, else one: an unpaired slot that crashed twice is one
game), and fills no seat order. It is not charged but counted (`games.unplaced_no_game_line`) with a warning when it
may be a missing order's crash: on a seed with a line in fewer seat orders than the experiment has, or past the one
order of an unpaired experiment. The strict score is then an upper bound. On a seed with a line in every order it is an
earlier attempt and is skipped. A file is charged only where its condition is known: in a directory that holds one experiment. In a
directory of several conditions `aggregate.py` charges it to none, counts it (`games.uncharged_no_game_line`) and says
so in a warning; `--experiment` does not change that. A file with no seed is never charged. A panel cell whose every
file has no game line has no known condition and counts as missing. A cell holding a game whose pilot lost a decision to
a dead model bridge is refused, and a resume plays its seed again (`docs/harness/ARCHITECTURE.md`, "A game the model's
bridge did not play is not scored").

**The pilot condition** (review E2, E9): the panels file's `pilot_condition` pins how an llm pilot plays:
the faithful profile, one tier, answers by the `answer` call, the native lookup tools and the lookahead
preview, no rules tool, no turn or one-chat window, no compound plans. `run_batch.py --panel` refuses flags
that give another condition, and `panel_score.py` refuses to score a pilot game that departs from it, even
with `--allow-mixed-conditions`. Another pilot condition is a new panels version. The answer-tool fields
belong to the llm seat, not the environment: a floor or random sanity arm beside a model pilot records
`answer_tool=false` and pools with it (before 2026-10-17 that refused every real panel with sanity arms).

## Sanity arms (`sanity_arms.py`)

```bash
python3 eval/sanity_arms.py runs/some-block          # or: python3 eval/aggregate.py runs/some-block --sanity-arms
```

| Arm | Rule |
|---|---|
| floor | wins nothing: a floor win means the opponent lost on its own or the harness played for the seat |
| empty, garbage | never win a (seed, seat order) the floor lost, and never more games in total |
| random | win rate at most the floor's + 5 points (random plays real menus, so a chance win is not a defect; more means the cell is too easy or the AI side is broken) |

`sanity_arms.py` exits 1 on a violation and 2 when an arm is missing; `aggregate.py --sanity-arms`
exits 5 and 6 for the same, after its own gates (2, 3, 4).

## Rating (`rating.py`)

```bash
python3 eval/rating.py ROOT/burn ROOT/burn__sanity-floor ROOT/burn__sanity-random [more dirs] [--json]
```

- Model: logit P(A beats B) = r_A − r_B + h·s + m(d_A, d_B) over decisive games, s = +1 when identity
  A sat in seat 1 (`swap` false). σ(h) is the first player's win chance between equals.
- Players: `llm:<smart>` (or `llm:<smart>/<fast>` when they differ), `ai`, `ai-log`, `random`, `floor`.
  A sanity arm on the fake model is `llm:openrouter:fake/harness-model[empty]` or `[garbage]`: the
  policy comes from the arm's manifest, and a loopback endpoint's port is not a setting.
- Matchups: m(d, d′), how much deck d favours its pilot against deck d′, one term per pair of
  different decks (antisymmetric, 0 in a mirror), with the weak prior N(0, 1) in logits. A game's decks
  are its run directory's `batch-manifest.json` `deck_a`/`deck_b`; a game without a manifest has no
  term. The AI-vs-AI controls of a cross panel pin each term down, so a pilot that only ever plays the
  favoured side is rated on its play, not its deck. Each term is reported with its CI.
- Scale: `ai` pinned at 1500 (400/ln 10 Elo per logit). Every other rating has the weak prior
  N(1500, 400²); h has N(0, 1). Floor and random, which win almost nothing, therefore stay finite.
  The prior is printed with every report.
- CIs: a cluster bootstrap (default 1,000 replicates) resampling seeds of one deck pair, both seat
  orders together, within each pair of players. Each player gets a rating CI and a **rank CI**.
- Sanity: floor ≤ random < ai and floor < ai in the point fit and in every replicate, or the report
  is invalid (exit 1). Floor and random tie exactly when both win nothing against the same
  opponents, so that pair is "floor not above random".
- Goodness of fit: observed vs predicted wins per pair of players, a z residual (flagged above 2), a
  chi-square over pairs. Intransitivity: directed 3-cycles among pairs whose head-to-head Wilson CI
  excludes 50%.
- Conditions: `aggregate.py`'s experiment key, split into the environment (engine, overlay,
  heuristics switches, context profile and sha, harness epoch), each llm player's own settings
  (tools, tiers, budgets, served model, ...) and the matchup (deck sha, plans). The environment must
  be one; each player's settings must be one across its games; decks may differ. Anything else is
  refused (exit 2) unless `--allow-mixed-conditions`, which the report then prints. `test_rating.py`
  fails when `aggregate.py` gains a condition field this split does not place.

## Benchmark version (`benchmark_version.py`)

`bv1-<12 hex>` hashes: `FORGE_COMMIT` (every game's `engine_sha` must equal it), the games'
`overlay_sha` (every overlay source, harness classes and engine patches; game lines from before
2026-10-16 carry a hash of `LlmSpikeGui.java` and `LlmSpikeHeadless.java` only), the deck pool (each
file checked against its pin), `seed_registry.json`, the panels file, every frozen
`suites/puzzles-*.json`, `tooluse/tools.json`, the panels file's `anchors`, and `context_profile`,
`context_sha` and `harness_epoch` as the game lines recorded them.
Games that disagree on any of these are refused. `panel_score.py` and `rating.py` stamp the id and
its components into their reports;
`python3 eval/benchmark_version.py --check-pool r1.json r2.json` exits 1 unless the reports share one
version.

## Not determined yet

- **Decisive vs strict as the headline.** Both are printed; the decisive score stays the default
  until the owner decides how a wall-clock cap is charged (BENCHMARK-RESEARCH.md, proposal 1).
- **Crash re-runs.** A crashed game counts 0 in the strict score and is absent from the decisive one.
  `run_batch.py` plays it once more on the same seed (`--retries`, default 1), setting the failed
  attempt aside under `crashed/`. Dropping the seed for every pilot when it crashes again is still
  manual.
- No panel has been played yet: the seeds above are unspent, and the first block should be the
  six-games-per-arm size the cost rule asks for.
