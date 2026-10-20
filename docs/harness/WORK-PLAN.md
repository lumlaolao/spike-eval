# Work plan: what is left (updated 2026-10-20)

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
2. **Now: the benchmark on a sample.** GPT-6 Luna 5 times on each puzzle of a sample (owner, 2026-10-20; was about
   10), with the tools on and with them off (the rules lookup above all): solve rate, which tools it calls (does it
   look up the rules?), and time per puzzle, for each condition (for example: 60% solved with the rules lookup, 20%
   without). That comparison is the point of the benchmark. Steps:
   - the sample: `eval/puzzles/rules-pilot/benchmark-sample-v1.tsv` (36 open-split puzzles, seed 2026);
   - free baselines on it (`eval/puzzle_baselines.py run --ids ...`), then freeze it as a suite;
   - the paid run (`eval/puzzle_solve.py ... --k 5 --budget-usd X`, with and without `--rules-tool`): state the
     cost first; the owner sets `OPENROUTER_ALLOW_EGRESS=1`.

## Open work, after the benchmark

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
- **Tool calls, not text parsing**, for every decision and note (`CLAUDE.md`).
- **No heuristics deciding for the model.** The only skips: the menu is PASS only, or the identical spot was
  already passed. Never Forge's AI for a choice the seat owns.
- **Edit `forge-overlay/` only**; engine patches through the overlay, listed in
  [ENGINE-PATCHES.md](ENGINE-PATCHES.md).
- **Tests first**, watched failing. Acceptance and the gauntlets only when the owner asks, except the full
  acceptance run a merge needs.
- **Subagents only with the owner's explicit permission.**
- **Compound actions (`PLAN_EXECUTE`) stay off** (2026-10-16): the plan is a note, re-read at every decision.
