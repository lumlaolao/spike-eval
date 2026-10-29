# eval/: the batch driver, scoring and review tools

Every Python script here is stdlib-only and has its tests in `eval/tests/test_<name>.py` (or is listed in
`eval/UNTESTED.txt`). The core (running, scoring, review) sits at the top; three families have folders:
`demo/` (the live demo), `harness_env/` (the harness-building environment) and `puzzles/` (the puzzle tools,
beside the puzzle boards). `evalpath.py` puts every folder on `sys.path`, so a script imports any other by
its module name. Where the rest is described:

- the benchmark and its three scores: `docs/eval/SCORING.md`, `docs/eval/BENCHMARK-V1.md`;
- the puzzle suite (`puzzle_*.py`): `docs/eval/PUZZLES.md`;
- the review sheet after a game (`match_review.py`, `tool_audit.py`, `plan_audit.py`, `defect_analyzer.py`,
  `cost_report.py`): `.claude/skills/game-loop/SKILL.md`;
- how many games run at once: `docs/eval/CONCURRENCY.md`;
- the harness-building eval (`harness_env_*.py`): `docs/harness/HARNESS-ENV-FRAMEWORK.md`.

This file covers the game batch driver and its statistics:

- `run_batch.py`: plays N headless games by invoking `bash run-llm-headless.sh`
  from FORGE_HOME with per-game env (`GAME_SEED`, `SWAP_SEATS`, `EVAL_OUT`,
  `GAME_CAP_MS`, per-seat models/controllers/decks). Resumable.
- `aggregate.py`: turns a directory of `game-*.jsonl` logs into win rates
  with Wilson 95% CIs, a paired analysis with a paired-bootstrap CI on the
  win-rate delta, and decision-level latency/fallback stats. `--json` for
  machine-readable output.

## The paired-mirror design

Each seed is played **twice with the seats swapped** (`--pairs`):

- same `GAME_SEED` → same deck shuffles and coin flip in both games
- same deck on both seats (mirror), so deck strength cancels
- `SWAP_SEATS=1` on the second game, so each arm plays both the
  advantaged and disadvantaged seat of the *same* shuffle

The two games of a seed share almost all their randomness, so the
difference in outcomes within a pair is nearly pure arm skill. That
correlation is why `aggregate.py` bootstraps **at the seed level** (pairs
travel together in each resample) rather than resampling individual games —
resampling games independently would overstate the variance and produce a
CI wider than the design actually earns.

`aggregate.py` also prints a **seat-bias check** — the seat-1 (first
player) win rate, computed as wins-by-identity-A among `swap=false` games
plus wins-by-identity-B among `swap=true` games, over all decisive games.
In a swapped mirror it should hover near 50% even when one arm dominates
(the dominant arm wins from both seats equally often); a big skew means
seat order matters a lot or `SWAP_SEATS` isn't working.

## Why draws and invalids are excluded

Win rate answers "when somebody won, who was it?". A draw carries no
information about relative strength, and an *invalid* game (wall-clock cap
hit, crash, JSONL with no final game line) is a measurement failure, not an
outcome — folding either into the denominator would drag every arm toward
zero and make runs with different crash rates incomparable. Both are
counted and reported separately; a high invalid rate is a harness problem
to fix, not a result. A pair is dropped from the paired analysis if either
of its games is non-decisive (the decisive half still counts in the
unpaired table).

## Power math (why you need a lot of games)

Detecting a 10-point gap (55% vs 45%, or 60% vs 50%) with a two-sided
α = 0.05 test at 80% power needs roughly **390 games per arm** unpaired
(standard two-proportion sample size at p ≈ 0.5, where variance is
maximal). Rules of thumb at 80% power:

| true gap | unpaired games/arm |
|---------:|-------------------:|
| 20 pts   | ~97                |
| 10 pts   | ~390               |
| 5 pts    | ~1,560             |

Pairing shrinks this substantially: if outcomes within a seed-pair
correlate with ρ (shared shuffle + mirror deck removes most game-to-game
luck), the paired comparison needs roughly a factor (1 − ρ) fewer games.
With even a modest ρ ≈ 0.5 the 10-point gap needs ~200 games/arm instead
of ~390. The paired-bootstrap CI printed by `aggregate.py` measures the
realized variance directly, so you don't have to guess ρ — run a batch,
look at the CI width, and extrapolate (CI width shrinks ~1/√n in pairs).

Practical upshot: 20–50 paired seeds tells you about big gaps (random vs
llm); separating two decent models by a few points needs hundreds of pairs.

## Cited vs exploratory runs

`run_batch.py` defaults to **cited** mode — the only mode whose numbers may be
quoted. Before launching anything (and before any directory is created) it
enforces:

