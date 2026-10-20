# Claude implementation work order — B: trustworthy harness evaluation

The user asks Claude to implement a fully usable MTG player harness while Codex
orchestrates and verifies. Another Claude is changing Java observation/privacy/
object handling. Your independent ownership is Python evaluation code and tests.
Do not modify Java or PLAYER-HARNESS.md; write a separate implementation report.

Read docs/harness/PLAYER-HARNESS.md for the overall contract and current memory
protocol. Inspect eval/aggregate.py, build_report.py, run_batch.py and relevant
source/tests. Current aggregation deduplicates by (seed,swap), so distinct models,
profiles, or memory configurations can silently replace one another. Provenance
warnings happen too late after deduplication. Tool call rows are supplementary to
legacy decision rows and currently disappear from summary accounting.

Implement, integrate, and test:
1. Robust experiment identity and grouping: identify runs using all behaviorally
   relevant recorded configuration/provenance, preserve distinct experiments for
   the same seed/seat pair, and ensure win rates and paired statistics never pool
   incompatible conditions. Backward compatibility must remain explicit. Handle
   missing identity, missing provenance, duplicate retry artifacts and conflicting
   repeats transparently. Do not silently invent attributable identity.
2. Tool-accounting reporting from existing context_call and context_tool schemas:
   per-seat model round counts, tool requests/successes/denials, elapsed times,
   reported token counts and unknown/incomplete accounting. Avoid double counting
   overlapping decision-row tokens. Report billing incompleteness from backend
   retries instead of pretending these are exact charges. Use synthetic fixtures
   only: no reading real game logs, datasets or model credentials.
3. Batch runner options for explicit context profile and memory tools, with saved
   provenance/resume identity that cannot mix conditions. Preserve seed governance,
   historical baseline semantics, no AI fallback policy, timeout behavior and
   external-egress consent. Do not run actual batches or external model calls.
4. Meaningful tests covering same seed/swap under two profiles/models/tools settings,
   matching pairs within each experiment only, legacy rows, conflicting duplicates,
   tool failure/unknown tokens, and batch resume behavior. Existing tests and CLI
   reporting should still work or migrate with clear justification and tests.

Edit only eval Python source/tests and a new docs/harness/history/EVAL-IMPLEMENTATION.md.
No other edits; do not commit, stage, push, install, deploy, contact people, read
secrets.env/credentials/datasets/game logs/personal files, or run paid evaluations.
Use local Python and synthetic fixtures. Do not modify or weaken
 eval/defect_analyzer.py to get a pass. Do not spawn additional agents. The older
CLAUDE.md pre-commit directions do not apply: no commit is authorized.

Do the implementation, not only an audit or plan. At completion report changed
files, test commands/results, compatibility changes and every remaining gap.
