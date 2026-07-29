# eval/ — batch driver + statistics for the LLM-vs-LLM Forge eval

All tools here are stdlib-only Python 3. The v1.0 five-axis benchmark layer
(PUZ / PLAN / DQ / PUNT axes + the scorecard) is documented in its own
section below and, end-to-end, in [`EVAL-V1.md`](EVAL-V1.md).

Two stdlib-only Python 3 scripts:

- `run_batch.py` — plays N headless games by invoking `bash run-llm-headless.sh`
  from FORGE_HOME with per-game env (`GAME_SEED`, `SWAP_SEATS`, `EVAL_OUT`,
  `GAME_CAP_MS`, per-seat models/controllers/decks). Resumable. Deck paths are
  absolutised and existence-checked up front (`resolve_deck_paths`) in BOTH
  cited and exploratory modes — games run with `cwd=$FORGE_HOME`, so a
  repo-relative deck path would otherwise resolve against the forge checkout
  and crash every game in the batch (guards that hold in both modes refuse via
  `_refuse_always`, which never suggests `--exploratory` as a bypass).
- `aggregate.py` — turns a directory of `game-*.jsonl` logs into win rates
  with Wilson 95% CIs, a paired analysis with a paired-bootstrap CI on the
  win-rate delta, and decision-level latency/fallback stats. `--json` for
  machine-readable output.
- `test_aggregate.py` — self-test over synthetic fixtures
  (`python3 eval/test_aggregate.py`).

## The paired-mirror design

Each seed is played **twice with the seats swapped** (`--pairs`):

- same `GAME_SEED` → same deck shuffles and coin flip in both games
- same deck on both seats (mirror), so deck strength cancels
- `SWAP_SEATS=1` on the second game, so each arm plays both the
  advantaged and disadvantaged seat of the *same* shuffle

The two games of a seed share almost all their randomness, so the
difference in outcomes within a pair is nearly pure arm skill. That
correlation is why `aggregate.py` bootstraps **at the seed level** (pairs
travel together in each resample) rather than resampling individual games —
resampling games independently would overstate the variance and produce a
CI wider than the design actually earns.

`aggregate.py` also prints a **seat-bias check** — the seat-1 (first
player) win rate, computed as wins-by-identity-A among `swap=false` games
plus wins-by-identity-B among `swap=true` games, over all decisive games.
In a swapped mirror it should hover near 50% even when one arm dominates
(the dominant arm wins from both seats equally often); a big skew means
seat order matters a lot or `SWAP_SEATS` isn't working.

## Why draws and invalids are excluded

Win rate answers "when somebody won, who was it?". A draw carries no
information about relative strength, and an *invalid* game (wall-clock cap
hit, crash, JSONL with no final game line) is a measurement failure, not an
outcome — folding either into the denominator would drag every arm toward
zero and make runs with different crash rates incomparable. Both are
counted and reported separately; a high invalid rate is a harness problem
to fix, not a result. A pair is dropped from the paired analysis if either
of its games is non-decisive (the decisive half still counts in the
unpaired table).

## Power math (why you need a lot of games)

Detecting a 10-point gap (55% vs 45%, or 60% vs 50%) with a two-sided
α = 0.05 test at 80% power needs roughly **390 games per arm** unpaired
(standard two-proportion sample size at p ≈ 0.5, where variance is
maximal). Rules of thumb at 80% power:

| true gap | unpaired games/arm |
|---------:|-------------------:|
| 20 pts   | ~97                |
| 10 pts   | ~390               |
| 5 pts    | ~1,560             |

Pairing shrinks this substantially: if outcomes within a seed-pair
correlate with ρ (shared shuffle + mirror deck removes most game-to-game
luck), the paired comparison needs roughly a factor (1 − ρ) fewer games.
With even a modest ρ ≈ 0.5 the 10-point gap needs ~200 games/arm instead
of ~390. The paired-bootstrap CI printed by `aggregate.py` measures the
realized variance directly, so you don't have to guess ρ — run a batch,
look at the CI width, and extrapolate (CI width shrinks ~1/√n in pairs).

Practical upshot: 20–50 paired seeds tells you about big gaps (random vs
llm); separating two decent models by a few points needs hundreds of pairs.

## Cited vs exploratory runs

