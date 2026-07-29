# EVAL v1.0 — what it measures, how to run it, and what it cannot tell you

Status of this document: written at integration time, from what the five v1.0
modules actually do when run, not from what they were designed to do. Where
the two differ, the observed behaviour is what is written down.

**Headline honesty statement.** v1.0 is a working *harness* with a working
*scorecard*. It is not yet a working *benchmark*, because the golden suite it
would score models against does not exist: the answer-key generation run
(EVAL-DESIGN Phase 1.2, ~2000 ai-vs-ai rollout games) has **not been
executed**. Today the whole repository yields **3 verified puzzle positions**,
all from the offline exhaustive lane. Nothing in this document should be read
as "we can rank frontier models now." See
[§6 What v1.0 still cannot tell you](#6-what-v10-still-cannot-tell-you).

---

## 1. The one command

```
python3 eval/build_report.py eval/suites/stage3-v1.json \
    --student <model-spec> \
    --puz-run     runs/puz/<run> \
    --plan-run    runs/plan/<run> \
    --blunder-run runs/blunder/<run> \
    --out runs/reports/<name> [--generated-utc YYYYmmdd-HHMMSS] [--json]
```

Emits `report.json`, `report.md`, `scorecard.json` (schema
`eval-scorecard.v1`), `suite.json`, and per-arm defect evidence.

Every argument is optional except that you must give *something*: the suite
path drives STR, `--puz-run` drives PUZ, `--plan-run` drives PLAN,
`--blunder-run` drives DQ **and** PUNT. With no suite and no axis input it
exits 2 (usage error).

Exit codes: `0` PASS, `1` FAIL (a suite gate broke or a holdout was
contaminated), `2` usage, `3` INCOMPLETE.

**An axis with no input data is never a zero.** It renders as an explicit
`not measured` row in stdout, in the markdown table, and in `scorecard.json`,
carrying (a) the reason it is unmeasured and (b) the exact command that would
measure it. An unmeasured axis cannot produce a PASS: it demotes the verdict to
INCOMPLETE (exit 3). This is enforced by
`eval/test_build_report.py::test_missing_axis_never_zero` and
`::test_incomplete_scorecard_cannot_pass`.

There is a third axis state, `partial`: the input existed but every *headline*
metric had zero observations (real today for PUNT — a textlane-only corpus
measures `punt.rate_textlane` but not the rollout punt rate). `partial` counts
as unmeasured for coverage and for ranking.

---

## 2. Provenance header

Every scorecard carries, at the top:

| field | source |
|---|---|
| `contract_sha_running` | `prompt_contract.contract_sha()` in *this* tree — currently `abaddd69db49a9d2`, contract `prompt-contract.v1`, parser `parser.v1.1` |
| `contract_sha_by_axis` | the `contract_sha` stamped into each axis artifact when it was produced |
| `engine_sha` | `{sha: [axes reporting it]}` — currently `c1c64a53586459cd0d4c70f27abd1aa8bbc92891` (`FORGE_COMMIT`) |
| `overlay_sha` | same shape, from `forge-overlay/` |
| `seed_bank` | the suite's declared band (`stage3-v1`: `7000..7999`) plus the observed `seed_blocks` histogram from the PUZ corpus |

Two provenance alarms fire automatically and are worth trusting:

* **CONTRACT DRIFT** — an axis artifact stamped with a `contract_sha` other
  than the running one. Those numbers were produced by a different prompt/parser
  and must not be compared to numbers produced by this one.
* **MIXED engine_sha / overlay_sha** — axes measured on different engine builds
  are not one scorecard. This fires for real in the smoke below: a PUZ corpus
  mined across eight run directories reports `overlay_sha: "mixed"`.

PLAN carries no `contract_sha` **by design**: it reads raw engine decision rows
out of the game logs, not contract-rendered prompts, so the prompt contract is
not part of its identity. The scorecard says so in the axis provenance rather
than leaving a suspicious hole.

---

## 3. The five axes

### STR — match strength

```
python3 eval/run_suite.py eval/suites/stage3-v1.json      # prints the run_batch commands
# run them, then:
python3 eval/build_report.py eval/suites/stage3-v1.json --student <spec>
```

Metrics (one pair per enabled arm):

| metric | trivial floor | why that is the floor |
|---|---|---|
| `str.win_rate.<arm>` | `mirror_coinflip = 0.50` | every suite arm is mirror-deck, so a policy indistinguishable from its opponent wins half |
| `str.paired_delta.<arm>` (non-headline) | `no_difference = 0.00` | the null: student and opponent split every seed |

`se` on the win rate is the normal-approximation binomial `sqrt(p(1-p)/n)`; the
interval is Wilson, computed by `aggregate.py`, not recomputed here. Clusters =
seed pairs.

Arms that produced no usable data are *excluded from the axis and named in a
PARTIAL caveat* — they are never averaged in as zeros.

### PUZ — puzzle accuracy on verified answer keys

```
python3 eval/mine_puzzles.py <run-dirs...> --out cand.jsonl
python3 eval/verify_keys.py cand.jsonl --out positions.jsonl --lane both --driver engine
python3 eval/puzzle_run.py positions.jsonl --backend offline --out RUNDIR
python3 eval/build_report.py --puz-run RUNDIR
```

Backends: `offline` (fixture or one of the five `--offline-policy` values:
`always-pass`, `random`, `first`, `oracle`, `garbage`), `http` (local endpoint),
`openrouter`, `bedrock` (AWS, via `eval/bedrock.py` — per-model
max_tokens/thinking/timeout come from `BEDROCK_MODEL_CFG`, not the CLI flags,
and are stamped into `summary.model_cfg`). **Egress refuses by default**: a
non-local endpoint host aborts with exit 2 unless `OPENROUTER_ALLOW_EGRESS=1`
is explicitly set — the same single switch for all backends, Bedrock included.
Do not set it casually — the payload is the full board snapshot.
`openrouter`/remote-`http` resolve `OPENROUTER_API_KEY` (env, then
`<repo>/secrets.env`) and send it as a Bearer token, checked before any
request so a missing key cannot spend the consent on a 401; the key never
reaches an artifact (only `api_key_present`). No live backend has ever been
exercised; see §6.

| metric | trivial floors |
|---|---|
| `puz.score_incl` **(headline, 0–10)** | `random_choice`, `always_pass`, `always_first`, `majority_class`, plus `oracle_ceiling` as a ceiling |
| `puz.pass_1` | the same four, on their `pass_1` twins |
| `puz.pass_k` (non-headline) | the same four, on their `pass_k` twins (deterministic floors repeat, so their `pass_k` equals their `pass_1`; `random_choice`'s is `p**k`) |
| `puz.interface_failure_rate` (non-headline) | `never_fail = 0.0` |
| `puz.score_excl` (**never** headline) | the same four |

Read `margin_over_always_first`, not just `margin_over_random_choice`. The
engine emits menu options in its own order and nothing shuffles them, so a
corpus can put the correct answer at slot 1 far more often than chance, and
`majority_class` — a *label* floor — cannot see that. The scorecard raises a
`POSITIONAL SKEW` caveat carrying `best_index_counts`. In the smoke run below,
`always_first = 10.0/10` on a 3-position corpus: a policy that always answers
"1" is perfect there. That is a property of the corpus, not of any model.

`puz.score_excl` drops refused samples from its own denominator, so a model
that answers easy positions and emits garbage on hard ones is scored only on
the subset it chose. The scorecard computes
`selective_refusal_lift = score_excl - score_incl` and, whenever
`interface_failure_rate > 0.10` (or is unmeasured), demotes `score_excl` out of
the headline and raises a `SELECTIVE REFUSAL` caveat. Quote `score_incl`.

### PLAN — deck-plan adherence

```
python3 eval/plan_adherence.py <run-dirs...> --out RUNDIR
python3 eval/build_report.py --plan-run RUNDIR
```

One scorecard row per `plan.<deck-slug>.<milestone>`. **PLAN is deliberately a
profile, never a scalar.** Collapsing six milestones of different kinds into one
"plan score" would give the same number to a deck that aces the cheap milestones
and misses the deciding one as to the reverse. `test_build_report.py::
test_plan_is_a_profile_not_a_score` asserts no aggregate row exists.

| milestone kind | metric | trivial floor | ceiling |
|---|---|---|---|
| deploy-by-turn / combo-assembly / payoff-condition | `attain_rate` | `never_attained = 0.0` | `always_attained = 1.0` |
| resource-discipline / protection-usage | `rate` over opportunities | `never_take = 0.0` | `always_take = 1.0` |
| target-split | `face_rate` | `uniform_random_face_rate` (computed per row's actual menu) | — |

Seats that cannot be resolved to a deck archetype are excluded from every
denominator and counted in a caveat; a corpus where *no* seat resolves (e.g.
the `Identity-A`/`Identity-B` mirror runs) renders PLAN as **not measured**, not
as zero adherence.

### DQ — decision quality (win-probability loss)

```
python3 eval/blunder_report.py <run-dirs...> --out RUNDIR --adjudicate rollout --driver engine
python3 eval/build_report.py --blunder-run RUNDIR
```

| metric | trivial floor |
|---|---|
| `dq.wpl_mean` **(headline, lower is better)** | `random_choice_wpl` — the WPL a uniform-random pick from the same menu would have incurred |
| `dq.per_game_worst_p95` (non-headline) | `zero_loss = 0.0` |

**DQ requires rollouts.** The textlane can prove a blunder happened but cannot
price it. With `--driver none` the axis renders **not measured** with that
reason spelled out — not 0.0 WPL, which would read as flawless play.

### PUNT — blunder rate

Same command as DQ; the two axes read the same `summary.json`.

| metric | trivial floors |
|---|---|
| `punt.rate_rollout` **(headline, lower is better)** | `never_punt = 0.0`; `always_good_classifier = 1 - punt_rate` (majority class); `random_choice_punt_rate` — **explicitly UNMEASURED in v1**, it needs its own rollout pass |
| `punt.rate_textlane` (non-headline) | `never_punt = 0.0` — rules-engine-verifiable punts only; **never** add this to `rate_rollout` |
| `punt.flag_rate` (instrumentation) | `never_flag = 0.0` / `always_flag = 1.0` |
| `punt.audit_lane_rate` (**leakage measurement**) | `never_punt = 0.0` |
| `punt.flagger_lift` | `no_lift = 0.0` — flaggers selecting nothing above base rate |

`punt.rate_rollout` is a **lower bound**: only the flagged and audit lanes are
adjudicated. `punt.audit_lane_rate` is what tells you how much the flaggers
miss; when it is unadjudicated the summary says leakage is *unmeasured, not
zero*, and the scorecard repeats that verbatim.

---

## 4. Every floor in one place

A score without its floor is not a score. Complete list as emitted by v1.0:

```
STR   str.win_rate            mirror_coinflip = 0.50
      str.paired_delta        no_difference   = 0.00
PUZ   score_incl/excl         random_choice, always_pass, always_first,
                              majority_class   (+ oracle_ceiling as a CEILING)
      pass_1 / pass_k         the pass_1 / pass_k twins of the same four
      interface_failure_rate  never_fail = 0.0
PLAN  attain_rate             never_attained = 0.0   (ceiling always_attained = 1.0)
      rate                    never_take     = 0.0   (ceiling always_take    = 1.0)
      face_rate               uniform_random_face_rate (per-row menu, not 0.5)
DQ    wpl_mean                random_choice_wpl
      per_game_worst_p95      zero_loss = 0.0
PUNT  rate_rollout            never_punt = 0.0; always_good_classifier = 1-punt_rate;
                              random_choice_punt_rate = UNMEASURED in v1
      rate_textlane           never_punt = 0.0
      flag_rate               never_flag = 0.0 / always_flag = 1.0
      audit_lane_rate         never_punt = 0.0
      flagger_lift            no_lift = 0.0
```

Every measured row also carries `n`, `se`, `ci95`, `n_clusters` and `method`.
`test_build_report.py::test_every_measured_metric_has_n_se_ci` and
`::test_every_measured_metric_has_a_floor` fail if any row loses them.

---

## 5. What was actually run (integration smoke, zero egress)

Real commands, real output. Scratch under `$CLAUDE_JOB_DIR/tmp`.

```
$ python3 eval/mine_puzzles.py runs/stage0-calibration runs/haiku-vs-random \
    runs/haiku-vs-floor runs/haiku-vs-ai-2 runs/haiku-vs-ai-3 \
    runs/tournament-1 runs/improve runs/haiku-vs-ai --out $TMP/cand.jsonl
wrote 807 candidates
  lethal-now 7700 eligible / 253 matched, save-vs-lethal 7420/357, forced-block 25/4,
  free-win 7833/125, mulligan 167/167, graded-midgame 2868/2868

$ python3 eval/verify_keys.py $TMP/cand.jsonl --out $TMP/positions.jsonl \
    --lane exhaustive --driver none --summary $TMP/verify-summary.json
verified 3 / 807 (yield 0.004)

$ python3 eval/puzzle_run.py $TMP/positions.jsonl --backend offline \
    --offline-policy random --k 3 --out $TMP/puzrun --generated-utc 20260728T000000Z
puzzle_run: 3 positions x k=3 (offline:random) score_incl=2.22 pass_1=0.22 iface=0.0

$ python3 eval/plan_adherence.py runs/stage0-calibration --out $TMP/planrun
PLAN adherence — 50 games, 100 seats resolved, 0 unresolved   [ur-delver, 6 milestones]

$ python3 eval/blunder_report.py runs/stage0-calibration --out $TMP/blunderrun
blunder report: 50 games, 2098 decisions, 1606 scoreable, 428 flagged rows emitted
  adjudicated: rollout=0 textlane=17 unadjudicable=411

$ python3 eval/build_report.py eval/suites/stage3-v1.json --student "haiku(real-run)" \
    --arm-dir forge-ai-ur-delver=runs/haiku-vs-ai \
    --puz-run $TMP/puzrun --plan-run $TMP/planrun --blunder-run $TMP/blunderrun \
    --out $TMP/report2 --generated-utc 20260728-000000
verdict: INCOMPLETE
scorecard: 3/5 axes measured (STR, PUZ, PLAN); partial: PUNT
  [measured    ] STR   str.win_rate.forge-ai-ur-delver 0.000 ci95 [0.000, 0.161] n=20
                       floor mirror_coinflip=0.500 margin -0.500
  [measured    ] PUZ   puz.score_incl 2.222 ci95 [0.000, 3.333] n=9
                       floor random_choice=3.611 margin -1.389
  [measured    ] PLAN  plan.ur-delver.threat_by_2 0.620 ci95 [0.482, 0.741] n=50 ...
  [not measured] DQ    ... adjudicated 0 decisions by rollout (driver=none,
                       adjudicate=textlane). DQ is UNMEASURED, not 0.
  [partial     ] PUNT  every HEADLINE metric (punt.rate_rollout) has zero observations
  ! MIXED overlay_sha across axes: {"532c01a82b16": ["STR"], "mixed": ["PUZ"]}
```

What this smoke does and does not prove:

* **Proves** the PUZ lane runs end to end from raw game logs to a scorecard row
  with floors, with no engine and no network.
* **Proves** the missing-data path: DQ renders as *not measured*, PUNT as
  *partial*, and the verdict is INCOMPLETE (exit 3), not PASS.
* **Proves** the provenance alarms fire on real data (the mixed `overlay_sha`
  is genuine — the PUZ corpus spans eight run directories built at different
  times).
* **Does not prove** anything about any model's Magic skill. The PUZ number
  above is the `--offline-policy random` floor generator over **3** positions;
  `puz.score_incl = 2.22` sits *below* its own `random_choice = 3.61` floor,
  which at n=9 is noise, exactly as it should look.
* **Does not exercise** the rollout lane. `verify_keys --driver engine`,
  `blunder_report --driver engine` and the Java `LlmSpikeFork` rollout entry
  point were not run (running games was out of scope for this pass).

---

## 6. What v1.0 still cannot tell you

The first eight are EVAL-DESIGN §9, unchanged and still true. Items 9–14 are
what integrating the modules exposed.

1. **Absolute strength.** Every strength number is anchored to Forge's
   heuristic AI, whose human-relative strength is unknown (probably weak
   amateur). "Beats the anchor 60%" places the model on no human ladder.
2. **Combo-line quality.** The rollout continuation policy is the same AI that
   wins 0% with Ant Storm, so WP answer keys systematically undervalue combo
   lines. Combo-deck graded positions are flagged, not fixed.
3. **Hidden-information judgment.** Single-deal rollouts cannot score
   information-set-correct play (bluff-Daze, playing around unseen cards). A
   "punt" that was correct against the opponent's *range* is miscounted.
4. **Cross-turn intent.** PLAN measures observable milestone pursuit, not
   whether the model meant it; DQ scores decisions locally, so a coherent losing
   line and an incoherent winning one are only partially separated.
5. **Interface aptitude vs Magic aptitude.** Every model plays through this
   harness's menu/text contract; a model bad at `CHOICE:` discipline scores low
   regardless of Magic knowledge. `puz.interface_failure_rate` makes this
   visible; it does not remove it.
6. **Metagame breadth.** 16 decks, Legacy, 1v1, best-of-1, headless: nothing
   about sideboarding, archetypes outside the pool, or multiplayer.
7. **Frontier fine resolution.** At consented budgets frontier CIs are wide
   (±13–19 pts); rankings between models closer than that are not claims this
   system can make.
8. **Teacher-relative metrics stay teacher-capped.** DQ-shadow and replay-match
   agreement are bounded by Forge-AI quality. Only PUZ, rollout-WPL and STR can
   show a student exceeding its teacher — and if those three move while
   DQ-shadow *falls*, that is the surpassing-the-teacher signature.

Learned during integration:

9. **THERE IS NO VERIFIED GOLDEN SUITE.** EVAL-DESIGN Phase 1.2 — the
   ~2000-game ai-vs-ai answer-key generation run — has **not been executed**.
   The Java rollout support in `LlmSpikeFork.java` / `LlmSpikeGui.java` compiles
   clean (checkstyle included) and the seed-42 floor/floor canary reproduces its
   pinned outcome exactly, so the code is present and inert on the normal path —
   but "compiles and does not break the canary" is not "produces correct win
   probabilities." Nothing has ever run it in anger. Until that run happens,
   PUZ is measurable on **3** positions from the offline exhaustive lane
   (yield 0.4% of 807 mined candidates), which is not a benchmark.
10. **The exhaustive lane is narrow on purpose and narrower in practice.** It
    only adjudicates `lethal-now @ play` with an empty stack and a single
    face-lethal burn spell. Over 807 candidates it rejected 156 for a non-empty
    stack, 56 as count-encoded, and 38 for having no lethal option. Every other
    category (`mulligan`, `save-vs-lethal`, `graded-midgame`, `free-win`,
    `forced-block`) is rollout-only or out of v1 scope, so today they contribute
    **zero** verified positions.
11. **DQ has never been measured at all.** Every blunder corpus in this repo is
    textlane-only (`rollout=0`). `punt.rate_textlane` is real evidence but it is
    a different quantity from the rollout punt rate, and the two must never be
    added. Flagger leakage is likewise unmeasured, not zero.
12. **PLAN cannot see the mirror-deck corpora.** Plan milestones need a resolved
    deck archetype; the `Identity-A`/`Identity-B` runs (most of `runs/`) resolve
    to nothing, so PLAN is measurable today only on `runs/stage0-calibration`
    (ur-delver / burn). A PLAN profile from one deck is not a plan-adherence
    score for a model.
13. **No live model has ever been scored on PUZ — still true, for a different
    reason.** The v1.0 auth gap (`_post_chat` sent no `Authorization` header)
    is closed: `replay_match` and `puzzle_run` now resolve `OPENROUTER_API_KEY`
    and send it as a Bearer token on the gated path, and a `bedrock` backend
    exists (`eval/bedrock.py`, stdlib SigV4). But **no live OpenRouter or
    Bedrock call has ever been made** through this harness (consent never
    granted, by design). What is proven: the SigV4 math (AWS known-answer
    vectors), the wire shape, retry/redirect/secret handling (localhost mock).
    What is NOT proven: account permissions (`bedrock:InvokeModel`), that the
    Bedrock model ids exist in the target region (they come from the brief,
    unverified; cross-region `us.anthropic.*` profile ids also resolve in the
    cfg table but are unexercised), and that live responses match the parsed
    shape. The only backend with real end-to-end evidence remains `offline`.
    Two further honesty notes: Bedrock thinking rows (opus/sonnet) omit
    `temperature` (the API rejects `temperature != 1` with thinking on), so a
    per-model determinism claim is false for those rows; and
    `puzzle_run --backend bedrock` silently ignores
    `--max-tokens`/`--req-timeout`/`--retries` — the `ModelCfg` row wins and
    is stamped into `summary.model_cfg`.
14. **Cross-axis comparability is asserted, not enforced.** The scorecard warns
    on `contract_sha` drift and mixed `engine_sha`/`overlay_sha`, but nothing
    stops you from passing a PUZ run from one build and a PLAN run from another
    and reading the result as one model's profile. Read the provenance caveats;
    they are not decoration. (The byte-reproducibility gap noted here in v1.0
    is closed: `plan_adherence.py` and `blunder_report.py` now take
    `--generated-utc` like `puzzle_run.py`/`build_report.py`, and their
    cluster-bootstrap inputs are key-sorted, so equal inputs give
    byte-identical summaries — enforced by `test_determinism_byte_identical`
    in both test files.)

---

## 7. Tests

21 self-tests, all stdlib, no network, no engine (`test_bedrock.py` is new —
its SigV4 known-answer vectors and egress/redirect/secret checks run against
localhost or pure fixtures only; the AWS example key `AKIDEXAMPLE` is a
published test vector, not a credential):

```
for t in eval/test_*.py; do python3 "$t" >/dev/null || echo "FAIL $t"; done
```

`eval/test_build_report.py` is new with this integration and its docstring
lists, per test, exactly what edit would make it fail. Four of those were
falsified live during integration (rendering a missing axis as 0.0; stripping
floors from metric rows; removing the `partial` demotion; un-flattening
`_collect`) and each broke the intended test and only that test.
