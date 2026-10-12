# Independent continuation review — 2026-09-07

Current staged and unstaged work was inspected before continuing. Prior Claude A exited 1 and B exited 0; neither was assumed active from old metadata. Additional interactive work supplied faithful-profile wiring, ownership inventory and acceptance suite.

## Verified baseline

Command: `STAGES="preflight classpath compile javatests pytests" HARNESS_DEPS_FILE=/tmp/harness-validation-classpath.txt OUT=/tmp/harness-independent-baseline-20260907 bash eval/harness_acceptance.sh`.

Passed pinned engine preflight, JDK17 isolated compilation (54 source classes), 189/189 declared Java tests with no skips, 95 Python tests and standalone aggregate checks. This is the baseline compiled before C/D edits, not verification of subsequent implementations. The first canary/faketool/faithful invocation falsely failed the class-load check because macOS canonicalized /tmp to /private/tmp. Repaired the runner to canonicalize OUT and match literal paths with a directory boundary. A rerun is now using those same baseline classes; see summary.txt in that directory for actual outcomes. Note the runner truncates summary.txt on each invocation; first-stage results are recorded here and individual test reports remain in that directory.

## Actual open findings

- 18 inherited strategic/payment hooks remain silent Forge-AI decisions. Ledger and tests openly count them; ai=0 does not prove model ownership. Assigned C (CLAUDE-OWNERSHIP-WORK-ORDER.md).
- SeatObservation.hiddenFaceNames plus global string replacement depends on hidden card identity and may erase unrelated public same-name evidence. History rendering also bypasses this layer through legacy castHistory. Assigned D to reproduce and repair, including historical event-time visibility (CLAUDE-OBSERVATION-REVIEW-WORK-ORDER.md).
- Confirmed via synthetic calls to aggregate.group_experiments: two same-condition records with complementary unresolved model identity (smart=model-X/fast=unknown and smart=unknown/fast=model-Y) merge into one experiment labelled smart=model-X/fast=model-Y. Neither game observed that complete identity. Both records are flagged unresolved but become eligible for a common pair. Fair evaluation should keep unresolved identity separate and must not infer a shared model condition from missing evidence. Existing tests explicitly permit this wildcard behavior; changing them requires correcting the contract, not merely preserving green tests. Assigned E to fix this and verify pairing/gates (CLAUDE-EVAL-IDENTITY-WORK-ORDER.md).

## Implementation sessions

C: Claude Fable 5.1, session 80b5c964-0a90-4c5c-a598-243dcadd7729; launcher /tmp/launch-claude-ownership.py; output runs/harness-development/claude-C.jsonl. Owns controller routing/tests/ownership ledger and report.

D: Claude Fable 5.1, session 7d7414bd-31b6-4eab-931c-5ee3b6799491; launcher /tmp/launch-claude-observation-review.py; output runs/harness-development/claude-D.jsonl. Owns SeatObservation.java, SeatObservationTest.java and observation review report only.

Process metadata is a locator, never proof of liveness. Revalidate live handles before restarting. Both sessions exclude secrets, credentials, datasets, real game logs and personal files. No staging/committing/installing/paid game evaluation is authorized by these work orders. Preserve existing user-staged changes.

Full goal remains active: durable memory, strategic notebook lifecycle, complete event/outcome evidence, typed tools/retry accounting, safe information-set simulation and full usability gates remain unfinished. Passing baseline tests is not completion.

E is now dispatched through /tmp/launch-claude-eval-identity.py; output runs/harness-development/claude-E.jsonl, restricted to eval identity source/tests/report.

Acceptance runner repair also truncates its synthetic server request log before each stage so reusing OUT cannot supply stale evidence. A rerun of a script while its file was being edited produced a shell read-offset syntax failure after stages passed; bash -n on the finalized script passes. The final rerun started after all runner edits (handle 50967), with no further runner mutation.

Final baseline acceptance rerun exited 0: floor A/108 turns/1004 decisions; random B/23 turns/130 decisions; both analyzer counts zero. Faketool: 69 model decisions, 71 context calls, 2 successful tools, exactly 72 fresh requests, one injected stall and three retry rows. Faithful: 69 model decisions, 71 context calls, 2 successful tools, 136 menu-handle references. Evidence /private/tmp/harness-independent-baseline-20260907/summary.txt. These validate the pre-C/D/E build only.
