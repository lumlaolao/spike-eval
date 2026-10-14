# Eval implementation report — work order B (trustworthy harness evaluation)

Date: 2026-09-06. Scope: Python evaluation code and tests under `eval/`, plus
this document. No Java, no `PLAYER-HARNESS.md`, no `defect_analyzer.py`, no
commits, no batches, no model calls, no real run directories or datasets were
read by the new tests (all fixtures are synthetic and written to temp dirs).

## What was wrong

`aggregate.py` deduplicated games by `(seed, swap)` alone, keeping the latest
file. A rerun of the same seed under a different context profile, memory-tool
setting, model, checkpoint or budget silently replaced the original, and the
mixed-provenance warning was computed only afterwards, over the survivors.
Paired statistics matched `swap=false` with `swap=true` across conditions.
`context_call` / `context_tool` rows were parsed as unknown types and dropped.
`run_batch.py` had no flag for `CONTEXT_PROFILE` / `MEMORY_TOOLS`, inherited
them from the ambient environment, and resumed on "any game line for this
seed/swap exists", so a resumed batch could skip games played under another
condition or append a second condition to a directory.

## 1. Experiment identity and grouping (`eval/aggregate.py`)

- **Condition key.** `game_condition()` collects every behaviorally relevant
  recorded field: the arm configuration (`deck_a/b`, `controller_a/b`,
  `smart_a/b`, `fast_a/b`) plus every `PROVENANCE_FIELDS` entry except
  `discipline` and `seed_bank`, which describe how a run was labelled rather
  than how it behaved (they remain in the mixed-provenance report). Two new
  game-line fields the harness already emits, `claude_effort_smart` and
  `claude_effort_fast`, were added to `PROVENANCE_FIELDS` and therefore to the
  key. `experiment_id()` is `sha256(canonical condition)[:12]`, stable across
  directories.
- **Grouping before dedupe.** `parse_dir_full()` groups real game lines by
  condition first (`group_experiments()`), then dedupes `(seed, swap)` only
  within an experiment. A rerun under another condition is a second
  experiment, never a replacement. Crashed files (no game line) carry no
  condition, are counted invalid, and join no experiment.
- **Duplicates.** Within an experiment the latest file is kept (unchanged
  policy). Each drop is recorded in `duplicates` with `conflicting`
  (different winner) and `same_outcome`; the warning text distinguishes
  "identical outcome: retry artifact" from "CONFLICTING outcome (A vs B)".
  Conflicts are reported, never averaged.
- **Served identity is part of the key, as recorded.** `served_*` values
  partition experiments exactly as each game line observed them. *(Corrected
  2026-09-07, see `EVAL-IDENTITY-IMPLEMENTATION.md`: the original version of
  this work admitted an `unknown` game into a group with one resolved value
  and could combine two half-resolved games into a joint condition neither
  observed. That wildcard rule is gone.)* An unresolved value is never filled
  from another game, never paired with or deduped against a resolved game of
  the same seed, and forms its own experiment labelled with the recorded
  value (`served_smart_a=unknown`). Each experiment reports
  `served_identity` (per field: value and `resolved` / `unresolved` /
  `missing` / `not-applicable`) and `identity_status`. The model-identity
  gate is decided per cited game and rolled up over experiments
  (`failed_experiments`).
- **Legacy rows.** Fields the harness did not emit read as `(pre-provenance)`
  and form their own experiment with `legacy: true` and `legacy_fields`. A
  legacy line and a modern line for the same seed/swap never merge. A
  single-condition legacy directory is summarized exactly as before.
- **Pooling refused.** With one experiment the top-level `games`, `arms`,
  `paired`, `model_identity`, `fallback_gate` blocks are computed as before
  (byte-compatible with the old JSON apart from added keys). With more than
  one, `arms` is `{}`, `paired` carries `pooled_refused: true`, the pooled
  seat-bias rate is `null`, and the human report prints a loud
  "N DISTINCT CONDITIONS" banner. Per-experiment blocks live under
  `experiments.by_id[<id>]` (each with its own games/arms/paired/decisions/
  model_identity/fallback_gate/tool_accounting). `--experiment ID` (or a
  unique prefix) restricts the whole summary to one condition explicitly.
- **Warnings.** The multi-condition warning is emitted during parsing, before
  any statistic, and names every experiment with its differing fields.

## 2. Tool accounting (`eval/aggregate.py`)

