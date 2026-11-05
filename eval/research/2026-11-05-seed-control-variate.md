# A free seed covariate for STR-AI: the premise, measured for free (2026-11-05)

**Question.** STR-AI is a win rate over paid games against Forge's AI. A control variate (AIVAT, Burch et al.
2018, arXiv:1612.06915; Brief B section 6 in `docs/eval/EVAL-DESIGN.md`) shrinks its interval with a covariate that
shares the game's luck and whose mean is known. The candidate here: the outcome of a free game on the same seed with
Forge's AI in the seat. If the model's result on a seed tracks that game's result, the paid sample needed for one
interval falls by the factor 1 - rho^2.

**Answer so far: the covariate is valid and free, but its value is not determined and the prior is against it.**
- The AI-in-seat game is a fixed function of the seed: 10 of 10 seeds replayed with the same winner and turn count
  (`runs/cv/ai-vs-ai` against `runs/cv/ai-vs-ai-rep`, seeds 1000 to 1009).
- A random seat is no covariate: it lost all 40 seeds to Forge's AI (`runs/cv/random-vs-ai`), so it has no variance
  to share.
- Its value needs the model's own games on the same seeds, which this note does not have. The one related number
  in the repository is against it: within a seat-swap pair the measured correlation was about 0.02 (n = 22 pairs,
  `docs/eval/EVAL-DESIGN.md`, "Honest pairing math"). If seed luck explains that little, an AI game on the seed will
  explain little too.

## What was run (free, zero-model)

Build: harness-epoch-82, overlay `0582191d06fb`, burn mirror, no seat swap, `--cap-minutes 30`. Run directories live
in the worktree `cv-wt` under `runs/cv/` (git-ignored, like every run).

| Run | Command | Result |
|---|---|---|
| `ai-vs-ai` | `run_batch.py --seeds 1000..1039 --exploratory --jobs 4 --deck eval/decks/burn.txt --controller-a ai --controller-b ai --budget-usd 0.01` | A 24, B 15, draw 1; median 13 turns |
| `random-vs-ai` | the same with `--controller-a random` | A 0, B 40; median 9 turns |
| `ai-vs-ai-rep` | `ai-vs-ai` again on seeds 1000..1009 | 10 of 10 identical to `ai-vs-ai` (winner and turns) |

`runs/cv/tools/seed_corr.py RUN_X RUN_Y` pairs two runs by `(seed, swap)` and prints both win rates, phi with a
bootstrap interval and 1 - rho^2. On `ai-vs-ai` against `random-vs-ai` it has nothing to compute: Y is constant.

## How to settle it at no extra cost

Every paid STR-AI block already plays fixed seeds. Before or after it, play the same seeds with `--controller-a ai`
(free, minutes) and run `seed_corr.py` on the two directories. One block of 20 seeds gives rho to about +-0.4; a
second block narrows it. Adopt the estimator only if the interval on 1 - rho^2 sits clearly below 1, and then report
the plain win rate beside the adjusted one, each with its interval (a covariate never replaces the raw number).

## What was not determined

- rho between the model's games and the AI game on the same seed (needs paid games, or the claude-cli development
  lane, never a score).
- Whether a mirror differs from the non-mirror matchup the 0.02 came from.
- Seat A won 24 of 39 decided burn-mirror games here (0.62); with 39 games the Wilson interval is [0.46, 0.75], so a
  first-seat edge is not shown.
