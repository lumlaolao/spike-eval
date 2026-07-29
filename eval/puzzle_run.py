#!/usr/bin/env python3
"""puzzle_run.py — execute the PUZ axis (EVAL-DESIGN.md §3): serve verified
puzzle positions to a backend k times each, parse the replies through the
frozen prompt contract, score against the engine-verified answer keys with
partial credit (§3.3), and emit results.jsonl + summary.json with baselines
and clustered uncertainty.

Usage:
  python3 eval/puzzle_run.py POSITIONS.jsonl --out RUNDIR
      [--backend offline|http|openrouter|bedrock]  (default offline; zero egress)
      [--offline-fixture FILE.jsonl] [--offline-policy always-pass|random|first|oracle|garbage]
      [--endpoint http://localhost:8080/v1] [--model NAME]
      [--k 4] [--max-tokens 512] [--req-timeout 120] [--retries 1] [--workers 4]
      [--strictness strict|lenient] [--categories a,b] [--split open|holdout|all]
      [--max-positions N] [--sample-seed 20260728] [--generated-utc STAMP]
      [--json] [--dry-run]

Backends:
  offline    (DEFAULT) no network at all. Replies come from --offline-fixture
             (jsonl of {"id","k","reply"}, k is 1-based) or are synthesized by
             --offline-policy. The policy generators double as the floor
             generators, so they must agree exactly with the analytic
             baselines (test_always_pass_matches_its_own_baseline enforces
             this).
  http       OpenAI-compatible endpoint. The egress predicate
             (replay_match.egress_allowed) runs FIRST, pure, before any
             network use; non-local hosts refuse with exit 2 unless
             OPENROUTER_ALLOW_EGRESS=1 is set in the environment.
  openrouter HARD-REFUSES with exit 2 unless OPENROUTER_ALLOW_EGRESS=1 is in
             the environment, checked before any URL is even constructed.
             This module never sets that variable; holding an API key is not
             consent.
  bedrock    AWS Bedrock via eval/bedrock.py (hand-rolled stdlib SigV4).
             HARD-REFUSES with exit 2 unless OPENROUTER_ALLOW_EGRESS=1 — the
             SAME single consent switch, deliberately not a second flag.
             Needs an explicit --model (e.g. anthropic.claude-opus-5); the
             per-model max_tokens / thinking budget / timeout / retries come
             from bedrock.BEDROCK_MODEL_CFG (overridable with the
             BEDROCK_MODEL_CFG env JSON), NOT from --max-tokens/--req-timeout,
             and the row that was used is stamped into summary.model_cfg.

AUTH: --backend openrouter / a remote --backend http endpoint require
OPENROUTER_API_KEY (environment, or <repo>/secrets.env). It is resolved and
checked BEFORE any request, so a missing key does not spend the egress
consent on a run that can only 401; it is sent as a Bearer token on the gated
path only, is never sent to a localhost endpoint, and never reaches
results.jsonl, summary.json, or a log line (summary records only
api_key_present).

Scoring (EVAL-DESIGN.md §3.3):
  * interface_failure (could not be turned into a legal action) is NEVER
    scored as a wrong Magic play: it scores 0 in the inclusive variant and is
    EXCLUDED from the exclusive denominator, and its rate is its own metric.
  * an in-range option scores answer_key.option_scores[option_index] (0..10).
  * position score = mean of its k sample scores; solved = score == 10;
    pass_1 = per-sample solved rate; pass_k = fraction of positions with all
    k samples solved (positions with < k usable samples are excluded and
    counted in pass_k_excluded).
  * transport errors / fixture misses are missing DATA, not model behavior:
    they are excluded from every scoring denominator and reported as
    transport_error_rate / missing_fixture_samples.
  * score means are cluster-bootstrapped by source.game_file (positions from
    one game correlate — EVAL-DESIGN.md §6.1); simple proportions use
    aggregate.wilson_ci. Every block states its method.

Baselines ("the 88% lesson" — a score without its floor is not a score) are
computed ANALYTICALLY from the same answer keys over the same positions:
random_choice (+ pass_1/pass_k twins), always_pass, majority_class (with the
token), oracle_ceiling; every headline metric carries margin_over_* fields.

  *** EVERY floor is defined over the SAME menu the model is offered — the
  1-based numbers in rendered["menu_numbers"] — via menu_score(), NOT over
  range(n_options). These differ: prompt_contract pins mulligan to
  menu_numbers==[1,2] whatever n_options says, so a floor derived from
  n_options would silently disagree with the policy that actually picks a
  menu number. One helper, one menu, no drift. ***
  *** Every floor's pass_1/pass_k twin uses the SAME success predicate as the
  measured metric (score == 10, i.e. `solved`). A floor scored on a different
  predicate (e.g. "the token is a maximal-scoring option") can exceed the
  achievable ceiling and make a PERFECT player show a negative margin — the
  exact failure the 88% lesson is about. The maximality fraction is still
  reported, as its own field majority_class.frac_positions_maximal, and is
  never used as a floor. ***

Skip buckets (each counted separately, never silently dropped):
  skipped_split / skipped_category — filtered out by --split / --categories
  skipped_unscoreable_type — decision_type not in
      prompt_contract.SCOREABLE_INDEX_TYPES ({"play","mulligan"}): per-option
      answer keys are only DEFINED for index-encoded types (contract §1.3),
      so count-encoded rows (attackers, discard, ...) cannot be served here.
      (Checked before the key checks: it is a structural property of the row.)
  skipped_unverified — status == "candidate" or no answer_key
  skipped_malformed_key — option_scores is not a list of len(n_options)
      numbers in [0, 10] (a bad upstream key is DATA, never a crash)
  skipped_render_error — prompt_contract.render raised ValueError
  skipped_sampled_out — deterministically sampled away by --max-positions

Exit codes (contract §8): 0 OK; 1 reserved for a declared threshold gate
(unused in v1); 2 usage error / egress refusal / bad input file / zero
scoreable positions; 3 INCOMPLETE — the run happened but the backend did not
deliver usable data (transport-error budget blown, or zero valid samples
because every reply was a transport error / fixture miss). 3 is NOT 0: a
summary whose denominators are empty must never read as a pass.

WHAT WOULD MAKE THIS FAIL
  * An off-by-one between menu numbers (1-based) and option indices (0-based)
    would score the wrong array slot — the oracle policy would stop scoring
    10.0 and test_offline_oracle_scores_ten fails (verified by mutation:
    indexing scores[(oi+1)%n] fails 10 checks).
  * Conflating interface failures with wrong plays would move
    puz_score_excl away from None/n=0 under the garbage policy (verified by
    mutation: returning score 0 for interface_failure fails 3 checks).
  * A drifted floor (generator vs analytic baseline disagreeing) breaks
    test_always_pass_matches_its_own_baseline and
    test_menu_score_is_the_pipeline_score.
  * A floor whose success predicate differs from the metric's breaks
    test_floors_never_exceed_the_oracle_ceiling.
  * Naive (unclustered) CIs would not widen when positions concentrate in one
    game_file — test_clustering_uses_game_file catches that (it asserts the
    metric block's own n_clusters, not just the summary count).
  * Any code path reaching a non-local host without OPENROUTER_ALLOW_EGRESS=1
    breaks test_openrouter_refuses / the egress predicate tests (verified by
    mutation: disabling the gate fails 3 checks).
  * Crashing on a malformed answer key or a missing fixture file instead of
    counting it breaks test_malformed_scores_are_skipped_not_crashed /
    test_missing_fixture_file_is_usage_error — a traceback is exit 1, which
    this tool reserves for a real gate verdict.
"""

