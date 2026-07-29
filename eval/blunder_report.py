#!/usr/bin/env python3
"""Blunder adjudication + report (EVAL-DESIGN.md S4 / S10.2, axes 2 & 5).

Consumes decision corpora (game-*.jsonl), flags decisions via
eval/blunder_flags.py (text lane + shadow-disagreement lane + 5% audit lane),
adjudicates flagged rows, and emits blunder-report.v1 rows plus the aggregate
summary block. Fully offline by default: no network, no engine unless
--driver engine is explicitly requested AND eval/verify_keys.py is present.

Usage:
  python3 eval/blunder_report.py DIR [DIR ...] --out RUNDIR
      [--model NAME] [--audit-rate 0.05] [--sample-seed 20260728]
      [--adjudicate none|textlane|rollout]   (default textlane)
      [--driver engine|fixture|none] [--fixture FILE.jsonl]
      [--n-per-option 16] [--max-adjudications N] [--json]

Adjudication methods (blunder-report.v1 `adjudication.method` enum):
  rollout            wpl = wp_best - wp_chosen from the rollout driver;
                     classes per EVAL-DESIGN S4.2 (good <0.05, inaccuracy
                     0.05-0.10, mistake 0.10-0.25, blunder >=0.25 or any drop
                     crossing WP 0.5 by >=0.15); GATE: wpl > 2*wpl_se required
                     to classify above 'good', else 'good' + noise_limited.
  exhaustive         not implemented in v1; never emitted.
  textlane-verified  the honest v1 path for LLM games (not deterministically
                     replayable, S4.3). Only TEXTLANE_VERIFIED_FLAGS rules
                     qualify; class 'blunder', wp fields omitted, caveat
                     'textlane_not_rollout_verified'. Counted ONLY in
                     punt_rate_textlane, never in punt_rate.
  unadjudicable      everything else, with an additive unadjudicable_reason.

Exit codes: 0 OK; 1 (reserved, no gates declared here); 2 usage error /
nothing usable to measure; 3 INCOMPLETE (--adjudicate rollout requested but
no rollout driver was available).

WHAT WOULD MAKE THIS FAIL
  * A rollout driver returning option_wp arrays misaligned with `options` —
    wp_chosen would silently score the wrong action; the pinned rollout_key
    reference and the FixtureRolloutDriver conformance test are the guards.
  * Counting textlane-verified blunders into punt_rate — the two denominators
    are deliberately separate; test_textlane_never_enters_punt_rate fails.
  * Classifying noise: dropping the wpl > 2*wpl_se gate would turn rollout
    variance into blunder counts; test_classification_thresholds fails.
  * Reporting the audit lane as clean when it was never adjudicated — the
    summary must carry flaggers_leaky=None (unmeasured), never False.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

import blunder_flags  # noqa: E402
from blunder_flags import (  # noqa: E402
    scan, cluster_bootstrap_ci, TEXTLANE_VERIFIED_FLAGS)
from make_sft_data import iter_game_dirs, load_complete_games  # noqa: E402

# GUARDED import of the rollout driver (t3 owns verify_keys.py; it may not
# exist yet). Absence degrades every rollout-lane row to method
# 'unadjudicable' reason 'no_engine_available' — never an ImportError.
try:
    from verify_keys import (RolloutDriver, FixtureRolloutDriver,
                             rollout_key, EMPTY_STACK_TYPES,
                             V1_FORCEABLE_TYPES)
    HAVE_ROLLOUT_DRIVER = True
except ImportError:
    HAVE_ROLLOUT_DRIVER = False
    RolloutDriver = FixtureRolloutDriver = None
    EMPTY_STACK_TYPES = frozenset(("play", "attackers", "blockers", "mulligan"))
    V1_FORCEABLE_TYPES = frozenset(("play", "mulligan"))

# GUARDED import of the frozen prompt contract (t1 owns prompt_contract.py).
try:
    import prompt_contract
    HAVE_PROMPT_CONTRACT = True
except ImportError:
    prompt_contract = None
    HAVE_PROMPT_CONTRACT = False


def _rollout_key_local(address):
    """Byte-identical private copy of verify_keys.rollout_key (contract S3);
    the pinned reference test asserts the two agree when both exist."""
    payload = "|".join(str(address.get(k)) for k in
                       ("engine_sha", "overlay_sha", "deck_a", "deck_b",
                        "controllers", "seed", "swap", "decision_index",
                        "game_file"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


if not HAVE_ROLLOUT_DRIVER:
    rollout_key = _rollout_key_local


# ------------------------------------------------------------ classification

# EVAL-DESIGN S4.2 thresholds (v1 priors).
WPL_INACCURACY = 0.05
WPL_MISTAKE = 0.10
WPL_BLUNDER = 0.25
WPL_CROSSING = 0.15   # any drop crossing WP 0.5 by >= this is a blunder

METHOD_ENUM = ("rollout", "exhaustive", "textlane-verified", "unadjudicable")
CLASS_ENUM = ("good", "inaccuracy", "mistake", "blunder", "unadjudicable")


def classify_wpl(wpl, wpl_se, wp_best, wp_chosen):
    """-> (class, noise_limited). Raw class from the S4.2 hierarchy, then the
    noise gate: classifying above 'good' requires wpl > 2*wpl_se (do not
    classify noise) — a gated-down row is 'good' with noise_limited True."""
    crossing = (wp_best is not None and wp_chosen is not None
                and wp_best > 0.5 and wp_chosen < 0.5
                and wpl >= WPL_CROSSING)
    if wpl >= WPL_BLUNDER or crossing:
        raw = "blunder"
    elif wpl >= WPL_MISTAKE:
        raw = "mistake"
    elif wpl >= WPL_INACCURACY:
        raw = "inaccuracy"
    else:
        raw = "good"
    if raw != "good" and (wpl_se is None or not (wpl > 2.0 * wpl_se)):
        return "good", True
    return raw, False


def _unadjudicable(row, reason):
    row["adjudication"] = {"method": "unadjudicable", "class": "unadjudicable",
                           "caveats": []}
    row["unadjudicable_reason"] = reason


def _adjudicate_textlane(row):
    """textlane-verified path: only the four engine-verifiable-from-snapshot
    rules qualify; those rules fire only on pass/decline actions, so a
    qualified row is a blunder by construction of the rule."""
    if any(f in TEXTLANE_VERIFIED_FLAGS for f in row.get("flags") or []):
        row["adjudication"] = {
            "method": "textlane-verified", "class": "blunder",
            "caveats": ["textlane_not_rollout_verified"]}
        return True
    _unadjudicable(row, "not_textlane_verifiable")
    return False


def _chosen_wp_index(row, n_wp):
    ch = row.get("choice")
    if isinstance(ch, int) and not isinstance(ch, bool) and 0 <= ch < n_wp:
        return ch
    if ch == -1 and row.get("decision_type") == "play" and n_wp >= 1:
        return n_wp - 1   # PASS is always the last play-menu option
    return None


def _adjudicate_rollout(row, driver, n_per_option):
    """One rollout adjudication. Never raises on driver failure; degrades to
    unadjudicable with the driver's reason token. Returns True when a rollout
    adjudication (any class) was recorded."""
    addr = row["address"]
    ok, reason = driver.supports(addr)
    if not ok:
        _unadjudicable(row, reason or "unsupported")
        return False
    try:
        res = driver.rollout(addr, n_per_option)
    except Exception as e:   # a driver must not raise, but never crash on one
        _unadjudicable(row, "driver_error:%s" % type(e).__name__)
        return False
    if not isinstance(res, dict) or not res.get("ok"):
        _unadjudicable(row, (res or {}).get("reason") or "rollout_failed")
        return False
    wps = res.get("option_wp") or []
    ses = res.get("option_wp_se") or []
    idx = _chosen_wp_index(row, len(wps))
    if idx is None:
        _unadjudicable(row, "choice_not_mappable")
        return False
    wp_chosen = wps[idx]
    valid = [(w, i) for i, w in enumerate(wps) if w is not None]
    if wp_chosen is None or not valid:
        _unadjudicable(row, "rollout_no_decisive_games")
        return False
    wp_best, best_i = max(valid)
    se_chosen = ses[idx] if idx < len(ses) else None
    se_best = ses[best_i] if best_i < len(ses) else None
    wpl = max(0.0, wp_best - wp_chosen)
    wpl_se = (math.sqrt(se_best * se_best + se_chosen * se_chosen)
              if (se_best is not None and se_chosen is not None) else None)
    cls, noise_limited = classify_wpl(wpl, wpl_se, wp_best, wp_chosen)
    caveats = list(res.get("caveats") or [])
    if "single_deal_hidden_info" not in caveats:   # every v1 rollout is
        caveats.append("single_deal_hidden_info")  # single-deal (S9.3)
    adj = {"method": "rollout",
           "n_per_option": res.get("n_per_option", n_per_option),
           "wp_chosen": wp_chosen, "wp_best": wp_best,
           "wpl": wpl, "class": cls, "caveats": caveats}
    if wpl_se is not None:
        adj["wpl_se"] = wpl_se
    row["adjudication"] = adj
    row["noise_limited"] = noise_limited
    # Trivial-baseline ingredient for dq_wpl_mean (the 88% lesson): WPL of a
    # uniformly random pick over the same menu, from the same rollout data.
    row["random_choice_wpl"] = max(
        0.0, wp_best - (sum(w for w, _ in valid) / len(valid)))
    return True


def adjudicate_rows(rows, mode, driver, n_per_option=16,
                    max_adjudications=None):
    """Adjudicate every flagged row in place (deterministic order). -> stats.

    mode 'none':     every row unadjudicable ('adjudication_disabled').
    mode 'textlane': textlane-verified where qualified, else unadjudicable.
    mode 'rollout':  driver rollout; when the driver is absent EVERY row is
                     unadjudicable 'no_engine_available' (no silent textlane
                     substitution — the caller exits 3 INCOMPLETE); when the
                     driver refuses a row or the cap is hit, fall back to
                     textlane-verified where qualified.
    """
    n_rollout_calls = 0
    for row in sorted(rows, key=lambda r: r["rid"]):
        row["rollout_key"] = rollout_key(row["address"])
        if mode == "none":
            _unadjudicable(row, "adjudication_disabled")
            continue
        if mode == "textlane":
            _adjudicate_textlane(row)
            continue
        # mode == "rollout"
        if driver is None:
            _unadjudicable(row, "no_engine_available")
            continue
        if (max_adjudications is not None
                and n_rollout_calls >= max_adjudications):
            if not _adjudicate_textlane(row):
                row["unadjudicable_reason"] = "max_adjudications_reached"
            continue
        n_rollout_calls += 1
        if not _adjudicate_rollout(row, driver, n_per_option):
            if row["adjudication"]["method"] == "unadjudicable":
                reason = row.get("unadjudicable_reason")
                if _adjudicate_textlane(row):
                    row["textlane_fallback_from"] = reason
                else:
                    row["unadjudicable_reason"] = reason
    return {"n_rollout_calls": n_rollout_calls}


# ----------------------------------------------------------------- summary

def metric_block(est, se, ci95, n, n_clusters, method):
    return {"est": est, "se": se, "ci95": list(ci95), "n": n,
            "n_clusters": n_clusters, "n_decisions": n, "n_games": n_clusters,
            "method": method}


def _cb_block(clusters):
    r = cluster_bootstrap_ci(list(clusters))
    return metric_block(r["est"], r["se"], r["ci95"], r["n"],
                        r["n_clusters"], "cluster-bootstrap")


def _p95(values):
    """Nearest-rank 95th percentile (aggregate.p90 style)."""
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(0.95 * len(vs)) - 1)]


def _gk(row):
    return "%s|%s" % (row["run_dir"], row["game_file"])


def _lane_rate_clusters(rows, lane):
    """Per-game 0/1 blunder observations over ROLLOUT-adjudicated rows in a
    lane ('*' = any lane membership irrelevant; 'flagged' = text-flag or
    shadow-disagree)."""
    per_game = {}
    for r in rows:
        if r["adjudication"]["method"] != "rollout":
            continue
        lanes = r.get("lanes") or []
        if lane == "*":
            ok = True
        elif lane == "flagged":
            ok = ("text-flag" in lanes) or ("shadow-disagree" in lanes)
        else:
            ok = lane in lanes
        if not ok:
            continue
        per_game.setdefault(_gk(r), []).append(
            1.0 if r["adjudication"]["class"] == "blunder" else 0.0)
    return per_game


def _lift_block(flagged_pg, audit_pg, resamples=2000, seed=20260728):
    """flagger_lift = flagged-lane punt rate minus audit-lane punt rate, with
    a joint cluster (per-game) bootstrap CI. A lift whose CI includes 0 means
    the flaggers are not selecting anything above chance."""
    import random as _random
    games = sorted(set(flagged_pg) | set(audit_pg))
    nf = sum(len(v) for v in flagged_pg.values())
    na = sum(len(v) for v in audit_pg.values())
    if nf == 0 or na == 0 or len(games) == 0:
        return {"est": None, "se": None, "ci95": [None, None],
                "n": nf + na, "n_clusters": len(games),
                "n_decisions": nf + na, "n_games": len(games),
                "method": "cluster-bootstrap",
                "note": "unmeasurable: a lane has zero adjudicated rows"}
    rf = sum(sum(v) for v in flagged_pg.values()) / nf
    ra = sum(sum(v) for v in audit_pg.values()) / na
    est = rf - ra
    rng = _random.Random(seed)
    diffs = []
    for _ in range(resamples):
        fs, fn, as_, an = 0.0, 0, 0.0, 0
        for _ in range(len(games)):
            gk = games[rng.randrange(len(games))]
            for v in flagged_pg.get(gk, ()):
                fs += v
                fn += 1
            for v in audit_pg.get(gk, ()):
                as_ += v
                an += 1
        if fn == 0 or an == 0:
            continue
        diffs.append(fs / fn - as_ / an)
    if len(diffs) < 2:
        return {"est": est, "se": None, "ci95": [None, None], "n": nf + na,
                "n_clusters": len(games), "n_decisions": nf + na,
                "n_games": len(games), "method": "cluster-bootstrap"}
    diffs.sort()
    mu = sum(diffs) / len(diffs)
    var = sum((x - mu) ** 2 for x in diffs) / (len(diffs) - 1)
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(len(diffs) - 1, int(0.975 * len(diffs)))]
    return {"est": est, "se": math.sqrt(var), "ci95": [lo, hi], "n": nf + na,
            "n_clusters": len(games), "n_decisions": nf + na,
            "n_games": len(games), "method": "cluster-bootstrap"}


def build_summary(rows, stats, params, generated_utc=None):
    """The aggregate block (EVAL-DESIGN S10.2). Every metric is a standard
    metric block; every denominator is explicit; every baseline is a field."""
    n_scoreable = stats["n_scoreable"]
    per_game_scoreable = stats["per_game_scoreable"]

    rollout_rows = [r for r in rows
                    if r["adjudication"]["method"] == "rollout"]
    textlane_rows = [r for r in rows
                     if r["adjudication"]["method"] == "textlane-verified"]

    # dq_wpl_mean over rollout-adjudicated decisions, clustered by game.
    wpl_pg, rand_pg = {}, {}
    for r in rollout_rows:
        wpl_pg.setdefault(_gk(r), []).append(r["adjudication"]["wpl"])
        if r.get("random_choice_wpl") is not None:
            rand_pg.setdefault(_gk(r), []).append(r["random_choice_wpl"])
    dq_wpl_mean = _cb_block([wpl_pg[k] for k in sorted(wpl_pg)])
    random_choice_wpl = _cb_block([rand_pg[k] for k in sorted(rand_pg)])
    dq_wpl_mean["denominator"] = "rollout-adjudicated decisions"
    dq_wpl_mean["baseline_random_choice_wpl"] = random_choice_wpl["est"]
    dq_wpl_mean["margin_over_random_choice"] = (
        dq_wpl_mean["est"] - random_choice_wpl["est"]
        if dq_wpl_mean["est"] is not None
        and random_choice_wpl["est"] is not None else None)

    # punt_rate: blunders among ROLLOUT-adjudicated decisions only.
    punt_pg = _lane_rate_clusters(rows, "*")
    punt_rate = _cb_block([punt_pg[k] for k in sorted(punt_pg)])
    punt_rate["denominator"] = "rollout-adjudicated decisions"
    n_rollout_blunders = sum(int(x) for v in punt_pg.values() for x in v)
    punt_rate["per_100_scoreable"] = (
        100.0 * n_rollout_blunders / n_scoreable if n_scoreable else None)
    punt_rate["n_scoreable"] = n_scoreable
    punt_rate["note"] = ("lower bound over the corpus: only flagged + audit "
                         "lanes are adjudicated; flagger recall is measured "
                         "by audit_lane_punt_rate")

    punt_rate_by_lane = {}
    for lane in ("shadow-disagree", "text-flag", "audit-sample"):
        lane_pg = _lane_rate_clusters(rows, lane)
        b = _cb_block([lane_pg[k] for k in sorted(lane_pg)])
        b["denominator"] = "rollout-adjudicated decisions in lane %r" % lane
        punt_rate_by_lane[lane] = b

    # punt_rate_textlane: textlane-verified blunders per scoreable decision,
    # clustered by game; NEVER added to punt_rate.
    tl_pg = {}
    for gk, n_sc in per_game_scoreable.items():
        n_bl = 0
        for r in textlane_rows:
            if _gk(r) == gk and r["adjudication"]["class"] == "blunder":
                n_bl += 1
        if n_sc > 0:
            tl_pg[gk] = [1.0] * min(n_bl, n_sc) + [0.0] * max(0, n_sc - n_bl)
    punt_rate_textlane = _cb_block([tl_pg[k] for k in sorted(tl_pg)])
    punt_rate_textlane["denominator"] = "all scoreable decisions"
    punt_rate_textlane["label"] = (
        "textlane-verified only (rules engine-verifiable from the prompt "
        "snapshot); kept strictly separate from rollout punt_rate")

    # per_game_worst_p95: p95 across games of each game's max WPL.
    worst = [max(wpl_pg[k]) for k in sorted(wpl_pg) if wpl_pg[k]]
    per_game_worst_p95 = metric_block(
        _p95(worst), None, [None, None], len(worst), len(worst), "exact")
    per_game_worst_p95["denominator"] = "games with >=1 rollout adjudication"

    # flag_rate: flagged (text or shadow lane) / scoreable, clustered by game.
    fr_pg = []
    for gk, n_sc in sorted(per_game_scoreable.items()):
        if n_sc <= 0:
            continue
        n_fl = min(stats["per_game_flagged"].get(gk, 0), n_sc)
        fr_pg.append([1.0] * n_fl + [0.0] * (n_sc - n_fl))
    flag_rate = _cb_block(fr_pg)
    flag_rate["denominator"] = "all scoreable decisions"
    flag_rate["margin_over_never_flag"] = flag_rate["est"]

    # audit lane punt rate — THE LEAKAGE MEASUREMENT.
    audit_pg = _lane_rate_clusters(rows, "audit-sample")
    audit_lane_punt_rate = _cb_block(
        [audit_pg[k] for k in sorted(audit_pg)])
    audit_lane_punt_rate["denominator"] = (
        "rollout-adjudicated decisions in the audit sample")
    flagged_pg = _lane_rate_clusters(rows, "flagged")
    flagged_lane_punt_rate = _cb_block(
        [flagged_pg[k] for k in sorted(flagged_pg)])
    flagged_lane_punt_rate["denominator"] = (
        "rollout-adjudicated decisions in text-flag or shadow-disagree lanes")

    if audit_lane_punt_rate["n"] == 0:
        flaggers_leaky = None
        leaky_note = ("audit lane unadjudicated (no rollout driver ran over "
                      "it): flagger leakage is UNMEASURED, not zero")
    else:
        lo = audit_lane_punt_rate["ci95"][0]
        flaggers_leaky = bool(lo is not None and lo > 0.0)
        if flaggers_leaky:
            leaky_note = ("audit lane finds blunders at a rate whose 95% CI "
                          "excludes 0: the flaggers are LEAKY (recall < 1) "
                          "and flagged-lane counts undercount the true punt "
                          "rate")
        else:
            leaky_note = ("audit-lane blunder rate CI includes 0: no "
                          "measured leakage at this sample size (n=%d)"
                          % audit_lane_punt_rate["n"])

    flagger_lift = _lift_block(flagged_pg, audit_pg)
    flagger_lift["note"] = (
        "flagged-lane punt rate minus audit-lane punt rate; a lift whose CI "
        "includes 0 means the flaggers are not selecting anything above "
        "chance (their precision equals base-rate prevalence)")
    flagged_lane_punt_rate["margin_over_audit_lane"] = flagger_lift["est"]

    always_good_est = (1.0 - punt_rate["est"]
                       if punt_rate["est"] is not None else None)
    baselines = {
        "always_flag": {"est": 1.0, "se": 0.0, "ci95": [1.0, 1.0],
                        "n": n_scoreable, "n_clusters": stats["n_games"],
                        "n_decisions": n_scoreable,
                        "n_games": stats["n_games"], "method": "exact",
                        "note": "flag everything: the flag_rate ceiling"},
        "never_flag": {"est": 0.0, "se": 0.0, "ci95": [0.0, 0.0],
                       "n": n_scoreable, "n_clusters": stats["n_games"],
                       "n_decisions": n_scoreable,
                       "n_games": stats["n_games"], "method": "exact",
                       "note": "flag nothing: the flag_rate floor"},
        "random_flag": {
            "est": flag_rate["est"],
            "expected_precision_under_random_flagging":
                audit_lane_punt_rate["est"],
            "note": ("flagging uniformly at the observed rate has precision "
                     "equal to the audit-lane punt rate (base prevalence); "
                     "a flagged-lane punt rate equal to the audit-lane rate "
                     "means ZERO precision above chance — see flagger_lift")},
        "always_good_baseline": {
            "est": always_good_est,
            "note": ("accuracy of classifying every rollout-adjudicated "
                     "decision 'good' = 1 - punt_rate (majority-class "
                     "baseline for the classification)")},
    }

    summary = {
        "t": "summary",
        "summary_schema": "blunder-summary.v1",
        "generated_utc": (generated_utc if generated_utc is not None else
                          datetime.datetime.utcnow().strftime(
                              "%Y%m%dT%H%M%SZ")),
        "params": params,
        "counts": {
            "n_games": stats["n_games"],
            "n_decisions": stats["n_decisions"],
            "n_scoreable": stats["n_scoreable"],
            "n_text_flagged": stats["n_text_flagged"],
            "n_audit": stats["n_audit"],
            "n_rows_emitted": len(rows),
            "n_rollout_adjudicated": len(rollout_rows),
            "n_textlane_verified": len(textlane_rows),
            "n_unadjudicable": len(rows) - len(rollout_rows)
                               - len(textlane_rows),
            "skip_reasons": stats["skip_reasons"],
            "flag_counts": stats["flag_counts"],
            "flagged_by_precision_class": stats["flagged_by_precision_class"],
        },
        "n_shadow_rows_available": stats["n_shadow_rows_available"],
        "shadow_lane_note": stats["shadow_lane_note"],
        "dq_wpl_mean": dq_wpl_mean,
        "punt_rate": punt_rate,
        "punt_rate_by_lane": punt_rate_by_lane,
        "punt_rate_textlane": punt_rate_textlane,
        "per_game_worst_p95": per_game_worst_p95,
        "flag_rate": flag_rate,
        "audit_lane_punt_rate": audit_lane_punt_rate,
        "flagged_lane_punt_rate": flagged_lane_punt_rate,
        "flaggers_leaky": flaggers_leaky,
        "flaggers_leaky_note": leaky_note,
        "flagger_lift": flagger_lift,
        "baselines": baselines,
        "warnings": list(stats.get("warnings") or []),
    }
    if stats["n_shadow_rows_available"] > 0:
        summary["shadow_disagree_rate"] = {
            "est": (stats["n_shadow_disagree"]
                    / stats["n_shadow_rows_available"]),
            "n": stats["n_shadow_rows_available"], "method": "exact"}
    if HAVE_PROMPT_CONTRACT:
        prompt_contract.stamp(summary)
    return summary


# ------------------------------------------------------------------ output

def emit_row(row):
    """Project a scanned+adjudicated internal row onto the blunder-report.v1
    line (required keys) plus the contract-listed additive fields. The
    verbose prompt/options are NOT emitted (the row carries the replay
    address; the source game file has the verbatim prompt)."""
    out = {
        "schema": "blunder-report.v1",
        "run_dir": row["run_dir"],
        "game_file": row["game_file"],
        "decision_index": row["decision_index"],
        "seat": row["seat"],
        "model": row["model"],
        "turn": row["turn"],
        "decision_type": row["decision_type"],
        "n_options": row["n_options"],
        "choice": row["choice"],
        "flag_lane": row["flag_lane"],
        "flags": row["flags"],
        "shadow_choice": row.get("shadow_choice"),
        "adjudication": row["adjudication"],
        "lanes": row["lanes"],
        "deck_slug": row.get("deck_slug"),
        "evidence": row.get("evidence") or [],
        "rollout_key": row.get("rollout_key"),
    }
    for k in ("precision_class", "noise_limited", "unadjudicable_reason",
              "textlane_fallback_from"):
        if row.get(k) is not None:
            out[k] = row[k]
    if HAVE_PROMPT_CONTRACT:
        prompt_contract.stamp(out)
    return out


def write_outputs(out_dir, rows, summary):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "decisions.jsonl", "w", encoding="utf-8") as f:
        for row in sorted(rows, key=lambda r: r["rid"]):
            f.write(json.dumps(emit_row(row), sort_keys=True) + "\n")
        f.write(json.dumps(summary, sort_keys=True) + "\n")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, sort_keys=True, indent=2) + "\n")


def _fmt_mb(name, mb):
    est = mb.get("est")
    ci = mb.get("ci95") or [None, None]
    return "  %-24s est=%s ci95=[%s, %s] n=%s games=%s" % (
        name,
        ("%.4f" % est) if isinstance(est, float) else est,
        ("%.4f" % ci[0]) if isinstance(ci[0], float) else ci[0],
        ("%.4f" % ci[1]) if isinstance(ci[1], float) else ci[1],
        mb.get("n"), mb.get("n_clusters"))


def print_text_summary(summary, out=sys.stdout):
    c = summary["counts"]
    print("blunder report: %d games, %d decisions, %d scoreable, "
          "%d flagged rows emitted"
          % (c["n_games"], c["n_decisions"], c["n_scoreable"],
             c["n_rows_emitted"]), file=out)
    print("  adjudicated: rollout=%d textlane=%d unadjudicable=%d"
          % (c["n_rollout_adjudicated"], c["n_textlane_verified"],
             c["n_unadjudicable"]), file=out)
    if summary.get("shadow_lane_note"):
        print("  NOTE: %s" % summary["shadow_lane_note"], file=out)
    for name in ("flag_rate", "dq_wpl_mean", "punt_rate",
                 "punt_rate_textlane", "audit_lane_punt_rate",
                 "flagger_lift"):
        print(_fmt_mb(name, summary[name]), file=out)
    print("  flaggers_leaky=%s — %s"
          % (summary["flaggers_leaky"], summary["flaggers_leaky_note"]),
          file=out)
    print("  flag fire counts: %s" % json.dumps(
        c["flag_counts"], sort_keys=True), file=out)


# --------------------------------------------------------------------- CLI

def _make_driver(args, warnings):
    if args.driver == "none":
        return None
    if not HAVE_ROLLOUT_DRIVER:
        warnings.append("verify_keys.py not importable: rollout driver "
                        "unavailable, degrading to unadjudicable")
        return None
    if args.driver == "fixture":
        if not args.fixture:
            return None
        return FixtureRolloutDriver(args.fixture)
    return RolloutDriver(n_per_option=args.n_per_option)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", metavar="DIR",
                    help="run directories scanned recursively for game-*.jsonl")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--model", default=None,
                    help="override the per-seat model attribution")
    ap.add_argument("--audit-rate", type=float, default=0.05)
    ap.add_argument("--sample-seed", type=int, default=20260728)
    ap.add_argument("--adjudicate", default="textlane",
                    choices=("none", "textlane", "rollout"))
    ap.add_argument("--driver", default="none",
                    choices=("engine", "fixture", "none"))
    ap.add_argument("--fixture", default=None,
                    help="rollout-result fixture jsonl (--driver fixture)")
    ap.add_argument("--n-per-option", type=int, default=16)
    ap.add_argument("--max-adjudications", type=int, default=None)
    ap.add_argument("--json", action="store_true",
                    help="dump the summary object to stdout as the only "
                         "stdout content")
    ap.add_argument("--generated-utc", default=None,
                    help="override the summary timestamp (for byte-"
                         "reproducible runs; defaults to now)")
    args = ap.parse_args(argv)

    if not (0.0 <= args.audit_rate <= 1.0):
        print("audit-rate must be in [0,1]", file=sys.stderr)
        return 2
    if args.generated_utc is None:
        args.generated_utc = datetime.datetime.utcnow().strftime(
            "%Y%m%dT%H%M%SZ")
    if args.driver == "fixture" and not args.fixture:
        print("--driver fixture requires --fixture FILE.jsonl",
              file=sys.stderr)
        return 2

    entries = []
    for dir_arg in args.dirs:
        root = Path(dir_arg)
        if not root.is_dir():
            print("not a directory: %s" % dir_arg, file=sys.stderr)
            return 2
        for gd in iter_game_dirs(root):
            games, warns = load_complete_games(gd)
            for w in warns:
                print("WARNING: %s: %s" % (gd, w), file=sys.stderr)
            for e in games:
                e["run_dir"] = str(gd)
                entries.append(e)
    if not entries:
        print("nothing usable to measure: no complete games under %s"
              % ", ".join(args.dirs), file=sys.stderr)
        return 2

    rows, stats = scan(entries, model=args.model,
                       audit_rate=args.audit_rate,
                       sample_seed=args.sample_seed)

    warnings = stats["warnings"]
    driver = _make_driver(args, warnings)
    adjudicate_rows(rows, args.adjudicate, driver,
                    n_per_option=args.n_per_option,
                    max_adjudications=args.max_adjudications)

    params = {"run_dirs": args.dirs, "model": args.model,
              "audit_rate": args.audit_rate, "sample_seed": args.sample_seed,
              "adjudicate": args.adjudicate, "driver": args.driver,
              "fixture": args.fixture, "n_per_option": args.n_per_option,
              "max_adjudications": args.max_adjudications,
              "have_rollout_driver": HAVE_ROLLOUT_DRIVER,
              "have_prompt_contract": HAVE_PROMPT_CONTRACT}
    summary = build_summary(rows, stats, params,
                            generated_utc=args.generated_utc)
    write_outputs(args.out, rows, summary)

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print_text_summary(summary)

    if args.adjudicate == "rollout" and driver is None:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
