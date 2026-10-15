---
name: game-loop
description: Play ONE game, watch it, notice everything the harness did wrong, fix the harness, then play the next. Use when asked to play games, test the harness, find harness defects, improve how the model plays, or "run some games". This is the default way games are played in this repository; a batch of many games is not.
---

# The game loop: one match, read it, fix the harness, next match

Wins over a batch tell you almost nothing here: sixteen paired games resolve a 35-point difference.
Every real defect so far was found by READING one game (a harness that attacked for the model, a
retry prompt with no board in it, previews that never worked). So games are played one at a time,
and each is read before the next is played.

**The rule of the loop: a problem is the harness's until the prompt proves the model had what it
needed.** You are looking for decisions the model did not get to make, made blind, or made and had
thrown away. Bad play by a model that was shown everything is recorded as a puzzle candidate and
left alone; it is not what this loop fixes.

## 0. Before the first game

```bash
uptime                                   # above 4 load per core: wait
pgrep -f LlmSpikeHeadless | wc -l        # must be 0 before any rebuild
git status --short                       # start from a clean tree on a branch
tail -40 docs/harness/GAME-LOOP-LOG.md   # what the last games found; do not rediscover it
```

Paid games use only an authorised model and always a budget (`CLAUDE.md`, "Cost of paid runs").
One game costs about $0.04 two-tier; with `--one-tier` every decision reasons (runs/loop/g08: 54 calls,
~23 s each, $0.15 for 13 turns), so budget about $0.35 for a full game. The cap is 120 minutes: since
harness-epoch-7 the seat may think for 32000 tokens (up to 300 s a call) and make 16 tool calls a
decision, and a capped game is scored invalid, so the cap must not end a game while the model thinks
(docs/harness/MODEL-LIMITS.md).

## 1. Play one game

```bash
N=$(printf %02d $(( $(ls -d runs/loop/g* 2>/dev/null | wc -l) + 1 )))     # g01, g02, ...
SEED=<a seed not used in runs/loop yet; not in a reserved block (eval/seed_policy.py)>
python3 eval/run_batch.py --seeds $SEED --exploratory --jobs 1 --budget-usd 0.40 \
  --deck $PWD/eval/decks/<deck>.txt --name-a Seat --name-b ForgeAI \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:deepseek/deepseek-v4.1-flash --fast-a openrouter:deepseek/deepseek-v4.1-flash \
  --context-profile v4-faithful --memory-tools --lookahead --one-tier --env MULLIGAN_HEURISTIC=0 --env SKIP_HEURISTICS=0 \
  --out $PWD/runs/loop/g$N --cap-minutes 120
```

