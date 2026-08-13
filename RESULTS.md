# MTG-Forge LLM Eval — shipped results

Status of this document: it ships only **citable** numbers — every one with its
trivial floor, engine-grounded, on a frozen suite. A number without its floor is
not a number (the "88% lesson"). `PENDING` marks the one result still being
measured at the time of writing.

## Axis: PUZ — golden puzzle suite (`eval/suites/puz-v1`, 25 positions)

Teacher-independent: answer keys come from thousands of engine rollouts per
position, not from a human or a stronger model's opinion. Frozen 2026-07-29.

Floors (a model that solves only the free positions already scores 0.360):

| trivial policy | floor |
|---|---|
| always-pass | 0.20 |
| always-first | 0.28 |
| random choice | 0.29 |
| majority class | 0.32 |
| oracle ceiling | 1.00 |

7-model leaderboard (pass@1, shared positions, exact McNemar for rank claims):

| model | overall | disc | 95% CI on disc |
|---|---|---|---|
| anthropic/claude-fable-5 | 0.880 | 0.867 | +/- 0.172 |
| x-ai/grok-4.5 | 0.870 | 0.850 | +/- 0.181 |
| google/gemini-3.5-flash | 0.840 | 0.800 | +/- 0.202 |
| qwen/qwen3.7-max | 0.830 | 0.783 | +/- 0.208 |
| anthropic/claude-opus-4.8 | 0.790 | 0.717 | +/- 0.228 |
| deepseek/deepseek-v4-pro | 0.750 | 0.650 | +/- 0.241 |
| moonshotai/kimi-k3 | 0.720 | 0.600 | +/- 0.248 |

Rank claims are supported ONLY for the pairs the tool certifies:
claude-fable-5 > deepseek-v4-pro (p=0.0039), claude-fable-5 > kimi-k3
(p=0.0039), grok-4.5 > kimi-k3 (p=0.0386). The suite's difficulty, not its
size, is the ranking bottleneck — quote only certified pairs.

Rebuild: `python3 eval/puz_index.py leaderboard --pick-latest runs/puz/tournament-8/*/`

## Axis: PZL — Forge's own 300 hand-authored puzzles (`eval/suites/pzl-v1`)

No answer key of ours: Forge's hidden "Puzzle Goal" card adjudicates, so the
rules decide and nothing of ours is in the loop. Floors MEASURED (executed over
all 300 positions, 2026-08-12):

| arm | solve rate |
|---|---|
| do-nothing (floor) | 0.0070 |
| random | 0.0110 |
| Forge AI | **0.0287** (the bar to beat — strongest agent that does no LLM reasoning) |

9 of 300 rows are unscoreable at the pinned engine (3 crash Forge's own
attachment handling, 6 have a goal trigger that can never fire); deck-out
taint is excluded from both numerator and denominator of every rate.

Model solve rate: **PENDING** (LLM arm in progress — `runs/pzl/llm`, 8 parallel
JVMs, deepseek-v4-flash). This is the axis's first model score; it updates this
row when complete.

## What this benchmark still cannot tell you (EVAL-DESIGN.md §9)

Absolute human-relative strength (anchored to Forge's weak-amateur AI); combo-
line quality; hidden-information judgment; interface aptitude vs Magic aptitude;
metagame breadth beyond the 16-deck Legacy pool. The PZL contamination threat is
uneven (older puzzles may be recalled, not solved) — report the 2025-06+ subset
separately before pooling.