import argparse
import hashlib
import heapq
import json
import math
import os
import random
import sys
import time
import urllib.error
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

import bedrock  # noqa: E402  (stdlib SigV4 AWS Bedrock backend)
import prompt_contract  # noqa: E402  (t1's frozen render+parse contract)
import replay_match  # noqa: E402
import seed_policy  # noqa: E402
from aggregate import wilson_ci  # noqa: E402

SCHEMA_RESULT = "puzzle-result.v1"
SCHEMA_SUMMARY = "puzzle-run-summary.v1"
DEFAULT_SAMPLE_SEED = 20260728
DEFAULT_K = 4
OFFLINE_POLICIES = ("always-pass", "random", "first", "oracle", "garbage")
GARBAGE_REPLY = "I would probably attack here."
OPENROUTER_DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"

# EVAL-DESIGN.md §3.5 — stated on every report, verbatim-ish.
ANSWER_KEY_LIMITS_CAVEAT = (
    "answer-key limits (EVAL-DESIGN.md 3.5): rollout-derived keys are "
    "single-deal, perfect-information continuations by the Forge AI; the "
    "continuation policy is weak at combo, so combo-line WPs are "
    "underestimated (combo-deck graded positions carry "
    "rollout_policy_caveat); the deal is fixed, so plays correct under "
    "hidden information can be scored as wrong — tactic categories are "
    "restricted to public-information-dominant states, graded categories "
    "carry the caveat."
)


# ===========================================================================
# STATISTICS — shared helpers (contract §5). cluster_bootstrap_ci is the
# byte-identical pasted copy mandated by the contract (integration hoists it
# into aggregate.py later; the pinned test in test_puzzle_run.py detects
# drift between the per-track copies).
# ===========================================================================

def cluster_bootstrap_ci(clusters, resamples=2000, seed=20260728):
    """Percentile bootstrap CI for a pooled mean, resampling CLUSTERS with
    replacement (EVAL-DESIGN.md S6.1: decision/position metrics cluster by
    game; naive SEs understate up to 3x).

    clusters: iterable of iterables of floats; each inner iterable is one
    cluster's observations. Returns {"est","se","ci95","n","n_clusters",
    "resamples","bootstrap_seed"}. est is the POOLED mean over all
    observations (not the mean of cluster means). se is the sd of the
    bootstrap distribution. <2 clusters -> se/ci95 None (an interval a
    single cluster cannot earn)."""
    clusters = [list(c) for c in clusters if c]
    n_clusters = len(clusters)
    n = sum(len(c) for c in clusters)
    if n == 0:
        return {"est": None, "se": None, "ci95": [None, None], "n": 0,
                "n_clusters": 0, "resamples": resamples,
                "bootstrap_seed": seed}
    est = sum(sum(c) for c in clusters) / n
    if n_clusters < 2:
        return {"est": est, "se": None, "ci95": [None, None], "n": n,
                "n_clusters": n_clusters, "resamples": resamples,
                "bootstrap_seed": seed}
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        tot = 0.0
        cnt = 0
        for _ in range(n_clusters):
            c = clusters[rng.randrange(n_clusters)]
            tot += sum(c)
            cnt += len(c)
        means.append(tot / cnt if cnt else 0.0)
    means.sort()
    mu = sum(means) / len(means)
    var = sum((m - mu) ** 2 for m in means) / (len(means) - 1)
    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, int(0.975 * resamples))]
    return {"est": est, "se": math.sqrt(var), "ci95": [lo, hi], "n": n,
            "n_clusters": n_clusters, "resamples": resamples,
            "bootstrap_seed": seed}


def metric_block(est, se, ci95, n, n_clusters=None, method="exact"):
    """The standard metric block every reported number is emitted as."""
    return {"est": est, "se": se, "ci95": list(ci95) if ci95 else [None, None],
            "n": n, "n_clusters": n_clusters, "method": method}


def wilson_block(hits, n):
    """Metric block for a simple proportion (method 'wilson')."""
    if n <= 0:
        return metric_block(None, None, [None, None], 0, method="wilson")
    p = hits / n
    lo, hi = wilson_ci(hits, n)
    se = math.sqrt(p * (1.0 - p) / n)
    return metric_block(p, se, [lo, hi], n, method="wilson")


def cb_block(clusters, bootstrap_seed=20260728):
    """Metric block for a clustered mean (method 'cluster-bootstrap')."""
    r = cluster_bootstrap_ci(clusters, seed=bootstrap_seed)
    return metric_block(r["est"], r["se"], r["ci95"], r["n"],
                        n_clusters=r["n_clusters"], method="cluster-bootstrap")


def scaled_block(block, factor):
    """A copy of `block` with est/se/ci95 multiplied by `factor` (for the
    normalized 0..1 twin of a 0..10 score)."""
    out = dict(block)
    for key in ("est", "se"):
        if out.get(key) is not None:
            out[key] = out[key] * factor
    out["ci95"] = [c * factor if c is not None else None for c in block["ci95"]]
    return out


