# Eval campaign 2: LLM vs Forge AI — 2026-07-18

A second cited-discipline campaign on the Mardu Energy mirror
(`eval/decks/mardu-energy.txt`), run sequentially with `jobs=1`.

## Provenance

Two overlays are involved; the report is honest about which batch used which:

- **haiku-vs-ai** (attempted 20 games): `discipline=cited`,
  `engine_sha=c1c64a53586459cd0d4c70f27abd1aa8bbc92891`,
  `overlay_sha=532c01a82b16`, `deck_sha=4e7be5785b40`,
  `mulligan_heuristic=0`, `skip_heuristics=0`, `plan_a=""`, `plan_b=""`,
  `smart_budget=320`, `fast_budget=900`, `claude_deadline_ms=90000`,
  `game_cap_ms=7200000`.

- **floor-vs-ai** (20 games): same engine/deck/discipline, but
  `overlay_sha=3ca3280e1fc2` (post-instrumentation overlay with log-only AI
  surface observation, choose-entity routing, and bridge retry-on-dead-bridge),
  `game_cap_ms=900000`.

Identity names `Identity-A`/`Identity-B`; winner is identity-keyed (never
seat-keyed); every seed is played twice with seats swapped. Wilson 95% CIs;
paired bootstrap at the seed level (10,000 resamples, seed 20260715).

## Results

| batch | dir | games | result | Wilson 95% CI | paired delta [95% CI] | seat-1 bias | fallback gate |
|---|---|---|---|---|---|---|---|
| haiku vs ai | `haiku-vs-ai` | 20/20 decisive | haiku 0/20 = **0.0%** | [0.0%, 16.1%] | −100 pts [−100, −100] | 50.0% | passed (gate vacuously true, but see contamination note) |
| floor vs ai | `floor-vs-ai` | 20/20 decisive | floor 0/20 = **0.0%** | [0.0%, 16.1%] | −100 pts [−100, −100] | 50.0% | vacuous (no llm arm) |

## Why the haiku-vs-ai number is NOT citable

The haiku batch completed, but **the raw replies are contaminated by a Claude CLI
session-rate-limit**. `analyze_corpus.py` now flags this explicitly:

```
model-consulted (prompt!=null): 405
full round-trip (prompt+raw_reply): 405
prompt-without-reply (bridge died mid-call): 0
contaminated replies: 324
WARNING: 324 model-consulted line(s) carry a non-answer raw_reply
```

That is **80% of model-consulted decisions** across the 20 games. Most games are
100% contaminated; game 1 (the 14.5-minute game) is ~46% contaminated. The
contaminated replies contain strings such as:

> "You've hit your session limit · resets 3pm (America/Los_Angeles)"

The pre-instrumentation bridge treated these non-answers as ordinary replies and
parsed/defaulted them into choices, logging them as tier `smart` with
`fallback=null`. They were **not** real haiku decisions. The 0/20 result is
therefore **not a measurement of haiku vs Forge AI**; it is a measurement of the
deterministic fallback path (plus a small amount of genuine but overwhelmed
haiku play in game 1) against Forge AI.

A post-campaign bridge hardening change now detects these non-answer results and
routes them through the honest `bridge-down` fallback path instead.

## Honest read of what we did learn

1. **When the model is unavailable, the harness loses to Forge AI every game.**
   The floor-vs-ai batch is clean: 20/20 decisive, AI wins all 20, paired delta
   −100 pts. This is expected — the deterministic fallback plays first-legal
   options and is not a Magic strategy — but it is now measured rather than
   assumed.

2. **The first campaign's headline is unchanged.** Haiku still sweeps random
   20/20 and its own fallback 19/19. The floor is weaker than random (12.5% vs
   random) and vastly weaker than Forge AI (0% vs AI).

3. **The rate-limit contamination is a data-quality bug, not a model-evaluation
   result.** The fix is in the bridge; a re-run with budgeted CLI usage is
   needed before we can quote a true haiku-vs-Forge-AI number.

## Dataset additions

- `runs/floor-vs-ai/`: 20 games, 93 decision lines, 0 malformed, all with the
  new log-only AI-surface observation lines (mulligan bottoming, damage order,
  announce-x, sacrifice).
- `runs/haiku-vs-ai/`: 20 games, 405 decision lines, preserved for diagnosis.
  Marked contaminated; do not use for win-rate claims.

## Reproduce

```bash
# clean floor-vs-ai baseline (no model cost, ~3 minutes)
python3 eval/run_batch.py --seeds 1..10 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a floor --controller-b ai --smart-a haiku --smart-b opus

# DO NOT reproduce haiku-vs-ai without first confirming claude CLI quota:
# the recorded batch was rate-limited and the result is invalid.
python3 eval/aggregate.py runs/floor-vs-ai
python3 eval/analyze_corpus.py runs/haiku-vs-ai runs/floor-vs-ai
```
