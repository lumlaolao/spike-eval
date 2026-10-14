---
name: defect-hunt
description: Play ONE game, read it decision by decision, and file every harness defect as a self-contained GitHub issue with its labels and severity, then comment on the hunt-log issue. Use when asked to hunt defects, to run the hunt, to work the harness-defect backlog's evidence side, or to "find what the harness got wrong". It never fixes anything: a fix is `defect-fix`, a separate pass with its own claim.
---

# Defect hunt: one game, all of its defects, no fixes

A hunt is `game-loop` steps 1-3 (play one game, run the sheet, read it) with two differences: its
output is GitHub issues rather than a fixed file, and it holds **the shared Forge lock** so a second
hunter cannot play or rebuild against the same checkout while this game is being read.

The rule of the hunt, unchanged from `game-loop` and `harness-audit`: **a finding is a HARNESS
defect unless you can show the model had what it needed.** A model that was shown everything and
chose badly is a puzzle candidate, not an issue. Check the prompt of the decision row before you
write an issue about it.

Do not edit any file in the repository during a hunt — not a fix, not a test, not a skill. A hunt
that changes the tree invalidates the run it is reading.

## 0. Take the shared Forge lock

The lock covers a whole hunt: it excludes a second hunter's game, its build and its Forge checkout.
`flock(1)` is not on macOS, so the lock is a `mkdir`, which is atomic on every filesystem here.

```bash
LOCK="$HOME/.cache/mtg-hunt/forge.lock"
mkdir -p "$(dirname "$LOCK")"
take_lock() {
  mkdir "$LOCK" 2>/dev/null && return 0
  # a lock whose owner is gone is stale; reclaim it once, then give up
  p=$(awk '{print $1}' "$LOCK/owner" 2>/dev/null)
  if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null && return 0; fi
  return 1
}
take_lock || { echo "Forge lock held: $(cat "$LOCK/owner" 2>/dev/null)" >&2; exit 1; }
printf '%s %s %s\n' "$$" "$(date -u +%FT%TZ)" "$(id -un)" > "$LOCK/owner"
```

No `trap` releases it. Each step of a hunt is its own shell, so a trap on `EXIT` would drop the lock
the moment the step that took it returned — before the game, the sheet and the rebuild it exists to
cover. It is held until step 7 releases it explicitly, and the dead-owner test reclaims it if the
session dies without doing so.

The lock is a convention between `defect-hunt` and `defect-fix`. `run_batch.py`, `install.sh` and
`eval/harness_acceptance.sh` read no lock file, so a hunter who ignores the convention is not
blocked by it. That is why the preconditions below are not optional even holding the lock:

```bash
uptime                                   # above 4 load per core: wait, and say so
pgrep -f LlmSpikeHeadless | wc -l        # must be 0
git status --short                       # a clean tree on main
cat FORGE_COMMIT                         # the engine this hunt's rules claims are grounded in
```

## 1. Pick a deck and a seed nobody has played

Read before choosing, so the backlog is not rediscovered:

```bash
HUNTLOG=$(gh issue list --label hunt-log --state open --json number --jq '.[0].number')
gh issue view "$HUNTLOG" --comments | tail -80        # the games already played
gh issue list --label harness-defect --state open --json number,title,labels   # the open backlog
tail -60 docs/harness/GAME-LOOP-LOG.md               # the older games, newest last
```

Choose a seed not already in `runs/hunt/` or `runs/loop/`, and not in a reserved block
(`eval/seed_policy.py` is the source of truth; 501-525, 20000-29999, 30000-30099 are never playable
targets). Vary the deck and the seating from the last game — a defect usually belongs to a card
type, and one deck shows few — with `--deck-a/--deck-b` in place of `--deck`, and `--pairs` only
when you want both seatings of one seed. `--deck` alone is a mirror match and `run_batch.py` refuses
it alongside `--deck-a/--deck-b`.

## 2. Play exactly one game

