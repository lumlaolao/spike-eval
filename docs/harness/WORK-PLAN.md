# Work plan: what is left (updated 2026-10-25)

The living to-do list. Only open work is listed here; what is done is in git history,
[GAME-LOOP-LOG.md](GAME-LOOP-LOG.md), [ACCEPTANCE.md](ACCEPTANCE.md) and the run reports. The plan as it stood
through 2026-10-19, with every finished item and its evidence, is
[history/WORK-PLAN-2026-10-19.md](history/WORK-PLAN-2026-10-19.md). Update this file when an item is finished or
a new one is agreed, so the next session can pick up from it.

## Awaiting the owner: benchmark v2

**[BENCHMARK-V2.md](BENCHMARK-V2.md)** (proposed 2026-10-20): measured difficulty tiers, a closed-book headline, the
seat's puzzle contract, the hard sources and the measurement protocol. Phase 2 and phase 1's difficulty fields are
built (#280-#282); nothing else is built until the owner reviews it. Why: Luna on the sample suite's 29 active
puzzles (`eval/suites/puzzles-rules-sample-v1.json`, PR #272) saturated, 130/145 with and without tools at epoch 66,
119/145 off and 127/145 on at epoch 67
([eval/puzzles/rules-pilot/benchmark-v1/REPORT.md](../../eval/puzzles/rules-pilot/benchmark-v1/REPORT.md)).

Landed 2026-10-21 to 26: PRs #290 to #327, among them #320 (the `claude-cli:` lane), #321-#323 (a docs pass),
#324 (the full run record), #325 (`play-vs-llm.sh`, a person against the seat), #326 (`setup.sh`) and #327 (the
routing filters: a price ceiling and a precision list for puzzle runs). The hard suites
are in [eval/PUZZLES.md](../../eval/PUZZLES.md); the harness-building environment is in
[HARNESS-ENV-FRAMEWORK.md](HARNESS-ENV-FRAMEWORK.md).

## Open work

- **The frontier comparison on the 5 hardest puzzles** (owner, 2026-10-25; it replaces the 3-board plan) waits on
  the owner's go (2026-10-25: the caps stand, the quirks below are signed off; 2026-10-26: a board-1 trial first,
  and every model at full capability):
  - the plan is `notes/frontier5-plan.sh` on branch `docs/writeup-notes`: the 5 boards of the gen2 suite's open
    split named in `notes/WRITEUP-NOTES.md` section 4b (with the rule that picked them), 1 attempt each, for 21
    chat models plus Jev 1.14 (bridge on main, PR #305): 110 attempts, about $62 expected; Astra, Fable and Opus
    are not in it, and Jev Router is out (owner, 2026-10-24);
  - caps at the dearest endpoint come to about $489, kept (owner, 2026-10-25; Haiku 5.5's recomputed by the cap rule
    in the Fable pre-run check, 2026-10-26): the sum of the script's cap column, the first command below (488.67). A
    closed model runs with `puzzle_solve.py --max-price-at-list`, so no request is routed to a priority tier above
    list; an open-weight model (DeepSeek V4 Pro and V4.1 Flash, Kimi K3 and K2.6, GLM 5.3) runs with `--quantizations`
    at its maker's released precision and no ceiling, since its list price is a 4-bit host's (PR #327). The run script
    checks before it pays for anything that each model has an endpoint its filter allows and that no ceiling model's
    list price moved, and starts with a board-1 trial (`STAGE=1`, 22 of the 110 attempts, so about a fifth of the $62)
    that is read before `RESUME=1` plays boards 2 to 5;
  - the board-1 trial ran (2026-10-26, $10.12): every model answered by tool with ai=0; two games (GLM 5.3 Prime,
    Kimi K2.6) reached the win after the 30-minute game cap, so the run plays at `--game-cap-minutes 90` (owner,
    2026-10-26) and those two are replayed under it;

    ```bash
    git show origin/docs/writeup-notes:notes/frontier5-plan.sh \
      | awk '/^MODELS="/{f=1;next} f&&/^"/{f=0} f&&NF>=2{s+=$2} /^JEV=/{gsub(/"/,"");split($0,a," ");s+=a[2]} END{print s}'
    git ls-files -z runs | du -ch --files0-from=- | tail -1      # the tracked runs/ size below
    ```

  - the owner requires full logs for this run, from the model chats to the engine logs: PR #324 landed it;
    `puzzle_solve.py` records every HTTP round-trip and the engine log, and `eval/archive_run.py` packs and verifies
    a run ([eval/PUZZLES.md](../../eval/PUZZLES.md), "Keeping a run");
  - one paid Jev smoke test (2026-10-24, one gen2 puzzle, $0.0023, `runs/puzzles/jev-test-1`, WRITEUP-NOTES
    section 4 on `docs/writeup-notes`): 15 decisions through the Decisions bridge, ai=0; the response shape is
    confirmed on that run only;
  - the owner decisions in PR #303's Not done bear on it: each model runs at its own default reasoning effort (max
    for Kimi K3 and GLM 5.3), Mistral may refuse a user message after a tool result, and caching is unmeasured.
- **The 25 hand-authored niche-rules puzzles** (PR #298) are not frozen into a suite and have no held-out split;
  torbran is provisional because Forge orders its damage replacements from a `HashMap`
  ([eval/puzzles/hard/niche/README.md](../../eval/puzzles/hard/niche/README.md), "Known limits"), which also lists
  the puzzles short of the bar and the row each missed.
- **The third hard suite** (`puzzles-hard-gen3-v1`, PR #302): the Luna run of its 44 open boards finished
  (`runs/puzzles/gen3-luna`, $1.01 by `python3 eval/cost_report.py runs/puzzles/gen3-luna/hard-*`; unscored, and the
  zero-model arms not run). Score it with `eval/puzzle_score.py` and
  record it in [eval/PUZZLES.md](../../eval/PUZZLES.md).
- **Harness-building environment:** no real episode has run. The owner's twelve questions are in
  HARNESS-ENV-FRAMEWORK.md section 8:
  - first, the builder model and budget;
  - question 12: run the candidate seat in its own process. Until then a candidate's score is withheld without
    `--trust-candidate`.
- **Seat defects the environment found** (`combat.block.must-block`, issue #311, fixed in PR #314 at
  harness-epoch-71; `costs.x`, issue #312, fixed in PR #315 at harness-epoch-72): the grader has not been re-run
  on the fixed build.
- **Jev, owner questions:**
  - multi-picks and trigger order are built from independent yes/no questions;
  - the 40-option cap;
  - the Decisions endpoint is derived from `OPENROUTER_BASE_URL`, with a 120 s deadline and no env var of its own;
  - whether CLAUDE.md states the tool-call exception (recorded in ARCHITECTURE.md section 9).
- **Two older owner questions:**
  - from PR #294: a STANDARDS rule that a doc's command runs as written from the repository root;
  - from PR #301: acceptance under 5 minutes, which needs more cores or running only the stages a change can
    affect; the second changes the gate rule (STANDARDS sections 1 and 5), so it is the owner's.
- **Repository housekeeping:** the 78 remote branches already merged into main were not deleted (it needs the
  owner's permission; `git branch -r --merged origin/main | grep -v 'HEAD\|origin/main$' | wc -l`, 2026-10-26).
- **Owner decisions from a hygiene audit (2026-10-25):**
  - about 310 MB of `runs/` is tracked, against STANDARDS section 3 (the second command in the frontier entry's
    block, 2026-10-26);
  - four eval scripts nothing current calls: `latency_report.py`, `line_compare.py`, `coverage_audit.py` and
    `model_sweep.py`, whose default list includes an Opus model and which takes no budget;
  - six `secrets.env` loaders with no parity test: `play-vs-llm.sh`, `run_batch.py`, `tournament.py`,
    `tool_use_suite.py`, `model_sweep.py` and `puzzle_solve.py`;
  - zero-model games still stamp `smart_a` "opus" and `fast_a` "haiku" on the game line;
  - `forge-overlay/forge-gui/res/skins/default/sprite_avatars.png` replaces Forge's file with no
    [ENGINE-PATCHES.md](ENGINE-PATCHES.md) entry;
  - `eval/puzzles/rules-pilot/batch14-drafts` is linked from nothing.
- **The `claude-cli:` lane:**
  - `archive_run.py` refuses a `claude-cli:` puzzle run as a gap (billed calls, no HTTP file): that lane makes no HTTP
    calls to record;
  - a `claude -p` child outlives a killed JVM and keeps billing;
  - owner question: should the lane, which sends the game state to Anthropic under the CLI login, have its own
    egress opt-in?
- **`run_batch.py` games do not set `TRANSCRIPTS`**, so a batch game has no HTTP record.
- **Playing the seat yourself** (#325, #326): no paid-model game has been played through `play-vs-llm.sh` yet (the
  smokes used `greedy:`); `setup.sh` is untested on a Mac and on WSL2, and the cold first build is untimed. Three of
  #326's five pr-review LOWs remain: the ^C window between the marker and the remote add, the kept-secrets consent
  check being looser than `play-vs-llm.sh`'s, and a Maven on Java 8 reported as "Java 1".
- **The README and the front-facing text** await the owner's review.
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
- **The `claude-cli:` lane is a local-development opt-in lane** (owner, 2026-10-24; PR #320, merged 2026-10-25,
  ARCHITECTURE.md section 9). Its one paid smoke run (Claude Opus 5.5 via `claude -p`, owner-requested test,
  2026-10-24, solved, $2.18 by `python3 eval/cost_report.py runs/puzzles/opus-cli-smoke/hard-*`) is
  `runs/puzzles/opus-cli-smoke`.
- **Compound actions (`PLAN_EXECUTE`) stay off** (2026-10-16): the plan is a note, re-read at every decision.
