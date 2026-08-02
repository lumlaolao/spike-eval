# VISION — what this benchmark is, who it serves, and what "done" looks like

*Written 2026-07-31, after the v1.0 eval landed and the first live model scores
existed. This is the durable statement of intent; current numbers live in the
artifacts it points at, not here.*

## What this is

**A general benchmark of Magic: The Gathering play skill for language models,
run inside the real Forge rules engine.** Models pilot full Legacy games — every
decision type, every priority window — with the engine validating every option,
so illegal moves are impossible by construction. The eval **is** the
deliverable. Training work, agents, and infrastructure exist to make the
benchmark more trustworthy or broader; anything else is out of scope.

## Why Magic, and why this way

MTG is one of the hardest decision domains a language model can be dropped
into: enormous branching factor, hidden information, long horizons where one
early decision decides the game twenty turns later, and an adversary. Unlike
synthetic agent benchmarks, the rules are not simplifiable and not in dispute —
a 30-year-old game with a complete, machine-enforced ruleset. And unlike
self-reported "agentic" evals, the engine is the referee: a model cannot score
by producing plausible text, only by winning games and finding correct plays.

What makes this benchmark different from a demo of LLMs playing a game:

1. **Every metric is engine-grounded.** Win rates come from full games the
   engine adjudicated. Puzzle answer keys come from thousands of engine
   rollouts per position, not from a human or a stronger model's opinion.
2. **Every score ships with its trivial floor.** The founding lesson (the "88%
   lesson", CLAUDE.md): a decision-match rate of 88.1% once looked impressive
   until measured against its 78.2% do-nothing baseline. A score without its
   floor is not a score. This rule is mechanical now — tooling refuses to emit
   unfloored numbers — because it was violated once and cost a week.
3. **Teacher-independence.** The puzzle axis is verified by rollout win
   probabilities, so it survives the built-in AI being weak. The benchmark
   measures play, not agreement with any particular player.
4. **Reproducibility as a hard constraint.** Frozen suites carry sha256 + the
   exact build command; seeds are governed (reserved eval bands mechanically
   blocked from training corpora); the engine version is pinned; runs are
   byte-deterministic under parallelism. A number that cannot be rebuilt is
   not published.

## Who it serves

- **Model developers and evaluators** who need a hard, un-gameable measure of
  long-horizon strategic decision-making — a complement to saturating
  benchmark suites, with a near-zero trivial floor and real headroom.
- **Researchers** studying planning, hidden information, and memorization:
  the STR-AI vs STR-HELDOUT gap is a built-in memorization estimate, and the
  full decision-level JSONL corpus (every prompt, menu, and choice) supports
  analysis far beyond the headline numbers.
- **The MTG/AI community**: games are recordable (in-JVM capture), replayable,
  and per-decision inspectable — a leaderboard someone can actually audit.

## The three headline axes

1. **STR-AI** — win rate vs Forge's heuristic AI on a fixed published seed
   set, across a 16-deck archetype pool, **reported per-matchup against that
   matchup's measured floor** (the floors range ~26x by deck; a pooled scalar
   alone is near-meaningless and is only quoted with a deck-clustered CI).
2. **STR-HELDOUT** — the same measurement on mechanically held-out seeds; the
   gap to STR-AI is the memorization estimate.
3. **PUZ** — the golden puzzle suite: engine-verified single-best-answer
   positions, per-puzzle solved/not-solved. Floors at or below chance by
   construction; rankings quoted only when a paired significance test
   certifies them.

Supporting axes (PLAN adherence, DQ/blunder rate) exist and are reported with
their own documented limits; they never headline until their measurement
holes are closed.

**A stated asymmetry, by design:** the built-in Forge AI evaluates choices by
simulating resulting game states (it "sees the board after the move"), while
the evaluated model receives one static snapshot and must plan in its head.
STR-AI therefore measures **search-free play against a searching baseline** —
a well-defined target, stated here so nobody mistakes it for an oversight.
A search-augmented variant (the model may request "what would the state look
like after option N?") is a legitimate FUTURE axis, reported separately,
never folded into the base score.

## What "done" looks like

**v1 (the current campaign):**
- A puzzle suite with **60+ discriminating positions** — enough that a 7-8
  model leaderboard separates under exact McNemar, not just numerically.
  (Suite difficulty, not size, is the bottleneck: most easy positions are
  solved by every frontier model.)
- **STR-AI quoted per-matchup with floors and clustered CIs**, thousands of
  games over the full pool, seat-bias-calibrated (the ai-vs-ai mirror), on a
  harness whose infra failure rate is measured and gated.
- **STR-HELDOUT run once, honestly** — the first quantified memorization
  estimate for this domain.
- A frozen, versioned release: suites + manifests + scorecard + the exact
  commands to rebuild everything, so an outside party can verify any number.

**Beyond v1 (only if v1 earns it):**
- Broader formats/decks; LLM-vs-LLM ladders once model-vs-fixed-opponent is
  solid; richer positional axes (blunder cost in win-probability terms) once
  rollout-based DQ measurement exists at scale.

**Done for good** would be: the standard citation for "how well do LLMs play
Magic", with a reproducible harness anyone can run for ~$10 of CPU and their
own model keys.

## What this is NOT

- **Not a stronger student.** We do not optimize models for the benchmark;
  training experiments are instruments for validating the eval.
- **Not a marketing leaderboard.** If the significance test says the ordering
  is noise, we publish scores and say the ordering is noise. The tooling
  refuses ranks it cannot certify — that refusal is a feature, not a bug.
- **Not a data-collection funnel.** Model API keys never leave the local
  machine; rented compute does model-free CPU work only; nothing egresses
  without an explicit, per-session human opt-in.

## Operating principles (how the work stays honest)

- The engine is the oracle; the analyzer and test suites are gates, never
  weakened to pass.
- Claims in docs are tested mechanically where they have drifted before.
- Infrastructure failure is measured and separated from scientific outcome —
  a broken harness is never allowed to publish a yield or a score.
- Cheap validation first, always: the ladder runs from free-and-seconds to
  paid-and-hours, in that order.
- Every session leaves the repo runnable by the next one: versioned pipelines,
  a Makefile as the single implementation, handoffs generated from artifacts.

*Pointers: `eval/EVAL-DESIGN.md` (full design), `eval/EVAL-V1.md` (v1.0
walkthrough), `CLAUDE.md` (operating manual + earned lessons),
`eval/suites/*.json` (frozen suites), `runs/puz/INDEX.jsonl` (run registry).*