```bash
N=$(printf %02d $(( $(ls -d runs/hunt/h* 2>/dev/null | wc -l) + 1 )))
python3 eval/run_batch.py --seeds $SEED --exploratory --jobs 1 --budget-usd 0.15 \
  --deck $PWD/eval/decks/<deck>.txt --name-a Seat --name-b ForgeAI \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:deepseek/deepseek-v4.1-flash --fast-a openrouter:deepseek/deepseek-v4.1-flash \
  --context-profile v4-faithful --memory-tools --env LOOKAHEAD=1 --env MULLIGAN_HEURISTIC=0 \
  --env "TIER_FAST_TYPES=targets,target,zone-change,prevent-effect,ability-to-play,play-single,order" \
  --out $PWD/runs/hunt/h$N --cap-minutes 20
```

One game costs about $0.04 and `--budget-usd` is mandatory on a paid model (`CLAUDE.md`). Never set
`OPENROUTER_ALLOW_EGRESS` yourself; it is the owner's switch and it is already on or the run fails
at the factory. One game at a time on the Claude CLI lane.

## 3. The sheet (mechanical, never skip, never paraphrase its numbers)

```bash
python3 eval/match_review.py   runs/hunt/h$N        # TAKEN / LOST / TOOL / SLOW / LOG with JSONL lines
python3 eval/tool_audit.py     runs/hunt/h$N        # every tool request: served / REFUSED / FAILED / empty / bad-args
python3 eval/plan_audit.py     runs/hunt/h$N        # plays that contradict the seat's own stated plan
python3 eval/line_report.py    runs/hunt/h$N        # sacrifices, triggers, activations, casts from outside the hand
python3 eval/defect_analyzer.py runs/hunt/h$N | tail -5   # hard invariants: ai_tier_count=0, silent_drop_total=0
python3 eval/cost_report.py    runs/hunt/h$N        # the spend of this game, for the running total
```

A non-zero exit from `defect_analyzer.py` is a `sev:blocker` by itself. Never weaken the analyzer to
make a hunt clean; it is the oracle (`CLAUDE.md`).

## 4. Read the game decision by decision (the part a script cannot do)

The sheet finds candidates; it does not decide. Read `runs/hunt/h$N/game-<seed>-<0|1>.log` top to
bottom once, then open each of the seat's decision rows in the JSONL (`prompt`, `options`,
`raw_reply`) and answer `game-loop` step 3's six questions: was it asked; was the right action on
the menu; did the prompt show what the decision turns on; did the answer take effect; did it ask for
something and not get it; anything a person watching would call wrong. Trace each answer to its row.

Ground every rules claim in the engine (`~/code/forge`, card scripts under
`forge-gui/res/cardsfolder/`, pinned commit in `FORGE_COMMIT`), never in memory of how a card works.

## 5. Classify and severity

Class and kind come from `harness-audit` step 2; severity is the mapping below. A `MODEL` finding is
never filed here.

| Signal | Kind | Labels |
|---|---|---|
| `tier=ai` row, `[FALLBACK->AI]` in the log | the engine's AI chose | `harness-defect` `sev:blocker` |
| silent drop (an ask in the log with no JSONL row) | a decision missing from the record | `harness-defect` `sev:blocker` |
| `TAKEN` (the harness decided without asking) | `not-asked` | `harness-defect` `sev:high` |
| `LOST` (asked, and the answer was not used) | `parse` | `harness-defect` `sev:high` |
| tool `REFUSED` | `refused-tool` | `harness-defect` `sev:high` |
| the prompt omits the rules text of the deciding card | `not-shown` | `harness-defect` `sev:high` |
| a play a person would consider is absent from the menu | `not-offered` | `harness-defect` `sev:high` |
| `[ROUTER] play: FAILED to pay` | `wrong-effect` | `harness-defect` `sev:high` |
| tool `FAILED` for the whole arm | the tool broke | `harness-defect` `sev:blocker` |
| repeated `SLOW` / `truncated reply` / `attempts > 1` | `budget` | `harness-defect` `sev:medium` |
| tool `empty` when the thing exists under other words | `not-offered` | `harness-defect` `sev:medium` |
| a plan HOLD-VIOLATION the harness executed | `plan` | `harness-defect` `sev:medium` |
| a menu or preview worded wrongly, a sheet gap, a stale label | `wrong-effect` | `harness-defect` `sev:low` |

