# Work plan: what is left (updated 2026-10-24)

The living to-do list. Only open work is listed here; what is done is in git history,
[GAME-LOOP-LOG.md](GAME-LOOP-LOG.md), [ACCEPTANCE.md](ACCEPTANCE.md) and the run reports. The plan as it stood
through 2026-10-19, with every finished item and its evidence, is
[history/WORK-PLAN-2026-10-19.md](history/WORK-PLAN-2026-10-19.md). Update this file when an item is finished or
a new one is agreed, so the next session can pick up from it.

## The plan (owner, 2026-10-19)

1. **Digitize 500 puzzles: done.** 526 converted rules puzzles on 2026-10-20 (RP_001-RP_548 in
   `eval/puzzles/rules-pilot/manifest.jsonl`; RP_300, RP_402, RP_454 and RP_521 are near-duplicates kept but not
   counted), landed with harness-epoch-66 (PR #270). How they were made and the gate's lessons:
   [HANDOFF-PUZZLES.md](HANDOFF-PUZZLES.md).
2. **Done: the benchmark on a sample** (PR #272): Luna 5 times on each of 29 frozen sample puzzles
   (`eval/suites/puzzles-rules-sample-v1.json`), tools off and on, at epochs 66 and 67. Results and the paired
   comparisons: [eval/puzzles/rules-pilot/benchmark-v1/REPORT.md](../../eval/puzzles/rules-pilot/benchmark-v1/REPORT.md).

**Proposed next (2026-10-20, awaiting the owner): [BENCHMARK-V2.md](BENCHMARK-V2.md).** Sample v1
saturated (130/145 with and without tools at epoch 66; 119/145 off and 127/145 on at epoch 67, once the brief stopped
naming the trick); v2 proposes measured difficulty tiers, a closed-book headline, the seat's
puzzle contract, the hard sources and the measurement protocol. Nothing in it is built until the owner reviews it.

**Done since (2026-10-21 to 2026-10-24):**
- **Hard puzzles:** three generated suites, every board gated against every floor:
  - `puzzles-hard-gen-v1`: 116 boards (PR #290);
  - `puzzles-hard-gen2-v1`: 236 boards, with a trap 1 to 3 short (PR #299);
  - `puzzles-hard-gen3-v1`: 68 boards in tokens, landfall, equip and infect (PR #302).

  [eval/PUZZLES.md](../../eval/PUZZLES.md) covers them. Luna solved 15 of 156 on gen2's open split.
- **Engineering:**
  - acceptance in parallel (PR #301);
  - `puzzle_solve.py --resume` (PR #300);
  - provider compatibility for the frontier models: prices with a dearest-endpoint bound for budgets, one
    `provider` object, and a reasoning floor for models that cannot turn it off (PR #303);
  - a Jev 1.14 seat through OpenRouter's Decisions API (PR #305);
  - the harness-building environment (PR #304, [HARNESS-ENV-FRAMEWORK.md](HARNESS-ENV-FRAMEWORK.md)).

## Open work, after the benchmark

- **The frontier comparison on the 3 hardest puzzles** waits on the owner's go and a budget-cap choice:
  - the plan is `notes/frontier3-plan.sh` on branch `docs/writeup-notes`: 21 chat models plus Jev 1.14, about
    $37 expected;
  - caps at the dearest endpoint come to about $291, or about $125 if the bridge sends `provider.max_price` at the
    list price.
- **Harness-building environment, owner questions** (HARNESS-ENV-FRAMEWORK.md section 8):
  - the builder model and budget;
  - the scoring setting;
  - gating the payment executor;
  - question 12: run the candidate seat in its own process. Until then a candidate's score is withheld unless
    the operator passes `--trust-candidate`.

  No real episode has run.
- **Seat defects the environment found**, kept failing for `defect-hunt`: an illegal block stands under a
  must-block requirement (`combat.block.must-block`), and the X-cost instance (`costs.x`).
- **Jev, owner questions:**
  - multi-picks and trigger order from independent yes/no questions;
  - the 40-option cap;
  - whether CLAUDE.md states the tool-call exception (recorded in ARCHITECTURE.md section 9).
- **Repository housekeeping:** the remote branches already merged into main were not deleted (it needs the
  owner's permission).

- **Owner decisions** ([REVIEW-2026-10-16.md](REVIEW-2026-10-16.md)): E4 (does the headline STR score drop
  unfinished games), E5 (freeze a measurement epoch), then E7 (the first mirror block, paid, cost first); which
  models to compare ([eval/BENCHMARK-V1.md](../../eval/BENCHMARK-V1.md), decision 4); the headline score.
- **Re-runs that wait on a frozen epoch (E5):** the prompt A/B block (`runs/prompt-ab`, confounded by the yield
  bug before answer-tool-v9) and the one-tier against two-tier comparison; about $0.80 for 20 games per arm.
- **The gauntlets** (`eval/keyword_gauntlet.sh`, first, random and tools policies, each checked with
  `eval/gauntlet_scan.py`) were last run on epoch 46; run them once on the current epoch when the owner asks.
- **The game loop** continues at g34 (seeds 4101-4133 are used): one Luna game at a time, the `game-loop` skill.
- **Forge built-in puzzles:** `eval/suites/puzzles-forge-builtin-v1.json` (26 active) waits on a small Luna run
  on its open split (cost first). The prover's pass over the 368 candidates stopped at 25; not found at budget 200
  means nothing yet (HANDOFF-PUZZLES.md, track 2).
- **Deferred review LOWs** from PR #268: issue #269.
- **Small and known:** "(need 1 more B)" compares pips with the per-colour mana; the legacy profiles'
  `castHistory` still reads the log and a few of their decision texts keep capitals (the faithful profile sends
  neither; kept for the legacy bytes).

## Owner decisions this plan rests on

- **Game model: GPT-6 Luna** (`openrouter:openai/gpt-6-luna`) for every seat and tier unless the owner names another.
  Never Opus. Paid runs always carry `--budget-usd`; say the cost first. Never set `OPENROUTER_ALLOW_EGRESS`.
- **Tool calls, not text parsing**, for every decision and note (`CLAUDE.md`). One exception (owner, 2026-10-24):
  a `jev:` decision model, whose typed choice is turned into the `answer` call before validation.
- **No heuristics deciding for the model.** The only skips: the menu is PASS only, or the identical spot was
  already passed. Never Forge's AI for a choice the seat owns.
- **Edit `forge-overlay/` only**; engine patches through the overlay, listed in
  [ENGINE-PATCHES.md](ENGINE-PATCHES.md).
- **Tests first**, watched failing. Acceptance and the gauntlets only when the owner asks, except the full
  acceptance run a merge needs.
- **Subagents are allowed** (owner, 2026-10-23); they never message the live-demo session.
- **Compound actions (`PLAN_EXECUTE`) stay off** (2026-10-16): the plan is a note, re-read at every decision.