`run_batch.py` defaults to **cited** mode — the only mode whose numbers may be
quoted. Before launching anything (and before any directory is created) it
enforces:

1. **Mirror deck** — `DECK_A` and `DECK_B` must resolve to the *same*
   non-empty file (pass `--deck PATH` to set both), and the two identity names
   must differ (`--name-a`/`--name-b`, default `Identity-A`/`Identity-B`). A
   mirror cancels deck strength so the delta is pure pilot skill.
2. **Confounds off** — forces `MULLIGAN_HEURISTIC=0`, `SKIP_HEURISTICS=0`, and
   empty `PLAN_A`/`PLAN_B` (plans inject expert knowledge). Overriding any of
   these via `--env`, or passing `--plan-a/--plan-b`, is refused.
3. **`--jobs 1`** — parallel JVMs starve the `claude` CLI and inflate fallbacks.
4. **Per-tier cap** — if `--cap-minutes` is omitted it is derived: no llm seat
   → 15 min, all-haiku llm → 120, any opus → 240.
5. **Provenance + drift** — stamps `ENGINE_SHA` (forge `HEAD`), `OVERLAY_SHA`
   (sha256[:12] of the two *installed* spike files), `DECK_SHA`, and
   `RUN_DISCIPLINE` onto every game line, and refuses to run if the installed
   spike files have drifted from this checkout's `forge-overlay/` copies
   (`run bash install.sh first`).
6. **`--out` optional** — defaults to `runs/<UTC-timestamp>-<label>` under the
   repo root; the resolved dir is printed first thing.

Any violation exits **2** with a clear message. `--exploratory` skips guards
1–3 for quick pokes: it still stamps provenance (`discipline=exploratory`) but
prints a loud banner that the numbers are **not citable**.

The canonical mirror decks live in this repo:
`eval/decks/mardu-energy.txt` and `eval/decks/grixis-reanimator.txt` — pass one
of them to `--deck` for a citable mirror run.

## Example: cited haiku-tier mirror run

```bash
# 20 seeds x 2 seat orders = 40 games, one JVM at a time. --out is derived,
# the cap is derived (120 min for all-haiku), provenance is stamped.
python3 eval/run_batch.py \
    --seeds 1..20 --pairs \
    --deck eval/decks/mardu-energy.txt \
    --controller-a llm --controller-b llm \
    --smart-a haiku --smart-b haiku \
    --fast-a haiku --fast-b haiku

# Interrupted? Re-run the same command with the same --out — completed
# seed/swap combos are skipped (a combo is done once its JSONL has a game line).

python3 eval/aggregate.py runs/<the-printed-dir>            # human-readable
python3 eval/aggregate.py runs/<the-printed-dir> --json     # machine-readable
```

`aggregate.py` prints a **provenance block** (and a loud MIXED-PROVENANCE
warning if the dir blends incompatible runs), and enforces a **fallback gate**:
`--max-fallback-rate` (default 0.10) over decisions of llm-controlled
identities; exceeding it prints `FAILED FALLBACK GATE` and exits **2** after the
report. The `ai` tier count is **partial** (un-overridden decisions run on
Forge AI invisibly), so `ai=0` never means "the model drove everything".

Baseline / exploratory arm example (not citable — mismatched decks, plans):

```bash
python3 eval/run_batch.py --seeds 1..30 --pairs --exploratory \
    --deck-a eval/decks/grixis-reanimator.txt --deck-b eval/decks/mardu-energy.txt \
    --controller-a llm --smart-a haiku --controller-b random
```

## The JSONL contract (identity-keyed, final)

Ground truth is `LlmSpikeHeadless.java`; the game line is keyed by
**deck identity**, never by seat:

- `winner: "A"` means **deck-identity A** — the `DECK_A` / `DECK_A_NAME` /
  `CONTROLLER_A` / `SMART_MODEL_A` configuration — won, **regardless of
  seat swap**. The Java side maps by player name
  (`nameA.equals(winningLobbyPlayer.getName())`), so identity A is always
  arm A and `aggregate.py` never flips attribution on `swap`.