Add `needs-owner` when the fix needs a decision only the owner can make; that takes the issue out of
`defect-fix`'s queue. Add `cannot-reproduce` when the evidence no longer reproduces on the current
engine and overlay, with the command that failed to reproduce it.

## 6. File one issue per defect, then comment on the hunt-log issue

One self-contained issue per defect — self-contained means a reader who has never opened the run can
act on it. Title prefix is the kind (`not-shown:`, `wrong-effect:`, `refused-tool:`, `infra:` for
harness tooling that is not a game decision), then the defect in one sentence.

```bash
gh issue create \
  --title "<kind>: <what the harness did, in one sentence>" \
  --label harness-defect --label sev:<blocker|high|medium|low> \
  --body-file /tmp/defect-<n>.md
```

Body, in this order:

```
## Where
run `runs/hunt/h03`, seed 61, swap 0, turn 6, decision `play-single`, row `game-61-0.jsonl:812`

## What the harness did
the row, quoted: prompt says ... options are ... raw_reply says ... the log line after it does ...

## Why this is the harness, not the model
the prompt showed X and not Y; the model asked for Z and the harness refused/ignored it

## What a fix looks like
the smallest change, and which file behaves wrongly

## Evidence for the test that should exist
the row above and the assertion that fails today
```

Then comment once on the hunt-log issue (`$HUNTLOG`), in the `docs/harness/GAME-LOOP-LOG.md` entry
format, citing the issues this game filed or saw again. One comment per game; the next hunter reads
the newest comments to pick a deck and seed nobody has played.

```bash
gh issue comment "$HUNTLOG" --body "### h03  2026-10-14  seed 61  Burn (seat) vs Mardu Energy (AI)  lost T9  \$0.04  build a955cd8d
- #120 OPEN  attackers taken when one creature could attack (TAKEN x6, line 18)
- #121 OPEN  the seat asked to preview PLAY LAND three times (bad-args, lines 21, 109, 165)
- clean  mulligan, blocks, targets."
```

A game that files nothing is a result: comment that, with the sheet's counts. Two games in a row
that file nothing and leave no fixable issue open is the loop's stop condition.

## 7. Release the lock, exactly

Release explicitly — nothing else will — and do it even when the hunt failed:

```bash
rm -rf "$LOCK"
```

If a session died holding the lock, reclaim it once its owner pid is gone:

```bash
p=$(awk '{print $1}' "$LOCK/owner"); kill -0 "$p" 2>/dev/null || rm -rf "$LOCK"
```

Never leave a fresh-game rebuild queued behind a hunt you abandoned.

## What not to do

- Do not fix anything, not even a one-line fix you are sure of: that is `defect-fix`, with its own
  claim, its failing test on main and its gate. A hunt that edits the tree has no run left to read.
- Do not file a MODEL finding as `harness-defect`. If the prompt held everything, it is a puzzle
  candidate (`harness-audit` step 5), not an issue.
- Do not play a batch to "get more data" first: one game read properly beats six skimmed, and a
  number from a harness that is taking decisions measures the harness.
- Do not pool arms, and never compare runs whose `context_sha`, `memory_tools_version`,
  `native_tools` or `TIER_FAST_TYPES` differ: the env-backed knobs this skill sets are not all
  stamped on the game line yet (open issue #117), so two runs a stamp-comparison calls identical can
  still differ in the fast-tier list.
- Do not rebuild, install or run acceptance while any game is running, and never on a lock you hold
  for someone else.
- Do not set `OPENROUTER_ALLOW_EGRESS`, and do not play a model with no entry in
  `eval/pricing/prices.json`.
