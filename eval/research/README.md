# Research catalogue

Every research note, technique and idea for this benchmark, in one place. A note is a dated file in this folder;
it is evidence for a decision, not the decision (that lives in `docs/`). Add a row here in the same commit as a new
note (`eval/tests/test_research_index.py` checks it), and move an idea's status when a PR or a note changes it.

Status words: **adopted** (in the harness or the eval on main), **draft PR** (open, not merged), **running** (an
experiment in progress), **negative** (measured; does not help as proposed), **open** (proposed, nothing built),
**parked** (blocked; the reason is given).

## Notes

| Date | Note | Question | Finding, in one line |
|---|---|---|---|
| 2026-10-15 | [anthropic-openai-eval-guidance](2026-10-15-anthropic-openai-eval-guidance.md) | What public eval guidance says about STR/PUZ/PvP | sample sizes and contamination rules behind EVAL-DESIGN |
| 2026-10-15 | [hillclimb-mechanize-primeintellect](2026-10-15-hillclimb-mechanize-primeintellect.md) | How to hill-climb without fooling yourself | held-out seeds, frozen panels, floors beside every score |
| 2026-10-15 | [scoring-design](2026-10-15-scoring-design.md) | One benchmark where higher is better | the three headline scores and their intervals |
| 2026-10-15 | [tool-protocol-and-action-parsing](2026-10-15-tool-protocol-and-action-parsing.md) | Is the seat's answer read correctly | led to answer-by-tool-call and zero text parsing |
| 2026-10-18 | [benchmark-research](2026-10-18-benchmark-research.md) | Prior art behind BENCHMARK-V1 | four agents read primary sources |
| 2026-11-03 | [propose-condition-prior-work](2026-11-03-propose-condition-prior-work.md) | Engine as type checker for proposed plays | prior work for the propose arm |
| 2026-11-05 | [agent-compute-and-feedback-survey](2026-11-05-agent-compute-and-feedback-survey.md) | Test-time compute and feedback for agents | 33 sources checked; ideas E1-E8 |
| 2026-11-05 | [rules-probe](2026-11-05-rules-probe.md) | A static, engine-labelled rules probe | the labeller (merged #359) |
| 2026-11-05 | [thinking-vs-sampling](2026-11-05-thinking-vs-sampling.md) | Think longer or sample more on hard puzzles | max effort costs 3x to 4x low; a finished game is 14 to 34 calls |
| 2026-11-05 | [novel-ideas](2026-11-05-novel-ideas.md) | Ten ranked new ideas, with costs and first experiments | the "Ideas" table below |
| 2026-11-05 | [seed-control-variate](2026-11-05-seed-control-variate.md) | A free same-seed covariate for STR-AI | valid and free (the AI game is fixed per seed); its rho is not measured |
| 2026-11-05 | [irt-pilot](2026-11-05-irt-pilot.md) | Can existing results calibrate puzzle difficulty | no: one-model or k=1 matrices; zero-model arms misfit |
| 2026-11-05 | [step-regret-feasibility](2026-11-05-step-regret-feasibility.md) | Can the engine label the first losing decision | yes once a recorded attempt replays (#365), then search |
| 2026-11-05 | [twin-cards-feasibility](2026-11-05-twin-cards-feasibility.md) | Renamed cards as a recall-vs-reading test | Forge loads set-less scripts; a missing card is silently skipped |
| 2026-11-06 | [forecast-floors](2026-11-06-forecast-floors.md) | What a model's P(win) forecast must beat | life difference alone is the floor: Brier about 0.21 on Luna turns (base 0.245); no signal on Luna turns 1-3 |
| 2026-11-06 | [step-regret-search](2026-11-06-step-regret-search.md) | Does replay-then-search label the regret step | not yet: 0 drift in 72 searches, but no exact step on 4 attempts (one bracket); the middle positions are not found |
| 2026-11-06 | [step-regret-board-model](2026-11-06-step-regret-board-model.md) | Can the splice board's model label every position and the regret step | yes: agrees with the search on 31 of 31 decided (baseline "all lost" 30); all 18 model-winnable positions engine-witnessed (was 1); regret at the second bare Desperate Ritual in all 4, its lost side and floor (1/4 three times, 2/6) model-derived |
| 2026-11-06 | [where-models-lose-splice](2026-11-06-where-models-lose-splice.md) | Across every recorded splice attempt, where do models lose a won puzzle | 60 of 104 failures labelled; 45 cast Desperate Ritual alone while the win splices it, 10 burn in response; winning side engine-witnessed at 59 of 60 steps (0 failed, 1 propose-arm drift); step median decision 3 of 17.5, floor mean 0.37; puzzle prompts never show the seat its cards' rules text (no splice text): defect candidate |
| 2026-11-06 | [ai-profile-spread](2026-11-06-ai-profile-spread.md) | Does STR-AI change with the Forge AI profile | not measured: no way to select a profile; the harness AI is the compiled `AiProps` defaults, not `Default.ai` (25 keys differ) |
| 2026-11-06 | [rlvr-env-feasibility](2026-11-06-rlvr-env-feasibility.md) | Can the harness be an RL env with engine rewards | terminal rewards yes, no JVM change (loopback endpoint as `step`; legal set is prompt text, parsed; tens of ms per call on a stub, epoch-76 build); process rewards are offline labels, from unmerged work (41 of 72 searches `not_found`) |
| 2026-11-06 | [contrast-sets](2026-11-06-contrast-sets.md) | Can splice puzzles come in contrast pairs (one visible detail, a different winning line) | yes, from a card swap only: 58 single-detail pairs trap one way (0 two-way; a life, Mountain, Alchemist or added card alone never contrasts, the model is monotone); 36 trap both ways with the life moved too; 10 pairs engine-checked (own lines 20/20 solve, cross lines 15/15 fail); no model run |

## Ideas

From [novel-ideas](2026-11-05-novel-ideas.md) unless noted. Statuses as of 2026-11-06.

| # | Idea | Status | Where |
|---|---|---|---|
| 1 | Same-seed control variate for STR-AI | free part done; paid rho **parked** (the provider account answered HTTP 402, issue #369) | [seed-control-variate](2026-11-05-seed-control-variate.md), #364 |
| 2 | Step regret: the first decision that loses a won puzzle | ReplayBridge **adopted** (#365); exact search **negative** on 4 splice attempts; the board model labels all 4 (winnable side engine-witnessed, lost side model-derived; draft PR #379) | [step-regret-search](2026-11-06-step-regret-search.md), [step-regret-board-model](2026-11-06-step-regret-board-model.md) |
| 3 | Twin cards: every card renamed, recall vs reading | **draft PR** #371; engine check **running** | [twin-cards-feasibility](2026-11-05-twin-cards-feasibility.md) |
| 4 | IRT calibration from a free population | **negative** on today's data | [irt-pilot](2026-11-05-irt-pilot.md) |
| 5 | Self-forecast calibration: P(win) probes, Brier/AUROC | free floors done; paid probe (about $0.11) **parked** (HTTP 402, issue #369) | [forecast-floors](2026-11-06-forecast-floors.md) |
| 6 | A playbook the model curates across episodes | open | |
| 7 | Budget-visible lookahead and its compute curve | open | |
| 8 | Robustness across Forge AI profiles, best of three | open; blocked on an overlay switch (design in the note) | [ai-profile-spread](2026-11-06-ai-profile-spread.md) |
| 9 | MCTS with an LLM prior as a measured arm | open | |
| 10 | Exportable RLVR environment with engine process rewards | open; terminal-reward env feasible with a Python adapter (stub policy, epoch-76 build); process rewards offline, unmerged inputs | [rlvr-env-feasibility](2026-11-06-rlvr-env-feasibility.md) |
| E1-E8 | Survey ideas (error notebook, compute-matched lookahead, world-model fidelity, contrast sets, ...) | E5 **adopted** (#359) for rules; E5 for play (splice contrast pairs) built, no model run, **open**; rest open | [survey](2026-11-05-agent-compute-and-feedback-survey.md), [contrast-sets](2026-11-06-contrast-sets.md) |

## Techniques

Methods that worked here and are worth reusing, with where each lives.

- **Replay a recorded attempt** (`replay:<attempt dir>`, `ReplayBridge`, #365): serve recorded `answer`/`set_plan`
  calls, stop on any drift in the ask, and compare the outcome by engine-log fingerprint with wall-clock lines
  dropped (`LlmSpikePuzzle.logFingerprint`). Use it to re-reach any decision of a game for free.
- **Scan from the end for the regret step**: winnability along an attempt is monotone, so replay K decisions then run
  the exact search, starting from the last K; the first unwinnable K bounds the regret step.
- **Common random numbers**: a zero-model game is a fixed function of its seed (10 of 10 replays identical), so a
  same-seed AI-in-seat game is a free paired control (`analyze_ab_stage0.py ab`).
- **Hardlinked private checkout** (`cp -al "$FORGE_HOME" <copy>`): an isolated Forge to add generated files to (twin
  card scripts) without touching the shared checkout; only add files, never write a hardlinked one in place.
- **Invented card names**: Forge loads any set-less `.txt` under `res/cardsfolder`, and a board naming a missing card
  loses it silently, so every twin experiment carries one deliberately uninstalled twin as a negative control.
- **Floors before paid probes**: compute the constant and public-feature baselines on games already on disk before
  paying for any model forecast or score.
- **A test against a live server under load** waits for the server-side event with a deadline and fails naming it,
  never on a fixed sleep (#376, open).