Vary the deck and the seating between games (`--deck-a/--deck-b`, add `--pairs` only when you want
both seatings of one seed): a defect usually belongs to a card type, and one deck shows few.
`MULLIGAN_HEURISTIC=0` so the mulligan is the model's decision too. `SKIP_HEURISTICS=0` so the harness
does not guess which priority windows matter: every window with something usable is asked, and the seat
may answer a PASS with `YIELD: end-of-turn` / `YIELD: my-next-turn` to stop being asked until then
(the opponent's spells always ask). Passed windows are `t:"window"` rows; `yield:` ones were the seat's.

`--one-tier`: every decision goes to the same model with its reasoning on. The two-tier split sent
58% of real decisions in runs/loop g01-g07 (every target, most instant-speed plays) to a tier with
reasoning disabled; how hard the model may think is not the harness's call. A separate condition
(stamped `one_tier`): compare it with two-tier games only as two arms.

## 2. The sheet (mechanical, never skip)

```bash
python3 eval/match_review.py runs/loop/g$N        # TAKEN / LOST / TOOL / SLOW / LOG, with JSONL lines
python3 eval/tool_audit.py   runs/loop/g$N
python3 eval/plan_audit.py   runs/loop/g$N
python3 eval/defect_analyzer.py runs/loop/g$N | tail -5      # ai_tier_count=0, silent_drop_total=0
python3 eval/cost_report.py  runs/loop/g$N
```

| On the sheet | Means | It is a defect when |
|---|---|---|
| `TAKEN` | the harness decided without asking | always, unless the choice really was forced: say why in the log |
| `LOST` | the model was asked and its answer was not used | always: open the row, read `raw_reply` and the rounds before it |
| `SKIP` | a priority window the play loop passed without asking while the seat had something usable (`t:"window"` rows; `reason` names the shortcut) | always, until the shortcut is replaced by a pass the model asked for |
| `TOOL REFUSED` / `FAILED` | the model asked for a lookup and the harness refused or broke | always |
| `TOOL empty` / `bad-args` | the lookup found nothing or named something not there | when what it asked for exists under other words, or it is something the model reasonably wants (a land drop previewed, a token inspected) |
| `SLOW`, `truncated reply` | retries, cut-off replies | when it repeats: it costs time and money and often precedes a `LOST` |
| `LOG` | an AI fallback, an exception, a failed payment | always |

## 3. Watch the game (the part a script cannot do)

Read the game log top to bottom once: `runs/loop/g$N/game-<seed>-<0|1>.log`. Then, for each turn of
the seat, open its decision rows in the JSONL (`prompt`, `options`, `raw_reply`) and ask:

1. **Was it asked?** Every choice a player would make this turn: land, each spell, each ability,
   attackers, blockers, targets, modes, costs, triggers, discards, scry. A choice that happened with
   no row, or with tier `auto`, was not the model's.
2. **Was the right action on the menu?** If a play a human would consider is missing, find out why
   in the engine before calling it a bug (mana, timing, legality).
3. **Did the prompt show what the decision turns on?** The rules text of every card involved, the
   opponent's visible board and mana, what is in the graveyards, what was revealed earlier. Search
   the prompt for it; do not assume.
4. **Did the answer take effect?** The log line after the decision must do what `raw_reply` chose.
   A reply that says one thing and a game that does another is a parser or router defect.
5. **Did it ask for something and not get it?** Every tool call and its result. A model that asks
   three different ways for the same thing is telling you what the harness is missing.
6. **Anything a person watching would call wrong**, even if no row flags it: a creature attacking
   into certain death every turn, a land never played, a pass with mana up and a full hand. Trace
   each to its row. Most turn out to be 1-5.

Ground a rules claim in the engine (`~/code/forge`, card scripts under
`forge-gui/res/cardsfolder/`), never in memory of how a card works.

## 4. Fix what the harness got wrong

One defect at a time, smallest first:

1. Write the test FIRST, from the recorded row (cite the run and the turn). Run it and watch it
   fail: `LineFindingProbeTest`, `NativeToolsTest`, `LookaheadProbeTest`, `SeatObservationTest` are
   where these live. A test that passes before the fix is not a test of it.
2. Fix it in `forge-overlay/` (never the Forge checkout). If the fix changes what the model sees,
   bump the version that covers it (`docs/STANDARDS.md` section 4).
3. If a script missed it, teach the script: `match_review.py`, `tool_audit.py`, or an acceptance
   stage. The loop gets faster every time the sheet catches what you had to read for.
4. With no game running: `FORGE_HOME=~/code/forge bash install.sh`, then
   `cd ~/code/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests` (check its exit
   code), then `bash eval/harness_acceptance.sh`. A canary that moved because an automatic path
   changed on purpose is re-pinned with a dated reason; never to make a run pass.
5. Commit with `pr-format`. Land the batch of fixes through a PR and the gate when a natural stop
   comes (a few games, or one large fix), not after every line.

Not everything is fixed on the spot. A defect that needs design goes in the log as OPEN with its
evidence; a model mistake goes in as a PUZZLE candidate.

## 5. Write it down, then play the next game

Append to `docs/harness/GAME-LOOP-LOG.md`:

```
### g07  2026-10-14  seed 61  Burn (seat) vs Mardu Energy (Forge AI)  lost T9  $0.04  build a955cd8d
- FIXED  attackers taken when one creature could attack (TAKEN x6, line 18). Test: aSingleCreature... Commit 37cff95a.
- OPEN   the seat asked to preview PLAY LAND three times (TOOL bad-args, lines 21, 109, 165).
- PUZZLE T6 sacrificed Ajani to Cabal Therapy with Guide of Souls available (line 132).
- clean  mulligan, blocks, targets.
```

Then go to step 1. Stop when the budget the owner gave is spent, or when three games in a row put
nothing new on the sheet and nothing new in your reading; say which.

## What not to do

- Do not launch a batch to "measure" before the sheet is clean: a number from a harness that is
  taking decisions measures the harness.
- Do not fix play. No strategy hints, no heuristics that choose for the model, no fallback to the
  Forge AI. If the model plays badly with everything in front of it, that is the result.
- Do not rebuild or run acceptance while a game is running.
