# Forge AI profiles: can a free run measure their spread? (2026-11-06)

Idea 8 of [novel-ideas](2026-11-05-novel-ideas.md): does STR-AI against one Forge AI profile equal STR-AI against
another? The free method is [seed-control-variate](2026-11-05-seed-control-variate.md)'s (`--controller-a ai
--controller-b ai`, a game is a fixed function of its seed). Read-only code and file reading; no JVM, no game, no
model call.

## Question

Do Forge's AI profiles (`Cautious`, `Default`, `Experimental`, `Reckless` under
`/root/forge/forge-gui/res/ai/*.ai`) change outcomes enough that the win rate against one is not the win rate
against another? Planned measure: Forge AI (default) in seat A against each profile in seat B on one deck mirror and
one non-mirror pair, 40 seeds each (1100-1139; outside every reserved block in `eval/seed_policy.py`), plus the
random seat against each profile as a weak-pilot floor.

## Answer so far

**Not measured: the harness cannot select a profile without a code change, so no games were run.** The question stops
at a design (below). Two findings from the read, both bearing on STR-AI:

- **No switch exists.** `run_batch.py` has no profile option and no env var or JVM property reaches one. The seat
  factory builds the Forge AI as `new LobbyPlayerAi(name, null)` (`LlmSpikeGui.seatFor`, also
  `LlmSpikePuzzle`), whose `aiProfile` is the empty string. Forge selects a profile only in
  `GamePlayerUtil.createAiPlayer` (GUI preference `UI_CURRENT_AI_PROFILE`, or the `SimulateMatch -a` flag), which the
  harness does not call.
- **The AI the benchmark plays is not any shipped profile.** With an empty profile name
  `AiProfileUtil.getAIProp` finds nothing and returns the compiled default of each `AiProps` key. Those differ from
  `Default.ai` on 25 of its 121 keys (e.g. `MULLIGAN_THRESHOLD` 5 vs 4, `CHANCE_TO_ATTACK_INTO_TRADE` 40 vs 0,
  `USE_BERSERK_AGGRESSIVELY` false vs true); from `Cautious.ai` on 43, `Experimental.ai` 51, `Reckless.ai` 58
  (key-by-key parse of `AiProps.java` against each file; the parse covers the 122 enum keys, and a scripted
  comparison, not a game, is the evidence). So "the Forge AI" in STR-AI is a fifth, unnamed configuration, and even
  the default profile would be a different opponent. `AiProps` (not `Default.ai`) is the one the panels in
  `eval/suites/panels-v1.json` were scored against.

Floor: with no games there is no rate and no floor to quote. The profiles do differ on the aggression, trade, mulligan
and combat-trick keys, so a spread is plausible; its size is undetermined.

## What was run

```
cd /home/user/mtg-forge-llm-agent && git fetch -q origin main && git worktree add /home/user/prof-wt \
  -b research/ai-profiles origin/main            # head 8a2b943
grep -rn "AI_PROFILE\|aiProfile\|AiProfile" eval/run_batch.py forge-overlay   # no hit in either
grep -rn "AiProfile" /root/forge/forge-ai /root/forge/forge-gui/src/main      # LobbyPlayerAi, GamePlayerUtil
```

Forge checkout `/root/forge` at `34a8662b79` (it carries uncommitted changes to three files; none touches profiles).
No run directory exists under `runs/profiles/`.

## Design to make it measurable (needs an overlay change; not done here)

1. In `LlmSpikeGui.seatFor`, read `AI_PROFILE_A` / `AI_PROFILE_B` for an `ai` seat and call
   `LobbyPlayerAi.setAiProfile`; refuse a name not in `AiProfileUtil.getProfilesDisplayList()`. Check that
   `FModel` has run `loadAllProfiles` in the headless path, or an unknown profile silently falls back to compiled
   defaults again.
2. Add `--ai-profile-a/--ai-profile-b` to `run_batch.py` as condition keys, stamp the profile on the game line, and
   treat an empty value as the current compiled-default arm (kept as the baseline so old results stay comparable).
3. Update ARCHITECTURE and the version stamp, and record an acceptance run (CLAUDE.md "Keep the documents true").
4. Then run `run_batch.py --controller-a ai --controller-b ai --seeds 40 --exploratory --budget-usd 0.01 --jobs 3`
   per (deck pair, B profile), seeds 1100-1139, plus `--controller-a random`; analyse with
   `eval/analyze_ab_stage0.py ab`, paired by seed. Quote the A win rate with its Wilson 95% interval and median
   turns, and always beside the mirror's 50% and the random-seat floor.

## Power (arithmetic, not measured)

To resolve a 10-point difference between two profiles near 50% (two-sided 5%, 80% power), unpaired needs about
2 * (1.96 + 0.84)^2 * 0.25 / 0.10^2, which is 392 games per profile. 40 seeds resolve only a gap of about 31 points
(1.96 * sqrt(2 * 0.25 / 40) * 1.43 ~ 0.31). Pairing by seed helps only through the correlation rho of the two
profiles' outcomes on the same seed; the variance falls by the factor (1 - rho), so rho = 0.3 gives about 275 per
profile. rho is not known (the same open question as in the seed note). A 40-seed block is a screen for a large
spread, not a measurement of a small one.

## Not determined

- Any win rate, median turn count or difference between profiles, mirror or non-mirror, and the random-seat floor.
- Whether `FModel.loadAllProfiles` has run in the headless launch (needed for design step 1).
- How far the compiled-default AI is from `Default.ai` in play, not only in keys.
- The seed-pair correlation rho between profiles, which sets the real sample size.
