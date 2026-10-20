# Handoff: keep working the puzzles (2026-10-19, updated after batch 12 and the PR #268 review)

For a fresh session. Branch `feat/puzzle-search-prover` (PR #268, open). Read `CLAUDE.md`, then
`eval/PUZZLES.md` (the prover, acceptance, uniqueness) and `eval/puzzles/rules-pilot/REPORT.md` (batches 1 to 9),
then this file.

**The order of work is now `docs/harness/WORK-PLAN.md`, "The plan (owner, 2026-10-19)"**: land PR #268,
score PUZ, then decide on the suite's size. The tracks below are its detail, not its order.

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

harness-epoch-65, observation-faithful-v42, lookahead-preview-v15. Epoch 65: a source whose tap damage is lethal
pays last for a harness seat (City of Brass after Llanowar Elves). Epoch 64: a divided split gives each target at
least 1 and takes no more targets than its total (RP_221). v42: an effect of an effect carries no engine id (g33).
Epoch 63: a play a static forbids (Pithing Needle, Null Rod, Gaddock
Teeg) is no longer on the menu; Forge's canPlay() leaves those to checkRestrictions (RP_207, REPORT.md batch 10).
Before that: harness-epoch-62 (`docs/harness/VERSIONS.md`). Epochs 60 to 62 on this branch: Port-style
"tap a land" costs can't also tap the source for mana; the lookahead previews stack-targeting spells (Force of
Negation) and shows exile counts; payment sacrifices, a forced free cast's tax and an already-confirmed optional cast
are the seat's; `PuzzleArms.SETUP` makes choices during puzzle setup non-draws; named cards survive puzzle setup.
Details: `docs/harness/ARCHITECTURE.md`, `docs/harness/ENGINE-PATCHES.md`, last commits on the branch.

## Track 1: the rules backlog

**Done.** `eval/puzzles/rules-pilot/manifest.jsonl` has 996 rows, one per triage question: 204 converted, 10 Forge
disagreements, 8 multiple-review, 774 dropped with a reason, no harness bug. Batch 10 re-gated RP_197, RP_198, RP_207,
RP_209 and added RP_214 to RP_217; batch 11 rowed the 74 maybes and 221 no's. What is left is new questions, or
boards for drops whose reason a harness change removes. Known reasons to drop: replacement- and cost-order questions
(Forge never offers the order), a card Forge lacks, near-duplicates (name the puzzle), legality questions (the engine
offers only legal plays). A setup-time choice under a *model* solver still asks the model (`SETUP` covers the arms).

Batch 12 (RP_218 to RP_222) re-read the legality and unobservable drops: 204 converted now. **Next: batch 13.** Four
agents re-screened the 485 drops of batches 1-9 and proposed 19 boards, in `eval/puzzles/rules-pilot/batch13-drafts/`
(`partN.candidates.md` with the reasoning, `sN_K.pzl` drafts, `V_sN_K.summary` gate results on epoch 64). 8 were
gated: proven, unique, pass arm rejected for s0_1, s0_2, s2_0, s2_1, s2_3, s2_4; not found for s0_0 (Teysa/haunt)
and s2_2 (Ancient Vendetta). Still to gate: s0_3, s0_4, s1_0-3, s3_0-4. For each keeper: number it RP_223+ (fix the
`Name:` line), re-gate from the committed file, row it (`scripts/puzzle-gate/` helpers; the row writer used last is
a copy of batch 10's: one `converted(...)` call per puzzle, replacing the question's dropped row), and add a REPORT
section. Not found means read the search log: a board error or a Forge disagreement.

**2026-10-20: 526 converted (the 500 goal is met).** After batch 14's RulesGuru questions, the rest were authored
from the Comprehensive Rules: `authored-drafts/x_*` (by hand), `y_*` (keywords and mechanics) and `z_*` (stack,
priority, layers, timing), about 70% of drafts passing. Each keeper's row has question id `authored-<slug>`.
Near-duplicates kept but not counted: RP_300, RP_402, RP_454, RP_521. Lessons from the gate:
- "multiple" comes from a redundant action: an extra land drop from a drawn card (fill libraries with Colossal
  Dreadmaw), a spare mana or a free activation that can repeat, a yes/no on a trigger that changes nothing, a
  creature that can sacrifice itself. A second line that only reorders or picks other payers is accepted with
  `REVIEWED="reason"` (RP_322, RP_329, RP_371).
- When one spell both kills a creature and ends the game, the creature never reaches the graveyard: keep the
  opponent alive if the acceptance counts the graveyard.
- Fireball-style X and target choices exhaust the 600-rollout budget.

**Two harness gaps found and fixed (epoch 66):**
1. Silent Arbiter: the attack menu took declarations with more than one attacker; the engine refused each and asked
   again, so the search looped for hours (x_0, x_15, x_18). A refused attack is now asked once more with the reason,
   then made legal, and a refused set is not noted as a solver choice. All three now convert (RP_532-RP_534).
2. A copy that may choose new targets (Twincast) was targeted by Forge's AI under the search arm, so the prover never
   tried another target (z_4). The prover targets copies as the model seat does; z_4 converts (RP_535).

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

Last run: `python3 eval/puzzle_baselines.py prove --suite runs/puzzles/candidates.json --budget 200 -j 2` (368
candidates from `forge-candidates`, epoch 64, out `runs/puzzles/prove-all-e64-b200`; the run directory was not kept).
Stopped on request at 25 of 368 (4 proven: MTGP_02, MTGP_06, MTGP_08,
PC_020216; the rest not found at budget 200). To run it again, regenerate the candidates first. `prove` has no resume and
writes `--write` after each puzzle. Then a second pass at budget 1500 over the not_found ids (`--ids`). Not found at
budget 200 means nothing yet: most Forge puzzles need longer lines. `runs/puzzles/` is git-ignored.

## Track 3: Luna games

g30 to g33 are logged (`docs/harness/GAME-LOOP-LOG.md`). Games run from a copy of the checkout built for them
(`/root/forge-games`, `--forge-home /root/forge-games`, `/tmp/forge_cp.txt` with its module classes first), so a
rebuild never swaps classes under the prover. Next: g34 on a deck and seed not used yet (seeds 4101-4133 are used).

## Then

The order of work is `docs/harness/WORK-PLAN.md`, "The plan (owner, 2026-10-19)".
