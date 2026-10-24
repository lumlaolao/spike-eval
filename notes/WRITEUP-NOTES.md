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
| `puzzles-hard-gen3-v1` | 68 | 44 / 24 | gen2's bar, four new families | merged (PR #302) |

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
- **Models:** 21 chat models plus Jev 1.14.
- **Left out:**
  - GPT-6 Astra and Astra Pro, and Claude Fable 5.1 (owner, 2026-10-24);
  - Jev Router (owner, 2026-10-24: "I want jev");
  - Claude Opus 5.5 (CLAUDE.md game-model rule).
- **Jev 1.14** answers through its own bridge to OpenRouter's Decisions API (PR #305, in review). Its typed choice becomes the harness's `answer` call. This is an owner-approved exception to "every
  answer is a tool call", because Jev makes no tool calls. It gets no lookup tools.
- **Estimated cost:** about $37 typical. Each model is capped at its "high" estimate, $125 at most.
  - Chart: `cost-table-3.png` (scratchpad).
  - Script: `notes/frontier3-plan.sh`. It refuses without the owner's egress flag, runs 5 at a time and resumes.
- **Provider audit (2026-10-24), merged as PR #303:**
  - every planned model is now priced, and `puzzle_solve` refuses an unpriced one (unpriced was billed at $0, so
    the budget never stopped it);
  - budgets are checked at each model's dearest tool-taking endpoint (`max` in prices.json), because latency
    routing may land on a priority tier (GPT-6.1 Sol's "ultrafast" is 6x list; GLM 5.3's dearest provider 35x);
    reports keep the list price;
  - one `provider` object per request;
  - 13 models whose reasoning cannot be turned off get their lowest effort wherever "none" would be sent (fast
    tier, truncation retry), hashed into `model_cfg_sha`.
- **Before the run:** the plan's per-model caps were sized from list prices. Budgets now check at `max`, so re-derive
  each cap from the `max` prices, or a model is stopped early.
- **Still open from the audit (owner decisions):**
  - reasoning effort is each model's default, which differs (max for Kimi K3 and GLM);
  - Mistral may refuse a user message right after a tool result (plausible, unverified);
  - Anthropic caching has never been measured working.

## 5. Harness-building eval (PR #304, in review)

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
- **Open hole (owner question 12):** a candidate's code runs inside the grader's JVM, so it could rewrite its own
  report or switch off the AI counter. Until the seat runs in its own process, the episode grader withholds a
  candidate's score unless the operator passes `--trust-candidate`.
- **Seat defects it found:**
  - an illegal block stands against a must-block requirement;
  - Green Sun's Zenith's "Cancel Search?" loops forever.

## 6. Engineering done in this stretch

- **Acceptance runs in parallel:** under 5 minutes was the goal (PR #301). A dead child job can no longer show a
  false PASS (killtest proven).
- **`puzzle_solve.py --resume`** continues a stopped paid run, and refuses a different model, suite or seat
  (PR #300).
- **New hard suite merged:** gen3, 68 boards (PR #302).
- **Provider fixes merged:** PR #303 (above).
- **Jev Router pilot:** $0.92 for 18 decisions, stopped by the budget watcher. Projected $1.5-2.5 per attempt,
  because it reasons heavily and caches little.

## 7. Open owner decisions

- The frontier run: go / no-go, and the model list (above).
- Opus in or out (CLAUDE.md says out unless the owner names it).
- Jev 1.14 (approved 2026-10-24; PR #305): add the tool-call exception to CLAUDE.md? Multi-picks and trigger order
  are built from independent yes/no questions; a 40-option cap on listed numbers is a guess.
- Harness eval:
  - the builder model and budget;
  - whether to adopt the scoring setting;
  - whether handing mana payment to Forge's AI is penalized (that would zero our own score today);
  - a contamination flag;
  - question 12: run the candidate seat in its own process (closes the in-JVM hole) or keep it in-JVM.
- Whether to fix the two seat defects before the frontier run. They are game paths, not puzzle paths, so they
  likely don't affect these 3 boards; to verify.
