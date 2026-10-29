# Scoring the benchmark once the harness is right (research, 2026-10-15)

The question: once the harness gets out of the model's way, how do we score play so that **a higher
number always means better play**, on puzzles, against the Forge AI, and model against model?

Three research briefs, written by separate agents with different sources, are in `eval/research/`:

| Brief | Sources |
|---|---|
| [anthropic-openai-eval-guidance](../../eval/research/2026-10-15-anthropic-openai-eval-guidance.md) | Anthropic's eval guidance (agent evals, success criteria, "Adding error bars to evals"), OpenAI's evals guidance, SWE-bench Verified, MLE-bench, PaperBench, GDPval |
| [hillclimb-mechanize-primeintellect](../../eval/research/2026-10-15-hillclimb-mechanize-primeintellect.md) | hill-climbing guides (Anthropic's `hillclimb` workflow via secondary write-ups, LangChain "Better Harness"), Mechanize on RL environments, Prime Intellect's Environments Hub and `verifiers`, game benchmarks (Game Arena, TextArena, chess LLM leaderboards, Lichess puzzles, MageBench) |
| [scoring-design](../../eval/research/2026-10-15-scoring-design.md) | applies the above to this repository's decks, seeds, `aggregate.py` and the comparison games; worked sample sizes and dollar costs |

Each brief marks what a source says and what is the author's inference. One caveat on sources: no
company called "Hillclimb" was found; the brief read it as the hill-climbing workflow and says so.
Two OpenAI pages returned 403 and were read through secondary coverage.

This page is the synthesis: where the briefs agree, what that means here, and what was already done.

## Where all three agree

1. **The headline grades are engine outcomes, nothing else.** A win, a loss, a puzzle the engine
   says is solved. Process grades (agreement with a teacher, an LLM judge of decisions) are
   diagnostics. The 88% lesson is the local proof; PaperBench's process-only grade correlated
   with the full grade at r = 0.48.
2. **Every number is printed with its floor and its anchor.** For games: the floor seat (always
   passes), the random seat (a uniformly random legal option, the real "no skill" floor) and the
   Forge AI on the same deck. For puzzles: the random-option, always-PASS and first-option solve
   rates. A normalised score `(score − random) / (Forge AI − random)` makes 0 mean "no skill" and
   1 mean "as good as the Forge AI". It is **not capped at 1**, because beating the anchor is the
   result we are after.
3. **A game that did not finish cannot vanish.** Today a draw or an unfinished game drops out of the
   win rate, so a losing model that stalls into the time cap erases its loss. That breaks "higher is
   better". Model-caused non-completion counts as a loss; a harness crash is re-run on the same
   seed and, if it crashes again, the seed is dropped for every model compared.
4. **Weight matchups equally, and cluster the error bars by matchup.** The claim is "plays Magic
   well", so decks and matchups are a sample. With matchups varying by about 15 points, no number of
   games narrows the pooled CI below about ±7 points at 16 matchups (±3.7 at 64). More matchups
   buy more than more games per matchup. Always print the worst and best matchup next to the pool.
5. **Pair everything on the same seeds.** Every model plays the same published seed list, so
   model-against-model differences are paired. Swapping seats cancels first-player bias. The
   measured correlation within a seat-swapped pair is +0.02, so seat swapping buys bias control,
   not fewer games.
6. **One rating scale for vs-AI and PvP: Bradley–Terry, fit in one batch, with fixed anchors.** The
   Forge AI is pinned (for example at 1500), with random and floor as sanity anchors. Online Elo
   depends on the order of games; Chatbot Arena moved to Bradley–Terry for this reason. Use
   bootstrap CIs over seed pairs, publish rank CIs, and check for intransitivity: Magic's
   rock-paper-scissors between archetypes may make a single number wrong.
7. **Puzzles: solved by the engine, attempted several times, rated jointly with models.**
   - Make 4 attempts per puzzle. Report pass@1 as the headline and pass^k (all k attempts succeed)
     for reliability.
   - Subtract each puzzle's random-rollout solve rate, and drop puzzles that random solves more
     than 20% of the time.
   - Rate puzzles and models with a Rasch / Glicko-style model, as Lichess rates its puzzles.
   - Include positions where the right answer is to hold; otherwise the suite only teaches action.
   - Count time-to-solve in calls and tokens, and only as a secondary number.
8. **Contamination is two problems.**
   - The held-out seed band stops our trained student from memorising seeds.
   - It does nothing about frontier models having read a published puzzle. The 996 rules Q&A items
     in `eval/puzzles/modern` are public web text and are almost certainly in pretraining, so they are
     a separate rules-knowledge score, never part of PUZ.
   - Mined puzzles come from seeds 20000–29999 with a private held-out split. Add canary strings to
     the open files.
9. **Freeze and version the whole condition.** One benchmark version id hashes the engine commit,
   overlay, decks, seed registry, puzzle suite, tool definitions, observation profile and anchors.
   Never pool across versions (the repository already refuses mixed experiments). Before freezing,
   have humans audit every puzzle no model solves: OpenAI found 59% of the hard unsolved tasks in
   SWE-bench Verified were flawed.
10. **The way to game this benchmark is the harness, not the rules.** The engine makes illegal moves
    impossible, so what is left to exploit is the fallback paths: what happens on an empty,
    malformed or timed-out reply. The test is an **always-empty-reply arm** and an
    **always-malformed-reply arm**, run through the games and the puzzles. Neither may score above
    the floor. This is the same principle as Prime Intellect's "garbage answers must score zero".

## Sample sizes and cost (80% power, α = 0.05)

| Question | Games |
|---|---|
| Does one model beat the Forge AI in a mirror (one-sample against 0.5), Δ = 10 points | about 194 |
| the same, Δ = 5 points | about 783 |
| Model A vs model B, each playing its own games, Δ = 5 points near 0.5 | about 1,565 per arm (fewer when paired on seeds; about 630–940 at a correlation of 0.4–0.6) |
| Pooled STR-AI to ±5 points | about 400 (25 per matchup) |
| Every matchup to ±10 points | about 1,600 |

`EVAL-DESIGN.md` §6.4's "near-parity mirrors, ~390" row compares two versions (two arms, both near
0.5), which is right for that question. Testing one model against the Forge AI in a mirror is the
one-sample row above (194). The row should say which it means.