# ===========================================================================
# LOADING + SELECTION
# ===========================================================================

def iter_jsonl(path, warnings):
    """Tolerant STREAMING jsonl reader: yields one row at a time and skips
    blank/unparseable lines into warnings. Selection consumes this directly,
    so peak memory is O(selected positions), not O(corpus) — a 100k-position
    corpus with --max-positions 200 holds 200 rows, not 100k."""
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                warnings.append("%s:%d: unparseable line skipped"
                                % (path, lineno))


def read_jsonl(path, warnings):
    """Materialized iter_jsonl. Kept for the fixture loader and for callers
    that genuinely need the list; the corpus path uses iter_jsonl."""
    return list(iter_jsonl(path, warnings))


def _sample_hash(seed, rid):
    """Deterministic sampling order (mirrors mine_puzzles._sample_hash)."""
    return hashlib.sha256(("%d:%s" % (seed, rid)).encode("utf-8")).hexdigest()


def valid_option_scores(scores, n_options):
    """True when `scores` is a usable answer key: a list of exactly n_options
    real numbers (not bools) in [0, 10]. A key that fails this is upstream
    DATA corruption (t3/verify_keys), so it is counted into
    skipped_malformed_key — never allowed to raise out of the run."""
    if not isinstance(scores, list) or len(scores) != n_options:
        return False
    for v in scores:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if v != v or v < 0 or v > 10:      # v != v catches NaN
            return False
    return True


def select_positions(rows, split, categories, max_positions, sample_seed,
                     warnings):
    """Apply the skip ladder; returns (selected_rows, skips OrderedDict).
    Check order: split/category filters, then unscoreable type (a structural
    property of the row), then unverified, then malformed key.

    `rows` may be ANY iterable (iter_jsonl streams it). With --max-positions
    set, only that many rows are ever retained: a bounded max-heap keyed on
    the same _sample_hash keeps the N smallest hashes and drops the rest as
    they stream past. The retained SET and its corpus ORDER are identical to
    sorting the whole list and slicing — the tie-break on the row's corpus
    index reproduces the old stable sort exactly."""
    skips = OrderedDict((k, 0) for k in (
        "skipped_split", "skipped_category", "skipped_unscoreable_type",
        "skipped_unverified", "skipped_malformed_key", "skipped_render_error",
        "skipped_sampled_out"))
    selected = []
    heap = []            # max-heap of (-hash_int, -order, row), size <= N
    n_selected = 0
    for order, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("t") == "summary":
            continue
        src = row.get("source") or {}
        if split != "all" and (row.get("split") or "open") != split:
            skips["skipped_split"] += 1
            continue
        if categories and row.get("category") not in categories:
            skips["skipped_category"] += 1
            continue
        dtype = src.get("decision_type") or row.get("type")
        if dtype not in prompt_contract.SCOREABLE_INDEX_TYPES:
            skips["skipped_unscoreable_type"] += 1
            continue
        key = row.get("answer_key")
        if row.get("status") == "candidate" or not isinstance(key, dict):
            skips["skipped_unverified"] += 1
            continue
        scores = key.get("option_scores")
        options = row.get("options") or []
        n_options = row.get("n_options")
        if not isinstance(n_options, int) or isinstance(n_options, bool):
            n_options = len(options)
        if not valid_option_scores(scores, n_options):
            skips["skipped_malformed_key"] += 1
            warnings.append("id=%s: malformed answer_key.option_scores "
                            "(need %d numbers in [0,10])"
                            % (row.get("id"), n_options))
            continue
        n_selected += 1
        if max_positions is None:
            selected.append(row)
            continue
        h = int(_sample_hash(sample_seed, str(row.get("id"))), 16)
        heapq.heappush(heap, (-h, -order, row))
        if len(heap) > max_positions:
            heapq.heappop(heap)          # evict the LARGEST hash still held

    if max_positions is not None:
        skips["skipped_sampled_out"] = max(0, n_selected - len(heap))
        selected = [row for _, _, row in
                    sorted(heap, key=lambda t: -t[1])]   # back to corpus order
    return selected, skips


def prep_position(row, strictness, skips, warnings):
    """Render a selected row once; returns the internal position dict or None
    (render failure -> skipped_render_error).

    The source `row` is deliberately NOT retained: every field the run needs
    (scores, caveats, provenance, menu) is extracted here, and holding the raw
    row pinned the whole corpus in memory for the life of the run. Nothing
    downstream reads it — result_row(), build_summary(), compute_metrics(),
    compute_baselines() and group_summary() go through these keys only."""
    src = row.get("source") or {}
    try:
        rendered = prompt_contract.render(row, strictness=strictness)
    except ValueError as exc:
        skips["skipped_render_error"] += 1
        warnings.append("render failed for id=%s: %s" % (row.get("id"), exc))
        return None
    for w in (rendered.get("warnings") or []):
        warnings.append("render warning for id=%s: %s" % (row.get("id"), w))
    return {
        "id": row.get("id"),
        "category": row.get("category"),
        "split": row.get("split") or "open",
        "game_file": src.get("game_file") or "unknown",
        "seed": src.get("seed"),
        "dtype": rendered.get("decision_type"),
        "family": rendered.get("family"),
        "options": row.get("options") or [],
        "n_options": rendered.get("n_options"),
        # THE menu the model is offered and every floor is defined over.
        "menu": list(rendered.get("menu_numbers")
                     or range(1, (rendered.get("n_options") or 0) + 1)),
        "scores": row["answer_key"]["option_scores"],
        "key_caveats": list((row.get("answer_key") or {}).get("caveats") or []),
        "engine_sha": src.get("engine_sha"),
        "overlay_sha": src.get("overlay_sha"),
        "rendered": rendered,
    }


# ===========================================================================
# BACKENDS
# ===========================================================================

def load_fixture(path, warnings):
    """--offline-fixture loader: {(id, k)): reply}. k is 1-based."""
    fixture = {}
    for row in read_jsonl(path, warnings):
        if isinstance(row, dict) and "id" in row and "k" in row:
            fixture[(row["id"], int(row["k"]))] = row.get("reply")
    return fixture


