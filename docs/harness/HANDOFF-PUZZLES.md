# Handoff: keep working the puzzles (2026-10-19, updated after batches 10 and 11)

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

harness-epoch-64, observation-faithful-v42, lookahead-preview-v15. Epoch 64: a divided split gives each target at
least 1 and takes no more targets than its total (RP_221). v42: an effect of an effect carries no engine id (g33).
Epoch 63: a play a static forbids (Pithing Needle, Null Rod, Gaddock
Teeg) is no longer on the menu; Forge's canPlay() leaves those to checkRestrictions (RP_207, REPORT.md batch 10).
Before that: harness-epoch-62 (`docs/harness/VERSIONS.md`). Epochs 60 to 62 on this branch: Port-style
"tap a land" costs can't also tap the source for mana; the lookahead previews stack-targeting spells (Force of
Negation) and shows exile counts; payment sacrifices, a forced free cast's tax and an already-confirmed optional cast
are the seat's; `PuzzleArms.SETUP` makes choices during puzzle setup non-draws; named cards survive puzzle setup.
Details: `docs/harness/ARCHITECTURE.md`, `docs/harness/ENGINE-PATCHES.md`, last commits on the branch.

## Track 1: the rules backlog

**Done.** `eval/puzzles/rules-pilot/manifest.jsonl` has 996 rows, one per triage question: 199 converted, 10 Forge
disagreements, 8 multiple-review, 779 dropped with a reason, no harness bug. Batch 10 re-gated RP_197, RP_198, RP_207,
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

Running when this was written: `runs/puzzles/prove-all-e64-b200` (368 candidates from `forge-candidates`, budget
200, `-j 2`, epoch 64; launcher `/tmp/puzzle-scratch/prove64.sh`, then `prove64-pass2.sh` runs budget 1500 over the
not_found ids by itself). Stopped on request at 25 of 368 (4 proven: MTGP_02, MTGP_06, MTGP_08,
PC_020216; the rest not found at budget 200). A new container has neither: regenerate the candidates and start it again. `prove` has no resume and
writes `--write` after each puzzle. Then a second pass at budget 1500 over the not_found ids (`--ids`). Not found at
budget 200 means nothing yet: most Forge puzzles need longer lines. `runs/puzzles/` is git-ignored.

## Track 3: Luna games

g30 to g33 are logged (`docs/harness/GAME-LOOP-LOG.md`). Games run from a copy of the checkout built for them
(`/root/forge-games`, `--forge-home /root/forge-games`, `/tmp/forge_cp.txt` with its module classes first), so a
rebuild never swaps classes under the prover. Next: g34 on a deck and seed not used yet (seeds 4101-4133 are used). `scripts/puzzle-gate/waitg.sh NN`
waits for a game in `$SCRATCH/live/gNN` (pgrep with a bracketed pattern so it can't match itself).

## Then

Open the PR for this branch (`pr-format`, `pr-review`, `simplify`, `standards`, `scripts/pr_merge.sh`) when
batch 10 and g30 are in.
