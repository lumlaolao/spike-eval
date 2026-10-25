# Per-decision latency: the 2026-10-13 measurements and the knobs

The 2026-10-13 run directories were not kept; these numbers cannot be reproduced and are kept as a dated record.

A turn can hold dozens of model decisions, so seconds per decision decide whether a game finishes. This page
records what was measured on 2026-10-13, on `deepseek/deepseek-v4.1-flash` through OpenRouter, mirror Burn, one game
per arm (so read the throughput columns as indicative, not as rates), and says which knobs exist now.

## What is set now

- **One tier is the default** (owner, 2026-10-15): every decision goes to the smart row. `run_batch.py --two-tier`
  (`ONE_TIER=0`) is the measured opt-out that sends minor decisions to a fast tier.
- **Token budgets, timeouts, reasoning settings and the truncation retry** are set in `ModelCfg.buildTable` and the
  OpenRouter bridge's truncation retry (`LlmSpikeGui.java`); [MODEL-LIMITS.md](MODEL-LIMITS.md) gives the values and
  why each is a loop guard. Every field is tunable without a rebuild through `OPENROUTER_MODEL_CFG` (JSON per row),
  and the effective table is hashed into `model_cfg_sha` (`ModelCfg.canonical()`), so runs configured differently
  do not pool.
- **Opt-in routing levers:** `TIER_FAST_TYPES` (`run_batch.py --tier-fast-types`, for example `play-single`, which
  sends an own-main-phase window with exactly one play to the fast tier) and `PLAN_EXECUTE`
  (`--plan-execute`). Compound actions stay off (owner, 2026-10-16): the plan is a note, re-read at every decision.

## The fast tier with no reasoning parameter (fixed 2026-10-13)

The fast tier answered mechanical questions (which target? keep or mulligan?) but sent no `reasoning` parameter, so
a reasoning model spent its whole 4000-token budget on hidden reasoning before any answer token:
`finish_reason=length`, a retry at double the budget, two calls and 182 seconds for one number. Setting the fast
tier's effort to minimal (reasoning is off there now) took retries from 4 to 0. The arms are in the levers table
below.

Prompt about 5.1k to 5.5k tokens; smart-tier completion mean about 1.3k to 2.3k, max about 8.5k to 13k.

The general shape: a reasoning model asked for one token still reasons first. A tier whose contract is "terse" must
turn reasoning off explicitly.

## One real play prompt under each reasoning setting

Replaying a real smart-tier play prompt (2026-10-13):

| setting | latency | completion tokens | of which hidden reasoning |
|---|---|---|---|
| `effort=medium` | 6.8-7.3 s | 815-848 | 770-804 |
| `effort=low` | 5.9-9.6 s | 759-1025 | 714-999 |
| `effort=minimal` | 7.6-10.4 s | 910-1247 | 875-1194 |
| no `reasoning` key | 17.3-23.7 s | 2704-4019 | 2657-3965 |
| `reasoning.enabled=false` | 1.3-12.7 s | 65-263 | 0 |

About 95% of every completion was hidden reasoning for an answer of about 50 tokens. `effort` did not reliably
reduce it; only disabling did, and `reasoning.max_tokens` was not honoured (it went up).

## The unbounded tail

A real game hit `[TRUNCATED->RETRY] finish_reason=length @ max_tokens=16000 -> retrying at 32000`: 16,000 tokens of
thinking with no answer, then 32,000 more, minutes of an 8-minute cap. Giving a reasoner more room after it spent
its budget thinking is the wrong response, which is why a reasoning call's retry turns reasoning off.

## The levers, one seed each (2026-10-13)

Per-decision columns are the signal; with one seed per arm the turn counts are noise. The two records of these arms
disagree on the baseline's retries (4 in the prose above, 1 here); with the run directories gone, which is right
cannot be checked.

| arm | turns | decisions | smart | fast | plan | auto | completion median | completion max | retries |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 7 | 40 | 26 | 5 | - | 9 | 720 | 6,564 | 1 |
| + fast tier disables reasoning | 8 | 40 | 30 | 1 | - | 9 | 488 | 8,519 | 0 |
| + smart tier `effort=low` | 9 | 54 | 39 | not recorded | - | not recorded | not recorded | not recorded | 3 |
| + tier routing | 9 | 58 | 24 | 23 | - | 11 | 103 | 15,539 | 2 |
| + smart budget 4000 | 8 | 44 | 20 | 11 | - | 13 | 175 | 3,784 | 1 |
| A: + `play-single` routing | 11 | 65 | 18 | 37 | - | 10 | 49 | 3,314 | not recorded |
| B: A + plan-then-execute | 10 | 63 | 10 | 34 | 7 | 12 | 26 | 3,642 | not recorded |

- With reasoning off, `target` and `prevent-effect` answered with a median of 6 completion tokens.
- `play` was the cost: in the tier-routing arm, 34 of 58 decisions, median 394 completion tokens, one of 15,539
  (about 50 s).
- `play-single` took `play`'s median from 309 to 80 tokens; plan-then-execute fired 7 times and halved the smart
  calls (18 to 10). Its interruption signature is opponent-side only, so it cannot notice that a step stopped being
  right for a reason on the seat's own side.
- The 4000-token smart budget of that day is not the budget now: the smart row was raised to 32000 on 2026-10-15
  because the cap clipped the hardest decisions ([MODEL-LIMITS.md](MODEL-LIMITS.md)).

## The playtest (4 games per arm against Forge's AI, mirror Burn, 20-minute cap)

`--pairs` swaps seats, so each arm played both sides of the play and draw. All 8 games finished.

| arm | LLM won | smart | fast | plan | completion median | completion max | retry time | calls a turn |
|---|---|---|---|---|---|---|---|---|
| base (no levers) | 2/4 | 59 | 19 | 0 | 250 | 3,118 | 410 s | 2.0 |
| levers on | 3/4 | 28 | 40 | 8 | 47 | 3,725 | 32 s | 1.9 |

Both arms played 39 turns. 3/4 against 2/4 is one game apart and is not evidence of quality: four games per arm can
show that an arm is broken, not that it is 5% weaker. Neither lever has a quality measurement.

## How to measure a change

Single games are dominated by variance (on the same seed one arm made 54 decisions and another 40). Use paired seeds,
state the cost first (`python3 eval/cost_report.py <runs> --plan N`), start with a small block, and read the block
before buying the next:

```bash
python3 eval/run_batch.py --seeds 42..44 --pairs --jobs 1 --deck eval/decks/burn.txt \
  --smart-a openrouter:openai/gpt-6-luna --fast-a openrouter:openai/gpt-6-luna \
  --controller-a llm --controller-b ai --memory-tools --budget-usd 0.40 \
  --out runs/perf/<arm> --cap-minutes 120
```

Then read `retry_ms`, `attempts`, `completion_tokens` and decisions per minute from the game lines. The acceptance
`canary` stage guards the zero-model paths; latency work should not move it.