def menu_reply(pos, num):
    """The wire reply that selects 1-based menu number `num`. Mulligan's wire
    format is the WORD, not a number (prompt_contract pins that menu to
    [1,2] == [KEEP, MULLIGAN]); everything else is the CHOICE tag. Derived
    from the menu, NOT from options[] text, so a row with a short/absent
    options list cannot make a generator raise."""
    if pos["family"] == "mulligan":
        return "KEEP" if num == 1 else "MULLIGAN"
    return "CHOICE: %d" % num


def policy_reply(policy, pos, k, sample_seed):
    """Deterministic synthetic reply for (position, sample k). These double
    as the floor generators, so they must agree exactly with the analytic
    baselines in compute_baselines() — both sides go through the same
    pos["menu"] / menu_reply() / menu_score() triple."""
    if policy == "garbage":
        return GARBAGE_REPLY
    if policy == "always-pass":
        return menu_reply(pos, pass_menu_number(pos))
    if policy == "first":
        return menu_reply(pos, pos["menu"][0])
    if policy == "random":
        seed_int = int(hashlib.sha256(
            ("%s|%s|%s" % (sample_seed, pos["id"], k)).encode("utf-8")
        ).hexdigest(), 16)
        rng = random.Random(seed_int)
        return menu_reply(pos, rng.choice(pos["menu"]))
    if policy == "oracle":
        best = max(pos["menu"], key=lambda num: (menu_score(pos, num), -num))
        return menu_reply(pos, best)
    raise ValueError("unknown offline policy %r" % policy)


def http_call(endpoint, model, messages, max_tokens, timeout, tries,
              api_key=None):
    """One sample over HTTP with retries. Returns (reply, error, latency_ms).
    Never raises on transport failure.

    `api_key` is set ONLY by the already-egress-gated caller and only for a
    non-local endpoint (replay_match.require_api_key enforces both). It is
    sent as a Bearer token and never lands in the error string: the error text
    is the exception TYPE plus its message, and _post_chat never echoes its
    headers."""
    last_err = None
    for _ in range(max(1, tries)):
        t0 = time.time()
        try:
            text = replay_match._post_chat(endpoint, model, messages,
                                           max_tokens, timeout,
                                           api_key=api_key)
            return text, None, int((time.time() - t0) * 1000)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last_err = "%s: %s" % (type(exc).__name__, exc)
    return None, last_err, None


def bedrock_call(model, messages, region, creds, cfg, env, warnings, pos_id):
    """One sample through the AWS Bedrock backend. Retries live inside
    bedrock.send_signed (per-model cfg.retries, 429/5xx only). Returns
    (reply, error, latency_ms) and never raises on transport failure.

    An EMPTY reply with stop_reason=max_tokens is the CLAUDE.md failure mode
    (hidden reasoning ate the whole cap): it is surfaced as a WARNING naming
    the position, not silently scored as an interface failure, because the fix
    is a config change (BEDROCK_MODEL_CFG) and not a model verdict."""
    system, msgs = bedrock.split_system(messages)
    try:
        res = bedrock.invoke(model, msgs, system=system, region=region,
                             creds=creds, cfg=cfg, env=env)
    except bedrock.BedrockError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc), None
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc), None
    if res.get("empty_reply"):
        warnings.append(
            "id=%s: EMPTY reply with stop_reason=max_tokens — the token cap "
            "was spent (largely on hidden reasoning) before a visible token. "
            "This scores as an interface failure but is a CONFIG problem: "
            "raise max_tokens or lower thinking_budget_tokens via %s."
            % (pos_id, bedrock.CFG_ENV))
    return res.get("text"), None, res.get("latency_ms")


def collect_replies(positions, args, env, warnings):
    """-> list of raw sample dicts {pos_idx, k, reply, error, latency_ms} in
    deterministic (position order, k) order. Offline never touches the
    network; http fans out over --workers threads. Fixture-parse warnings are
    appended to `warnings` (never dropped)."""
    tasks = [(i, k) for i in range(len(positions))
             for k in range(1, args.k + 1)]
    raw = {}
    if args.backend == "offline":
        fixture = None
        if args.offline_fixture:
            fixture = load_fixture(args.offline_fixture, warnings)
        for (i, k) in tasks:
            pos = positions[i]
            if fixture is not None:
                if (pos["id"], k) in fixture:
                    reply = fixture[(pos["id"], k)]
                    raw[(i, k)] = (reply, None, None)
                else:
                    raw[(i, k)] = (None, "fixture_miss", None)
            else:
                reply = policy_reply(args.offline_policy, pos, k,
                                     args.sample_seed)
                raw[(i, k)] = (reply, None, None)
    elif args.backend == "bedrock":
        # Resolved ONCE: credentials and the per-model config are the same for
        # every sample, and re-reading ~/.aws per call would be pointless I/O.
        region = getattr(args, "region", None) or bedrock.resolve_region(env)
        creds = bedrock.resolve_credentials(env)
        cfg = bedrock.resolve_model_cfg(args.model, env)

        def one(task):
            i, k = task
            messages = positions[i]["rendered"]["messages"]
            return task, bedrock_call(args.model, messages, region, creds,
                                      cfg, env, warnings, positions[i]["id"])

        if args.workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for task, result in pool.map(one, tasks):
                    raw[task] = result
        else:
            for task in tasks:
                task, result = one(task)
                raw[task] = result
    else:
        endpoint = args.endpoint
        tries = 1 + max(0, args.retries)
        api_key = getattr(args, "api_key", None)

        def one(task):
            i, k = task
            messages = positions[i]["rendered"]["messages"]
            return task, http_call(endpoint, args.model, messages,
                                   args.max_tokens, args.req_timeout, tries,
                                   api_key=api_key)

        if args.workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for task, result in pool.map(one, tasks):
                    raw[task] = result
        else:
            for task in tasks:
                task, result = one(task)
                raw[task] = result
    return [{"pos_idx": i, "k": k, "reply": raw[(i, k)][0],
             "error": raw[(i, k)][1], "latency_ms": raw[(i, k)][2]}
            for (i, k) in tasks]


# ===========================================================================
# SCORING
# ===========================================================================