1. **Mirror deck** — `DECK_A` and `DECK_B` must resolve to the *same*
   non-empty file (pass `--deck PATH` to set both), and the two identity names
   must differ (`--name-a`/`--name-b`, default `Identity-A`/`Identity-B`). A
   mirror cancels deck strength so the delta is pure pilot skill. The one
   exception is a cross-panel cell (`--panel cross`): its two decks may differ,
   but both must still hash to the pins in the panels file.
2. **Confounds off** — forces `MULLIGAN_HEURISTIC=0`, `SKIP_HEURISTICS=0`, and
   empty `PLAN_A`/`PLAN_B` (plans inject expert knowledge). Overriding any of
   these via `--env`, or passing `--plan-a/--plan-b`, is refused.
3. **No Claude CLI seat**: a `claude-cli:` seat is a development lane, never in a cited number, so cited mode
   refuses it (`cited_pre_guards`); a bare Claude name (`opus`, `haiku`) is refused in every mode. All-`openrouter:`
   runs may run in parallel (RAM-bound, `docs/eval/CONCURRENCY.md` §7).
4. **Held-out seeds** — every seed must sit in an eval-only band of
   `eval/seed_registry.json` and in no training corpus (see below).
5. **Per-tier cap** — if `--cap-minutes` is omitted it is derived: no llm seat
   → 15 min, all-haiku llm → 120, any opus → 240, any other llm seat → 240.
6. **Provenance + drift** — stamps `ENGINE_SHA` (forge `HEAD`), `OVERLAY_SHA`
   (sha256[:12] over every overlay source as installed under FORGE_HOME:
   the harness classes and the files the engine patches make), `DECK_SHA`,
   `SEED_BANK` and `RUN_DISCIPLINE` onto every game line, and refuses to run if
   the installed files differ from this checkout's `forge-overlay/` and
   `engine-patches/`
   (`run bash install.sh first`).
7. **`--out` optional** — defaults to `runs/<UTC-timestamp>-<label>` under the
   repo root; the resolved dir is printed first thing.

Any violation exits **2** with a clear message. `--exploratory` skips guards
1–4 for quick pokes (drift is then a warning): it still stamps provenance
(`discipline=exploratory`) but prints a loud banner that the numbers are
**not citable**. In every mode an llm seat must name both its models
(`--smart-x` and `--fast-x`), or the run is refused.

The canonical mirror decks live in this repo:
`eval/decks/mardu-energy.txt` and `eval/decks/grixis-reanimator.txt` — pass one
of them to `--deck` for a citable mirror run.

## Example: cited haiku-tier mirror run

```bash
# 20 seeds x 2 seat orders = 40 games, one JVM at a time. --out is derived,
# the cap is derived (120 min for all-haiku), provenance is stamped.
python3 eval/run_batch.py \
    --seeds 1..20 --pairs \
    --deck eval/decks/mardu-energy.txt \
    --controller-a llm --controller-b llm \
    --smart-a openrouter:anthropic/claude-haiku-5.5 --smart-b openrouter:anthropic/claude-haiku-5.5 \
    --fast-a openrouter:anthropic/claude-haiku-5.5 --fast-b openrouter:anthropic/claude-haiku-5.5

# Interrupted? Re-run the same command with the same --out — completed
# seed/swap combos are skipped (a combo is done once its JSONL has a game line).

python3 eval/aggregate.py runs/<the-printed-dir>            # human-readable
python3 eval/aggregate.py runs/<the-printed-dir> --json     # machine-readable
```

`aggregate.py` prints a **provenance block** (and a loud MIXED-PROVENANCE
warning if the dir blends incompatible runs), and enforces a **fallback gate**:
`--max-fallback-rate` (default 0.10) over decisions of llm-controlled
identities; exceeding it prints `FAILED FALLBACK GATE` and exits **2** after the
report, and a **harness-decided gate**: a ceiling of 2% (no flag) on the
share of those decisions the harness made when the model could have (TAKEN,
LOST and Forge-AI rows); exceeding it prints `FAILED HARNESS-DECIDED GATE`
and exits **4**. The `ai` tier count is **partial** (un-overridden decisions run on
Forge AI invisibly), so `ai=0` never means "the model drove everything".

Baseline / exploratory arm example (not citable — mismatched decks, plans):

```bash
python3 eval/run_batch.py --seeds 1..30 --pairs --exploratory \
    --deck-a eval/decks/grixis-reanimator.txt --deck-b eval/decks/mardu-energy.txt \
    --controller-a llm --smart-a openrouter:anthropic/claude-haiku-5.5 --fast-a openrouter:anthropic/claude-haiku-5.5 \
    --controller-b random
```

## The JSONL contract (identity-keyed, final)

Ground truth is `LlmSpikeHeadless.java`; the game line is keyed by
**deck identity**, never by seat:

