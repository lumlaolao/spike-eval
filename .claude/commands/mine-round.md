---
description: Run one mining round (preflight → gen → mine → exhaustive → rollout → health gate → register), refusing to spend compute on a broken harness
---

Run a puzzle-mining round end to end. The implementation is
`eval/pipelines/mine_round.sh` (invoked as `make mine ROUND=<n>`); this
command is the operating procedure around it, not a re-implementation.

Arguments: `$ARGUMENTS` — expect a round number; ask if missing.

1. **Preflight first, refuse on failure.** The script runs
   `eval/preflight.py` on the matchup table before generating anything and
   again on the mined candidates. If preflight fails, STOP and fix the cause
   (deck-name typo, degenerate classpath, engine-pin drift) — round 3 lost
   30/320 positions and ~3.5 hours to a case typo preflight now catches in
   one second.
2. **Check nothing else is running games** (`pgrep -f LlmSpike`), and never
   rebuild (`mvn compile` / `install.sh`) while games run.
3. Launch: `make mine ROUND=<n>` (background it; a round is hours). Seeds are
   derived inside the 20000-29999 mining band automatically.
4. **Health gate.** The rollout phase runs `verify_keys --strict`: if it
   exits 4, the round's yield is a measurement of a broken harness, not of
   mining strategy. Read `by_driver_reason` in rollout-summary.json, fix,
   re-run (the script resumes; completed matchups and rollouts are skipped).
5. On success, register the run in the PUZ registry
   (`python3 eval/puz_index.py record ...`) and report: candidates mined,
   positions attempted, verified, yield — ALWAYS net of infra failures, and
   always next to the previous round's numbers.

Sampling policy (measured, do not regress): RANDOM across all option counts
via `eval/pipelines/select_sample.py` — the 3-4-option-band targeting cut
usable-key yield ~5x. Mine for INFORMATIVENESS (0.05 < solve < 0.95), not
raw yield. See CLAUDE.md.