def score_parsed(pos, parsed):
    """-> (score int|None, solved, is_pass, declined, iface) for one parsed
    reply against the position's answer key."""
    if parsed.get("outcome") == "interface_failure":
        return None, False, False, False, True
    oi = parsed.get("option_index")
    rendered = pos["rendered"]
    pass_idx = rendered.get("pass_option_index")
    if oi is None:
        # decline slot on a non-play single type: a legal "do nothing".
        return 0, False, True, True, False
    if 0 <= oi < len(pos["scores"]):
        score = pos["scores"][oi]
    else:  # defensive: parser guaranteed in-range, but never crash on data
        score = 0
    is_pass = pass_idx is not None and oi == pass_idx
    return score, score == 10, is_pass, False, False


def build_samples(positions, raws):
    """Attach parses + scores to raw replies. -> list of sample dicts."""
    samples = []
    for r in raws:
        pos = positions[r["pos_idx"]]
        both = prompt_contract.parse_both(r["reply"], pos["rendered"])
        primary = both[pos["rendered"]["strictness"]]
        lenient = both["lenient"]
        score, solved, is_pass, declined, iface = score_parsed(pos, primary)
        l_score, _, _, _, l_iface = score_parsed(pos, lenient)
        samples.append({
            "pos": pos, "k": r["k"], "reply": r["reply"],
            "error": r["error"], "latency_ms": r["latency_ms"],
            "primary": primary, "lenient": lenient,
            "score": score, "solved": solved, "is_pass": is_pass,
            "declined": declined, "iface": iface,
            "lenient_score": None if l_iface else l_score,
            "valid": r["error"] is None,
        })
    return samples


def _clusters(pairs):
    """[(game_file, value)] -> list of per-cluster value lists, key-sorted."""
    by = {}
    for gf, v in pairs:
        by.setdefault(gf, []).append(v)
    return [by[k] for k in sorted(by)]


def compute_metrics(positions, samples, k):
    """The standard metric set over one group of positions+samples."""
    valid = [s for s in samples if s["valid"]]
    iface = [s for s in valid if s["iface"]]
    nonif = [s for s in valid if not s["iface"]]
    incl = cb_block(_clusters(
        [(s["pos"]["game_file"], 0.0 if s["iface"] else float(s["score"]))
         for s in valid]))
    excl = cb_block(_clusters(
        [(s["pos"]["game_file"], float(s["score"])) for s in nonif]))
    pass_1 = wilson_block(sum(1 for s in valid if s["solved"]), len(valid))
    ifr = wilson_block(len(iface), len(valid))
    declined = wilson_block(sum(1 for s in valid if s["declined"]), len(valid))
    # pass^k over positions with a full set of k usable samples
    usable = 0
    all_solved = 0
    excluded = 0
    by_pos = {}
    for s in valid:
        by_pos.setdefault(id(s["pos"]), []).append(s)
    for pos in positions:
        vs = by_pos.get(id(pos), [])
        if len(vs) >= k:
            usable += 1
            if all(v["solved"] for v in vs[:k]):
                all_solved += 1
        else:
            excluded += 1
    pass_k = wilson_block(all_solved, usable)
    pass_k["k_used"] = k
    return {
        "puz_score_incl": incl,
        "puz_score_incl_norm": scaled_block(incl, 0.1),
        "puz_score_excl": excl,
        "puz_score_excl_norm": scaled_block(excl, 0.1),
        "pass_1": pass_1,
        "pass_k": pass_k,
        "interface_failure_rate": ifr,
        "declined_slot_rate": declined,
    }, {"valid_samples": len(valid), "interface_failures": len(iface),
        "declined_slot_n": sum(1 for s in valid if s["declined"]),
        "pass_k_excluded": excluded}


def menu_score(pos, num):
    """The 0..10 answer-key score a reply selecting 1-based menu number `num`
    earns. THE single definition of the menu->score map: the scoring path
    (score_parsed, via parse().option_index == num-1) and every analytic floor
    both go through it, so they cannot drift. Out-of-key numbers score 0."""
    scores = pos["scores"]
    idx = num - 1
    if 0 <= idx < len(scores):
        return float(scores[idx])
    return 0.0


def pass_menu_number(pos):
    """The 1-based menu number of the PASS/decline/KEEP slot, taken from the
    render (never re-derived): play -> pass_option_index+1 (the last menu
    entry), mulligan -> 1 (KEEP), other single types -> the decline slot."""
    r = pos["rendered"]
    pi = r.get("pass_option_index")
    if pi is not None:
        return pi + 1
    if pos["family"] == "mulligan":
        return 1                                  # KEEP
    decline = r.get("decline_menu_number")
    if decline:
        return decline
    return pos["menu"][-1]


def _solved(value):
    """The success predicate, shared by the measured metric and EVERY floor's
    pass_1/pass_k twin. A floor scored on a different predicate is not a
    floor (it can exceed the oracle ceiling)."""
    return 1.0 if value == 10 else 0.0


