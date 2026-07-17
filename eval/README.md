# eval/ — batch driver + statistics for the LLM-vs-LLM Forge eval

Two stdlib-only Python 3 scripts:

- `run_batch.py` — plays N headless games by invoking `bash run-llm-headless.sh`
  from FORGE_HOME with per-game env (`GAME_SEED`, `SWAP_SEATS`, `EVAL_OUT`,
  `GAME_CAP_MS`, per-seat models/controllers/decks). Resumable.
- `aggregate.py` — turns a directory of `game-*.jsonl` logs into win rates
  with Wilson 95% CIs, a paired analysis with a paired-bootstrap CI on the
  win-rate delta, and decision-level latency/fallback stats. `--json` for
  machine-readable output.
- `test_aggregate.py` — self-test over synthetic fixtures
  (`python3 eval/test_aggregate.py`).

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
   mirror cancels deck strength so the delta is pure pilot skill.
2. **Confounds off** — forces `MULLIGAN_HEURISTIC=0`, `SKIP_HEURISTICS=0`, and
   empty `PLAN_A`/`PLAN_B` (plans inject expert knowledge). Overriding any of
   these via `--env`, or passing `--plan-a/--plan-b`, is refused.
3. **`--jobs 1`** — parallel JVMs starve the `claude` CLI and inflate fallbacks.
4. **Per-tier cap** — if `--cap-minutes` is omitted it is derived: no llm seat
   → 15 min, all-haiku llm → 120, any opus → 240.
5. **Provenance + drift** — stamps `ENGINE_SHA` (forge `HEAD`), `OVERLAY_SHA`
   (sha256[:12] of the two *installed* spike files), `DECK_SHA`, and
   `RUN_DISCIPLINE` onto every game line, and refuses to run if the installed
   spike files have drifted from this checkout's `forge-overlay/` copies
   (`run bash install.sh first`).
6. **`--out` optional** — defaults to `runs/<UTC-timestamp>-<label>` under the
   repo root; the resolved dir is printed first thing.

Any violation exits **2** with a clear message. `--exploratory` skips guards
1–3 for quick pokes: it still stamps provenance (`discipline=exploratory`) but
prints a loud banner that the numbers are **not citable**.

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
    --smart-a haiku --smart-b haiku \
    --fast-a haiku --fast-b haiku

# Interrupted? Re-run the same command with the same --out — completed
# seed/swap combos are skipped (a combo is done once its JSONL has a game line).

python3 eval/aggregate.py runs/<the-printed-dir>            # human-readable
python3 eval/aggregate.py runs/<the-printed-dir> --json     # machine-readable
```

`aggregate.py` prints a **provenance block** (and a loud MIXED-PROVENANCE
warning if the dir blends incompatible runs), and enforces a **fallback gate**:
`--max-fallback-rate` (default 0.10) over decisions of llm-controlled
identities; exceeding it prints `FAILED FALLBACK GATE` and exits **2** after the
report. The `ai` tier count is **partial** (un-overridden decisions run on
Forge AI invisibly), so `ai=0` never means "the model drove everything".

Baseline / exploratory arm example (not citable — mismatched decks, plans):

```bash
python3 eval/run_batch.py --seeds 1..30 --pairs --exploratory \
    --deck-a eval/decks/grixis-reanimator.txt --deck-b eval/decks/mardu-energy.txt \
    --controller-a llm --smart-a haiku --controller-b random
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
