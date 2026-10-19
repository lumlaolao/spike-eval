# Handoff: keep working the puzzles (2026-10-19)

For a fresh session. Branch `feat/puzzle-search-prover` (pushed, not yet a PR). Read `CLAUDE.md`, then
`eval/PUZZLES.md` (the prover, acceptance, uniqueness) and `eval/puzzles/rules-pilot/REPORT.md` (batches 1 to 9),
then this file.

## The owner's ask

"Work on those basic puzzles and then work through our rules backlog through the whole 900, while also in the
background running Luna games that we can find errors in the harness." Three tracks, in priority order:

1. **Rules backlog** (`eval/puzzles/rules/triage.jsonl`, 996 questions): turn each into an engine-checked puzzle
   (start state, acceptance, one proven solution, unique) or a manifest row saying why not.
2. **Forge built-in puzzles** through the prover (`eval/puzzle_baselines.py prove`).
3. **Luna games** one at a time with the `game-loop` skill, to find harness defects.

Owner rules that bite here: game model GPT-6 Luna only (`openrouter:openai/gpt-6-luna`), never Opus; never set
`OPENROUTER_ALLOW_EGRESS` yourself (the user sets it); `--budget-usd 0.40` per game and say the cost; edit only
`forge-overlay/`; tests first and watch them fail; commit lines at most 100 characters with a body saying why; no
model identifiers in commits. Skip acceptance and the gauntlets unless asked (owner, 2026-10-18 night).

## State of the code

harness-epoch-62, lookahead-preview-v15 (`docs/harness/VERSIONS.md`). Epochs 60 to 62 on this branch: Port-style
"tap a land" costs can't also tap the source for mana; the lookahead previews stack-targeting spells (Force of
Negation) and shows exile counts; payment sacrifices, a forced free cast's tax and an already-confirmed optional cast
are the seat's; `PuzzleArms.SETUP` makes choices during puzzle setup non-draws; named cards survive puzzle setup.
Details: `docs/harness/ARCHITECTURE.md`, `docs/harness/ENGINE-PATCHES.md`, last commits on the branch.

## Track 1: the rules backlog

- `eval/puzzles/rules-pilot/manifest.jsonl`: 701 rows (191 converted, 488 dropped with a note, 10 Forge
  disagreements, 9 multiple-review, 3 harness bugs). 218 `.pzl` files, last one RP_216.
- Triage is 216 good / 559 maybe / 221 no. **Every good question has a row.** Without a row: **74 maybes and all
  221 no's.** List them:
  ```bash
  python3 -c "import json;m={json.loads(l)['question_id'] for l in open('eval/puzzles/rules-pilot/manifest.jsonl')}
  [print(r['id'],r['convert'],r['note'][:90]) for r in map(json.loads,open('eval/puzzles/rules/triage.jsonl')) if r['id'] not in m]"
  ```
- **Batch 10 is half done and has no manifest rows or REPORT section yet.** Results from the old container (all on
  epoch 62, budget 600, cap 60 s; every one proven, accepted, and the pass arm rejected):
  | puzzle | search replays | note |
  |---|---|---|
  | RP_197 | 4 | re-gate after the epoch 60-62 fixes |
  | RP_198 | 37 | re-gate |
  | RP_207 | 2 | re-gate |
  | RP_209 | 8 | re-gate |
  | RP_214, RP_215, RP_216 | 2, 10, 4 | new drafts (committed in 09c8a14) |
  Re-run them with the gate below (cheap, about 1 to 4 minutes each) to get uniqueness fields, then write their rows
  and a "Batch 10" section in REPORT.md in the style of batches 5 to 9.
- Still deferred inside batch 10: triage lines L542, L61 and the face-down questions (L292, L425, L477, L491, L500).
  Face-down cards are turned face up on loss (`Game.onPlayerLost`), so acceptance must not depend on a face-down
  card staying hidden; see PUZZLES.md.
- Known reasons to drop (write the reason in the row's `note`): replacement-order and cost-order questions (Forge
  never offers the order); questions needing a card Forge lacks; near-duplicates (name the puzzle repeated).
- Known gap: a setup-time choice under a *model* solver still asks the model (`SETUP` covers only the arms).

### The gate (helpers in `scripts/puzzle-gate/`)

```bash
export SCRATCH=/tmp/puzzle-scratch; mkdir -p $SCRATCH
STAGES="classpath compile" FORGE_HOME=/root/forge bash eval/harness_acceptance.sh | tail -3
# the run dir it prints (runs/harness-acceptance/<ts>) is an isolated build; pin it:
ls -d runs/harness-acceptance/* | tail -1 > $SCRATCH/epoch62-build.txt
bash scripts/puzzle-gate/gate10.sh RP_214          # search arm then pass arm -> $SCRATCH/rp10/RP_214.summary
bash scripts/puzzle-gate/gate10.sh V_draft         # a scratch variant at $SCRATCH/rp10v/draft.pzl
bash scripts/puzzle-gate/gate10.sh R_RP_214 1,2,3  # random arm, logs only
GREP=PASSED bash scripts/puzzle-gate/gtest.sh -testclass forge.spike.PuzzleSearchTest   # compile + TestNG
```

`gate10.sh` runs at most two JVMs and waits while `$SCRATCH/HOLD` exists (create it before
`install.sh` or a rebuild, delete it after). Accept a puzzle only when the search arm is `proven` and `accepted`
and the pass arm is `rejected`; a pass that wins means the puzzle is trivial.

## Track 2: Forge built-in puzzles

`runs/puzzles/prove-all-e60-b200` (budget 200, epoch 60) **died** at 36 of 333 (35 not_found, 1 proven:
MTGP_06); the JVM was gone when this was written. `runs/puzzles/` is git-ignored, so a new container has no copy.
Restart it detached (`setsid nohup ... &`), with `--budget 200 -j 2`, on the current build (command in
PUZZLES.md, "prove"), and resume rather than restart if the directory survives. Then a second pass at a higher
budget (1500) over the not_found ones. Not-found at budget 200 means nothing yet: most Forge puzzles need longer
lines.

## Track 3: Luna games

Last logged game g29 (clean). **g30 is played but not reviewed:** `runs/loop/g30` (seed 4130, epoch 62, Jund
Sacrifice, $0.05, 99 calls). Run the sheet (`match_review.py`, `tool_audit.py`, `plan_audit.py`,
`defect_analyzer.py`, `cost_report.py`), read the sacrifice payments (the new epoch-62 path), log it in
`docs/harness/GAME-LOOP-LOG.md`, then play g31 on a deck and seed not used yet. `scripts/puzzle-gate/waitg.sh NN`
waits for a game in `$SCRATCH/live/gNN` (pgrep with a bracketed pattern so it can't match itself).

## Then

Open the PR for this branch (`pr-format`, `pr-review`, `simplify`, `standards`, `scripts/pr_merge.sh`) when
batch 10 and g30 are in.
