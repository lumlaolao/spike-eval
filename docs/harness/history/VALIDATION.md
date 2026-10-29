# Validation — 2026-09-06

Sources were compiled with `javac --release 17` into an isolated temporary output
directory, against the adjacent Forge checkout's existing dependency classpath.
The installed engine source/classes were not overwritten or rebuilt. Tests and
canaries ran on the available Homebrew JDK 26. A clean Maven/JDK-17 build remains
an additional release check; these results do not claim that check was run.

## Tests

- 89 recovered history/profile/fork/puzzle/HTTP-timeout tests passed.
- 71 controller tests passed with `MEMORY_TOOLS=1`, including the new real-controller
  retrieval/transcript test and the revised deterministic reveal-failure regression.
  The original 70-test controller suite also passed before these changes.
- 11 memory tests passed, covering retention after compaction, independent seats,
  evidence-cited hypotheses, exact pagination, invalid requests, stateless bridge
  continuation, tool limits across asks, failure retention, and mixed tool/action replies.
- 2 route-label tests passed: random labels must not claim model calls; LLM labels
  must retain the reconciliation marker.
- Python unittest discovery: 56 tests passed, including context provenance checks.
- `python3 eval/tests/test_aggregate.py`: all checks passed.
- `git diff --check`: passed.

HTTP tests initially could not bind a local socket inside the sandbox. They were
rerun with approved local-server access and all seven passed. No external model
calls were made during validation.

## Zero-model engine canaries

| Seed | Arms | Outcome | Decisions | AI-tier count | Silent-drop count |
| --- | --- | --- | --- | --- | --- |
| 42 | floor / floor | A wins, 108 turns | 1004 | 0 | 0 |
| 42 | random / random | B wins, 23 turns | 130 | 0 | 0 |

Both final canaries passed the unchanged `eval/defect_analyzer.py` hard gates.

The first random run exposed misleading `-> asking` logs, generating 58 apparent
missing-model-call reports. After correcting the router's messages, the repeat
produced identical decisions and game outcome. Comparing all decision fields
except measured latency passed; one row's latency differed by 1 ms. No analyzer
exception or filter was added to obtain a pass.

These checks validate the exercised mechanics. They do not certify hidden-state
noninterference, full strategic ownership, player-safe rollouts, complete billing,
or every recovered rollout/puzzle path. Those gates remain in PLAYER-HARNESS.md.

# Validation — 2026-09-07

Everything below is `bash eval/harness_acceptance.sh` output (evidence under
`runs/harness-acceptance/run5/`, not committed; run4 was identical before the verifier fixes). Sources compiled with `javac --release 17`
into an isolated directory against the pinned engine classes (`c1c64a53…`); the Forge
checkout was neither rebuilt nor edited (the classpath stage asserts its working tree is
unchanged). JDK 17.0.19 ran everything. No external model endpoint was contacted.

| Stage | Result |
| --- | --- |
| javatests | 189/189 declared `@Test` methods passed, 0 skipped (adds `SeatObservationTest` 9, `ControllerOwnershipTest` 3, `LoopbackKeyTest` 2, +1 each in ContextMemoryTest and ContextProfileTest) |
| pytests | 95 unittest tests OK; `test_aggregate.py` all checks passed |
| canary floor/floor seed 42 | A wins, 108 turns, 1004 decisions (pinned); `tier=ai` count 0; silent drops 0; class-load log proves the compiled build ran |
| canary random/random seed 42 | B wins, 23 turns, 130 decisions (pinned); same gates |
| faketool (legacy profile, MEMORY_TOOLS=1, loopback fake model) | 69 model-tier rows, 71 `context_call` rows, 2/2 successful tool rows (SEARCH, NOTE), 72 requests with no Authorization header, 1 injected 9 s stall surfaced as a request timeout with 3 retried decision rows, analyzer gates clean |
| faithful (CONTEXT_PROFILE=v4-faithful) | same game shape; every non-mulligan prompt carries the observation header and legend, 7137 seat handles and 136 handle-bearing menu options across prompts, zero legacy engine-id references, provenance stamped `v4-faithful/7896dbb44d9f`, analyzer gates clean |