Costs come from the scoring brief's worked numbers. deepseek-v4.1-flash costs $0.10–0.16 a game; a
frontier model costs about $5 a game.

| | deepseek-v4.1-flash | frontier model |
|---|---|---|
| a full scorecard for one model | about $250–400 | about $3,200 |
| pooled STR-AI to ±5 points | about $40–64 | about $2,000 |

Puzzles are about 100 times cheaper per bit of information than games: about $2.40 per 1,200
attempts on deepseek and about $100 on a frontier model. **That argues for building the mined,
engine-verified puzzle suite first.**

## LLM judges (the blunder judge)

Use an LLM judge for decisions only as an audit that finds candidates to read, never as a score.
If it is ever scored, calibrate it first:

- blind labels from strong MTG players, with agreement between the humans as the ceiling (GDPval:
  humans agreed 71% of the time, the automated grader 66%);
- the judge's majority-class baseline printed beside its accuracy;
- options shown in random order;
- a judge from a different model family than the player;
- an "unknown" answer allowed.

`eval/blunder_judge.py` follows this. It prints a sheet to read, and its verdicts are not reported
as play strength.

## What this changes in the repository

Done on 2026-10-15:

- `aggregate.py` prints a **strict score** next to the decisive-only win rate: wins plus half the
  draws, over every started game, with an unfinished game scoring 0. A stall can no longer erase a
  loss in that number.
- `aggregate.py` reports and gates the **harness-decided share**: decisions the harness made when
  the model could have (match_review's TAKEN and LOST, and any Forge-AI row), with forced picks
  excluded. The ceiling is 2% and exit code 4. The existing fallback rate counts forced picks and
  so hides this number. The comparison games show 0/490 and 0/409.
- `eval/blunder_judge.py`: the per-decision audit described above. It is given the Oracle text of
  every card named, after a judge working from memory called correct plays blunders.
- **The gaming check (point 10) exists and found three ways the harness played for a seat that did
  not answer:** a land drop, an all-in attack, and Forge's AI choosing targets. All three are now the
  do-nothing option (pass, no attack, decline or first legal target). Acceptance stage `gaming`
  plays an empty-reply and a garbage-reply seat against the Forge AI next to the floor seat on the
  same seed, and fails if either does better or the harness acts for them.

Built (the scoring machinery; how to use it is [SCORING.md](SCORING.md)):

- **The two STR panels** (`suites/panels-v1.json`): the 16-cell mirror panel and the cross panel
  (Latin square *d* vs *d+5*, each cell with its AI-vs-AI control), with published seeds
  7800-7849 (control 7800-7999) from the stage3-holdout bank. `run_batch.py --panel` launches one
  under a single required `--budget-usd`; `panel_score.py` scores it: per-cell win rates, the
  equal-weight pool with a stratified and a bootstrap CI, worst and best cell, the cross margin over
  the control, and STR_norm (uncapped).
- **Sanity arms beside every scored block** (`run_batch.py --sanity-arms`, `sanity_arms.py`): floor,
  random and the empty- and garbage-reply fake model on the same seeds, checked to never beat the
  floor (proposal 2 below).
- **Bradley-Terry rating** (`rating.py`): Forge AI pinned at 1500, a first-player term, a weak prior
  so floor and random stay finite, cluster-bootstrap rating and rank CIs, the floor ≤ random < Forge
  AI check in every replicate, goodness of fit and 3-cycles; it refuses to mix conditions.
- **Benchmark version id** (`benchmark_version.py`, proposal 7 without the puzzle audit): stamped
  into panel and rating reports; reports from different versions refuse to pool.

Proposed, in order:

1. Make the strict score the headline (and decide how a cap hit is charged: to the seat whose
   model time dominated). That changes how existing numbers read, so it is your decision, not a
   silent change.
2. Run the empty-reply and garbage-reply arms beside every **scored block** too, not only in
   acceptance (one seed there; many seeds and decks in a block).
3. Build the **mined, engine-verified puzzle suite**, with:
   - random, PASS and first-option baselines per puzzle;
   - pass@1 and pass^4;
   - difficulty tiers by whether the Forge AI solves it and how many decisions it takes;
   - a private held-out split.
4. **Two STR panels.** The mirror panel is the headline: 16 decks, each piloted by the model
   against the Forge AI on the same deck. The cross panel is a Latin square of matchups, each
   scored as a margin over an AI-vs-AI control on the same seeds, which costs only CPU.
5. **PvP as Bradley–Terry** with the Forge AI pinned. Every model plays the STR-AI block (which
   links it to the anchor), then PvP against its nearest neighbours by rating.
6. A **composite** only as a derived column: each axis normalised so random = 0 and Forge AI = 1,
   combined with fixed weights (or a geometric mean), always shown next to the three axis scores.
7. A **benchmark version id** hashing the full condition, and a human audit of unsolved puzzles
   before a version is frozen.
