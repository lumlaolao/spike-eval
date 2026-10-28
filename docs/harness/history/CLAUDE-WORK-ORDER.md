# Claude implementation work order — A: player observation boundary

You are the implementation engineer. The user explicitly requests Claude to do the
implementation while Codex orchestrates and verifies. The overall objective is a
fully usable, faithful MTG player harness, not a demo or an increment that merely
preserves green tests. Read docs/harness/PLAYER-HARNESS.md and VALIDATION.md first.

Implement the production observation/visibility/object-identity foundation and its
adversarial tests. Work directly in this workspace. Preserve all existing changes:
we recovered newer Forge sources and added memory tools in the preceding session.
Do not revert those or confuse recovered code with your changes.

## Deliverable

- Audit every agent-facing current-state, candidate-label, stack, log/history and
  memory input path. An opponent's hidden hand, library, face-down true identity,
  or deck label must not leak into observations or available tools. Own legally
  visible information must remain usable. Avoid solving privacy by withholding
  public state a real player needs.
- Implement a coherent viewer-aware observation representation and serialization,
  consumed by live prompts/tools rather than a disconnected schema. Cover priority,
  player resources/counts, visible zones, stack source/controller/targets, combat
  assignments, attachments, counters/damage, public object characteristics and
  explicit missing/omitted information. Consult the pinned Forge APIs on disk.
- Implement or improve safe viewer-visible object handles, distinguish duplicate
  copies and new rules objects after zone changes. Avoid deriving player-facing
  handles from engine IDs that reveal hidden ordering; do not correlate objects
  across hidden shuffles. Connect menu labels with observable objects. Evaluate
  how to preserve a separately versioned legacy profile, but make the new faithful
  path usable, not an unconnected experimental scaffold.
- Seat-aware narration/archive must retain only legitimately available evidence.
  Public narration currently comes from GameHistory.collect; examine its actual
  engine formatter. Private reveals must remain seat-local and accurately marked
  historical. Expose full permitted evidence instead of silent truncation.
- Add engine-backed paired-world tests: alter only unseen opponent hand/library
  identities/order; observations and history/tool results must match. Include
  public face-down permanents and exile, own visibility, private reveal semantics,
  duplicate copies, blink/zone re-entry and control change. Tests must call the live
  implementation, not duplicate its logic. Add useful coverage where APIs permit;
  explicitly record any remaining unsupported contract rather than claiming complete.
- Update docs/harness with actual implementation and remaining requirements.

## Boundaries

Edit only this repository and temporary validation files. NEVER edit or install
into ~/code/forge; it is read-only source/API/dependency reference.
Do not commit, stage, push, deploy, contact other people, or read secrets.env,
credentials, token stores, personal files, or unrelated datasets/runs. Do not call
external model endpoints or run paid game evaluations. You may use local tests and
fake bridges. Do not weaken eval/defect_analyzer.py or existing hard invariants to
pass. Do not launch other coding agents; work within this assigned session.

The repository's older CLAUDE.md/HANDOFF.md contain stale status and a pre-commit
workflow: this task does not authorize committing or staging. This work order takes
precedence for this session. Source of truth remains forge-overlay/.

## Validation available

JDK: /opt/homebrew/opt/openjdk/bin/{java,javac} (JDK 26), plus openjdk@17 installed.
Existing dependency classpath: /tmp/harness-validation-classpath.txt.
Compile with javac --release 17 into a NEW private temporary class directory, using
that classpath, all Java sources in forge-overlay/forge-gui-desktop/src/{main,test}/java/forge/spike.
Do not rebuild or modify the installed engine classes. Existing runner example:
/tmp/run-harness-java-tests.py (points to preceding build; adapt your own copy to
new compiled output). TestNG tests need cwd ~/code/forge/forge-gui-desktop
for assets. Use -Dapple.awt.UIElement=true. Initialize model only when needed.
Some local-server tests need loopback socket permissions. Report permission denials
rather than silently omitting a gate. Never hide test failures.

At completion provide changed files, actual tests/commands/results, architecture
choices, and a precise list of unfinished requirements. If blocked, report the
specific missing capability. Do not stop after writing a plan; implement the work.