`summarize_tool_accounting()` reads the `context_call` rows (`seat`, `turn`,
`call`, `tier`, `elapsed_ms`, `prompt_tokens`, `completion_tokens`, `prompt`,
`raw_reply`) and `context_tool` rows (`seat`, `turn`, `call`, `request`,
`result`) exactly as `LlmSpikeGui.ask()` writes them. Per seat, mapped to
identity and controller through the game line:

- model rounds total and by tier, rounds without a reply (`raw_reply: null`),
  rounds never followed by a decision row (crash);
- tool requests, `ok` / `errors` (`ERROR:` results) / `denied`
  (`ERROR: tool budget exhausted`), by command (SEARCH/READ/NOTE/other), and
  the per-game maximum;
- elapsed sum / mean / p90;
- reported tokens: sums over rounds whose usage was reported, with
  `rounds_known` / `rounds_unknown` (the Java side writes `-1` in context
  rows and `null` in decision rows when the backend reported nothing);
- **no double counting:** context rows are attributed to the decision row
  that follows them for the same seat in file order (`attach_context_calls`).
  A decision row with attached context rows contributes no tokens (its
  fields repeat the final round); only decisions without context rows count
  their own decision-row tokens;
- **retry incompleteness:** decision-row `attempts` minus recorded rounds
  (or minus one for a plain decision) is the number of backend attempts with
  no token record. Reported as `attempts_without_token_record`, and the seat's
  `billing.complete` is false whenever any round or attempt is uncosted, any
  round had no reply, or legacy rows lack `attempts`. Non-model seats report
  `applicable: false` rather than "incomplete".

The accounting is printed as a `=== tool accounting ===` section, appears in
`--json` at the top level (directory-wide cost) and per experiment, and
carries `TOOL_ACCOUNTING_CAVEAT` stating that these are last-attempt reports,
not billing. Files holding more than one game line are flagged because
order-based attribution there may cross games.

## 3. Batch runner (`eval/run_batch.py`, `eval/run_suite.py`)

- `--context-profile NAME` sets `CONTEXT_PROFILE`; `--memory-tools` sets
  `MEMORY_TOOLS=1`. Both are the only route: `CONTEXT_PROFILE`,
  `MEMORY_TOOLS` and every `CTX_*` knob are scrubbed from the ambient
  environment; `--env` naming `CONTEXT_PROFILE`, `MEMORY_TOOLS`, `RULES_TOOL`,
  `NATIVE_TOOLS` or `LOOKAHEAD` is refused in every mode before anything runs
  (each has its own flag); cited mode also refuses `--env CTX_*` (a cited
  condition is a named profile only). Unset flags leave the JVM defaults
  (`v1-compat`, tools off): the historical baseline is byte-identical.
- **Batch manifest.** `<out>/batch-manifest.json` (`batch-manifest.v1`)
  records the condition (discipline, decks and identity names, controllers,
  models, plans, context profile, memory tools, CTX overrides, checkpoint,
  engine/overlay/deck shas, cap, shadow-AI, non-secret `--env` overrides;
  values whose key looks like a credential are written as `<redacted>`) and
  appends one entry per invocation (seeds, pairs, jobs, argv). Resuming with
  a different condition is refused (exit 2) listing every differing key.
  Extending the seed set is not a condition change.
- **Pre-manifest directories** are compared on whatever their game lines
  record; a mismatch is refused. If the lines cannot record the condition at
  all (no `context_profile` / `memory_tools` / `checkpoint_sha`), cited mode
  refuses and exploratory mode warns.
- **Condition-aware resume.** `plan_combos()` skips a combo only when a game
  line exists under this batch's condition (`resume_expectation()`: the
  requested profile, memory-tools state, and any explicitly given
  controllers/models; a legacy line without the fields matches only the
  baseline). A combo whose only game line is under another condition is
  listed as foreign and replayed. After a game, a line recorded under a
  different condition than requested is a `condition-mismatch` failure, not
  a completion.
- The default `--out` label gains `-ctx-<profile>` / `-memtools`.
  `run_suite.py` passes `--context-profile` / `--memory-tools` through to
  every arm and suffixes the arm directories the same way.
- Untouched: seed governance (`warn_reserved_seeds`, `cited_seed_guard`,
  `SEED_BANK`), the mirror/heuristic/jobs guards, cap derivation and the
  subprocess backstop timeout, `preflight_openrouter` egress consent,
  `load_secrets`, and the no-AI-fallback policy (which lives in Java).

## 4. Report builder and A/B compare

- `build_report.py`: new gate `single_experiment` (fails when an arm dir
  holds more than one condition), `n_experiments` / `experiment_ids` /
  `memory_tools_games` / `student_tool_accounting` in each arm result, and
  both rendered into `report.md`.
