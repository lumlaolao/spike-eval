# Generated hard puzzles (2026-10-20)

The owner asked for 200 hard puzzles of 10 to 15 steps that are neither easy nor obvious. This note records
what the research said, how the boards are made and how each one is checked. Counts are filled in from the
gate's own output (`scripts/puzzle-gate/gen_gate.py`, `OUT/gate.json`).

## What makes a puzzle hard (reading, 2026-10-20)

- Possibility Storm's own commentary on its hardest puzzles names three sources: opponent choices that multiply
  the paths, damage or life that can be split many ways (pingers and healers), and a dense set of triggers and
  choices with no odd rule at all ([first responders](https://articles.starcitygames.com/articles/possibility-storm-first-responders/),
  [2017 in review](https://beta.mtggoldfish.com/articles/possibility-storm-2017-a-year-of-puzzling-in-review)).
- Planning benchmarks find length is the lever: SokoBench sees performance fall consistently past about 25 moves
  ([arXiv 2601.20856](https://arxiv.org/html/2601.20856v1)); PuzzlePlex and the Countdown benchmark generate fresh
  instances so a suite cannot be memorised ([2510.06475](https://arxiv.org/html/2510.06475v1),
  [2508.02900](https://arxiv.org/html/2508.02900v1)).
- Our own suite: 452 of 525 proven rules-puzzle lines are one or two actions (`retier` of the 526, 2026-10-20).

## How a board is made (`eval/puzzle_gen.py`)

A family draws a board's numbers, computes its line exactly and writes it as the puzzle's `[solution]` in the
words the seat is shown. The opponent's life is set to exactly what the best line deals, so there is no slack.

| Family | Steps come from | What hides the line |
|---|---|---|
| ping | Thermo-Alchemist pinging before every instant or sorcery (it untaps on each) | ping before each spell, never after; Fireblast only once both Mountains are tapped; with Kiln Fiend every spell before combat; Goblin Guide costs a burn spell's mana for less |
| storm | every spell cast before Grapeshot | rituals in a payable order (Seething Song needs three open, Rites back to back), burn before Grapeshot |
| splice | Arcane spells, pings | which Arcane spell carries Desperate Ritual or Glacial Ray; the line is the exhaustive-search best and must beat every unspliced line |
| red | all of the above, plus Lava Dart's flashback, Goblin Electromancer, Kiln Fiend | the searched best line; kept only when a plain player (ping, then the biggest spell, never splice, Grapeshot last) falls short |
| fling | every instant or sorcery before Fling, each one growing Kiln Fiend and the prowess creatures | Fling after combat deals the attacker's power a second time, but the pool empties when the first main phase ends: a Mountain and a ritual are held for the second; kept only when the searched best beats every line that casts it all before combat and every line that pays Fling only with held Mountains |

What the engine taught the written lines: a choice with one legal answer gets no line; a splice is asked only
for cards the seat could pay; two of the seat's triggers at once are an order question; a Grapeshot copy is
logged as a cast, so each solution states its real step count (`# steps: N`). A written line casts each card the
moment it is castable, so an instant goes on the stack over a ritual that has not resolved and taps a Mountain
the line meant to hold: the fling search plays the stack the way the line does.

A combat puzzle whose answer depends on the opponent's blocks cannot be a fixed line, so those stay with the
search prover and are not in this set.

## How a board is kept (`scripts/puzzle-gate/gen_gate.py`)

1. The written line plays in the engine as written and solves, in 10 to 15 steps (casts, non-mana activations,
   land plays, the attack).
2. Every floor fails it: random 0 of 20, PASS 0 of 4, first option 0 of 4, the greedy preview floor 0 of 4, and
   Forge's AI at most 1 of 4.

Nothing paid is run; no model is called.

## Where it stands (paused 2026-10-20, owner asked to wrap up)

Kept so far: **116** boards in `eval/puzzles/hard/gen/kept/` (ping 24, splice 29, storm 6, fling 5, drain 46, elf 6), each passed both gate
steps above; the gate's rows are in `eval/puzzles/hard/gen/gate/<family>.json`.

| Batch | Kept | Main drop reason |
|---|---|---|
| ping (36) | 20 | the old Kiln Fiend trigger-order bug (fixed in e80b523), one random-floor solve |
| ping-rest | 4 | |
| storm (40) | 6 | Lotus Petal's colour and Gitaxian Probe payment questions (Petal removed in 43ea29c) |
| splice (40) | 29 | 11 lines did not solve as written (`solved=False unconsumed=12..19`): the line stops early |

Not gated yet (the gate was stopped to pause): `eval/puzzles/hard/gen/ungated/red` (60), `ungated/ping2` (60) and
`ungated/storm2` (50, generated after the Petal fix). Two more families (drain and elf; fling) are on their own
branches, see their notes.

Next, in order:
1. Read one dropped splice board's line log (`puzzle_baselines.py line --pzl ...`): find where the line stops and
   fix the generator, then re-gate the 11.
2. Gate the ungated batches, one at a time (the box has 4 cores; `-j 2`, about 1 to 2 hours a batch of 60):
   `FORGE_HOME=/root/forge python3 scripts/puzzle-gate/gen_gate.py --build <build> --forge /root/forge
   --boards eval/puzzles/hard/gen/ungated/red --out <out> -j 2`, then copy `<out>/kept/*.pzl` into `kept/`.
3. Generate more of whichever families keep best (`python3 eval/puzzle_gen.py --family <f> --count N --seed S
   --out DIR`) until 200 are kept, deduping by board key.
4. Land the generator, `gen_gate.py`, the kept boards and this note through a PR and the full gate (acceptance is
   owed: `eval/` changes).

## Handoff (all work paused, 2026-10-20)

### Done today
- Merged #280, #281, #282, #283, #285 (HARNESS-ENV.md: building the harness as an RL environment, GBA Eval framing).
- Demo-session prompt: scratchpad/demo-session-prompt.md (paste into a new session).

### Hard puzzles (59 of 200 kept) — branch feat/hard-gen @ ea9774e, no PR yet
Full plan in docs/harness/history/2026-10-20-generated-hard-puzzles.md ("Where it stands"):
1. Fix the 11 splice lines that stop early (solved=False unconsumed=12..19), re-gate them.
2. Gate eval/puzzles/hard/gen/ungated/{red,ping2,storm2} (170 boards), one batch at a time.
3. Generate more of the best-keeping families (splice 29/40, ping 24/36) until 200.
4. PR with the full gate (acceptance owed: eval/ changes).
Subagent branches (drain/elf, fling): see their pushed notes (added below when they report).

### Other open items
- Re-tier data PR: measurements saved on branch data/retier-v2 @ 9f7bc75 (eval/puzzles/retier/); needs
  a test for summarize.py, a PR and acceptance (eval/).
- Owner questions: tier rule for B; h_0's held-out split; the 2-life Phyrexian; HARD-PUZZLES item 5; rulesEffective().
- Standards proposals: §2 model-id rule (and a pr_format.py check), §6 model line, versions pin check,
  plan-status rule, test-file subjects rule, bridge-forwarding reflection test, xargs -r, GraphQL-free gh commands.
- Pending: run Luna on the hard set (tools off/on) — paid, ask first.

### Subagent branches
- fling family: feat/hard-gen-more @ 0760579 (code + 3 tests; 60/60 lines solve as written). Floors ran on 6 of
  60: 5 kept (now in feat/hard-gen kept/, 64 total). Gate the other 55 (feat/hard-gen ungated/fling; one dir, no
  resume in gen_gate.py). Note: its two commits lack the Co-Authored-By trailer; 50/60 lines use Rite of Flame
  after combat, so vary the draw. fling_23e615214cc3 is a borderline AI-floor case.
- drain and elf families: feat/hard-gen-engines @ afaee5b (code + tests; status in HARD-PUZZLES.md section 3).
  52 of 52 gated kept (46 drain, 6 elf), now in feat/hard-gen kept/ (116 total). Gate the 40 elf boards in
  ungated/elf. Nothing was dropped, so add stricter checks: one action removed must fail, a near-miss leaves the
  opponent at 1 to 3, no mana left unused. Elf boards with one Sentinel repeat a few life totals (31, 35, 47).
- Landing: merge the three branches (feat/hard-gen, -engines, -more) into one PR; all touch eval/puzzle_gen.py
  and eval/test_puzzle_gen.py, so expect conflicts. Full gate with acceptance.

## Where the fling family stands (2026-10-20, stopped at the owner's wrap-up)

- 60 boards from `--family fling --count 60 --seed 1`. All 60 written lines played as written and solved in
  the engine (10 to 12 steps); none drifted, so no generator fix was needed after the stack-aware search.
- The floors stage was stopped after 6 boards to wrap up: 5 finished every floor and are kept (random 0/20,
  PASS, first, greedy and Forge's AI each 0/4); the sixth (`fling_23e615214cc3`) had every floor but the AI
  at 0, and kept in an earlier six-board run with the old description wording. No board was dropped by a floor.
- Next: re-run `gen_gate.py` on the 54 boards without floors (about 2.5 minutes a board at `-j 1`). 50 of
  the 60 lines open the second main phase with Rite of Flame; for variety, weight the draw away from two Rites
  or add a second finisher (Temur Battle Rage before combat, then Fling).
