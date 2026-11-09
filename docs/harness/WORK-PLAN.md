# Work plan: what is left (updated 2026-11-08)

The living to-do list. Only open work is listed here (plus a three-line "Done since" pointer, dropped at the next
refresh); what is done is in git history,
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

## Done since 2026-10-26 (dropped at the next refresh)

The record is `git log --merges --since=2026-10-26` and [ACCEPTANCE.md](ACCEPTANCE.md). For a reader the ones that
change what to do: unscorable games (dead bridge, cut log) are refused everywhere (#374, #429, #431); the seat pays
by the rules (#419, #433); the RL environments are on main (`eval/rl/`, #423; harness-building tasks, #422).

## Facts the owner set (2026-11)

- **No CI.** There are no GitHub Actions minutes: the test workflow is paused to a manual run (#401). Plan no CI work;
  the local acceptance run is the evidence a merge needs.
- **No paid model runs for now.** Every item below that needs one waits. Model experiments use the `claude-cli:` dev
  lane (`claude -p` with Haiku or Sonnet, `CLAUDE_CLI_SEAT=1`), never a benchmark score.

## Open work

- **The frontier comparison on the 5 hardest puzzles** ran on 2026-10-26: 20 models on 100 boards, exploratory
  and not a PUZ score ([FRONTIER5.md](FRONTIER5.md)). Open:
  - Kimi K2.6 lost decisions on elf2 and hoofdrain to `HTTP 400` context-length refusals
    ([FRONTIER5.md](FRONTIER5.md), "Decisions the harness lost"; owner: investigate). Every refused call was a
    follow-up to a `set_plan`-only reply, carrying its reasoning back, sent with `max_tokens` 235929; the bridge
    retried it unchanged and the seat passed as bridge-down. To find out: whether dropping the echoed reasoning, a
    lower cap, or both clear it (an A/B on the 8 logged requests; size alone does not, since a 0.41 MB follow-up
    passed), then either `puzzle_solve.py` refuses a cap with no room for a
    follow-up or the bridge reads that 400 and retries with the room left (a seat-visible change, its own version).
    Either way the 400 should be its own row reason, not "bridge-down";
  - the seat's prompt still says "a second illegal call costs you the decision"; since answer-tool-v12 (PR #330)
    two illegal calls in one reply are one invalid reply, so "reply" is the true word (a prompt change, its own
    version bump);
  - the branch `archives/frontier5-2026-10-26` (still on the remote, 2026-11-08) holds the full record
    until the owner has saved it elsewhere; deleting it is the owner's call.
- **The 25 hand-authored niche-rules puzzles** (PR #298) are not frozen into a suite and have no held-out split;
  torbran is provisional because Forge orders its damage replacements from a `HashMap`
  ([eval/puzzles/hard/niche/README.md](../../eval/puzzles/hard/niche/README.md), "Known limits"), which also lists
  the puzzles short of the bar and the row each missed.
- **The third hard suite** (`puzzles-hard-gen3-v1`, PR #302): the Luna run of its 44 open boards finished
  (`runs/puzzles/gen3-luna`, $1.01 by `python3 eval/cost_report.py runs/puzzles/gen3-luna/hard-*`); no row for it is
  in [docs/eval/PUZZLES.md](../eval/PUZZLES.md) yet, and the zero-model arms are not run. Score it with
  `eval/puzzles/puzzle_score.py` (no model call) and record it there. Its boards have run on an older epoch than
  the current one (97), so a new Luna run waits on the no-paid-runs fact above.
- **Harness-building environment:** no real episode has run. PR #422 added tasks built from red-then-green PRs
  (`eval/harness_env/tasks-v0.json`) with a grader that runs their tests; 19 of its 22 tasks are not validated (each
  needs a whole-overlay javac and a JVM), a non-target test already failing at base counts as a regression, the green
  state ran once (flaky tests unchecked), and the grader is dev-grade, not isolation-grade (a real RL run needs a
  separate user or a container with `/root/forge` read-only). The next grader change re-runs `validate` (its stored
  javac notes carry scratch paths). The owner's twelve questions are in HARNESS-ENV-FRAMEWORK.md section 8:
  - first, the builder model and budget;
  - question 12: run the candidate seat in its own process. Until then a candidate's score is withheld without
    `--trust-candidate`.
- **Seat defects the environment found** (`combat.block.must-block`, issue #311, fixed in PR #314 at
  harness-epoch-71; `costs.x`, issue #312, fixed in PR #315 at harness-epoch-72): the grader has not been re-run
  on the fixed build.
- **`MtgEnv`, the RL environment** (PR #423, `eval/rl/README.md`): one JVM and one port per episode, run serially;
  determinism per seed is untested; game mode ran once, and `blocks`, `card` and `keep` answers have no closed legal
  list; every step stores its full prompt (about 10 KB); step regret's winnable labels are offline, not per-step
  rewards; the guard stops accidents, not a forged episode line. No policy has trained on it.
- **Jev, owner questions:**
  - multi-picks and trigger order are built from independent yes/no questions;
  - the 40-option cap;
  - the Decisions endpoint is derived from `OPENROUTER_BASE_URL`, with a 120 s deadline and no env var of its own;
  - whether CLAUDE.md states the tool-call exception (recorded in ARCHITECTURE.md section 9).
- **Two older owner questions:**
  - from PR #294: a STANDARDS rule that a doc's command runs as written from the repository root;
  - from PR #301: acceptance under 5 minutes, which needs more cores or running only the stages a change can
    affect; the second changes the gate rule (STANDARDS sections 1 and 5), so it is the owner's.
- **Repository housekeeping:** the 163 remote branches already merged into main were not deleted (it needs the
  owner's permission; `git branch -r --merged origin/main | grep -v 'HEAD\|origin/main$' | wc -l`, 2026-11-08). Stale
  and open, for the owner to close or revive: issue #248 and PR #249 (the canary-random re-pin of 2026-10-17, long
  since overtaken), draft PR #11 (eval v1.0, the five axes of the first design), and PR #267 (its misreading
  probe is ported; see below).
- **Owner decisions from a hygiene audit (2026-10-25)** (tracked `runs/` is gone since #332):
  - four eval scripts nothing current calls: `latency_report.py`, `line_compare.py`, `coverage_audit.py` and
    `model_sweep.py`, whose default list includes an Opus model and which takes no budget;
  - six `secrets.env` loaders with no parity test: `play-vs-llm.sh`, `run_batch.py`, `tournament.py`,
    `tool_use_suite.py`, `model_sweep.py` and `puzzle_solve.py`;
  - zero-model games still stamp `smart_a` "opus" and `fast_a` "haiku" on the game line (the Java defaults of
    `SMART_MODEL` and `FAST_MODEL`).
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
- **The README and the front-facing text** (condensed to a front page in #333) await the owner's review.
- **Owner decisions** ([REVIEW-2026-10-16.md](REVIEW-2026-10-16.md)): E4 (does the headline STR score drop
  unfinished games), E5 (freeze a measurement epoch), then E7 (the first mirror block, paid, cost first); which
  models to compare ([docs/eval/BENCHMARK-V1.md](../eval/BENCHMARK-V1.md), decision 4); the headline score.
- **Re-runs that wait on a frozen epoch (E5) and on paid runs:** the prompt A/B block (`runs/prompt-ab`, confounded by
  the yield bug before answer-tool-v9) and the one-tier against two-tier comparison; about $0.80 for 20 games per arm.
- **The gauntlets** (`eval/keyword_gauntlet.sh`, first, random and tools policies, each checked with
  `eval/gauntlet_scan.py`) were last run in full on epoch 46; only the Forecast gauntlet (kw28, `first` policy,
  2026-11-04) is more recent. Run them once on the current epoch (97) when the owner asks; they are free.
- **The game loop** continues at g34 (seeds 4101-4133 are used; the last logged game is g33, 2026-10-19): one Luna
  game at a time, the `game-loop` skill, which now waits on the no-paid-runs fact or runs on the `claude-cli:` lane
  (`CLAUDE_CLI_SEAT=1`, never a score). Each game played is logged as a comment on issue #114, the defect-hunt log.
- **Forge built-in puzzles:** `eval/suites/puzzles-forge-builtin-v1.json` (26 active) waits on a small Luna run
  on its open split (cost first). The prover's pass over the 368 candidates stopped at 25; not found at budget 200
  means nothing yet (HANDOFF-PUZZLES.md, track 2).
- **Deferred review LOWs** from PR #268: issue #269 is down to its item 4, `PuzzleSearch.gaveUp` has no test (a toy
  board with `linesPerNode=1` where a nested prove gives up after the tree is fully played; assert `!r.exhausted`).
- **The Forge AI opponent's profile** (issue #381, labelled needs-owner): STR-AI's opponent runs with no AI profile,
  the compiled `AiProps` defaults, which are none of Forge's shipped profiles (note:
  `eval/research/2026-11-06-ai-profile-spread.md`). Owner decision: keep the defaults and document them as the
  opponent, or pin a shipped profile (an epoch bump; earlier STR-AI numbers stop comparing). The per-seat switch
  `AI_PROFILE_A/B` is built and the default unchanged (#421); the spread experiment (about 390 games per profile for
  a 10-point gap) is paid and waits. `puzzle_baselines` strips the variable, so PUZ always meets the default opponent
  and the puzzle row does not stamp it; a raw JVM launch with an ambient `AI_PROFILE_B` is stamped on the game line
  but a hand-built cited aggregate has no separate check.
- **Twin puzzles** (draft PR #371): `puzzle_twin.py` renames every card on a board to an invented name with the same
  rules text, so a solve rate on twins against originals separates recall from reading. Zero-model checks show the
  renamed cards play the same. Wiring in progress: `twin_of` in `puzzle_format`, twin pairing in `puzzle_score`, an
  `install.sh` step, and version coverage for the twin scripts (`overlay_sha` does not hash `res/`). A twin name of
  another length moves where the seat cuts a long description (a documented limit). The model comparison (Luna on
  originals against twins, about $0.40 on the rules sample) is owner-gated and paid; the dev lane may stand in for a
  smoke, never a score.
- **Misreading probes for the rules-pilot suite** (`PZL_PROBE`, `puzzle_baselines.py probe`, ported from stale
  PR #267): only RP_001's misreading has been run as a complete probe line (`Solver:4:0 Solver:2:0`: Ray of
  Revelation on Blood Moon, then Dark Depths; rejected, no Marit Lage; PR #441). Its menu indices were inferred
  from one-step probes' `[ROUTER] play` lines (`docs/harness/evidence/2026-11-09-pzl-probe/`). 220 more converted
  puzzles carry a `misread_line` in `eval/puzzles/rules-pilot/manifest.jsonl` (221 rows with `result` "converted"
  and a `misread_line`, of 526 converted): write each as a whole probe line, run it, and record the verdict beside
  `pass_arm`. Follow-up: a search-arm mode with the prefix fixed, which asks whether ANY completion of the
  misreading is accepted (a probe asks about one). #267 can be closed once these are done.
- **Measurement and score integrity, left by #429, #431 and #374:** the dead-bridge and cut-log checks in
  `rating` and `defect_analyzer` are not folded into `aggregate.lost_decisions` (separate messages and totals);
  a failed puzzle-row write has no non-zero exit or marker; the batch does not stop on a non-retryable HTTP error
  (402, 401), so a run out of credits keeps launching games; `puzzle_baselines.reusable` does not read the
  write-failure line; `defect_analyzer` and `puzzle_watch` judge a puzzle row by its own count only.
- **Seat and menu follow-ups from merged PRs** (each a known LOW or a gap, none blocking):
  - the repeated-mode preview (#397): a `CanRepeatModes` spell cast for an alternative cost, with one targeted mode
    chosen twice at different targets, previews both copies at one target (Mystic Confluence, Snapcaster Mage's
    flashback); a bestow or prototype cast is counted as before (#403); a tap-type mana ability that chooses a color
    or has another cost part is not searched, and the search is bounded at 3 deep and 48 tests (#375);
  - the observation: the guide's "any keyword the player has" overclaims, since ten keywords with no engine title
    are left out, and other engine effect objects in the command zone are still listed (#407); the game seat's
    YOUR CARDS has no type line, prints the script's literal `\n` and says "no cost" for a land (#394, changes STR,
    so its own issue); `YIELD_HINT` in the text arm keeps "your own main phases always ask too", and whether a
    yield should pass the seat's own main phase with items pending is the owner's (#402);
  - pawprint charms (#416, #418): the jev bridge's padding to a minimum can stop short when the surest mode fits
    once but not twice (no card reaches it); `budgetSafeDefault`'s catch branch has no test;
  - the HTTP deadline (#396): the client's `connectTimeout` equals the deadline, so the transport race survives in
    the connect phase when the deadline is under `IDLE_MS`; `DecisionsBridgeTest` could pass vacuously;
  - puzzle replay and search (#365, #363, #361, #400): the hand-over to the search arm at decision K and the
    committed replay driver are not built, lookups, `yield`, `keep_untapped`, blocks and multi-picks are untested in
    replay; a CLI deadline above 5 minutes still needs `--k 1`; the CLI lane has no lookahead arm (`claudeCli`
    refuses `LOOKAHEAD=1`); a puzzle's last-turn clock has no turn count for "win before the opponent's next turn",
    and whether it moved solve rates needs a paid epoch-87 against epoch-88 comparison;
  - the gate and versions (#406, #383, #410): preflight's PIN/CUR comparison has no test, the Java block-comment
    strip in the stamp test is not string-aware, and about 15 `assertTrue(ctrl.playChosenSpellAbility(...))` sites in
    `GoldenChecksTest` and `PromptFidelityTest` would fail the STANDARDS section 5 grep; `ScriptBridge`'s own range
    patterns are an unpinned second copy (#373);
  - the payment path (#433, #386): the three payment commits' canaries predate the last two, so re-run them in the
    full acceptance (no `ACCEPTANCE.md` record yet); `getAIPlayableMana` and the splice veto on the copy are AI
    structural limits left alone; `seatCheck`'s hiding of `AIPhyrexianPayment` is now redundant; `tapPartHolds` can
    name a held card as the tap-type witness (no board found).
- **Detectors not built** (issue text from #393, #394, #375, #407): `defect_analyzer.py` flagging a `(castable)` hand
  card with no CAST option; `match_review.py` flagging a puzzle prompt with no `<your_deck>` block and an effect
  object listed as a card; a check that the state's "mana available" estimate covering a spell the menu refuses is
  flagged. Each reopens if a review finds the defect again.
- **Research notes without an owner decision** ([eval/research/](../../eval/research/)): the IRT fitting scripts of
  #377 are not committed (an `eval/` script needs a test); the control-variate idea (#364) has an open value; a
  matched-compute arm for the CLI thinking-against-sampling study (#361) is not run.
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
