---
description: Run the validation ladder (make test → jtest → canary) and report each rung with what would have made it fail
---

Run the repo's validation ladder, cheapest rung first, via the Makefile — the
single implementation. Do NOT re-implement any rung inline; if a rung's
definition looks wrong, fix the Makefile, not this file.

1. `make test` — every `eval/test_*.py` (~10s, no JVM)
2. `make jtest` — LlmSpikeForkTest + LlmSpikeTest (~1 min, no game; expect 98 tests)
3. `make canary` — seed-42 floor mirror; the target itself ASSERTS
   `outcome: A after 108 turns (AllOpponentsLost)`

Stop at the first failing rung and diagnose it before running the next.

Report format — one line per rung: pass/fail, plus **what would have made it
fail** (the repo's evidence rule: before trusting a green gate, say what
would have made it fail; if you can't, it isn't evidence). For the canary
specifically: it fails on any change to the floor/auto path that shifts the
pinned 108-turn outcome; if you changed such a path deliberately, re-pin the
number in BOTH the Makefile and CLAUDE.md, in the same commit.

Never weaken a test to pass it. `eval/defect_analyzer.py` and the test suite
are oracles, not obstacles.
