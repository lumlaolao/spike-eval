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
| 2026-11-06 | [forecast-floors](2026-11-06-forecast-floors.md) | What a model's P(win) forecast must beat | life difference alone: Brier about 0.21 on Luna turns (base 0.245); turns 1-3 carry no signal |

## Ideas

From [novel-ideas](2026-11-05-novel-ideas.md) unless noted. Statuses as of 2026-11-06.

| # | Idea | Status | Where |
|---|---|---|---|
| 1 | Same-seed control variate for STR-AI | free part done; paid rho **parked** (the provider account answered HTTP 402, issue #369) | [seed-control-variate](2026-11-05-seed-control-variate.md), #364 |
| 2 | Step regret: the first decision that loses a won puzzle | ReplayBridge **adopted** (#365); labeller **running** | [step-regret-feasibility](2026-11-05-step-regret-feasibility.md) |
| 3 | Twin cards: every card renamed, recall vs reading | **draft PR** #371; engine check **running** | [twin-cards-feasibility](2026-11-05-twin-cards-feasibility.md) |
| 4 | IRT calibration from a free population | **negative** on today's data | [irt-pilot](2026-11-05-irt-pilot.md) |
| 5 | Self-forecast calibration: P(win) probes, Brier/AUROC | free floors done; paid probe (about $0.13) **parked** (HTTP 402, issue #369) | [forecast-floors](2026-11-06-forecast-floors.md) |
| 6 | A playbook the model curates across episodes | open | |
| 7 | Budget-visible lookahead and its compute curve | open | |
| 8 | Robustness across Forge AI profiles, best of three | open | |
| 9 | MCTS with an LLM prior as a measured arm | open | |
| 10 | Exportable RLVR environment with engine process rewards | open | |
| E1-E8 | Survey ideas (error notebook, compute-matched lookahead, world-model fidelity, contrast sets, ...) | E5 **adopted** (#359); rest open | [survey](2026-11-05-agent-compute-and-feedback-survey.md) |

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
