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

## Example: haiku-tier mirror run

```bash
# 20 seeds x 2 seat orders = 40 games, one JVM at a time (claude CLI rate
# limits usually make --jobs > 2 counterproductive: decisions time out and
# fall back to auto, polluting the eval).
python3 eval/run_batch.py \
    --seeds 1..20 --pairs \
    --out runs/haiku-mirror-01 \
    --controller-a llm --controller-b llm \
    --smart-a haiku --smart-b haiku \
    --fast-a haiku --fast-b haiku \
    --cap-minutes 30

# Interrupted? Re-run the same command — completed seed/swap combos are
# skipped (a combo counts as done once its JSONL has a final game line).

python3 eval/aggregate.py runs/haiku-mirror-01           # human-readable
python3 eval/aggregate.py runs/haiku-mirror-01 --json    # machine-readable
```

Baseline arm example (llm vs random):

```bash
python3 eval/run_batch.py --seeds 1..30 --pairs --out runs/llm-vs-random \
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