Earlier the same day: run1 failed only its faketool stage because the runner deleted the
fake server's request log (fixed); run3 failed only because the mulligan ask has no board
to observe (now exempted explicitly and counted in the summary line).

These checks validate mechanics, visibility fixtures, and the tool/transport round trip.
They do not certify strategic ownership (18 silent Forge-AI decisions remain, see
OWNERSHIP.md), a structured observation schema, player-safe rollouts, or complete billing.

Pre-commit verifier (claude-obsidian verifier, staged diff): one BLOCKER, fixed the same
day — the OpenRouter bridge attached `OPENROUTER_API_KEY` to loopback requests whenever the
key was in the environment; the header decision is now a pure function that never returns
a key for a loopback endpoint (`LoopbackKeyTest`). One HIGH, fixed — the acceptance
classpath stage guarded the checkout with `git status` only, which cannot see the
gitignored `target/` trees; it now also fails on any file Maven writes under
`forge-*/target`. One MEDIUM recorded as an open item in PLAYER-HARNESS.md (menu labels
trust the engine's presented candidate set). One LOW fixed (zone suffix normalised).

## Direct implementation validation — 2026-09-07

After the user requested local implementation, all three remaining Claude sessions were confirmed terminal (subscription limit). Numeric and binary routing was implemented directly in the overlay, including full integer ranges and previously inherited overloads. New real-controller fixtures cover an interior value omitted by the old three-option range, full int boundaries, sparse options, invalid/overflow/mixed replies, and every binary kind through both overloads.

Fresh JDK17 isolated compilation and Java tests: 193/193 passed, zero skips. Ownership reflection: 16 remaining silent core AI hooks. Python suite: 107 passed, standalone aggregate checks passed; independent complementary-unknown model-identity fixture splits correctly. Pinned floor canary A/108 turns/1004 decisions and random canary B/23 turns/130 decisions both unchanged; analyzer ai=0, silent drops=0, class-load provenance verified. Evidence: /private/tmp/harness-direct-ownership-20260907. Initial sandboxed Java test JVM aborted before results; the same tests passed outside that restriction. No installed Forge source/classes were modified.

Remaining privacy/history, trigger/payment ownership, durable memory and player-safe simulation requirements remain open. These tests do not certify full harness fairness.

## Optional confirmations — direct implementation

Added model-owned optional trigger, payment and replacement confirmations; mandatory triggers auto-accept. General confirmAction exceptions now decline instead of calling Forge AI. Tests verify YES/NO decisions, malformed-response decline, mandatory non-query, and target identity/content preservation.

JDK17 isolated build: 196/196 Java tests pass; ownership ledger now counts 13 silent core hooks. Floor canary unchanged. Loopback memory/timeout and faithful-profile stages pass. The first random run deliberately changed from B/23 turns/130 decisions to A/24 turns/144 decisions: first stream divergence at decision index 61 is the newly observable confirm-trigger at turn 15 for Mardu Energy. Four optional trigger decisions now belong to the random policy instead of inherited Forge AI. Analyzer remains unchanged and reports ai=0/silent drops=0. A second run is being compared before updating the expected canary. Evidence /private/tmp/harness-direct-confirmations-20260907 and preserved first random run /private/tmp/harness-direct-confirmations-random-first.

The second random run reproduced all 144 decision records (type/seat/turn/phase/choice/legal menu/fallback), with the same A/24 outcome. Updated the random expectation alongside the routing change, with this causal explanation; tightened the ownership regression ceiling from 18 to 13. The analyzer and its thresholds were not changed. Rechecking the recorded canaries against the new expectations passes both arms.
