# One tier vs two tiers (2026-10-15)

`deepseek/deepseek-v4.1-flash` as seat A (Death & Taxes) against Forge's AI (Delver), seeds 81–83,
both seat orders, `v4-faithful`, native tools, memory tools, `SKIP_HEURISTICS=0`, 45-minute cap,
`harness-epoch-4`. One tier: `ONE_TIER=1` (the one model, reasoning on, for every decision). Two
tiers: smart for strategic asks, fast tier for the rest.

| Arm | A wins | Wilson 95% | Cost |
|---|---|---|---|
| one tier | 5 / 6 | 0.44 – 0.97 | $0.94 ($0.156 a game) |
| two tiers | 2 / 6 | 0.10 – 0.70 | $0.61 ($0.102 a game) |
| random seat (floor of play) | 0 / 6 | 0.00 – 0.39 | $0 |
| floor seat (passes everything) | 0 / 6 | 0.00 – 0.39 | $0 |

Both model arms are above the zero-model baselines on the same seeds. The direction favours one
tier, but six games per arm cannot separate the two: the intervals overlap widely. Read as "one tier
is not worse, and costs about 50% more", not as a measured gain.

What the games found in the harness (fixed on `fix/parse-fidelity`, harness-epoch-5):

- an unparsed play reply was recorded as a PASS identical to a real one; a block reply with no
  BLOCKS line was read as "no blocks"; a block naming a card with a comma lost the pair;
- a degenerate reply's later "Final Answer: ... 3" overrode its `CHOICE: 4`;
- engine notices (a card named, a colour chosen) never reached the seat;
- `inspect` of "Cavern of Souls @11" said no such card;
- every preview failed after Palace Jailer's monarchy changed hands (GameCopier stale effect zone);
- the 25-decision per-turn play cap ended long turns under the faithful profile.

Review: `python3 eval/match_review.py runs/compare/one-tier` (and `two-tier`). Cost:
`python3 eval/cost_report.py runs/compare/one-tier runs/compare/two-tier` ($1.55 total).
