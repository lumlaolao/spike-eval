# Eval identity implementation — served identity is evidence, not a wildcard

Date: 2026-09-07. Work order: `CLAUDE-EVAL-IDENTITY-WORK-ORDER.md`, finding 3 of
`INDEPENDENT-REVIEW-20260907.md`. Scope: `eval/aggregate.py`,
`eval/build_report.py`, `eval/tests/test_experiments.py`, `eval/README.md`,
`docs/harness/history/EVAL-IMPLEMENTATION.md` and this file. No Java, no acceptance
runner, no `defect_analyzer.py`, no seed governance, no commits, no model
calls, no real run directories or datasets were read; every fixture is
synthetic and written to a temp dir.

## What was wrong

`group_experiments()` keyed experiments on the condition *minus* the four
`served_*` fields, then reattached a served identity per group:

- if a group held at most one resolved value per field, every member was put
  into one experiment stamped with that value, so a game whose endpoint
  named nothing (`served_smart_a="unknown"`) was **filled in** from a
  neighbour's endpoint identity, could be **paired** with a resolved game of
  the same seed, and could be **dropped by or drop** the resolved game of the
  same `(seed, swap)` as a "duplicate";
- with `smart=model-X/fast=unknown` and `smart=unknown/fast=model-Y` in one
  group, each field had one resolved value, so the two games merged into a
  single experiment labelled `model-X/model-Y`, a joint condition **neither
  game observed** (the review's reproduction);
- when a group held several resolved values, unresolved games were relabelled
  to a synthetic `(unresolved)` value, so an experiment's condition no longer
  equalled the condition of its own game lines.

The cited identity gate also read the directory as a whole: `discipline` had
to be uniquely `cited` (a mixed-discipline experiment was never gated) and any
pre-provenance line in the directory excused every modern line beside it.

## What changed

### `eval/aggregate.py`

- **Partition on the full recorded condition.** `group_experiments()` now
  groups by `_canon(game_condition(g))` with `served_*` included exactly as
  recorded. There is no base-key pass, no fill-in, no split-and-relabel, and
  the `UNRESOLVED` placeholder constant is gone. Invariant, tested for every
  grouped game: `experiment_id(game_condition(g)) == g["_experiment"]` and
  `experiment["condition"] == game_condition(g)`.
- **Consequences that are now the contract.** An unresolved game never joins a
  resolved experiment; two half-resolved games are two experiments; a
  same-seed rerun with a different served value is a second experiment (no
  `duplicates` entry, no warning, both games counted); `swap=false` resolved
  and `swap=true` unresolved is not a pair in either experiment nor at the top
  level; `(pre-provenance)` (field absent) and `unknown` (field present,
  empty) are different recorded states and different experiments; `""` and
  `"unregistered"` stay as recorded rather than being rewritten to `unknown`.
- **Missing / unknown / not-applicable are told apart, not merged.**
  `served_identity_status(condition)` classifies each served field without
  changing its value: `resolved`, `unresolved` (present, in
  `UNATTRIBUTABLE_VALUES`), `missing` (absent, legacy) or `not-applicable`
  (the seat's controller is not `llm`, so the harness's stamped `unknown`
  names nothing because there was nothing to name). `identity_status(cond)`
  reduces that to `unresolved` > `legacy` > `resolved` > `not-applicable`.
  Both appear on every experiment (`served_identity`, `identity_status`) in
  place of the removed `served_unresolved_games` counter, which under the new
  rule could only ever be zero.
- **Cited eligibility is decided per game and rolled up.**
  `summarize_model_identity()` now fails when *any* real game stamped
  `discipline=cited` has an llm seat whose served field is unresolved
  (`unresolved_cited_games`); the `disciplines` list is reported next to the
  old single-valued `discipline`. `unresolved_fields` / `legacy_fields` are
  likewise computed per game and per seat, so a baseline seat in one
  experiment no longer names the directory unresolved. `build_summary()`
  adds `failed_experiments` (ids) and `experiments` (per-id status) to the
  top-level `model_identity` block and sets its `failed` to the union of the
  directory-level and per-experiment verdicts, so a legacy or baseline
  experiment beside a cited-unresolved one never excuses it. Exit code 3 is
  unchanged. With `--experiment` on a resolved id the summary is clean and
  attributable.
- **Labels and human report.** `experiment_labels()` prints the differing
  fields with their recorded values (`served_smart_a=unknown`); the
  unreachable "(identical condition, distinct served identity)" fallback is
  removed. The per-experiment flag reads `served identity UNRESOLVED:
  served_fast_a='unknown', ...` instead of "admitted with unresolved served
  model", and the model-identity block lists `ineligible experiment(s)`.

### `eval/build_report.py`

The `model_identity` gate checks aggregate's rolled-up `failed` first
(`pass: false`, detail names the failed experiment ids), and only then
reports `None` (not judged) for legacy or vacuous dirs. Previously a legacy
experiment anywhere in the arm dir turned the gate into "not judged" even
when a cited experiment beside it had failed. The `single_experiment` gate
already fails an arm dir holding more than one condition, so an arm with one
unresolved rerun is reported as such rather than silently absorbed.

### `eval/analyze_ab_stage0.py` (unchanged, behaviour follows)

It already refuses an arm dir whose `(seed, swap)` appears under two
`_experiment` ids. An unresolved rerun of a seed is now such a case, so the
A/B compare refuses instead of picking the newest file.

## Tests (`eval/tests/test_experiments.py`)

The former `test_unresolved_served_identity_is_never_invented`, which asserted
the wildcard admission and the `(unresolved)` relabel, is replaced by
`ServedIdentityTest` plus three CLI tests, all against public
`build_summary()` / `group_experiments()` / `aggregate.py` outputs:

| test | pins |
| --- | --- |
| `test_unresolved_game_never_joins_a_resolved_experiment` | two resolved + one unresolved: two experiments, recorded labels, per-field status (A unresolved, B not-applicable), only the unresolved experiment fails, directory inherits, ids equal own conditions |
| `test_complementary_unknowns_do_not_form_a_joint_condition` | the review's reproduction through both `build_summary()` and a direct `group_experiments()` call: no `(model-X, model-Y)` condition exists; both experiments unresolved and ineligible |
| `test_adversarial_multi_condition_dir` | v3, v4, unknown, legacy and half-resolved in one dir: five experiments with values as recorded, statuses `legacy/resolved/resolved/unresolved/unresolved`, only unresolved ones fail, `--experiment` on v3 is attributable |
| `test_same_seed_unresolved_rerun_is_not_a_duplicate` | same `(seed, swap)` resolved vs unresolved in either file order: two experiments, no duplicates, both games counted, original outcome intact |
| `test_swapped_pair_with_unresolved_half_is_not_a_pair` | resolved/unresolved halves never pair; two same-valued unresolved halves do pair but stay ineligible |
| `test_legacy_missing_is_distinct_from_unknown` | absent vs empty fields are separate experiments; legacy excused, modern failed, legacy neighbour does not excuse |
| `test_deterministic_baseline_seats_are_not_applicable` | floor vs random with stamped `unknown`: `not-applicable`, vacuous, passes, pairs; llm A resolved beside random B attributable; a baseline seat in another experiment does not mark the dir unresolved |
| `test_cited_gate_is_per_game_not_per_directory` | cited + exploratory under one condition fails; exploratory-only is reported not failed |
| `test_empty_and_unregistered_are_unresolved_but_kept_as_recorded` | `""`, `unknown`, `unregistered` are three unresolved experiments |
| `test_public_constants_do_not_expose_a_substitute_label` | no `aggregate.UNRESOLVED`; `identity_status` on real conditions |
| `CliTest.test_unresolved_rerun_fails_the_cited_gate_on_the_cli` | `--json` exits 3 with the gate banner, two experiments, recorded label, no duplicate warning; human output shows the UNRESOLVED flag and ineligible list and not the old "admitted" text; `--experiment <resolved>` exits 0 |
| `CliTest.test_complementary_unknowns_fail_on_the_cli` | exit 3, both half-resolved conditions present, `model-X model-Y` absent from the JSON |
| `CliTest.test_baseline_dir_passes_on_the_cli` | floor/random dir exits 0, `not-applicable` |

Existing tests corrected or confirmed: `test_aggregate.py` runs unmodified
(vacuous baseline, cited-unresolved failure, exploratory warn-only, legacy
excused, mixed checkpoint). `test_tool_accounting.py`,
`test_context_provenance.py`, `test_run_batch.py` unchanged.

```
python3 -m unittest discover -s eval -p 'test_*.py'    # 107 tests, OK (was 95)
python3 eval/tests/test_aggregate.py                         # All checks passed.
python3 -m unittest eval.test_experiments -v           # 28 tests
python3 -m py_compile eval/*.py && git diff --check
```

## Compatibility

- Single-condition directories whose served values are all resolved, all
  legacy or all baseline produce the same numbers, labels, exit codes and
  experiment ids as before. JSON is additive there (`served_identity`,
  `identity_status`, `disciplines`, `unresolved_cited_games`,
  `failed_experiments`, `model_identity.experiments`).
- Removed keys: per-experiment `served_unresolved_games`; module constant
  `aggregate.UNRESOLVED`. Neither was consumed outside `aggregate.py` and its
  tests.
- Behaviour change, intended: a directory that mixes resolved games with an
  unresolved same-condition game now reports two experiments and refuses the
  pooled blocks (it already exited 3 before; it now also says which
  experiment is ineligible and leaves the resolved one intact under
  `--experiment`). A cited-unresolved game is no longer excused by a
  mixed-discipline neighbour or by a pre-provenance line in the same dir.

## Not done / out of scope

- Seed governance, `defect_analyzer.py`, `run_batch.py`, Java sources and
  the acceptance runner are untouched (other workers own them).
- No real run directory was re-aggregated; the effect on existing `runs/`
  data is predicted from the contract above, not measured.
- A `None` arm field (e.g. `smart_b: null` for a modelless seat) still reads
  as `(pre-provenance)` in the condition and marks the experiment `legacy`.
  That predates this work and is unrelated to served identity; it is noted
  because a fixture tripped on it.
