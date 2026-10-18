# MTG-Bench v1: the benchmark plan (owner-approved 2026-10-18)

This is the plan the code will be written to. No code for it exists yet beyond what `PUZZLES.md`,
`SCORING.md` and `panel_score.py` already do. The research behind it is in
[research/2026-10-18-benchmark-research.md](research/2026-10-18-benchmark-research.md) (Anthropic,
OpenAI, Prime Intellect, Mechanize, xAI, Hillclimb, Epoch, METR, game benchmarks, prior MTG work) and
the 2026-10-15 briefs that [BENCHMARK-RESEARCH.md](BENCHMARK-RESEARCH.md) summarises.

## Owner decisions (2026-10-18)

| # | Decision | Answer |
|---|---|---|
| 1 | What is the headline | **Puzzles.** Games against the Forge AI are the validity check, reported beside the headline and never folded into it |
| 2 | Which tool condition is the headline | **C1 Reference** (below) |
| 3 | Human timing (a METR-style "minutes of a strong player" horizon) | **Skipped for now.** Speed is measured as cost and effort per solve |
| 4 | Models to compare | Open. Luna only until the owner names others (`CLAUDE.md`, owner rules). One model cannot show the benchmark separates strong from weak play |
| 5 | Publishing Possibility Storm puzzles | Internal use only; ask Possibility Storm before releasing any (no licence is stated) |

## Why puzzles lead

| | Puzzle | Game |
|---|---|---|
| Cost, time (Luna) | about one turn, ~10 decisions, ~$0.01, ~2 min | $0.13, 22+ min (g11) |
| Grader | goal met or not, decided by Forge's puzzle mode | mostly decided by the draw |
| Items to separate two models by 10 points | ~150 puzzles | ~390 games per arm (`EVAL-DESIGN.md` §6.4) |
| Says which skill | yes, by tag | no |

## The three pools

| Pool | Source | Size | Use |
|---|---|---|---|
| **Public** | Forge's built-in puzzles (375 files; 282 Possibility Storm) | ~100 | development, transparency, contamination baseline |
| **Perturbed** | public puzzles with numbers or cards changed so a memorised line fails, re-verified | one per public puzzle where possible | contamination: the public-minus-perturbed gap is reported |
| **Private (headline)** | positions with a win this turn, mined from free Forge-AI-vs-AI games on seeds 20000-29999 (`seed_policy.py`), verified by search | ~200 | the score. Never published; refreshed by mining more |

The private pool is also the answer to review row E6: **STR-HELDOUT becomes the private puzzle split**
instead of held-out decks, which an API model cannot be held out from.

## What a puzzle must pass before it is frozen

1. **A proven solution.** A reference line that the engine replays to the goal. A puzzle neither random
   play nor the Forge AI solves is no longer dropped for that: today's rule keeps only 26 of 368 and
   removes the hardest; v1 requires a proven line instead.
2. **Won against every opponent response.** Possibility Storm solutions must beat all blocks and
   activations. Forge's puzzle mode tests one line, the Forge AI's, so a model can be credited because the
   AI blocked badly. A solve counts only if the model's committed line wins against every opponent
   choice the engine offers (bounded search over the opponent's decisions), or the puzzle is restricted
   to positions where the opponent has no choice. This is the largest engineering item.
3. **Guess-proof.** The uniform random-legal arm solves it under 5% (FrontierMath's "guessproof").
4. **Shown faithfully.** The opening observation passes the same faithfulness checks as the harness audits.
5. **Tagged** with one or more skill families: combat math, stack and priority, sequencing, mana, combo,
   card selection, opponent interaction.
6. After the pilot, a puzzle every model fails on every attempt is audited as probably broken
   (Anthropic, "Demystifying evals": a 0% task is usually a broken task).

## Tool conditions (scored separately, never pooled)

| Condition | Seat's tools | Measures |
|---|---|---|
| **C0 Bare** | `answer`, `set_plan` | reading and reasoning alone |
| **C1 Reference (headline)** | C0 + `inspect`, `rules_lookup` | play with what a player at the table may look up |
| **C2 Full** | C1 + `simulate`, memory tools, with a fixed call budget | play with an engine search tool |

`simulate` lets a model try lines on the engine and pick the winner; uncapped, C2 measures search, not
Magic. **Tool leverage** (C2 minus C0, paired by puzzle) is reported as its own number. Each condition is
pinned in the suite file like `pilot_condition` in `panels-v1.json`; `rating.check_conditions` already
refuses mixed conditions.

## The score

- **Headline: solve rate** on the private split under C1. pass@1 averaged over k=4 attempts per puzzle
  (Avg@4), tag families weighted 1/sqrt(family size) (METR), with a 95% interval from a bootstrap over
  families, then puzzles, then attempts. Always printed beside the random, always-pass, first-option and
  Forge AI floors, and as a normalised score (random = 0, Forge AI = 1, uncapped).
- **Which ones: one rating scale for puzzles and models** (Lichess puzzle ratings; IRT). Puzzle difficulty
  starts from the Common/Uncommon/Rare/Mythic label and is refit from attempts. Gives each model a puzzle
  rating with an interval, a profile by tag, and flags puzzles that discriminate nothing.
- **Reliability: pass^4**, the share of puzzles solved on all four attempts (tau-bench).
- **Speed as cost and effort.** Each attempt has a fixed token and tool-call budget, and running out is a
  failure. Dollars, tokens and tool calls per solve are reported beside the score. Wall-clock time is not
  scored: infrastructure alone moved Terminal-Bench by 6 points (Anthropic, 2026-02), and our time is half
  provider waits.
- **Failures are attributed.** Each failed attempt is classed as model (shown everything, chose badly),
  harness (a `match_review.py` flag) or infrastructure (provider error, cap). Only model failures count
  against the model; harness and infrastructure failures make the run invalid, as `defect_analyzer.py`
  already does for games.
- **Games (validity check).** The mirror panel of `panels-v1.json` against the Forge AI, seats swapped on
  every seed, Bradley-Terry with a bootstrap. Reported to show that a higher puzzle score goes with more
  wins. Review row E4 (how unfinished games count) still needs an owner answer before the panel is played.

Every reported number carries: suite version and hash, engine commit, harness epoch, condition, model,
k, budget, and its interval. Best-of-N or consensus numbers are labelled as such and never headline.

## Phases

| Phase | Work | Cost |
|---|---|---|
| 1 | This spec | $0 |
| 2 | Puzzle verifier: proven lines, every-opponent-response check, tags, readmit hard puzzles | $0 |
| 3 | Miner: bounded search for win-this-turn positions in free Forge-AI-vs-AI games; perturbation generator | $0 |
| 4 | Pin C0, C1, C2 and their budgets in the suite file | $0 |
| 5 | Pilot: Luna, 30 public puzzles x 2 attempts x 3 conditions; read every failure the way the game loop reads games; fix grader and harness | ~$2 |
| 6 | Freeze v1; full run: ~300 puzzles x 4 attempts x 3 conditions | ~$30-50 per model |
| 7 | Games panel as the validity check, sized after the pilot | ~$50-100 |

Paid phases state their cost first and pass `--budget-usd` (`CLAUDE.md`, "Cost of paid runs").