def compute_baselines(positions, k):
    """Analytic floors from the same answer keys over the same positions and
    the SAME menu the model is offered (pos["menu"]). Each contributes ONE
    observation per position, clustered by game_file.

    Deterministic floors (always_pass, majority_class) repeat their single
    choice every sample, so their pass_k twin EQUALS their pass_1 twin;
    random_choice's is p**k."""
    def blk(fn):
        return cb_block(_clusters([(p["game_file"], fn(p)) for p in positions]))

    def rc_mean(p):
        return sum(menu_score(p, num) for num in p["menu"]) / float(len(p["menu"]))

    def rc_solved(p):
        return sum(_solved(menu_score(p, num))
                   for num in p["menu"]) / float(len(p["menu"]))

    rc = blk(rc_mean)
    rc["pass_1"] = blk(rc_solved)
    rc["pass_k"] = blk(lambda p: rc_solved(p) ** k)

    ap = blk(lambda p: menu_score(p, pass_menu_number(p)))
    ap_p1 = blk(lambda p: _solved(menu_score(p, pass_menu_number(p))))
    ap["pass_1"] = ap_p1
    ap["pass_k"] = ap_p1                  # deterministic: same pick every time

    # POSITIONAL floor. majority_class is a LABEL floor: it cannot see a corpus
    # whose correct answer sits at menu slot 1 more often than chance (the
    # engine emits options in its own order and nothing shuffles them), so
    # "always answer 1" — a policy this very runner can emit as
    # --offline-policy first — could beat random_choice with no floor next to
    # it to say so. best_index_counts makes the skew itself visible.
    af = blk(lambda p: menu_score(p, p["menu"][0]))
    af_p1 = blk(lambda p: _solved(menu_score(p, p["menu"][0])))
    af["pass_1"] = af_p1
    af["pass_k"] = af_p1                  # deterministic: same pick every time
    best_index_counts = Counter()
    for p in positions:
        best = max(p["menu"], key=lambda num: (menu_score(p, num), -num))
        best_index_counts[best] += 1
    af["best_index_counts"] = dict(sorted(best_index_counts.items()))

    oracle = blk(lambda p: max(menu_score(p, num) for num in p["menu"]))
    oracle["pass_1"] = blk(
        lambda p: max(_solved(menu_score(p, num)) for num in p["menu"]))
    oracle["pass_k"] = oracle["pass_1"]

    # majority class: the option-label token that is a correct (score==10)
    # answer most often; deterministic tie-break by token text.
    counts = Counter()
    for p in positions:
        for num in p["menu"]:
            i = num - 1
            if menu_score(p, num) == 10 and i < len(p["options"]):
                counts[str(p["options"][i])] += 1
    token = None
    if counts:
        token = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def tok_menu_number(p):
        """The menu number that emits `token` in this position, or None."""
        if token is None:
            return None
        for num in p["menu"]:
            i = num - 1
            if i < len(p["options"]) and str(p["options"][i]) == token:
                return num
        return None

    def tok_score(p):
        num = tok_menu_number(p)
        return 0.0 if num is None else menu_score(p, num)

    def tok_maximal(p):
        num = tok_menu_number(p)
        if num is None:
            return 0.0
        best = max(menu_score(p, m) for m in p["menu"])
        return 1.0 if menu_score(p, num) == best else 0.0

    mc = blk(tok_score)
    mc_p1 = blk(lambda p: _solved(tok_score(p)))
    mc["pass_1"] = mc_p1
    mc["pass_k"] = mc_p1                  # deterministic: same pick every time
    mc["token"] = token
    # Reported for interpretation ONLY — never used as a floor: "the token is
    # a maximal-scoring option" is a different predicate from `solved`
    # (score == 10) and can exceed the achievable ceiling.
    mc["frac_positions_maximal"] = blk(tok_maximal)["est"]
    return {"random_choice": rc, "always_pass": ap, "always_first": af,
            "majority_class": mc, "oracle_ceiling": oracle}


def _margin(est, baseline_est):
    if est is None or baseline_est is None:
        return None
    return est - baseline_est


def attach_margins(metrics, baselines):
    """margin_over_<floor> next to every headline est (the 88%-lesson rule)."""
    floors = ("random_choice", "always_pass", "always_first", "majority_class")
    for key in ("puz_score_incl", "puz_score_excl"):
        blk = metrics[key]
        for name in floors:
            blk["margin_over_%s" % name] = _margin(
                blk["est"], baselines[name]["est"])
    p1 = metrics["pass_1"]
    for name in floors:
        p1["margin_over_%s" % name] = _margin(
            p1["est"], baselines[name]["pass_1"]["est"])
    pk = metrics["pass_k"]
    for name in floors:
        pk["margin_over_%s" % name] = _margin(
            pk["est"], baselines[name]["pass_k"]["est"])


def group_summary(positions, samples, k):
    """Per-category / per-decision_type breakdown with the same blocks."""
    metrics, counters = compute_metrics(positions, samples, k)
    baselines = compute_baselines(positions, k)
    attach_margins(metrics, baselines)
    return {"n_positions": len(positions),
            "n_samples_valid": counters["valid_samples"],
            "metrics": metrics, "baselines": baselines}


# ===========================================================================
# OUTPUT
# ===========================================================================

def result_row(sample, args):
    pos = sample["pos"]
    rendered = pos["rendered"]
    primary = sample["primary"]
    lenient = sample["lenient"]
    return {
        "schema": SCHEMA_RESULT,
        "contract": prompt_contract.CONTRACT_VERSION,
        "contract_sha": rendered.get("contract_sha"),
        "position_id": pos["id"],
        "category": pos["category"],
        "split": pos["split"],
        "game_file": pos["game_file"],
        "seed": pos["seed"],
        "decision_type": pos["dtype"],
        "n_options": pos["n_options"],
        "k": sample["k"],
        "backend": args.backend,
        "model": effective_model(args),
        "strictness": args.strictness,
        "render_sha": rendered.get("render_sha"),
        "plan_sha": rendered.get("plan_sha"),
        "plan_slug": rendered.get("plan_slug"),
        "reply": sample["reply"],
        "outcome": primary.get("outcome"),
        "interface_error": primary.get("interface_error"),
        "recovered_by": primary.get("recovered_by"),
        "menu_number": primary.get("menu_number"),
        "option_index": primary.get("option_index"),
        "choice": primary.get("choice"),
        "is_pass": sample["is_pass"],
        "score": sample["score"],
        "solved": sample["solved"],
        "error": sample["error"],
        "latency_ms": sample["latency_ms"],
        "lenient": {"outcome": lenient.get("outcome"),
                    "option_index": lenient.get("option_index"),
                    "score": sample["lenient_score"]},
    }


def effective_model(args):
    if args.backend == "offline":
        if args.offline_fixture:
            return "offline-fixture"
        return "offline:%s" % args.offline_policy
    return args.model


def _uniform(values):
    vals = sorted(set(v for v in values if v))
    if not vals:
        return "unknown"
    if len(vals) == 1:
        return vals[0]
    return "mixed"