- `analyze_ab_stage0.py`: warns when an arm dir holds several conditions and
  refuses (exit) when one `(seed, swap)` appears under two, instead of
  silently keeping the last.

## Compatibility

- `aggregate.parse_dir()` keeps its `(games, decisions, warnings)` signature;
  games now carry `_experiment` and decisions carry `_file`. With one
  condition per directory (the documented practice) every previous number is
  unchanged; `test_aggregate.py` passes unmodified.
- JSON output is additive for single-condition dirs (`experiments`,
  `tool_accounting`). Multi-condition dirs now report `arms: {}` and
  `paired.pooled_refused` where they previously reported a silently deduped
  pool; this is the intended migration and is covered by tests.
- Exit codes are unchanged (2 fallback gate, 3 identity gate). `--experiment`
  with an unknown id exits 1 with a message.
- `run_batch.py` now writes `batch-manifest.json` into every `--out` and can
  refuse a resume that previously would have proceeded (different condition,
  or an unverifiable legacy dir in cited mode). `has_game_line(out, seed,
  swap)` without an expectation behaves as before.

## Tests

```
cd eval && python3 -m unittest discover -s . -p 'test_*.py'   # 95 tests, OK
python3 eval/test_aggregate.py                                # All checks passed.
python3 -m unittest eval.test_experiments -v                  # 16 tests
python3 -m unittest eval.test_tool_accounting -v              # 10 tests
python3 -m unittest eval.test_run_batch -v                    # 23 tests
python3 -m py_compile eval/*.py && git diff --check
python3 eval/run_suite.py eval/suites/stage3-v1.json --context-profile v2-memory --memory-tools
```

Covered: same seed/swap under two profiles, two models, memory tools on/off,
checkpoint, context sha, cap and effort; pairs matched only within an
experiment; single-condition backward compatibility; legacy rows alone and
mixed with modern rows; identical and conflicting duplicates; duplicates
across conditions not treated as duplicates; unresolved served identity
(kept apart, never joined; see `EVAL-IDENTITY-IMPLEMENTATION.md` for the
2026-09-07 test set); stable ids; crashed files;
`--experiment` selection and CLI JSON/human output; tool result and command
classification; per-seat rounds/tools/elapsed/tokens; overlap skipping;
retry incompleteness with and without memory tools; unattached rounds and
legacy decision rows; per-experiment accounting; batch env scrubbing and
flags; cited refusal of `CTX_*` and condition env; manifest write/append,
refusal on every condition key, corrupt manifest, legacy-dir comparison and
cited refusal of unverifiable dirs; condition-aware resume planning.

## Remaining gaps

- **Attribution inside interleaved files.** When two games share one JSONL
  file, context rows are attributed to decision rows by seat and order only;
  the file is flagged, not resolved. A per-row game id from the Java side
  would remove the ambiguity.
- **Retry cost is bounded, not measured.** The number of uncosted attempts is
  exact from `attempts`, but their token usage is unknown; the bridge does
  not record per-attempt usage. A dedicated total-compute report (per
  `PLAYER-HARNESS.md`) still needs Java-side per-attempt usage rows.
- **Tool budget invariants** (2 per ask, 64 per game) are reported
  (`max_per_game`) but not gated; `defect_analyzer.py` was not modified.
- **`eval/README.md` and `CLAUDE.md`** were not updated (outside the allowed
  edit set); the new flags, manifest and multi-condition semantics are
  documented here and in `--help` text only.
- **Condition key relies on what the game line records.** Unrecorded
  behaviour (e.g. `OPENROUTER_MODEL_CFG` overrides beyond `model_cfg_sha`, or
  bridge-level settings without a provenance field) cannot partition
  experiments. Adding a field to `PROVENANCE_FIELDS` is sufficient once the
  harness emits it.
- ~~**`served_*` wildcard rule** admits an unresolved game into a group with
  one resolved served value.~~ Closed 2026-09-07: `group_experiments()`
  partitions on recorded served values and never fills, pairs or dedupes an
  unresolved game against a resolved one (`EVAL-IDENTITY-IMPLEMENTATION.md`).
- **Manifest resume comparison uses exact equality**, including
  `engine_sha` / `overlay_sha`. Reinstalling the overlay between resumes of
  the same batch therefore requires a new `--out`; this is deliberate but may
  surprise exploratory loops.
- Not exercised: an actual JVM launch under the new flags (no batches were
  run), and the `condition-mismatch` post-game path beyond its unit-level
  matcher.