- `controller_a`, `smart_a`, `fast_a`, … always describe identity A's
  (resp. B's) **raw env config**, never post-swap seats.
- `swap: true` is pairing/seat metadata only: it records that identity B
  sat in seat 1 (went first into the players list); `swap: false` means
  identity A did. `aggregate.py` uses it for pairing the two games of a
  seed and for the seat-1 bias diagnostic — nothing else.
- One game per file is the contract, but `aggregate.py` tolerates two games
  interleaved into one file (same-millisecond parallel launches) and
  dedupes reruns of the same seed/swap combo by keeping the newest.
- A `.jsonl` file with no game line is a crashed game and counts as
  `invalid`. Malformed lines are skipped with a warning.
- **Resolved model identity** (additive, 2026-07-26). `smart_a` records the
  *configured spec*, which is not an identity: `openrouter:default_model`
  against a local server names no checkpoint, which is why
  `runs/stage1-student-eval` and `runs/stage2-student-eval` carry identical
  provenance despite being different adapters. `served_smart_a` /
  `served_fast_a` (and `_b`) record what the **endpoint said answered**, read
  from the response's own `model` field; `openrouter_base_url`,
  `checkpoint_sha` (from `--checkpoint`), `model_cfg_sha`, `temperature_a/b`
  and `seed_bank` complete the picture. A field the endpoint did not supply is
  `"unknown"` — never a guess inherited from the alias. `aggregate.py` prints
  a **model identity** block and exits **3** when a run stamped
  `discipline=cited` cannot name what played. Dirs written before these fields
  existed report `(pre-provenance)` and are excused, not failed.

## Frozen suites + the holdout registry

A **suite** (`eval/suites/*.json`) is the frozen artifact a checkpoint gets
pointed at: declared eval-only seed bank, mirror decks, opponent ladder, and
gates — the deck/opponent half of "the eval is a versioned module". Numbers
are citable only when they come from a validated suite run.

- `eval/holdouts.json` — the deck registry. Every eval deck is
  `in-corpus` (appears in training data; evals on it are in-distribution
  only) or `held-out` (must never appear in a new training dataset).
  Held-out decks carry a `grandfathered` list: contamination that predates
  the declaration is frozen and labeled, not hidden — so a held-out deck is
  a valid generalization measure only for models trained on corpora built
  after its declaration date.
- `eval/check_holdouts.py` — audits `datasets/*/meta.jsonl` against the
  registry: prints deck presence per dataset/split and derivable matchups,
  warns on meta-less dataset dirs (invisible to the audit), and exits **1**
  if a held-out deck leaks into a non-grandfathered dataset. Run it after
  every corpus build.
- `eval/seed_registry.json` + `eval/seed_registry.py` — the seed-side
  equivalent, and the artifact `stage3-v1.json`'s `seed_policy` deferred to.
  Every seed band is declared `eval-only`, `train`, or `harness`; a band may
  also list `burned` seeds (harvested *before* the band was declared — the
  seed analogue of a deck's `grandfathered` list). `check` exits **1** if an
  un-burned eval-only seed appears in `datasets/*/meta.jsonl`, if a corpus is
  unreadable, or if a frozen suite arm draws on burned/training seeds.
  `audit` retroactively labels every dir in `runs/` with how contaminated it
  was. `run_batch.py` refuses a cited batch on both counts: seeds outside an
  eval-only band, and seeds already present in a corpus.
- `eval/run_suite.py` — validates a suite against the registry and the
  `run_batch.py` CLI contract (role/deck-status consistency, disjoint
  in-bank seed ranges, cited discipline, gates), then prints the exact
  per-arm commands. Exit **2** on any violation. It launches nothing.

- `eval/build_report.py` — the report builder. Points at the per-arm output
  dirs (default `runs/<suite>-<arm>`, overridable with `--arm-dir`), shells
  out to `aggregate.py --json` / `defect_analyzer.py` / `check_holdouts.py`
  (no statistics are recomputed — those tools stay the single source), and
  writes a frozen report folder: `suite.json` copy, `report.json`,
  `report.md`, and per-arm defect evidence. Verdicts: **PASS** (every arm
  present, all gates green) / **FAIL** (a gate broke — exit 1) /
  **INCOMPLETE** (an arm has no usable data — exit 3). Per-arm gates:
  fallback rate, defect invariants, `discipline=cited`, unmixed provenance.
  One report per checkpoint; never quote arms added after the fact.

```bash
python3 eval/seed_registry.py check                     # seed contamination gate
python3 eval/seed_registry.py audit                     # label past runs
python3 eval/check_holdouts.py                          # corpus audit
python3 eval/run_suite.py eval/suites/stage3-v1.json \
    --student openrouter:default_model                  # validate + plan
# ... run the printed run_batch.py commands ...
python3 eval/build_report.py eval/suites/stage3-v1.json \
    --student openrouter:default_model                  # frozen report
```

`eval/suites/stage3-v1.json` is the first suite: two cheap sanity arms
(floor, random), two 200-pair in-distribution headline arms vs Forge AI
(ur-delver, burn), one 200-pair generalization arm (painter, held-out
2026-07-26), and a disabled teacher-mirror arm pending a frozen teacher
spec. All arms are mirror-deck so they run under cited discipline;
asymmetric student-deck-vs-AI-deck matchups need a `cited-nomirror` mode in
`run_batch.py` (both deck SHAs pinned) and are deliberately not in v1.

## Improvement loop

Two more stdlib-only scripts close the loop between "harness has a defect"
and "harness is fixed, verify it stayed fixed":

- `improve_loop.py` — plays a small, cheap batch (default: 2 games,
  `--controller floor` — deterministic legal-fallback controller, free, no
  API key needed) by shelling out to `run_batch.py` in `--exploratory` mode,
  then runs `defect_analyzer.py` over the fresh output directory and writes
  `metrics.json` + `summary.txt` next to the game files.
- `defect_analyzer.py` — reads a directory of `game-*.jsonl` / `game-*.log`
  pairs and reports: fallback counts by kind, the `tier=ai` hard invariant
  (C1 — sacrifice/combat-damage delegated to Forge AI, MUST be 0), silent
  decision-drop reconciliation (C5 — counts each log-side `-> asking` window
  against its matching jsonl row over mulligan/attackers/blockers/play and
  flags any gap; this is exactly the bridge-down→PASS branch that skips
  `evalDec()`), no-CHOICE/parse-fail counts by seat, latency percentiles by
  tier/seat, `finish_reason=length` counts, forced single-legal picks
  mislabeled `tier!=auto` (C6), and per-seat mulligan-row coverage (C7 —
  `MULLIGAN_HEURISTIC` auto-keeps that never call `evalDec()`). Exits 1 if
  `tier=ai` count > 0 or any silent-drop gap > 0 anywhere in the corpus.
- `test_defect_analyzer.py` — synthetic-fixture self-test
  (`python3 eval/test_defect_analyzer.py`).

### The iterate cycle

```bash
# 1. Run one iteration (floor mode: free, deterministic, no API key).
python3 eval/improve_loop.py --games 2 --controller floor
# prints the output dir, e.g. runs/improve/20260721-120000/, then writes
# runs/improve/20260721-120000/{metrics.json,summary.txt}

# 2. Read the metrics: any hard-invariant failure (tier=ai>0, silent drops>0)
# exits 1 and is spelled out in summary.txt with file/seat/turn detail.
cat runs/improve/20260721-120000/summary.txt

# 3. Fix the harness (forge-overlay/.../LlmSpikeGui.java), citing the exact
# defect class the summary flagged.

# 4. Rebuild (see the repo-root build/deploy cycle: copy the overlay spike
# files into the forge checkout, then `mvn -pl forge-gui-desktop -am
# -DskipTests compile`).

# 5. Rerun the same iteration and diff the two summary.txt files — a real
# fix drives the flagged count (or the whole hard-invariant gate) to zero;
# an unrelated regression shows up as a NEW count that wasn't there before.
python3 eval/improve_loop.py --games 2 --controller floor
```

`--controller llm` runs real model calls (`--seat-a`/`--seat-b`, default
`openrouter:google/gemini-3.5-flash` both — cheap) and refuses with a clear
message if `OPENROUTER_API_KEY` is unset; `--dry-run` prints the exact
`run_batch.py`/`defect_analyzer.py` commands it would run without launching
anything, useful for checking flags before spending API budget or JVM time.
`defect_analyzer.py` is also runnable standalone against any existing batch
(including `runs/tournament-1/match-*/`) for one-off defect audits outside
the loop.

## v1.0 eval axes (PUZ / PLAN / DQ / PUNT + the scorecard)

Five new stdlib-only modules landed 2026-07 and compose, via
`build_report.py`, into one scorecard over five axes: **STR** (match
strength, the existing `aggregate.py` arms), **PUZ** (puzzle accuracy),
**PLAN** (plan adherence), **DQ** (decision quality) and **PUNT** (blunder
rate). Full walkthrough with the real smoke transcript: [`EVAL-V1.md`](EVAL-V1.md).

**Maturity, stated plainly: this is a working harness, not yet a working
benchmark.** The answer-key generation run (EVAL-DESIGN Phase 1.2, ~2000
ai-vs-ai rollout games) has **not** been executed. The whole repo currently
yields **3 verified puzzle positions** (0.4% of 807 mined candidates), all
from the offline exhaustive lane. DQ has never been measured (every blunder
corpus is textlane-only, so `dq_wpl_mean` has n=0 everywhere). PLAN resolves a
deck on exactly one corpus (`runs/stage0-calibration`). The only PUZ backend
with end-to-end evidence is `offline`: the `openrouter` auth gap is now closed
(a Bearer token is resolved and sent — see AUTH below) and a `bedrock` backend
exists, but **no live frontier model has ever been scored end-to-end** — no
live OpenRouter or Bedrock call has been made through this harness. Until
Phase 1.2 runs, PUZ cannot rank anything.

### `prompt_contract.py` — the frozen prompt/reply contract

Not a metric: the versioned, content-hashed render+parse contract every axis
stamps its artifacts with, so numbers from different runs are comparable only
when their `contract_sha` matches. `--manifest` prints the identity
(currently `prompt-contract.v1`, `contract_sha abaddd69db49a9d2`, parser
`parser.v1.1`, frozen against Java sha `24fcfabcd2a71c25`);
`--render POSITIONS_JSONL [--index N] [--strictness strict|lenient]
[--profile PROFILE]` renders one row exactly as a backend would see it.
`build_report.py` compares `contract_sha_running` against every input's
stamped sha and raises a CONTRACT DRIFT caveat on mismatch.

```bash
python3 eval/prompt_contract.py --manifest
python3 eval/test_prompt_contract.py
```

### `puzzle_run.py` — the PUZ axis runner

Serves verified puzzle positions to a backend k times each, parses replies
through the frozen contract, scores against engine-verified answer keys with
partial credit (EVAL-DESIGN §3.3), and emits `results.jsonl` + `summary.json`.

```bash
python3 eval/puzzle_run.py POSITIONS.jsonl --out runs/puz/<name> \
    [--backend offline|http|openrouter|bedrock] [--offline-policy always-pass|random|first|oracle|garbage] \
    [--offline-fixture FILE.jsonl] [--endpoint http://localhost:8080/v1] [--model NAME] \
    [--region us-east-2] [--no-api-key] \
    [--k 4] [--strictness strict|lenient] [--split open|holdout|all] \
    [--max-positions N] [--sample-seed N] [--generated-utc STAMP] [--json] [--dry-run]
```

- **Inputs:** `puzzle-position.v1` jsonl with verified answer keys (from
  `verify_keys.py`). Unverified/malformed/unscoreable rows are counted in
  named skip buckets, never silently dropped.
- **Outputs:** `results.jsonl` (`puzzle-result.v1`) + `summary.json` — score
  mean (cluster-bootstrapped by `source.game_file`), `pass_1`/`pass_k`,
  `interface_failure_rate`, `transport_error_rate`.
- **Backends:** `offline` (default, zero network — fixture replies or a
  synthesized policy), `http` (OpenAI-compatible; non-local hosts refuse with
  exit 2 unless `OPENROUTER_ALLOW_EGRESS=1`), `openrouter` (hard-refuses
  without that flag), `bedrock` (AWS Bedrock via `eval/bedrock.py`; same
  single consent flag, hard-refuses without it; needs an explicit `--model`).
- **AUTH:** `openrouter` / a remote `http` endpoint require
  `OPENROUTER_API_KEY` (environment, then `<repo>/secrets.env` — read without
  mutating `os.environ`). The key is checked AFTER the pure egress gate but
  BEFORE any request, so a missing key exits 1 without spending the consent on
  a run that can only 401. It is sent as a Bearer token on the gated path
  only — never to a localhost endpoint, never into `results.jsonl` /
  `summary.json` / a log line (the summary records only `api_key_present`).
  `--no-api-key` opts a remote endpoint out of the requirement.
- **Bedrock specifics:** `--backend bedrock` takes its per-model
  max_tokens / thinking budget / timeout / retries from
  `bedrock.BEDROCK_MODEL_CFG` (env-overridable), **NOT** from
  `--max-tokens`/`--req-timeout`/`--retries` (those flags are silently
  ignored on this backend); the row used is stamped into `summary.model_cfg`
  with `model_cfg_sha` (additive fields, plus `api_key_present`). A run at a
  different thinking budget is a different measurement — quote the stamped
  cfg with the number. `--region` picks the AWS region (default `AWS_REGION`
  / `~/.aws/config` / `us-east-2`).
- **Trivial floors:** computed analytically from the same answer keys over
  the same offered menu — `random_choice` (+ pass_1/pass_k twins),
  `always_pass`, `majority_class`, and the `oracle_ceiling`; every headline
  metric carries `margin_over_*` fields. Interface failures score 0 in the
  inclusive variant and are excluded from the exclusive denominator — and
  `puz.score_excl` is a selective-refusal channel, so the scorecard forces it
  non-headline and gates it on `interface_failure_rate <= 0.10`.
- **Exit codes:** 0 OK; 2 usage/egress refusal/zero scoreable positions;
  3 INCOMPLETE (run happened, backend delivered no usable data — never a pass).

```bash
python3 eval/test_puzzle_run.py
```

### `bedrock.py` — AWS Bedrock backend (stdlib SigV4)

Anthropic Messages over AWS Bedrock with hand-rolled SigV4 signing
(`hmac`/`hashlib`/`urllib` — eval/ may not import boto3 or the anthropic SDK).
Used by `puzzle_run.py --backend bedrock`; also a standalone CLI:

```bash
python3 eval/bedrock.py --print-cfg [MODEL]   # show the resolved per-model table
python3 eval/bedrock.py --whoami              # credential SOURCE + akid prefix only
python3 eval/bedrock.py --model anthropic.claude-opus-5 --prompt "hi" \
    [--region us-east-2] [--system TEXT] [--dry-run] [--json]
```

- **Egress:** hard-refuses (exit 2) unless `OPENROUTER_ALLOW_EGRESS=1` — the
  SAME single consent switch as OpenRouter, deliberately not a second flag, so
  there is exactly one thing to audit. Holding AWS credentials is not consent.
  The gate is enforced twice (`invoke()` and `send_signed()` for any
  non-localhost host), and redirects are re-checked with `Authorization`
  stripped on any cross-host redirect.
- **Credentials:** env (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
  `AWS_SESSION_TOKEN`) → `~/.aws/credentials` → `~/.aws/config`, honouring
  `AWS_PROFILE`; assume-role/SSO/credential_process fail loudly rather than
  half-work. Secrets never appear in output: every error string is scrubbed,
  `--dry-run` redacts `Authorization` and scrubs the canonical request,
  `--whoami` prints source + key-id prefix only.
- **Per-model config** (`BEDROCK_MODEL_CFG`, ordered substring match + a
  `default` row, env-JSON override, `model_cfg_sha` provenance): opus/sonnet
  rows get `max_tokens 8000` with `thinking_budget_tokens 4000` (timeouts
  300s/240s, temperature omitted — the API rejects `temperature != 1` with
  thinking on, so thinking rows are non-deterministic); haiku gets 2000 / no
  thinking / 120s / temperature 0.0 (the cheap control arm); `default` is
  conservative and non-thinking (enabling thinking on a model without it is a
  hard 400). The thinking budget is carved OUT of `max_tokens`, and
  `validate()` rejects any row leaving <1024 visible-answer tokens — the
  hidden-reasoning-burns-the-cap → empty-reply failure is unrepresentable,
  including via env override. Retries (2) fire only on 429/5xx/transport
  errors, never on a 4xx validation error.
- **Evidence and its limits:** SigV4 is asserted against the AWS-published
  `aws-sig-v4-test-suite` known-answer vectors (canonical request,
  string-to-sign, and final signature separately); retry/redirect/secret
  handling is exercised against a localhost mock. **No live Bedrock call has
  ever been made** — bedrock:InvokeModel permission, model-id availability in
  the region, and the live response shape are all unproven. First real run
  should be the cheapest arm:
  `OPENROUTER_ALLOW_EGRESS=1 python3 eval/bedrock.py --model anthropic.claude-haiku-4-5 --prompt 'reply with the word OK' --json`.
- **Exit codes:** 0 OK; 1 transport/API error; 2 usage error or egress refusal.

```bash
python3 eval/test_bedrock.py
```

### `verify_keys.py` — candidate → verified answer key promotion

Turns `mine_puzzles.py` candidates into engine-adjudicated answer keys, or
records exactly why it couldn't (`unverifiable_reason`, additive). Two lanes:
**exhaustive** (pure, no engine: single-burn-spell face lethal, verified iff
at least one option scores 10 and one scores 0) and **rollout**
(play/mulligan only; per-option win probability with a gap gate
`wp_max - wp_min >= max(threshold, 2*max(se))`, so statistically
indistinguishable rollouts refuse to mint a key). Not a metric — its summary
reports yield per lane and per unverifiable_reason. A narrow honest key set
beats a wide fake one: current whole-repo yield is 3 verified / 807 mined.

```bash
python3 eval/verify_keys.py CANDIDATES.jsonl --out POSITIONS.jsonl \
    [--lane exhaustive|rollout|both] [--driver engine|fixture|none] \
    [--fixture FILE.jsonl] [--n-per-option 16] [--forge-home ~/code/forge] \
    [--determinize library|none] \
    [--max-positions N] [--summary summary.json] [--json] [--dry-run]
```

Default `--driver none` is pure exhaustive: zero engine, zero network,
runnable without a Forge checkout (keys minted that way carry the
`unprobed_replay_address` caveat). Exit 3 = INCOMPLETE when an engine driver
was requested but unreachable.

**`--determinize` (default `library`) is load-bearing — do not set it to
`none` to mint keys.** The rollout continuation re-deals every player's library
order at the fork point so the replays actually diverge. Without it they do
not: measured across 6 positions and 18 option-arms, at every `n`, every arm
returned exactly `0/n` or `n/n`, because reseeding `MyRandom` mid-game cannot
re-deal a library fixed at game start. `none` reproduces that defect and exists
only as evidence. The two modes stamp mutually-exclusive caveats
(`determinized_library_hidden_info` vs `single_deal_hidden_info`) and their WPs
are **not comparable**; the engine refuses any other value rather than
defaulting, so a typo cannot silently disable it.

```bash
python3 eval/test_verify_keys.py
```

### `plan_adherence.py` — the PLAN axis

Milestone attainment per deck archetype, generalizing the UR-Delver triple in
`process_metrics.py` to all 16 archetypes via data-driven specs
(`eval/plans/<slug>.milestones.json`, schema `milestones.v1`). All extractors
are text/regex over the logged prompts — no judge model, no engine.

```bash
python3 eval/plan_adherence.py DIR [DIR ...] --out runs/plan/<name> \
    [--decks slug,slug] [--specs eval/plans] [--min-games 1] [--json] [--strict-specs] \
    [--generated-utc STAMP]
```

- **Inputs:** run dirs scanned recursively for `game-*.jsonl`. The seat's
  archetype is resolved from plan text (`deck_a`/`deck_b` carry MODEL names
  in tournament runs and are never trusted); unresolvable seats are counted
  (`n_seats_unresolved`), never guessed. **Today only
  `runs/stage0-calibration` (ur-delver/burn) resolves** — the Identity-A/B
  mirror runs that fill most of `runs/` render PLAN as `not measured`. A
  one-deck profile is not a plan-adherence score.
- **Outputs:** `milestones.jsonl` + `summary.json`. PLAN-attain is reported
  as a per-deck **profile vector**, never collapsed to one number.
- **Trivial floors:** `never_attained` 0.0 floor / `always_attained` 1.0
  ceiling per milestone with `margin_over_never_attained`; rates carry the
  take-it-always/take-it-never endpoints; `target_split` carries the
  uniform-random face floor.
- **Exit codes:** 0 OK; 2 usage error / invalid specs under `--strict-specs`
  / nothing usable.
- **Reproducibility:** cluster-bootstrap inputs are key-sorted at the point of
  use (the `puzzle_run` `_clusters()` pattern), so summaries no longer depend
  on dict/scan order; with `--generated-utc STAMP` two runs over the same
  corpus are byte-identical (enforced by `test_determinism_byte_identical`).

```bash
python3 eval/test_plan_adherence.py
```

### `blunder_flags.py` + `blunder_report.py` — the DQ and PUNT axes

`blunder_flags.py` is a library (no CLI): pure text red-flag rules over
already-played games in three lanes — 11 text rules, shadow-disagreement
(**vacuous today**: zero rows in `runs/` carry `shadow_choice`), and a
deterministic 5% audit sample that measures flagger leakage. Flags are
triggers for adjudication, never verdicts.

`blunder_report.py` consumes decision corpora, flags, adjudicates, and emits
`blunder-report.v1` rows + a summary:

```bash
python3 eval/blunder_report.py DIR [DIR ...] --out runs/blunder/<name> \
    [--model NAME] [--audit-rate 0.05] [--sample-seed N] \
    [--adjudicate none|textlane|rollout] [--driver engine|fixture|none] \
    [--fixture FILE.jsonl] [--n-per-option 16] [--max-adjudications N] [--json] \
    [--generated-utc STAMP]
```

- **Adjudication methods:** `rollout` (wpl = wp_best − wp_chosen; classes per
  EVAL-DESIGN §4.2, gated on `wpl > 2*wpl_se` so rollout noise never becomes
  a blunder count), `textlane-verified` (the honest v1 path for
  non-replayable LLM games — counted ONLY in `punt_rate_textlane`, **never**
  added to the rollout `punt_rate`; these are different quantities), and
  `unadjudicable` (with reason).
- **Honesty invariants:** an unmeasured audit lane reports
  `flaggers_leaky=None`, never a fake clean; **DQ has never been measured** —
  every corpus in the repo is textlane-only, so `dq_wpl_mean` has n=0
  everywhere. `punt_rate_textlane` (0.011, n=1606 on the current corpus) is
  real evidence but of a different quantity.
- **Exit codes:** 0 OK; 2 usage/nothing usable; 3 INCOMPLETE (`--adjudicate
  rollout` requested but no driver available).
- **Reproducibility:** `build_summary`'s per-game aggregates iterate in sorted
  game order, and `--generated-utc STAMP` overrides the timestamp, so equal
  inputs give byte-identical summaries.

```bash
python3 eval/test_blunder_flags.py
python3 eval/test_blunder_report.py
```

### `build_report.py` — the v1.0 scorecard (extended)

The suite-report builder described above now also assembles the five-axis
scorecard. The `suite` positional is optional; new flags:

```bash
# full scorecard, all five axes
python3 eval/build_report.py eval/suites/stage3-v1.json \
    --student openrouter:default_model \
    --puz-run runs/puz/latest --plan-run runs/plan/latest \
    --blunder-run runs/blunder/latest [--generated-utc STAMP] [--out DIR] [--json]

# scorecard with no STR suite at all (STR renders "not measured")
python3 eval/build_report.py --puz-run runs/puz/latest
```

Emits `scorecard.json` (schema `eval-scorecard.v1`) alongside
`report.json`/`report.md`. Every measured row carries
`est/se/ci95/n/n_clusters/method/denominator` plus a `floors[]` list with a
`margin` per floor; no statistic is recomputed here (STR se is the
normal-approximation binomial, labeled as such — do not reconstruct a Wilson
interval from it). Axes are `measured`, `partial` (input present but every
headline metric has n=0 — true today for PUNT on textlane-only corpora), or
`not_measured` (zero metric rows + a `reason` + the verbatim command that
would measure it — **never rendered as 0**). An unmeasured/partial axis can
demote PASS to INCOMPLETE (exit 3) but can never read as PASS. The provenance
header compares `contract_sha_running` / `engine_sha` / `overlay_sha` /
`seed_bank` across inputs and raises CONTRACT DRIFT, MIXED-provenance,
SELECTIVE REFUSAL and POSITIONAL SKEW caveats. Note the caveats warn but do
not *prevent* mixing runs from different builds into one scorecard — read the
header before quoting.

```bash
python3 eval/test_build_report.py
```