def build_summary(args, positions, samples, skips, warnings,
                  transport_errors, missing_fixture, attempted):
    metrics, counters = compute_metrics(positions, samples, args.k)
    baselines = compute_baselines(positions, args.k)
    attach_margins(metrics, baselines)
    metrics["transport_error_rate"] = wilson_block(transport_errors, attempted)

    by_category = {}
    by_dtype = {}
    for key_fn, target in ((lambda p: p["category"], by_category),
                           (lambda p: p["dtype"], by_dtype)):
        groups = {}
        for pos in positions:
            groups.setdefault(key_fn(pos), []).append(pos)
        for name in sorted(groups):
            group_pos = groups[name]
            ids = set(id(p) for p in group_pos)
            group_samples = [s for s in samples if id(s["pos"]) in ids]
            target[name] = group_summary(group_pos, group_samples, args.k)

    caveats = [ANSWER_KEY_LIMITS_CAVEAT]
    key_caveats = set()
    for pos in positions:
        for c in pos["key_caveats"]:
            key_caveats.add("answer_key_caveat:%s" % c)
    caveats.extend(sorted(key_caveats))
    if transport_errors:
        caveats.append("transport errors excluded from scoring denominators "
                       "(n=%d of %d attempted)" % (transport_errors, attempted))
    if missing_fixture:
        caveats.append("missing fixture samples excluded from scoring "
                       "denominators (n=%d)" % missing_fixture)

    seed_blocks = Counter()
    for pos in positions:
        block = None
        if isinstance(pos["seed"], int):
            block = seed_policy.reserved_block(pos["seed"])
        seed_blocks[block or "unreserved"] += 1

    # CONTAMINATION PROVENANCE: the per-sample rows carry `split`, but the
    # summary is the artifact anyone actually reads. Without these two fields
    # a --split holdout (or a pooled --split all) summary.json is
    # indistinguishable from an open-split one, and a holdout score can be
    # reported — or published — as an open score.
    split_counts = Counter(pos["split"] for pos in positions)
    if args.split != "open" or set(split_counts) - {"open"}:
        caveats.append(
            "SPLIT: --split=%s; positions by split: %s. Holdout scores are "
            "NOT comparable to open-split scores and must not be published as "
            "such." % (args.split,
                       json.dumps(dict(sorted(split_counts.items())))))

    counters.update({
        "attempted_samples": attempted,
        "transport_errors": transport_errors,
        "missing_fixture_samples": missing_fixture,
    })

    summary = {
        "schema": SCHEMA_SUMMARY,
        "generated_utc": args.generated_utc,
        "positions_file": str(args.positions),
        "backend": args.backend,
        "model": effective_model(args),
        "offline_policy": args.offline_policy if args.backend == "offline" and not args.offline_fixture else None,
        "endpoint_host": replay_match.endpoint_host(args.endpoint)
        if args.backend != "offline" else "",
        # ADDITIVE provenance: WHICH per-model knobs produced these numbers.
        # A run at a different max_tokens / thinking budget is a different
        # measurement of the same model and must be distinguishable.
        "model_cfg": (bedrock.resolve_model_cfg(args.model).as_dict()
                      if args.backend == "bedrock" else None),
        "model_cfg_sha": (bedrock.model_cfg_sha()
                          if args.backend == "bedrock" else None),
        "api_key_present": bool(getattr(args, "api_key", None)),
        "k": args.k,
        "strictness": args.strictness,
        "split": args.split,
        "split_counts": dict(split_counts),
        "sample_seed": args.sample_seed,
        "n_positions": len(positions),
        "n_samples": attempted,
        "n_clusters": len(set(p["game_file"] for p in positions)),
        "skips": dict(skips),
        "counters": counters,
        "metrics": metrics,
        "baselines": baselines,
        "by_category": by_category,
        "by_decision_type": by_dtype,
        "caveats": caveats,
        "warnings": warnings,
        "engine_sha": _uniform([p["engine_sha"] for p in positions]),
        "overlay_sha": _uniform([p["overlay_sha"] for p in positions]),
        "seed_blocks": dict(seed_blocks),
    }
    prompt_contract.stamp(summary)
    return summary


# ===========================================================================
# MAIN
# ===========================================================================

def refuse_egress(host):
    print("REFUSING to send game state to non-local endpoint host '%s' "
          "without egress consent." % host, file=sys.stderr)
    print("This sends the full game snapshot (board, hands, deck plan) to "
          "the endpoint on every call. Holding an API key is not consent.",
          file=sys.stderr)
    print("Set OPENROUTER_ALLOW_EGRESS=1 to opt in (see CLAUDE.md / "
          "secrets.env.example).", file=sys.stderr)
    return 2


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("positions", help="positions.jsonl (puzzle-position.v1)")
    ap.add_argument("--out", help="output run directory")
    ap.add_argument("--backend",
                    choices=("offline", "http", "openrouter", "bedrock"),
                    default="offline")
    ap.add_argument("--region", default=None,
                    help="AWS region for --backend bedrock (default: "
                         "AWS_REGION / ~/.aws/config / %s)"
                         % bedrock.DEFAULT_REGION)
    ap.add_argument("--no-api-key", action="store_true",
                    help="allow a REMOTE http endpoint with no %s (default: "
                         "refuse before spending the egress consent)"
                         % replay_match.API_KEY_VAR)
    ap.add_argument("--offline-fixture", help="jsonl of {id,k,reply}")
    ap.add_argument("--offline-policy", choices=OFFLINE_POLICIES,
                    default="always-pass")
    ap.add_argument("--endpoint", default="http://localhost:8080/v1")
    ap.add_argument("--model", default="local-model")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--req-timeout", type=int, default=120)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--strictness", choices=prompt_contract.STRICTNESSES,
                    default="strict")
    ap.add_argument("--categories", default=None,
                    help="comma-separated category filter")
    ap.add_argument("--split", choices=("open", "holdout", "all"),
                    default="open")
    ap.add_argument("--max-positions", type=int, default=None)
    ap.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED)
    ap.add_argument("--generated-utc", default=None,
                    help="override the summary timestamp (for byte-"
                         "reproducible runs; defaults to now)")
    ap.add_argument("--json", action="store_true",
                    help="dump the summary object to stdout (only stdout "
                         "content)")
    ap.add_argument("--dry-run", action="store_true",
                    help="select + count only; no backend calls, no outputs")
    return ap


