# Full harness completion gates

The active objective remains: make the harness fully usable and faithful, with
Claude implementing and Codex orchestrating/verifying. A completed milestone does
not complete this goal. No gate below is satisfied merely by a source file, a
manifest, a test count or the absence of errors.

| Gate | Required authoritative evidence | Current status |
| --- | --- | --- |
| G1 Player observation | Live serializer covers current priority/resources/visible zones/stack/combat/attachments/effects and marks missing information; paired-world tests prove hidden-state noninterference | `v4-faithful` renderer + paired-world/face-down/reveal tests (SeatObservationTest); narration redaction repaired to be object-anchored (OBSERVATION-REVIEW.md, 2026-10-12); structured schema missing; not independently verified |
| G2 Object references | Live menu/object references agree; duplicate copies, zone re-entry, control change, tokens, face-down objects and hidden shuffle tests | Handles, duplicates, zone re-entry (public link / hidden break), control change, INSPECT tested; tokens untested; not independently verified |
| G3 Evidence history | All relevant publicly observed events and seat-private reveals captured without source truncation; proposals/outcomes separated; exact retrieval and loss indicators verified | Seat stream captures the log once with redaction and records reveals through both controller hooks; redaction is now object-anchored (no name-dictionary erasure) and `CAST`/`LANDS` history comes from the seat stream (2026-10-12, OBSERVATION-REVIEW.md); proposals/outcomes not separated; notifyOfValue uncaptured |
| G4 Durable context | Bounded context independent of game length; exact archive survives restart; game/seat isolation and stale-knowledge invalidation tests | In-memory tools only; incomplete |
| G5 Strategic context | Editable agent-authored hypotheses/plans with source references, contradiction handling and clear uncertainty; optional archetype knowledge disclosed/versioned | Basic notes only; incomplete |
| G6 Tool protocol | Consistent real backend support; typed requests, bounded per-decision/game budgets across retries; exact audit and complete usage/latency attribution | Text tools incl. INSPECT under one budget; eval accounting implemented by B (EVAL-IMPLEMENTATION.md); loopback round trip proven by the acceptance suite |
| G7 Action ownership | Every controller hook classified; every strategic choice model-owned or disclosed deterministic fallback; engine-mechanics boundary verified; legal menu completeness and invalid-choice handling tested | Ledger + reflection test (controller-ownership.tsv, OWNERSHIP.md): **4** silent core AI strategic/payment decisions remain (down from 13; 2026-10-12 moved chooseStartingPlayer/chooseColor/chooseColorAllowColorless/chooseSomeType/chooseProtectionType/chooseCardsToDiscardToMaximumHandSize/chooseCardsToDelve/payCostToPreventEffect/chooseOptionalCosts/orderSimultaneousSa to the model); mana payment and trigger targeting/playing incomplete |
| G8 Safe simulation | Agent-facing rollout uses only information set and disclosed samples/policy; hidden-world invariance; no live RNG/state mutation; unsupported decisions explicit | Offline fork exists (`LlmSpikeFork.runRollout` + `determinizeHiddenZones`); player-safe tool not built — design + work order in `LOOKAHEAD-DESIGN.md` (the model looks ahead by calling the engine, from its information set only) |
| G9 Evaluation integrity | Experiment isolation, pairing, resume identity, seed governance, observable failures, baseline/CIs and accurate tool accounting verified end to end | Implemented by B (EVAL-IMPLEMENTATION.md; 95 Python tests); not independently verified |
| G10 Operational usability | Documented clean install/build/launch, versioned harness config, runnable fake-model and subscribed-Claude example, real tool round trip, timeout/recovery behavior; reproducible acceptance suite | `eval/harness_acceptance.sh` (ACCEPTANCE.md): 8 stages green on 2026-09-07 incl. fake-model round trip, timeout recovery and faithful-profile game; clean install/build still via install.sh + Maven |

## Active implementation assignments

- A: `CLAUDE-WORK-ORDER.md`, Claude Fable 5.1. Java observation/identity/privacy.
  Ran out of session budget on 2026-09-06 mid-wiring; completed 2026-09-07 by the
  interactive session (see PLAYER-HARNESS.md "Implemented 2026-09-07").
- B: `CLAUDE-EVAL-WORK-ORDER.md`, Claude Fable 5.1. Python eval grouping/accounting.
  Reported complete 2026-09-06 (EVAL-IMPLEMENTATION.md).
- Codex: scope, independent source inspection, adversarial verification, integration,
  regression gates and next work orders. No acceptance by assertion alone.

Claude sessions are tracked locally in `runs/harness-development/`. Session metadata
is only a locator: process/tool handles and live output must be checked before
calling work active, finished or failed. Do not restart a job because a poll timed out.

## Independent ownership inventory (initial state)

`controller-inventory.tsv` was generated by reflection from the compiled harness
and pinned engine. Among 130 public overridable PlayerController hooks, 27 resolve
to PlayerControllerLLM, 89 to PlayerControllerAi and 14 to PlayerController. This is
an inventory, **not** a count of strategic decisions: inherited wrappers, UI methods
and mechanics require classification, and overrides may still delegate internally.

Confirmed AI-strategic examples from PlayerControllerAi source: chooseNumber's
Map/list overloads, chooseBinary overloads, chooseColors, delve, convoke/improvise,
card-cost choices. Existing LLM overrides also delegate announcement of X and
combat ordering. These must be handled by the ownership milestone after A's Java
changes land; avoid concurrent editing of the same controller.

The existing defect analyzer remains an invariant gate, but its `ai=0` output does
not cover uninstrumented inherited calls and cannot alone prove G7.

## Ownership change in workflow (2026-09-07)

User requested Codex take over implementation directly. Claude C/D/E all terminated at the subscription session limit; no further Claude sessions will be launched. C/D left no Java repairs. E left the strict identity partition/gate implementation, now independently verified by 107 passing Python tests and a complementary-unknown identity reproducer. Codex implemented numeric/binary controller routing and is validating it directly. Durable-context work order is a queued specification for local implementation, not an active Claude assignment.