- `winner: "A"` means **deck-identity A** — the `DECK_A` / `DECK_A_NAME` /
  `CONTROLLER_A` / `SMART_MODEL_A` configuration — won, **regardless of
  seat swap**. The Java side maps by player name
  (`nameA.equals(winningLobbyPlayer.getName())`), so identity A is always
  arm A and `aggregate.py` never flips attribution on `swap`.
- `controller_a`, `smart_a`, `fast_a`, … always describe identity A's
  (resp. B's) **raw env config**, never post-swap seats.
- `swap: true` is pairing/seat metadata only: it records that identity B
  sat in seat 1 (went first into the players list); `swap: false` means
  identity A did. `aggregate.py` uses it for pairing the two games of a
  seed and for the seat-1 bias diagnostic — nothing else.
- One game per file is the contract, but `aggregate.py` tolerates two games
  interleaved into one file (same-millisecond parallel launches) and
  dedupes reruns of the same seed/swap combo by keeping the newest.
- A `.jsonl` file with no game line is a crashed game and counts as
  `invalid`. Malformed lines are skipped with a warning.
- **Resolved model identity** (additive, 2026-07-26). `smart_a` records the
  *configured spec*, which is not an identity: `openrouter:default_model`
  against a local server names no checkpoint, which is why
  `runs/stage1-student-eval` and `runs/stage2-student-eval` carry identical
  provenance despite being different adapters. `served_smart_a` /
  `served_fast_a` (and `_b`) record what the **endpoint said answered**, read
  from the response's own `model` field; `openrouter_base_url`,
  `checkpoint_sha` (from `--checkpoint`), `model_cfg_sha`, `temperature_a/b`
  and `seed_bank` complete the picture. A field the endpoint did not supply is
  `"unknown"` — never a guess inherited from the alias. `aggregate.py` prints
  a **model identity** block and exits **3** when a run stamped
  `discipline=cited` cannot name what played. Dirs written before these fields
  existed report `(pre-provenance)` and are excused, not failed.
- **Served identity partitions experiments as recorded** (2026-09-07). A game
  whose endpoint named nothing is its own experiment, never merged with,
  paired against or deduplicated against a same-seed game that did name a
  model, and two games that each resolved only `smart` or only `fast` never
  combine into a joint condition. Baseline seats (floor/random/ai) have no
  model to name and are reported `not-applicable`, never unresolved. See
  `docs/harness/history/EVAL-IDENTITY-IMPLEMENTATION.md`.

## Frozen suites + the holdout registry

A **suite** (`eval/suites/*.json`) is the frozen artifact a checkpoint gets
pointed at: declared eval-only seed bank, mirror decks, opponent ladder, and
gates — the deck/opponent half of "the eval is a versioned module". Numbers
are citable only when they come from a validated suite run.

- `eval/holdouts.json` — the deck registry. Every eval deck is
  `in-corpus` (appears in training data; evals on it are in-distribution
  only) or `held-out` (must never appear in a new training dataset).
  Held-out decks carry a `grandfathered` list: contamination that predates
  the declaration is frozen and labeled, not hidden — so a held-out deck is
  a valid generalization measure only for models trained on corpora built
  after its declaration date.
- `eval/check_holdouts.py` — audits `datasets/*/meta.jsonl` against the
  registry: prints deck presence per dataset/split and derivable matchups,
  warns on meta-less dataset dirs (invisible to the audit), and exits **1**
  if a held-out deck leaks into a non-grandfathered dataset. Run it after
  every corpus build.
- `eval/seed_registry.json` + `eval/seed_registry.py` — the seed-side
  equivalent, and the artifact `stage3-v1.json`'s `seed_policy` deferred to.
  Every seed band is declared `eval-only`, `train`, or `harness`; a band may
  also list `burned` seeds (harvested *before* the band was declared — the
  seed analogue of a deck's `grandfathered` list). `check` exits **1** if an
  un-burned eval-only seed appears in `datasets/*/meta.jsonl`, if a corpus is
  unreadable, or if a frozen suite arm draws on burned/training seeds.
  `audit` retroactively labels every dir in `runs/` with how contaminated it
  was. `run_batch.py` refuses a cited batch on both counts: seeds outside an
  eval-only band, and seeds already present in a corpus.
- `eval/run_suite.py` — validates a suite against the registry and the
  `run_batch.py` CLI contract (role/deck-status consistency, disjoint
  in-bank seed ranges, cited discipline, gates), then prints the exact
  per-arm commands. Exit **2** on any violation. It launches nothing.

- `eval/build_report.py` — the report builder. Points at the per-arm output
  dirs (default `runs/<suite>-<arm>`, overridable with `--arm-dir`), shells
  out to `aggregate.py --json` / `defect_analyzer.py` / `check_holdouts.py`
  (no statistics are recomputed — those tools stay the single source), and
  writes a frozen report folder: `suite.json` copy, `report.json`,
  `report.md`, and per-arm defect evidence. Verdicts: **PASS** (every arm
  present, all gates green) / **FAIL** (a gate broke — exit 1) /
  **INCOMPLETE** (an arm has no usable data — exit 3). Per-arm gates:
  fallback rate, defect invariants, `discipline=cited`, unmixed provenance.
  One report per checkpoint; never quote arms added after the fact.

```bash
python3 eval/seed_registry.py check                     # seed contamination gate
python3 eval/seed_registry.py audit                     # label past runs
python3 eval/check_holdouts.py                          # corpus audit
python3 eval/run_suite.py eval/suites/stage3-v1.json \
    --student openrouter:default_model                  # validate + plan
# ... run the printed run_batch.py commands ...
python3 eval/build_report.py eval/suites/stage3-v1.json \
    --student openrouter:default_model                  # frozen report
```

`eval/suites/stage3-v1.json` is the first suite: two cheap sanity arms
(floor, random), two 200-pair in-distribution headline arms vs Forge AI
(ur-delver, burn), one 200-pair generalization arm (painter, held-out
2026-07-26), and a disabled teacher-mirror arm pending a frozen teacher
spec. All arms are mirror-deck so they run under cited discipline;
asymmetric student-deck-vs-AI-deck matchups need a `cited-nomirror` mode in
`run_batch.py` (both deck SHAs pinned) and are deliberately not in v1.

## Improvement loop

Two more stdlib-only scripts close the loop between "harness has a defect"
and "harness is fixed, verify it stayed fixed":

- `improve_loop.py` — plays a small, cheap batch (default: 2 games,
  `--controller floor` — deterministic legal-fallback controller, free, no
  API key needed) by shelling out to `run_batch.py` in `--exploratory` mode,
  then runs `defect_analyzer.py` over the fresh output directory and writes
  `metrics.json` + `summary.txt` next to the game files.
- `defect_analyzer.py` — reads a directory of `game-*.jsonl` / `game-*.log`
  pairs and reports: fallback counts by kind, the `tier=ai` hard invariant
  (C1 — sacrifice/combat-damage delegated to Forge AI, MUST be 0), silent
  decision-drop reconciliation (C5 — counts each log-side `-> asking` window
  against its matching jsonl row over mulligan/attackers/blockers/play and
  flags any gap; this is exactly the bridge-down→PASS branch that skips
  `evalDec()`), no-CHOICE/parse-fail counts by seat, latency percentiles by
  tier/seat, `finish_reason=length` counts, forced single-legal picks
  mislabeled `tier!=auto` (C6), and per-seat mulligan-row coverage (C7 —
  `MULLIGAN_HEURISTIC` auto-keeps that never call `evalDec()`). Exits 1 if
  `tier=ai` count > 0 or any silent-drop gap > 0 anywhere in the corpus.
- `test_defect_analyzer.py` — synthetic-fixture self-test
  (`python3 eval/tests/test_defect_analyzer.py`).

### The iterate cycle

```bash
# 1. Run one iteration (floor mode: free, deterministic, no API key).
python3 eval/improve_loop.py --games 2 --controller floor
# prints the output dir, e.g. runs/improve/20260721-120000/, then writes
# runs/improve/20260721-120000/{metrics.json,summary.txt}

# 2. Read the metrics: any hard-invariant failure (tier=ai>0, silent drops>0)
# exits 1 and is spelled out in summary.txt with file/seat/turn detail.
cat runs/improve/20260721-120000/summary.txt

# 3. Fix the harness (forge-overlay/.../LlmSpikeGui.java), citing the exact
# defect class the summary flagged.

# 4. Rebuild with no game running (.claude/rules/forge-overlay.md):
# FORGE_HOME=/root/forge bash install.sh, then
# (cd /root/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests).

# 5. Rerun the same iteration and diff the two summary.txt files — a real
# fix drives the flagged count (or the whole hard-invariant gate) to zero;
# an unrelated regression shows up as a NEW count that wasn't there before.
python3 eval/improve_loop.py --games 2 --controller floor
```

`--controller llm` runs real model calls (`--seat-a`/`--seat-b`, default
`openrouter:google/gemini-3.5-flash` both — cheap) and refuses with a clear
message if `OPENROUTER_API_KEY` is unset; `--dry-run` prints the exact
`run_batch.py`/`defect_analyzer.py` commands it would run without launching
anything, useful for checking flags before spending API budget or JVM time.
`defect_analyzer.py` is also runnable standalone against any existing batch
(including `runs/tournament-1/match-*/`) for one-off defect audits outside
the loop.