def main(argv=None, env=None):
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env

    # ---- egress gates FIRST, pure, before anything else -------------------
    args.api_key = None
    if args.backend == "openrouter":
        # checked before any URL is even constructed; never set this flag.
        if env.get("OPENROUTER_ALLOW_EGRESS") != "1":
            return refuse_egress("openrouter.ai")
        if replay_match.endpoint_host(args.endpoint) in replay_match.LOCAL_HOSTS:
            args.endpoint = OPENROUTER_DEFAULT_ENDPOINT
    elif args.backend == "bedrock":
        # bedrock.enforce_egress reads the SAME single consent flag; refuse
        # before resolving credentials, reading ~/.aws, or building a URL.
        if not bedrock.egress_allowed(env):
            return refuse_egress(bedrock.bedrock_host(
                args.region or bedrock.resolve_region(env)))
    elif args.backend == "http":
        if not replay_match.egress_allowed(args.endpoint, env):
            return refuse_egress(replay_match.endpoint_host(args.endpoint))

    # ---- auth, still before ANY request ----------------------------------
    # v1 gap: --backend openrouter sent no Authorization header, so the run
    # spent the egress consent and then 401'd. Resolve the key here and fail
    # loudly if it is missing, so the consent is not spent on a doomed run.
    if args.backend in ("openrouter", "http"):
        key = replay_match.resolve_api_key(env)
        remote = not replay_match.is_local_endpoint(args.endpoint)
        if remote and not key and not args.no_api_key:
            print("REFUSING to run --backend %s against %r with no %s set."
                  % (args.backend, replay_match.endpoint_host(args.endpoint),
                     replay_match.API_KEY_VAR), file=sys.stderr)
            print("Set it in the environment or in %s; the request would only "
                  "401 and the egress consent would be spent for nothing. "
                  "Pass --no-api-key if this endpoint needs no auth."
                  % replay_match.SECRETS_ENV, file=sys.stderr)
            return 2
        # A key is NEVER sent to an on-box endpoint.
        args.api_key = key if remote else None
    elif args.backend == "bedrock":
        if not args.model or args.model == "local-model":
            print("--backend bedrock needs an explicit --model (e.g. "
                  "anthropic.claude-opus-5); see `python3 eval/bedrock.py "
                  "--print-cfg`.", file=sys.stderr)
            return 2
        try:
            bedrock.resolve_credentials(env)
        except bedrock.CredentialsNotFound as exc:
            print("REFUSING to start: %s" % exc, file=sys.stderr)
            return 2
        args.region = args.region or bedrock.resolve_region(env)
        # So endpoint_host() in the summary reports where the data actually
        # went, instead of the unused localhost default.
        args.endpoint = bedrock.invoke_url(args.region, args.model)

    if not args.dry_run and not args.out:
        print("--out is required (except with --dry-run)", file=sys.stderr)
        return 2
    if args.k < 1:
        print("--k must be >= 1", file=sys.stderr)
        return 2
    if not Path(args.positions).exists():
        print("positions file not found: %s" % args.positions, file=sys.stderr)
        return 2
    if args.offline_fixture and not Path(args.offline_fixture).exists():
        print("offline fixture not found: %s" % args.offline_fixture,
              file=sys.stderr)
        return 2
    if args.generated_utc is None:
        args.generated_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    categories = None
    if args.categories:
        categories = set(c.strip() for c in args.categories.split(",") if c.strip())

    warnings = []
    # STREAMED: the corpus is never materialized as a list (see iter_jsonl /
    # select_positions). Peak memory is O(selected), not O(corpus).
    selected, skips = select_positions(
        iter_jsonl(args.positions, warnings), args.split, categories,
        args.max_positions, args.sample_seed, warnings)
    positions = []
    for i in range(len(selected)):
        row = selected[i]
        selected[i] = None          # release the raw row as we go
        pos = prep_position(row, args.strictness, skips, warnings)
        if pos is not None:
            positions.append(pos)
    del selected

    if args.dry_run:
        report = {"schema": SCHEMA_SUMMARY, "dry_run": True,
                  "positions_file": str(args.positions),
                  "split": args.split,
                  "split_counts": dict(Counter(p["split"] for p in positions)),
                  "n_positions": len(positions), "skips": dict(skips),
                  "warnings": warnings}
        prompt_contract.stamp(report)
        print(json.dumps(report, sort_keys=True))
        return 0 if positions else 2

    if not positions:
        print("nothing scoreable: 0 positions survived selection "
              "(skips: %s)" % json.dumps(dict(skips)), file=sys.stderr)
        return 2

    raws = collect_replies(positions, args, env, warnings)
    samples = build_samples(positions, raws)
    attempted = len(samples)
    transport_errors = sum(1 for s in samples
                           if s["error"] not in (None, "fixture_miss"))
    missing_fixture = sum(1 for s in samples if s["error"] == "fixture_miss")
    n_valid = sum(1 for s in samples if s["valid"])

    summary = build_summary(args, positions, samples, skips, warnings,
                            transport_errors, missing_fixture, attempted)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    with open(results_path, "w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(result_row(sample, args), sort_keys=True) + "\n")
        final = {"t": "summary"}
        final.update(summary)
        fh.write(json.dumps(final, sort_keys=True) + "\n")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, sort_keys=True, indent=1)
        fh.write("\n")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        m = summary["metrics"]
        print("puzzle_run: %d positions x k=%d (%s) score_incl=%s pass_1=%s "
              "iface=%s -> %s" % (
                  len(positions), args.k, effective_model(args),
                  m["puz_score_incl"]["est"], m["pass_1"]["est"],
                  m["interface_failure_rate"]["est"], results_path),
              file=sys.stderr)

    # Exit 3 == INCOMPLETE (contract §8): the run happened, but the backend
    # did not deliver usable data. Results are still written; the exit code
    # must not read as a pass, because empty denominators make every metric
    # None and a naive consumer would see "no failures".
    if n_valid == 0 and attempted > 0:
        print("INCOMPLETE: 0 of %d samples usable (%d transport errors, %d "
              "fixture misses) — every denominator is empty" % (
                  attempted, transport_errors, missing_fixture),
              file=sys.stderr)
        return 3
    if replay_match.transport_budget_exceeded(attempted, transport_errors):
        print("INCOMPLETE: transport error budget exceeded: %d/%d > %.0f%% — "
              "results written but denominators unreliable" % (
                  transport_errors, attempted,
                  100.0 * replay_match.TRANSPORT_ERROR_BUDGET),
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
