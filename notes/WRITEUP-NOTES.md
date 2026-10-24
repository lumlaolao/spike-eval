# Write-up notes (gathered 2026-10-24, for the write-up)

Raw facts with their sources, to write from. Nothing here is a new measurement.

## 1. What the benchmark is

- A model plays Magic: The Gathering inside the real Forge rules engine. Every decision the engine asks a player
  (89 decision hooks, 44 ways to play a card) goes to the model as a tool call. The engine checks every answer, so
  an illegal move cannot happen. Forge's AI never decides for the model.
- Three headline scores, each with a confidence interval and the trivial baseline it beats:
  - **STR-AI**: win rate against Forge's AI.
  - **STR-HELDOUT**: the same on held-out seeds.
  - **PUZ**: puzzles (README, CLAUDE.md).

## 2. Hard puzzles: what exists

| Suite | Boards | Open / held out | Bar | Status |
|---|---|---|---|---|
| `puzzles-hard-gen-v1` | 116 | 84 / 32 | 10-15 steps, life = exactly what the line deals | merged (PR #290) |
| `puzzles-hard-gen2-v1` | 236 | 156 / 80 | 13-15 steps, life = searched maximum, trap 1-3 short, no mana unused | merged (PR #299) |
| `puzzles-hard-gen3-v1` | 68 | 44 / 24 | gen2's bar, four new families | PR #302, in review |

- gen2 families: splice2 77, hoofdrain 51, copy 35, grave 35, drain2 21, atog 15, elf2 2.
- gen3 families: tokens 24, landfall 17, equip 15, infect 12 (infect is a poison win, not life).
- Every board beat every floor:
  - random play, 0 of 20;
  - always-PASS, first-option and greedy, 0 of 4 each;
  - Forge's AI, at most 1 of 4.
- Every board's own written solution replays and wins in the engine.
- Caveat: "maximum" means the maximum within the generator's model of the board. The gate replays only the written
  line, so an engine-legal line the model leaves out is not caught.

## 3. Model results so far (only GPT-6 Luna has played)

- **Luna on gen2, open split:** 15 of 156 solved, 9.6% (Wilson 95% 5.9-15.3%), cost $3.62. Data is on the
  `demo-recordings` branch under `puzzles/hard-gen2-luna`, commit 8849d80.
  - By family: copy 9/25, grave 5/22, atog 1/10. Splice2, hoofdrain, drain2 and elf2 were all 0.
  - Floors on the same boards: 0. So 9.6% is above every trivial baseline, and the CI excludes 0.
- **Luna on the 10 hardest gen1 boards:** 0 of 20 attempts (`puzzles/hard-gen-luna-top10`).
- Luna's real bill was about half the token-based estimate.

## 4. The 3 hardest puzzles (planned frontier run, NOT run; the owner gives the final say)

| Board (gen2 open) | Steps | Opponent life | Luna left it at |
|---|---|---|---|
| `hard-splice2-325cd33e0a57` | 15 | 21 | 13 |
| `hard-hoofdrain-60c5afda2f25` | 15 | 65 | 54 |
| `hard-drain2-0ab6f6ae05d9` | 15 | 19 | 9 |

- **How they were picked:** Luna failed every attempt and fell furthest short of the goal, and each comes from a
  family where Luna solved 0.
- **Models:** 21 chat models plus Jev Router.
- **Left out:**
  - GPT-6 Astra and Astra Pro, and Claude Fable 5.1 (owner, 2026-10-24);
  - Claude Opus 5.5 (CLAUDE.md game-model rule);
  - Jev 1.14 (Decisions API, no bridge yet).
- **Estimated cost:** about $43 typical. Each model is capped at its "high" estimate, $133 at most.
  - Chart: `cost-table-3.png` (scratchpad).
  - Script: `notes/frontier3-plan.sh`. It refuses without the owner's egress flag, runs 5 at a time and resumes.
- **Pending first:** a provider-compatibility audit, so that each model gets a fair seat.

## 5. Harness-building eval (branch `feat/harness-env-framework`, no PR yet)

- **What it measures:** whether an agent can BUILD a working Magic-playing harness without seeing the grading
  criteria.
- **How the questions are made:** they are generated from card scripts with a secret seed. The same seed reproduces
  identical question sets and scores.
- **Score:** five checks per instance, combined by a geometric mean with equal weights and a 0.001 floor. It was the
  only setting that passed every calibration test.
- **Calibration:**
  - our harness: 0.979 (67 of 69; the 2 failures are real seat defects);
  - the default bridge, the toy harnesses and the ablations: 0;
  - an "agent" that copies our harness: 0.979;
  - one that does nothing: 0.
- **Seat defects it found:**
  - an illegal block stands against a must-block requirement;
  - Green Sun's Zenith's "Cancel Search?" loops forever.

## 6. Engineering done in this stretch

- **Acceptance runs in parallel:** under 5 minutes was the goal (PR #301). A dead child job can no longer show a
  false PASS (killtest proven).
- **`puzzle_solve.py --resume`** continues a stopped paid run, and refuses a different model, suite or seat
  (PR #300).
- **Jev Router pilot:** $0.92 for 18 decisions, stopped by the budget watcher. Projected $1.5-2.5 per attempt,
  because it reasons heavily and caches little.

## 7. Open owner decisions

- The frontier run: go / no-go, and the model list (above).
- Opus in or out (CLAUDE.md says out unless the owner names it).
- A Jev 1.14 decision bridge: it answers with a structured choice, not the `answer` tool call that CLAUDE.md
  requires.
- Harness eval:
  - the builder model and budget;
  - whether to adopt the scoring setting;
  - whether handing mana payment to Forge's AI is penalized (that would zero our own score today);
  - a contamination flag.
- Whether to fix the two seat defects before the frontier run. They are game paths, not puzzle paths, so they
  likely don't affect these 3 boards; to verify.
