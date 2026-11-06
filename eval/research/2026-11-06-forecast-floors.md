# Self-forecast floors: what a model's P(I win) must beat (2026-11-06)

Idea 5 of [novel-ideas](2026-11-05-novel-ideas.md): before paying for a probe that asks the seat for P(I win) at the
start of each of its turns, measure the floors such a forecast must beat, on games already on disk. Free and
read-only; no JVM, no model call. The analysis scripts (stdlib: a log-to-state extractor, a check of that state
against the seat's own prompt, a logistic fit with game-level cross-validation and bootstrap) were not kept in the
repository; the run directories are git-ignored and kept in the author's worktrees.

## Bottom line

- **Life difference alone is the floor.** Every other public feature adds almost nothing.
- **On Forge's AI against itself** (burn mirror, held out by game), the floor reaches Brier 0.158 against 0.250 for
  the base rate, with AUROC 0.85.
- **On the Luna seat's turns**, the floor is Brier about 0.21-0.22 (skill +0.10 to +0.14) and AUROC 0.73-0.77.
- **Early turns carry no signal:** on turns 1-3 nothing beats the base rate.
- **A forecast probe resolves only large differences:** the 54 usable Luna games cost about $0.13 to probe offline
  from their prompts and detect a Brier difference of about 0.04 to 0.07. A difference of 0.02 needs 200 to 600
  games.

## Data

Forecaster: the player whose turn starts. "Usable" means finished, decisive, not a replay, and no `(bridge-down)`
fallback row.

| Condition | Run dirs | Usable games | Turn states | Decks |
|---|---|---|---|---|
| AI vs AI | `runs/cv/ai-vs-ai` | 39 (one draw, seed 1012, out) | 535 | burn mirror only |
| Luna seat vs AI | `runs/loop/g01-g33`, `runs/luna-*`, `runs/c3`, `runs/prompt-ab`, `runs/frontier/luna`, `runs/luna-answer/g1` (the re-tier worktree) | 54 (the seat won 32) | 434 from logs; 358 from prompts (44 games) | 42 pairings |
| left out | `runs/cv/ai-vs-ai-rep` (identical replays), `runs/cv/luna-vs-ai` (18 of 20 games lost decisions to a dead bridge, #369), `runs/cv/random-vs-ai` (random won 0 of 40) | | | |

The game log records neither draws nor noncreature permanents, so the board is rebuilt from events. Measured
against the seat's own prompt state:

| Field | Burn mirror, exact | Mixed decks, exact |
|---|---|---|
| life | 100% | 100% |
| creatures | 100% | 76-79% |
| lands | 80-100% | 76-77% |
| hand | 72% | 39-40% (undercount about 1.3; draw spells are not logged) |

## Floors

Each cell gives Brier [95% CI], skill against the base rate, AUROC and ECE (10 bins). Splits are 5-fold by game,
repeated 10 times. Intervals are a 2000-replicate game-level bootstrap.

| Eval set | Model | All turns | Turns 1-3 | Turns 7+ |
|---|---|---|---|---|
| AI vs AI held out | all 13 features | .158 [.107,.213] / +.37 / .85 / .06 | .192 / +.23 / .76 | .144 / +.42 / .89 |
| AI vs AI held out | life difference | .160 [.121,.207] / +.36 / .84 / .06 | .218 / +.13 / .65 | .129 / +.48 / .89 |
| Luna, fit on AI vs AI | all features | .220 [.166,.276] / +.10 [-.17,+.35] / .77 / .15 | .249 / -.04 / .61 | .200 / +.18 / .84 |
| Luna, fit on AI vs AI | life difference | .220 [.173,.274] / +.10 / .73 / .11 | .258 / -.07 / .51 | .195 / +.20 / .80 |
| Luna, fit on Luna | life difference | .210 [.171,.254] / +.14 [.00,+.27] / .69 / .06 | .253 / -.05 / n/a | .182 / +.25 / .78 |
| Luna, fit on Luna | all features | .259 / -.06 / .62 (overfits) | | |

Base rate Brier: 0.250 (AI vs AI), 0.245 (Luna). The floor fitted on AI vs AI ranks Luna states well but is
miscalibrated there (ECE 0.15), because a life swing means more in the burn mirror.

Raw life-difference AUROC with no fitting:

| Games | Turns 1-3 | Turns 4-6 | Turns 7+ |
|---|---|---|---|
| Luna | 0.51 | 0.61 | 0.80 |
| AI vs AI | 0.72 | 0.81 | 0.90 |

The in-domain early-turn AUROCs after cross-validation (0.08 to 0.31) are an artefact. About 80% of turn 1-3 states
have a life difference of 0, so the ranking reduces to each fold's intercept.

**Features.** Held-out AI-vs-AI Brier is 0.158 with every feature:

| Feature set | Brier |
|---|---|
| life difference only | 0.160 |
| without life | 0.221 |
| board only | 0.210 |
| without hand | 0.155 |
| without lands | 0.148 |

## Power for the paid probe

The test compares a model forecast with the floor on the same states, about 8 probed turns a game.
- The per-game SD of the floor's Brier is 0.180.
- Two different floors on the same states correlate per game at rho 0.84.
- Games needed for a two-sided 5% test at 80% power: n = 7.85 x 2 sd^2 (1 - rho) / delta^2.

| rho | delta 0.02 | delta 0.05 |
|---|---|---|
| 0.90 | 127 | 21 |
| 0.84 | 207 | 33 |
| 0.50 | 633 | 102 |
| 0.00 | 1266 | 203 |

## Not determined

- **Only one training deck.** No AI-vs-AI data with several decks exists on disk, so the fitted floor is a burn
  model applied out of domain. A free multi-deck AI-vs-AI block would fix this.
- **No engine-rollout reference.** It needs a Forge JVM and was not computed.
- **No Murphy split of Brier into calibration and refinement.**
- **rho between a model forecast and the floor is unknown** until a probe runs; the power table brackets it.
- **Hand and board features are noisy outside burn.** Their small weight may partly be measurement error.
