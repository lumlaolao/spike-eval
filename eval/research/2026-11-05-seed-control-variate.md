# A free seed covariate for STR-AI: the premise, measured for free (2026-11-05)

**Question.** STR-AI is a win rate over paid games against Forge's AI. A control variate shrinks such an estimate
with a covariate that shares each game's luck and whose mean is known. The candidate here is the outcome of a free
game on the same seed, in the same seat with the same deck, with Forge's AI as the pilot. This is the
common-random-numbers form (cf. AIVAT, Burch et al., AAAI 2018, arXiv:1612.06915, which instead builds zero-mean
corrections from value estimates; Brief B section 6 in `docs/eval/EVAL-DESIGN.md`). With the AI's win rate mu_X known,
the estimate `Y_bar - beta (X_bar - mu_X)` has 1 - rho^2 of the plain estimate's variance at the best beta.

**Answer so far: the covariate is valid and free; whether it helps is not determined.**
- The AI-in-seat game is a fixed function of the seed: 10 of 10 seeds replayed with the same winner and turn count
  (`runs/cv/ai-vs-ai` against `runs/cv/ai-vs-ai-rep`, seeds 1000 to 1009).
- A random seat is no covariate: it lost all 40 seeds to Forge's AI (`runs/cv/random-vs-ai`), so it has no variance
  to share.
- Its value is rho between the model's game and the AI's game on the same seed, not measured here ("What was not
  determined").

## What was run (free, zero-model)

Build: harness-epoch-82, overlay `0582191d06fb`, burn mirror, no seat swap, `--cap-minutes 30`. Run directories live
in the worktree `cv-wt` under `runs/cv/` (git-ignored, like every run).

| Run | Command | Result |
|---|---|---|
| `ai-vs-ai` | `run_batch.py --seeds 1000..1039 --exploratory --jobs 4 --deck eval/decks/burn.txt --controller-a ai --controller-b ai --budget-usd 0.01` | A 24, B 15, draw 1; median 12.5 turns |
| `random-vs-ai` | the same with `--controller-a random` | A 0, B 40; median 9 turns |
| `ai-vs-ai-rep` | `ai-vs-ai` again on seeds 1000..1009 | 10 of 10 identical to `ai-vs-ai` (winner and turns) |

`python3 eval/analyze_ab_stage0.py ab RUN_X RUN_Y` pairs two runs by `seed` and `swap` (identity-keyed, a game drawn
in either run left out) and prints the 2x2 table rho is computed from. On `ai-vs-ai` against `random-vs-ai` it
prints 39 shared decisive games, both win 0, both loss 15, a win for the AI seat only 24 and for the random seat
only 0: the random seat never wins, so rho is undefined.

## How to settle it

1. Play the paid block's seeds with `--controller-a ai` beside it (free, minutes) and pair the two runs with
   `analyze_ab_stage0.py ab`. It prints the 2x2 counts, not rho (its banner's deck names are hard-coded and do not
   apply): with a = both win, b = AI seat only, c = model only, d = both loss, rho = (ad - bc) /
   sqrt((a+b)(c+d)(a+c)(b+d)) and beta = rho sd_Y / sd_X.
2. mu_X is not those seeds' mean: estimating it from the same seeds removes the gain. Take it from a much larger
   free block from the same seed distribution (or the whole seed panel), also played with `--controller-a ai`.
3. Adopt the adjusted estimate only if the interval on 1 - rho^2 sits clearly below 1, and report the plain win rate
   beside it, each with its interval. At rho near 0 a block of 20 seeds gives rho to about +-0.45 (Fisher z), 40
   seeds about +-0.3. With 20 seeds an observed rho of 0.6 still bounds 1 - rho^2 only below about 0.95
   (Fisher z lower bound 0.21): a useful bound needs a larger observed rho or a second block.

## What was not determined

- rho between the model's games and the AI game on the same seed (needs paid games, or the claude-cli development
  lane, never a score).
- The one related number in the repository, a within-pair correlation of about 0.02 (n = 22 pairs, non-mirror,
  `docs/eval/EVAL-DESIGN.md`, "Honest pairing math"), is a different quantity: one identity's results in seat A and
  in seat B on the same deal, where a deal's luck can move the two games in opposite directions. It says nothing
  about two pilots in the same seat on the same deal.
- Seat A won 24 of 39 decided games (0.62, Wilson [0.46, 0.75]). Seat A is not the player on the play (a coin flip
  decides that: A went first in 19 of 40); the player on the play won 21 of 39 decided games (0.54, Wilson [0.39,
  0.68]). Neither shows an edge.
