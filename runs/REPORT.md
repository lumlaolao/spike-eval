# First trustworthy eval — 2026-07-17/18

Three cited-discipline batches on the Mardu Energy mirror (`eval/decks/mardu-energy.txt`),
run sequentially with `jobs=1`. Shared provenance for **all 79 games**:

```
discipline=cited  engine_sha=c1c64a53586459cd0d4c70f27abd1aa8bbc92891
overlay_sha=532c01a82b16  deck_sha=4e7be5785b40
mulligan_heuristic=0  skip_heuristics=0  plan_a=""  plan_b=""
smart_budget=320  fast_budget=900  claude_deadline_ms=90000
caps: 15 min (no-llm arms) / 120 min (haiku arms)
```

Identity names `Identity-A`/`Identity-B`; winner is identity-keyed (never seat-keyed);
every seed is played twice with seats swapped. Wilson 95% CIs; paired bootstrap at the
seed level (10,000 resamples, seed 20260715).

## Results

| batch | dir | games | result | Wilson 95% CI | paired delta [95% CI] | seat-1 bias | fallback gate |
|---|---|---|---|---|---|---|---|
| floor vs random | `20260717-193807-floor-vs-random` | 40/40 decisive | floor 5/40 = **12.5%** | [5.5%, 26.1%] | −75 pts [−90, −55] | 47.5% | vacuous (no llm arm) |
| haiku vs random | `haiku-vs-random` | 20/20 decisive | haiku 20/20 = **100%** | [83.9%, 100%] | +100 pts [+100, +100] | 50.0% | passed, 0.6% (10/1745) |
| haiku vs floor | `haiku-vs-floor` | 19/20 decisive (1 invalid) | haiku 19/19 = **100%** | [83.2%, 100%] | +100 pts [+100, +100] | 52.6% | passed, 0.4% (7/1579) |

## Verdict on haiku-vs-floor

Haiku decisively beats its own deterministic fallback: 19/19 decisive games, every
complete seed-pair swept, Wilson 95% lower bound 83.2%. The apparatus is not theater —
the model's choices, not the harness's legal-move plumbing, are what win these games.
Two qualifiers keep this honest. First, the floor turned out to be a *very* low bar:
in the mirror it loses to uniform random play 5/40 (all-in attacks, no blocks, and
first-legal-option produce actively bad Magic), so "beats floor" alone is weak
evidence — the stronger result is haiku's 20/20 sweep of the random arm, which the
floor itself loses to 87.5–12.5. Second, "haiku plays the game" comes with the known
PARTIAL scope: un-overridden decisions (combat damage order, X announcement, edict
sacrifices, mana tapping, mulligan bottoming) still run on Forge AI invisibly; the
0.4–0.6% fallback rates cover only routed decisions. Within that scope, model control
was near-total: zero smart/fast-tier fallbacks, one AI handoff per haiku batch.

## Dataset

Every decision line now round-trips: state prompt + full option list + raw model
reply + chosen index + tier + fallback flag. Campaign totals: **9,528 decision
records** (7,912 lines across the first two batches verified 0 unparseable, plus
1,676 in haiku-vs-floor), ~2,400 of them with full model prompt/reply pairs — the
project's first learning-usable corpus.

## Caveats / known flakes

- `duration_ms` is wall-clock: games running 16:00–21:45 on 2026-07-17 spanned host
  sleep (the batch host slept intermittently; `caffeinate` was armed from 21:45).
  In-game caps count awake time (JVM monotonic clock), so no game was wrongly
  invalidated by sleep.
- `haiku-vs-floor` seed 5 (swapped) exited cleanly mid-game with no summary line
  (8 decisions logged, no crash signature). Counted invalid and excluded per the
  discipline; sole loss of the campaign's 80 scheduled games.
- Batches were run from git worktree state `b40d8cb` (= overlay_sha 532c01a82b16).

## Reproduce

```
python3 eval/run_batch.py --seeds 1..20 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a floor --controller-b random
python3 eval/run_batch.py --seeds 1..10 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a llm --smart-a haiku --fast-a haiku --controller-b random
python3 eval/run_batch.py --seeds 1..10 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a llm --smart-a haiku --fast-a haiku --controller-b floor
python3 eval/aggregate.py runs/<dir>
```
