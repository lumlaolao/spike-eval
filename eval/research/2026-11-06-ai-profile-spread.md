# Forge AI profiles: can a free run measure their spread? (2026-11-06)

Idea 8 of [novel-ideas](2026-11-05-novel-ideas.md): does STR-AI against one Forge AI profile equal STR-AI against
another? The free method is [seed-control-variate](2026-11-05-seed-control-variate.md)'s (`--controller-a ai
--controller-b ai`, a game is a fixed function of its seed).

## Question

Do Forge's AI profiles (`Cautious`, `Default`, `Experimental`, `Reckless` under
`$FORGE_HOME/forge-gui/res/ai/*.ai`) change outcomes enough that the win rate against one is not the win rate
against another? Planned measure: Forge AI (default) in seat A against each profile in seat B on one deck mirror and
one non-mirror pair, 40 seeds each (1100-1139; outside every reserved block in `eval/seed_policy.py`), plus the
random seat against each profile as a floor for the A seat (expected near 0: the random seat won 0 of 40 games
against Forge's AI, `random-vs-ai` in the seed note).

## Answer so far

**Not measured: no games were run.** The switch now exists (below, "The switch"), but the experiment needs paid games
and has not been run. Owner decision for now: the default opponent stays the compiled-default AI, so every recorded
STR-AI score stays comparable; the profiles are a separate condition for this experiment. Two findings from the read,
both bearing on STR-AI:

- **(Before the switch) no switch existed.** `run_batch.py` had no profile option and no env var or JVM property reached one. The seat
  factory builds the Forge AI as `new LobbyPlayerAi(name, null)` (`LlmSpikeGui.seatFor`, also
  `LlmSpikePuzzle`), whose `aiProfile` is the empty string. Forge selects a profile only in
  `GamePlayerUtil.createAiPlayer` (GUI preference `UI_CURRENT_AI_PROFILE`, or the `SimulateMatch -a` flag), which the
  harness does not call.
- **The AI the benchmark plays is not any shipped profile.** With an empty profile name
  `AiProfileUtil.getAIProp` finds nothing and returns the compiled default of each `AiProps` key. Those differ from
  `Default.ai` on 25 of 121 keys (e.g. `MULLIGAN_THRESHOLD` 5 vs 4, `CHANCE_TO_ATTACK_INTO_TRADE` 40 vs 0,
  `USE_BERSERK_AGGRESSIVELY` false vs true); from `Cautious.ai` on 43, `Experimental.ai` 51, `Reckless.ai` 58
  (each count is over the 121 keys every profile file sets; `AiProps` has 122 and one has no profile value, so it never differs; a scripted comparison, no game). So "the Forge AI" in STR-AI is a
  fifth, unnamed configuration, and even the default profile would be a different opponent. Every game played
  through this harness before the switch, the panels in `eval/suites/panels-v1.json` included, met the compiled
  defaults: no profile was set anywhere in `forge-overlay`.

Floor: with no games there is no rate and no floor to quote. The profiles do differ on the aggression, trade, mulligan
and combat-trick keys, so a spread is plausible; its size is undetermined.

## What was run

```
# on research/ai-profiles from main 8a2b943
grep -rn "AI_PROFILE\|aiProfile\|AiProfile" eval/run_batch.py forge-overlay   # no hit in either
grep -rn "AiProfile" $FORGE_HOME/forge-ai $FORGE_HOME/forge-gui/src/main      # LobbyPlayerAi, GamePlayerUtil
```

Forge checkout `$FORGE_HOME` at `34a8662b79` (four modified source files, `ComputerUtilMana`,
`GameCopier`, `Player` and `MagicStack`; none touches profiles).
No run directory exists under `runs/profiles/`.

## The switch and its design (built; the experiment is not run)

`AI_PROFILE_A` / `AI_PROFILE_B` (`run_batch.py --ai-profile-a/-b`) seat a shipped profile on that identity's Forge AI
seat through `LlmSpikeGui.seatFor` and `LobbyPlayerAi.setAiProfile`. Empty, the default, builds `new LobbyPlayerAi(name,
null)` exactly as before (`AiProfileSwitchTest`). The JVM refuses a name not in the profiles `FModel.initialize` loaded
(an unknown name would silently play the compiled defaults), a Forge "Random" entry, and a profile on a seat that is not
`ai` / `ai-log`; `run_batch.py` refuses a profile on a panel or a cited run, and `panel_score.py` fails a cell that
carries one. The game line carries `ai_profile_a` / `ai_profile_b` (empty is the compiled defaults, as is a line from
before the stamp); `aggregate.py`, the batch manifest and `rating.check_conditions` never pool two profiles. The puzzle
runner strips the names, so PUZ meets the default. Remaining design step: the games.

Design of the experiment: run `run_batch.py --controller-a ai --controller-b ai --seeds 40 --exploratory --budget-usd
0.01 --jobs 3` per (deck pair, B profile), seeds 1100-1139, plus `--controller-a random`; analyse with
`eval/analyze_ab_stage0.py ab`, paired by seed. Quote the A win rate with its Wilson 95% interval and median turns,
and always beside the mirror's 50% and the random-seat floor.

## Power (arithmetic, not measured)

To resolve a 10-point difference between two profiles near 50% (two-sided 5%, 80% power), unpaired needs about
2 * (1.96 + 0.84)^2 * 0.25 / 0.10^2, which is 392 games per profile. 40 seeds resolve only a gap of about 31 points
(1.96 * sqrt(2 * 0.25 / 40) * 1.43 ~ 0.31). Pairing by seed helps only through the correlation rho of the two
profiles' outcomes on the same seed; the variance falls by the factor (1 - rho), so rho = 0.3 gives about 275 per
profile. rho is not known (the same open question as in the seed note). A 40-seed block is a screen for a large
spread, not a measurement of a small one.

## Not determined

- Any win rate, median turn count or difference between profiles, mirror or non-mirror, and the random-seat floor.
- How far the compiled-default AI is from `Default.ai` in play, not only in keys.
- The seed-pair correlation rho between profiles, which sets the real sample size.

Owner decision on which opponent STR-AI means: issue #381.
