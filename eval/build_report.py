#!/usr/bin/env python3
"""Build a frozen eval report / v1.0 scorecard.

Two things live here, and they compose into ONE command:

1. The SUITE report (original behaviour): consumes the per-arm output dirs
   produced by the run_batch.py commands that run_suite.py prints, judges the
   suite gates, and emits report.json/report.md.

2. The v1.0 SCORECARD: the five eval axes side by side —

       STR   match strength     (aggregate.py over the suite arms)
       PUZ   puzzle accuracy    (eval/puzzle_run.py summary.json)
       PLAN  plan adherence     (eval/plan_adherence.py summary.json)
       DQ    decision quality   (eval/blunder_report.py summary.json)
       PUNT  blunder rate       (eval/blunder_report.py summary.json)

   Every scorecard number carries n / se / ci95 AND its trivial floor with the
   margin over that floor. An axis with NO input data renders as an explicit
   `not measured` row carrying the exact command that would measure it. It is
   never rendered as 0 and never silently dropped: a benchmark that shows
   missing data as a zero is worse than one that shows nothing.

Statistics are NOT recomputed here: aggregate.py --json is the single source
for win rates/CIs, defect_analyzer.py for harness invariants,
check_holdouts.py for contamination, and the three axis runners own their own
blocks. This script orchestrates, judges, and lines the numbers up.

    <out>/suite.json        copy of the suite config (as validated)
    <out>/report.json       machine-readable results + gate verdicts + scorecard
    <out>/report.md         human-readable summary + scorecard
    <out>/scorecard.json    the scorecard alone (stable top-level artifact)
    <out>/defects-<arm>.json/.txt   defect_analyzer evidence per arm

Usage:
    # full scorecard, all five axes
    python3 eval/build_report.py eval/suites/stage3-v1.json \
        --student openrouter:default_model \
        --puz-run runs/puz/latest --plan-run runs/plan/latest \
        --blunder-run runs/blunder/latest [--out DIR] [--json]

    # scorecard with no STR suite at all (STR renders "not measured")
    python3 eval/build_report.py --puz-run runs/puz/latest

Arm dirs default to runs/<suite>-<arm> (what run_suite.py prints).
Exit 0 = PASS, 1 = FAIL (a gate failed), 2 = usage error, 3 = INCOMPLETE
(at least one arm has no usable data, so no verdict is earned). Axes that are
"not measured" do NOT by themselves make the verdict FAIL — they make it
INCOMPLETE, because an unmeasured axis is an absence of evidence, not a
failure.
"""
import argparse
import datetime
import json
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
from run_suite import parse_seed_range  # noqa: E402
import prompt_contract  # noqa: E402

PY = sys.executable or "python3"

SCORECARD_SCHEMA = "eval-scorecard.v1"
AXES = ("STR", "PUZ", "PLAN", "DQ", "PUNT")

AXIS_TITLE = {
    "STR": "match strength (win rate vs a fixed opponent ladder)",
    "PUZ": "puzzle accuracy on verified answer keys",
    "PLAN": "plan adherence (deck-plan milestone profile)",
    "DQ": "decision quality (win-probability loss per decision)",
    "PUNT": "blunder rate (punts per scoreable decision)",
}

# The exact command that produces each axis's input. Printed verbatim next to
# any axis that is not measured, so "not measured" is actionable rather than
# decorative.
AXIS_HOWTO = {
    "STR": (
        "python3 eval/run_suite.py eval/suites/stage3-v1.json   # prints the "
        "run_batch.py commands; run them, then re-run build_report.py with "
        "the suite path"),
    "PUZ": (
        "python3 eval/mine_puzzles.py <run-dirs...> --out cand.jsonl && "
        "python3 eval/verify_keys.py cand.jsonl --out positions.jsonl "
        "--lane both --driver engine && "
        "python3 eval/puzzle_run.py positions.jsonl --backend offline "
        "--out RUNDIR   # then: build_report.py --puz-run RUNDIR"),
    "PLAN": (
        "python3 eval/plan_adherence.py <run-dirs...> --out RUNDIR   "
        "# then: build_report.py --plan-run RUNDIR"),
    "DQ": (
        "python3 eval/blunder_report.py <run-dirs...> --out RUNDIR "
        "--adjudicate rollout --driver engine   "
        "# then: build_report.py --blunder-run RUNDIR"),
    "PUNT": (
        "python3 eval/blunder_report.py <run-dirs...> --out RUNDIR "
        "--adjudicate rollout --driver engine   "
        "# then: build_report.py --blunder-run RUNDIR"),
}

# Fixed bar above which puz_score_excl stops being comparable across models
# (a model that refuses on the hard positions is scored only on the subset it
# chose). See selective_refusal_lift.
IFACE_FAILURE_BAR = 0.10


def run_json(cmd):
    """Run cmd, parse stdout as JSON. Returns (data, returncode, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    try:
        return json.loads(p.stdout), p.returncode, p.stderr.strip()
    except json.JSONDecodeError:
        return None, p.returncode, (p.stderr.strip() or
                                    f"non-JSON stdout: {p.stdout[:200]}")


def pct(x):
    return f"{100 * x:.1f}%" if isinstance(x, (int, float)) else "n/a"


# ===========================================================================
# SCORECARD
# ===========================================================================

def _num(x):
    """The value if it is a real (non-bool) number, else None. Everything the
    scorecard prints goes through this: a string, a bool or a None from an
    upstream summary must never be arithmetic'd into a fake score."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if x != x:                      # NaN
        return None
    return x


def _fmt(x, nd=3):
    v = _num(x)
    return "—" if v is None else ("%%.%df" % nd) % v


def _ci(blk, nd=3):
    ci = (blk or {}).get("ci95") or [None, None]
    if len(ci) != 2 or _num(ci[0]) is None or _num(ci[1]) is None:
        return "—"
    return "[%s, %s]" % (_fmt(ci[0], nd), _fmt(ci[1], nd))


def floor_entry(name, est, metric_est, margin=None, note=None):
    """One trivial-floor entry: its value and the metric's margin over it.
    A floor whose own est is None is still emitted — an absent floor is
    information ("this floor is unmeasured"), and dropping it would let a
    number be read without one."""
    est = _num(est)
    if margin is None and est is not None and _num(metric_est) is not None:
        margin = metric_est - est
    e = {"floor": name, "est": est, "margin": _num(margin)}
    if note:
        e["note"] = note
    return e


def metric_row(name, blk, floors=None, denominator=None, note=None,
               headline=True, reason=None, kind=None):
    """Uniform scorecard row. status is `measured` only when there is an
    estimate standing on a non-empty denominator; everything else is
    `not_measured` and prints as such (never as 0.0)."""
    blk = blk if isinstance(blk, dict) else {}
    est = _num(blk.get("est"))
    n = blk.get("n")
    n = n if isinstance(n, int) and not isinstance(n, bool) else None
    measured = est is not None and (n is None or n > 0)
    row = {
        "metric": name,
        "kind": kind,
        "status": "measured" if measured else "not_measured",
        "headline": bool(headline),
        "est": est,
        "se": _num(blk.get("se")),
        "ci95": [_num(x) for x in ((blk.get("ci95") or [None, None])[:2] or
                                   [None, None])],
        "n": n,
        "n_clusters": blk.get("n_clusters"),
        "method": blk.get("method"),
        "denominator": denominator or blk.get("denominator"),
        "floors": list(floors or []),
        "note": note or blk.get("note"),
    }
    if not measured:
        row["reason"] = reason or (
            "no observations (n=%s) — UNMEASURED, not zero" % (n,))
    return row


def axis_not_measured(axis, reason):
    return {"axis": axis, "title": AXIS_TITLE[axis], "status": "not_measured",
            "reason": reason, "how_to_measure": AXIS_HOWTO[axis],
            "source": None, "metrics": [], "caveats": [], "provenance": {}}


def load_summary(path, filenames):
    """Resolve a --*-run argument to a summary dict. Accepts either the run
    dir or the summary file itself. -> (data, resolved_path_str, error_str)."""
    p = Path(path)
    if not p.is_absolute():
        p = REPO / p
    cands = [p] if p.is_file() else [p / f for f in filenames]
    for c in cands:
        if c.is_file():
            try:
                return json.loads(c.read_text()), str(c), None
            except Exception as exc:        # noqa: BLE001 - reported, not raised
                return None, str(c), "unreadable (%s)" % exc
    return None, str(p), "no %s under %s" % (" or ".join(filenames), p)


# ------------------------------------------------------------------- STR axis

def build_str_axis(arms, suite_path):
    if not arms:
        return axis_not_measured(
            "STR", "no suite given (build_report.py was run without a suite "
                   "path), so no arm produced a win rate")
    scored = [a for a in arms
              if (a.get("result") or {}).get("student_win_rate") is not None]
    if not scored:
        return axis_not_measured(
            "STR", "suite %s has %d enabled arm(s) but none produced usable "
                   "game data (%s)" % (
                       suite_path, len(arms),
                       ", ".join(sorted(set(a["status"] for a in arms)))))
    metrics = []
    for a in scored:
        r = a["result"]
        wr = _num(r.get("student_win_rate"))
        n = r.get("games_decisive")
        se = None
        if wr is not None and isinstance(n, int) and n > 0:
            se = math.sqrt(max(wr * (1.0 - wr), 0.0) / n)
        blk = {"est": wr, "se": se, "ci95": r.get("student_wilson95"),
               "n": n, "n_clusters": r.get("n_pairs_used"), "method": "wilson"}
        metrics.append(metric_row(
            "str.win_rate.%s" % a["arm"], blk,
            kind="win_rate",
            denominator="decisive games in arm %r" % a["arm"],
            floors=[floor_entry(
                "mirror_coinflip", 0.5, wr,
                note="both seats play the same deck list, so a policy that is "
                     "indistinguishable from its opponent wins 50%")],
            note="se is the normal-approximation binomial se; the interval is "
                 "Wilson (aggregate.py)"))
        d = _num(r.get("paired_delta"))
        dci = r.get("paired_delta_ci95") or [None, None]
        metrics.append(metric_row(
            "str.paired_delta.%s" % a["arm"],
            {"est": d, "se": None, "ci95": dci, "n": r.get("n_pairs_used"),
             "n_clusters": r.get("n_pairs_used"), "method": "paired-bootstrap"},
            kind="paired_delta", headline=False,
            denominator="seed pairs used in arm %r" % a["arm"],
            floors=[floor_entry(
                "no_difference", 0.0, d,
                note="a paired delta of 0 is the null: the student and the "
                     "opponent split every seed")]))
    prov = {}
    for a in scored:
        for k, v in ((a.get("result") or {}).get("provenance") or {}).items():
            prov.setdefault(k, set()).update(
                v if isinstance(v, list) else [v])
    axis = {"axis": "STR", "title": AXIS_TITLE["STR"], "status": "measured",
            "reason": None, "how_to_measure": AXIS_HOWTO["STR"],
            "source": suite_path, "metrics": metrics,
            "caveats": [], "provenance": {
                k: sorted(str(x) for x in v) for k, v in prov.items()}}
    unscored = [a["arm"] for a in arms if a not in scored]
    if unscored:
        axis["caveats"].append(
            "PARTIAL: arm(s) %s produced no usable data and are absent from "
            "this axis — the STR numbers below are NOT the full suite"
            % ", ".join(sorted(unscored)))
    return axis


# ------------------------------------------------------------------- PUZ axis

def build_puz_axis(path):
    if not path:
        return axis_not_measured(
            "PUZ", "no --puz-run given: eval/puzzle_run.py has not been run "
                   "(or its output was not passed in)")
    data, resolved, err = load_summary(path, ["summary.json"])
    if data is None:
        return axis_not_measured("PUZ", "--puz-run %s: %s" % (path, err))
    m = data.get("metrics") or {}
    b = data.get("baselines") or {}
    n_pos = data.get("n_positions")
    if not n_pos:
        return axis_not_measured(
            "PUZ", "%s scored 0 positions (skips: %s) — every candidate was "
                   "filtered out, so there is no accuracy to report"
                   % (resolved, json.dumps(data.get("skips") or {})))

    def fl(metric_blk, twin=None):
        """Every floor the run measured, + the oracle ceiling, for one metric.

        max_constant_index dominates always_first by construction and is the
        one to read; always_first is kept beside it because artifacts minted
        before that floor existed only have always_first, and dropping it here
        would silently change what old scorecards mean. Floors absent from the
        run are skipped rather than rendered as null -- a floor that reads
        `null` is indistinguishable from a floor of zero at a glance, which is
        the exact confusion this axis exists to prevent.
        """
        out = []
        for name in ("random_choice", "always_pass", "always_first",
                     "max_constant_index", "majority_class"):
            if name not in b:
                continue
            src = b.get(name) or {}
            src = (src.get(twin) or {}) if twin else src
            out.append(floor_entry(
                name, src.get("est"), _num(metric_blk.get("est")),
                margin=metric_blk.get("margin_over_%s" % name)))
        oc = b.get("oracle_ceiling") or {}
        oc = (oc.get(twin) or {}) if twin else oc
        out.append(floor_entry("oracle_ceiling", oc.get("est"),
                               _num(metric_blk.get("est")),
                               note="CEILING, not a floor: the best score the "
                                    "answer keys allow"))
        return out

    incl = m.get("puz_score_incl") or {}
    excl = m.get("puz_score_excl") or {}
    ifr = m.get("interface_failure_rate") or {}
    p1 = m.get("pass_1") or {}
    pk = m.get("pass_k") or {}

    metrics = [
        # HEADLINE is pass_1, not score_incl. Measured 2026-07-29 on a suite
        # deliberately balanced so the pass_1 floors sit at chance: a constant
        # "answer 1" policy still collected 5.17/10 on score_incl while solving
        # 25% of positions. Partial credit is forgiving, so score_incl carries a
        # trivial floor above 50% where pass_1's is 0.25 -- and the project rule
        # is to prefer metrics whose trivial baseline is near zero. score_incl
        # stays reported (it is the finer-grained signal once a model is
        # actually above the floor); it just does not lead.
        metric_row("puz.pass_1", p1, kind="rate", floors=fl(p1, "pass_1"),
                   denominator="all valid samples",
                   note="HEADLINE. Fraction of positions solved outright "
                        "(score == 10). Preferred over score_incl because its "
                        "trivial floor is near chance, not ~50%."),
        metric_row("puz.score_incl", incl, kind="score_0_10",
                   floors=fl(incl),
                   denominator="all valid samples (interface failures score 0)",
                   headline=False,
                   note="0..10 partial-credit score; refusals count against "
                        "you. NOT the headline: partial credit hands a "
                        "constant policy ~5/10 even on a floor-balanced suite. "
                        "Read it with margin_over_always_first."),
        metric_row("puz.pass_k", pk, kind="rate", floors=fl(pk, "pass_k"),
                   denominator="positions with a full set of k=%s samples"
                               % pk.get("k_used"),
                   headline=False),
        metric_row("puz.interface_failure_rate", ifr, kind="rate",
                   headline=False,
                   floors=[floor_entry("never_fail", 0.0, _num(ifr.get("est")))],
                   denominator="all valid samples"),
    ]

    caveats = list(data.get("caveats") or [])
    # SELECTIVE REFUSAL. puz_score_excl drops the samples the model refused on
    # from its own denominator, so a model that answers the easy positions and
    # emits garbage on the hard ones is scored on the subset it picked. The
    # lift makes the size of that choice visible; above the bar the metric is
    # demoted out of the headline entirely.
    lift = None
    if _num(excl.get("est")) is not None and _num(incl.get("est")) is not None:
        lift = excl["est"] - incl["est"]
    ifr_est = _num(ifr.get("est"))
    excl_comparable = ifr_est is not None and ifr_est <= IFACE_FAILURE_BAR
    metrics.append(metric_row(
        "puz.score_excl", excl, kind="score_0_10", headline=False,
        floors=fl(excl),
        denominator="valid non-interface-failure samples only",
        note="NOT COMPARABLE ACROSS MODELS when the interface failure rate is "
             "high: refusing removes the observation instead of scoring 0. "
             "selective_refusal_lift = %s (score_excl - score_incl); "
             "interface_failure_rate = %s vs bar %.2f -> %s"
             % (_fmt(lift), _fmt(ifr_est), IFACE_FAILURE_BAR,
                "within bar" if excl_comparable else "OVER BAR")))
    if not excl_comparable:
        caveats.append(
            "SELECTIVE REFUSAL: interface_failure_rate=%s exceeds the %.2f bar "
            "(or is unmeasured). puz.score_excl is not comparable across "
            "models; quote puz.score_incl only. selective_refusal_lift=%s"
            % (_fmt(ifr_est), IFACE_FAILURE_BAR, _fmt(lift)))
    # Positional skew, reported only when it is real. The previous version
    # fired this caveat whenever best_index_counts existed -- i.e. always,
    # including on a suite whose floors sit at chance -- and pointed the reader
    # at margin_over_always_first, which is the WRONG floor: slot 1 is one
    # constant policy of many. A caveat that cannot distinguish a balanced
    # suite from a skewed one is noise, and the repo already lost a night to a
    # warning that cried wolf.
    af = (b.get("always_first") or {}).get("best_index_counts")
    mci = (b.get("max_constant_index") or {}).get("pass_1") or {}
    mci_est, rnd_est = mci.get("est"), \
        ((b.get("random_choice") or {}).get("pass_1") or {}).get("est")
    n_pos = ((b.get("always_first") or {}).get("pass_1") or {}).get("n")
    if af and mci_est is not None and rnd_est is not None and n_pos:
        # Skewed = the best constant policy beats uniform guessing by more
        # than its own sampling noise. Same 1.96-sigma rule the rest of the
        # repo uses, so the bar is not a new invented constant.
        se = math.sqrt(max(mci_est * (1.0 - mci_est), 0.0) / float(n_pos))
        if mci_est - rnd_est > 1.96 * se:
            caveats.append(
                "POSITIONAL SKEW: 'always answer %s' scores pass_1=%s against "
                "a %s random baseline (slot counts %s). Quote "
                "margin_over_max_constant_index -- NOT "
                "margin_over_always_first, which measures only slot 1 and "
                "reads as chance while the skew sits one slot over."
                % ((b.get("max_constant_index") or {}).get("index"),
                   _fmt(mci_est), _fmt(rnd_est), json.dumps(af)))

    return {
        "axis": "PUZ", "title": AXIS_TITLE["PUZ"], "status": "measured",
        "reason": None, "how_to_measure": AXIS_HOWTO["PUZ"],
        "source": resolved, "metrics": metrics, "caveats": caveats,
        "selective_refusal_lift": _num(lift),
        "provenance": {
            "contract_sha": data.get("contract_sha"),
            "contract": data.get("contract"),
            "engine_sha": data.get("engine_sha"),
            "overlay_sha": data.get("overlay_sha"),
            "seed_blocks": data.get("seed_blocks"),
            "split": data.get("split"),
            "split_counts": data.get("split_counts"),
            "model": data.get("model"),
            "backend": data.get("backend"),
            "offline_policy": data.get("offline_policy"),
            "k": data.get("k"),
            "strictness": data.get("strictness"),
            "n_positions": n_pos,
            "n_samples": data.get("n_samples"),
            "n_clusters": data.get("n_clusters"),
        },
    }


# ------------------------------------------------------------------ PLAN axis

# PLAN is deliberately a PROFILE, never a scalar: collapsing six milestones of
# different kinds into one "plan score" would let a deck ace the cheap
# milestones and fail the deciding one at the same number.
_PLAN_KINDS = (
    # (block key, kind label, floor name, floor lookup)
    ("attain_rate", "attain_rate", "never_attained",
     lambda ms: (ms.get("baselines") or {}).get("never_attained", 0.0)),
    ("rate", "opportunity_rate", "never_take",
     lambda ms: (ms.get("baselines") or {}).get("never_take_rate", 0.0)),
    ("face_rate", "target_split", "uniform_random_face",
     lambda ms: (ms.get("baselines") or {}).get("uniform_random_face_rate")),
)


def build_plan_axis(path):
    if not path:
        return axis_not_measured(
            "PLAN", "no --plan-run given: eval/plan_adherence.py has not been "
                    "run (or its output was not passed in)")
    data, resolved, err = load_summary(path, ["summary.json"])
    if data is None:
        return axis_not_measured("PLAN", "--plan-run %s: %s" % (path, err))
    by_deck = data.get("by_deck") or {}
    if not by_deck:
        return axis_not_measured(
            "PLAN", "%s resolved no deck archetype (%s game(s), %s seat(s) "
                    "unresolved) — nothing to score against a plan"
                    % (resolved, data.get("n_games"),
                       data.get("n_seats_unresolved")))
    metrics = []
    for slug in sorted(by_deck):
        dk = by_deck[slug] or {}
        for mid in sorted((dk.get("milestones") or {})):
            ms = dk["milestones"][mid]
            for key, kind, fname, floor_of in _PLAN_KINDS:
                if key not in ms:
                    continue
                blk = ms.get(key) or {}
                est = _num(blk.get("est"))
                floors = [floor_entry(fname, floor_of(ms), est)]
                if kind == "attain_rate":
                    floors.append(floor_entry(
                        "always_attained", 1.0, est,
                        note="CEILING, not a floor"))
                elif kind == "opportunity_rate":
                    floors.append(floor_entry(
                        "always_take", (ms.get("baselines") or {}).get(
                            "always_take_rate", 1.0), est,
                        note="CEILING, not a floor"))
                metrics.append(metric_row(
                    "plan.%s.%s" % (slug, mid), blk, kind=kind, floors=floors,
                    denominator=("%s opportunities" % ms.get("n_opportunities")
                                 if kind == "opportunity_rate"
                                 else "%s seat-game(s)"
                                      % ms.get("n_seat_games")),
                    note="weight=%s" % ms.get("weight")))
                break
    caveats = list(data.get("caveats") or [])
    caveats.append(
        "PLAN IS A PROFILE, NOT A SCORE: these milestones are never collapsed "
        "into one number. A deck that aces the cheap milestones and misses the "
        "deciding one would look identical to the reverse at any single "
        "aggregate.")
    if data.get("n_seats_unresolved"):
        caveats.append(
            "%s seat(s) could not be resolved to a deck archetype and are "
            "absent from every milestone denominator"
            % data.get("n_seats_unresolved"))
    return {
        "axis": "PLAN", "title": AXIS_TITLE["PLAN"], "status": "measured",
        "reason": None, "how_to_measure": AXIS_HOWTO["PLAN"],
        "source": resolved, "metrics": metrics, "caveats": caveats,
        "provenance": {
            "contract_sha": data.get("contract_sha"),
            "contract_sha_note": (
                "PLAN reads raw engine decision rows, not contract-rendered "
                "prompts, so it carries no contract_sha by design"),
            "dirs": data.get("dirs"),
            "n_games": data.get("n_games"),
            "n_seats_resolved": data.get("n_seats_resolved"),
            "n_seats_unresolved": data.get("n_seats_unresolved"),
            "decks": sorted(by_deck),
        },
    }


# ------------------------------------------------------------- DQ / PUNT axes

def _blunder_provenance(data, resolved):
    counts = data.get("counts") or {}
    return {
        "contract_sha": data.get("contract_sha"),
        "contract": data.get("contract"),
        "params": data.get("params"),
        "n_games": counts.get("n_games"),
        "n_decisions": counts.get("n_decisions"),
        "n_scoreable": counts.get("n_scoreable"),
        "n_rollout_adjudicated": counts.get("n_rollout_adjudicated"),
        "n_textlane_verified": counts.get("n_textlane_verified"),
        "source": resolved,
    }


def build_dq_axis(path):
    if not path:
        return axis_not_measured(
            "DQ", "no --blunder-run given: eval/blunder_report.py has not "
                  "been run (or its output was not passed in)")
    data, resolved, err = load_summary(path, ["summary.json"])
    if data is None:
        return axis_not_measured("DQ", "--blunder-run %s: %s" % (path, err))
    wpl = data.get("dq_wpl_mean") or {}
    if not (wpl.get("n") or 0):
        return axis_not_measured(
            "DQ", "%s adjudicated 0 decisions by rollout (driver=%s, "
                  "adjudicate=%s). Win-probability loss REQUIRES rollouts; "
                  "the textlane can prove a blunder happened but cannot price "
                  "it. DQ is UNMEASURED, not 0."
                  % (resolved, (data.get("params") or {}).get("driver"),
                     (data.get("params") or {}).get("adjudicate")))
    worst = data.get("per_game_worst_p95") or {}
    metrics = [
        metric_row("dq.wpl_mean", wpl, kind="wpl", floors=[floor_entry(
            "random_choice_wpl", wpl.get("baseline_random_choice_wpl"),
            _num(wpl.get("est")),
            margin=wpl.get("margin_over_random_choice"),
            note="the WPL a uniform-random pick from the same menu would "
                 "have incurred")],
                   note="LOWER IS BETTER."),
        metric_row("dq.per_game_worst_p95", worst, kind="wpl", headline=False,
                   floors=[floor_entry(
                       "zero_loss", 0.0, _num(worst.get("est")),
                       note="a policy that never loses win probability")],
                   note="LOWER IS BETTER. p95 across games of each game's "
                        "single worst decision."),
    ]
    return {"axis": "DQ", "title": AXIS_TITLE["DQ"], "status": "measured",
            "reason": None, "how_to_measure": AXIS_HOWTO["DQ"],
            "source": resolved, "metrics": metrics,
            "caveats": list(data.get("warnings") or []),
            "provenance": _blunder_provenance(data, resolved)}


def build_punt_axis(path):
    if not path:
        return axis_not_measured(
            "PUNT", "no --blunder-run given: eval/blunder_report.py has not "
                    "been run (or its output was not passed in)")
    data, resolved, err = load_summary(path, ["summary.json"])
    if data is None:
        return axis_not_measured("PUNT", "--blunder-run %s: %s" % (path, err))
    counts = data.get("counts") or {}
    if not (counts.get("n_scoreable") or 0):
        return axis_not_measured(
            "PUNT", "%s found 0 scoreable decisions over %s game(s)"
                    % (resolved, counts.get("n_games")))
    pr = data.get("punt_rate") or {}
    tl = data.get("punt_rate_textlane") or {}
    fr = data.get("flag_rate") or {}
    al = data.get("audit_lane_punt_rate") or {}
    lift = data.get("flagger_lift") or {}
    base = data.get("baselines") or {}
    metrics = [
        metric_row(
            "punt.rate_rollout", pr, kind="rate",
            floors=[
                floor_entry("never_punt", 0.0, _num(pr.get("est")),
                            note="a policy that never blunders"),
                floor_entry(
                    "random_choice_punt_rate", None, _num(pr.get("est")),
                    note="UNMEASURED in v1: pricing a random policy's blunder "
                         "rate needs its own rollout pass"),
                floor_entry(
                    "always_good_classifier",
                    (base.get("always_good_baseline") or {}).get("est"),
                    None,
                    note="majority-class accuracy of calling every decision "
                         "good = 1 - punt_rate"),
            ],
            note="LOWER IS BETTER. Lower bound over the corpus: only the "
                 "flagged and audit lanes are adjudicated."),
        metric_row(
            "punt.rate_textlane", tl, kind="rate", headline=False,
            floors=[floor_entry("never_punt", 0.0, _num(tl.get("est")))],
            note="LOWER IS BETTER. Rules-engine-verifiable punts only; NEVER "
                 "add this to punt.rate_rollout."),
        metric_row(
            "punt.flag_rate", fr, kind="rate", headline=False,
            floors=[
                floor_entry("never_flag", 0.0, _num(fr.get("est"))),
                floor_entry("always_flag", 1.0, _num(fr.get("est")),
                            note="CEILING, not a floor"),
            ],
            note="INSTRUMENTATION: how much of the corpus the flaggers "
                 "selected for adjudication."),
        metric_row(
            "punt.audit_lane_rate", al, kind="rate", headline=False,
            floors=[floor_entry("never_punt", 0.0, _num(al.get("est")))],
            note="LEAKAGE MEASUREMENT: the blunder rate in the unflagged "
                 "random audit sample. If this is > 0 the flaggers miss "
                 "blunders and punt.rate_rollout undercounts."),
        metric_row(
            "punt.flagger_lift", lift, kind="rate_delta", headline=False,
            floors=[floor_entry(
                "no_lift", 0.0, _num(lift.get("est")),
                note="flaggers select nothing above the base rate")],
            note="flagged-lane punt rate minus audit-lane punt rate."),
    ]
    caveats = list(data.get("warnings") or [])
    caveats.append("flaggers_leaky=%s — %s" % (
        data.get("flaggers_leaky"), data.get("flaggers_leaky_note")))
    if data.get("n_shadow_rows_available") == 0:
        caveats.append(data.get("shadow_lane_note") or
                       "shadow lane vacuous (no SHADOW_AI=1 corpus)")
    return {"axis": "PUNT", "title": AXIS_TITLE["PUNT"], "status": "measured",
            "reason": None, "how_to_measure": AXIS_HOWTO["PUNT"],
            "source": resolved, "metrics": metrics, "caveats": caveats,
            "provenance": _blunder_provenance(data, resolved)}


# --------------------------------------------------------------- the assembly

def finalize_axis(ax):
    """An axis whose input existed but whose HEADLINE metric has no
    observations is `partial`, not `measured`. Reporting it as measured would
    let a scorecard claim coverage it does not have (e.g. PUNT with a
    textlane-only corpus: real evidence, but not the rollout punt rate the
    axis is defined as)."""
    if ax["status"] != "measured":
        return ax
    heads = [m for m in ax["metrics"] if m["headline"]]
    if heads and not any(m["status"] == "measured" for m in heads):
        ax["status"] = "partial"
        ax["reason"] = (
            "input present but every HEADLINE metric (%s) has zero "
            "observations; only non-headline metrics are measured. Treat this "
            "axis as UNMEASURED for ranking."
            % ", ".join(m["metric"] for m in heads))
    return ax


def _collect(axes, key):
    """{value: [axis names that reported it]} over axis provenance. A list
    value is FLATTENED to its members, so a scalar "abc" and a one-element
    ["abc"] from two different runners collapse to the same key instead of
    faking a mixed-provenance alarm."""
    out = {}
    for ax in axes.values():
        v = (ax.get("provenance") or {}).get(key)
        if v in (None, "", [], {}):
            continue
        for item in (v if isinstance(v, list) else [v]):
            if item in (None, ""):
                continue
            s = (json.dumps(item, sort_keys=True)
                 if isinstance(item, (dict, list)) else str(item))
            names = out.setdefault(s, [])
            if ax["axis"] not in names:
                names.append(ax["axis"])
    return out


def build_scorecard(arms, suite_path, suite, args, generated_utc):
    axes = {}
    axes["STR"] = finalize_axis(build_str_axis(arms, suite_path))
    axes["PUZ"] = finalize_axis(build_puz_axis(args.puz_run))
    axes["PLAN"] = finalize_axis(build_plan_axis(args.plan_run))
    axes["DQ"] = finalize_axis(build_dq_axis(args.blunder_run))
    axes["PUNT"] = finalize_axis(build_punt_axis(args.blunder_run))

    running_sha = prompt_contract.contract_sha()
    observed_sha = _collect(axes, "contract_sha")
    engine = _collect(axes, "engine_sha")
    overlay = _collect(axes, "overlay_sha")
    seed_bank = {
        "suite_declared": ((suite or {}).get("seed_policy") or {}).get("bank"),
        "puz_seed_blocks": (axes["PUZ"].get("provenance") or {}).get(
            "seed_blocks"),
    }
    prov_caveats = []
    stale = [s for s in observed_sha if s != running_sha]
    if stale:
        prov_caveats.append(
            "CONTRACT DRIFT: this build_report runs prompt contract %s but "
            "axis artifact(s) were produced under %s — the PUZ numbers were "
            "not generated by the contract in this tree and must not be "
            "compared to numbers that were."
            % (running_sha, ", ".join("%s (%s)" % (s, "/".join(observed_sha[s]))
                                      for s in sorted(stale))))
    for label, coll in (("engine_sha", engine), ("overlay_sha", overlay)):
        if len(coll) > 1:
            prov_caveats.append(
                "MIXED %s across axes: %s — axes measured on different engine "
                "builds are not one scorecard." % (label, json.dumps(coll)))
        if "mixed" in coll:
            prov_caveats.append(
                "%s is 'mixed' WITHIN axis %s: its own corpus spans more than "
                "one build." % (label, "/".join(coll["mixed"])))

    measured = [a for a in AXES if axes[a]["status"] == "measured"]
    partial = [a for a in AXES if axes[a]["status"] == "partial"]
    unmeasured = [a for a in AXES if axes[a]["status"] == "not_measured"]
    return {
        "schema": SCORECARD_SCHEMA,
        "generated_utc": generated_utc,
        "student": args.student,
        "provenance": {
            "contract_sha_running": running_sha,
            "contract": prompt_contract.CONTRACT_VERSION,
            "contract_sha_by_axis": observed_sha,
            "engine_sha": engine,
            "overlay_sha": overlay,
            "seed_bank": seed_bank,
            "caveats": prov_caveats,
        },
        "coverage": {
            "axes_total": len(AXES),
            "axes_measured": len(measured),
            "measured": measured,
            "partial": partial,
            "not_measured": unmeasured,
            "complete": not unmeasured and not partial,
        },
        "axes": [axes[a] for a in AXES],
    }


def render_scorecard_md(sc):
    L = []
    cov = sc["coverage"]
    L.append("## v1.0 scorecard — %d/%d axes measured"
             % (cov["axes_measured"], cov["axes_total"]))
    L.append("")
    p = sc["provenance"]
    L.append("- contract: `%s` sha `%s` (as run)"
             % (p["contract"], p["contract_sha_running"]))
    L.append("- contract_sha in artifacts: %s"
             % (json.dumps(p["contract_sha_by_axis"]) or "none"))
    L.append("- engine_sha: %s" % (json.dumps(p["engine_sha"]) or "none"))
    L.append("- overlay_sha: %s" % (json.dumps(p["overlay_sha"]) or "none"))
    L.append("- seed bank: %s" % json.dumps(p["seed_bank"]))
    for c in p["caveats"]:
        L.append("- **%s**" % c)
    if cov["not_measured"]:
        L.append("- **NOT MEASURED: %s — this scorecard is incomplete and "
                 "cannot rank models.**" % ", ".join(cov["not_measured"]))
    if cov.get("partial"):
        L.append("- **PARTIAL (headline metric has no observations): %s — "
                 "treat as unmeasured for ranking.**"
                 % ", ".join(cov["partial"]))
    L.append("")
    L.append("| axis | metric | est | se | 95% CI | n | clusters | "
             "trivial floor | margin over floor |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for ax in sc["axes"]:
        if ax["status"] == "not_measured":
            L.append("| **%s** | _not measured_ | — | — | — | — | — | — | — |"
                     % ax["axis"])
            continue
        if ax["status"] == "partial":
            L.append("| **%s** | _partial — headline not measured_ "
                     "| — | — | — | — | — | — | — |" % ax["axis"])
        for m in ax["metrics"]:
            if m["status"] != "measured":
                L.append("| %s | `%s` | _not measured_ | — | — | %s | — | — "
                         "| — |" % (ax["axis"], m["metric"],
                                    m["n"] if m["n"] is not None else "—"))
                continue
            fl = m["floors"][0] if m["floors"] else None
            L.append("| %s | `%s` | %s | %s | %s | %s | %s | %s = %s | %s |" % (
                ax["axis"], m["metric"], _fmt(m["est"]), _fmt(m["se"]),
                _ci(m), m["n"] if m["n"] is not None else "—",
                m["n_clusters"] if m["n_clusters"] is not None else "—",
                fl["floor"] if fl else "—",
                _fmt(fl["est"]) if fl else "—",
                _fmt(fl["margin"]) if fl else "—"))
    L.append("")
    for ax in sc["axes"]:
        L.append("### %s — %s" % (ax["axis"], ax["title"]))
        L.append("")
        if ax["status"] != "measured":
            L.append("**%s.** %s" % (
                "NOT MEASURED" if ax["status"] == "not_measured" else "PARTIAL",
                ax["reason"]))
            L.append("")
            L.append("To measure it:")
            L.append("")
            L.append("```")
            L.append(ax["how_to_measure"])
            L.append("```")
            L.append("")
            if ax["status"] == "not_measured":
                continue
        L.append("source: `%s`" % ax["source"])
        L.append("")
        for m in ax["metrics"]:
            if m["status"] != "measured":
                L.append("- `%s`: **not measured** — %s"
                         % (m["metric"], m.get("reason")))
                continue
            L.append("- `%s` = %s  se %s  CI95 %s  (n=%s, clusters=%s, %s)"
                     % (m["metric"], _fmt(m["est"]), _fmt(m["se"]), _ci(m),
                        m["n"], m["n_clusters"], m["method"]))
            for f in m["floors"]:
                L.append("    - floor `%s` = %s, margin %s%s"
                         % (f["floor"], _fmt(f["est"]), _fmt(f["margin"]),
                            (" — " + f["note"]) if f.get("note") else ""))
            if m.get("note"):
                L.append("    - %s" % m["note"])
        for c in ax["caveats"]:
            L.append("- caveat: %s" % c)
        L.append("")
    return "\n".join(L)


def print_scorecard(sc, out=sys.stdout):
    cov = sc["coverage"]
    print("scorecard: %d/%d axes measured (%s)%s"
          % (cov["axes_measured"], cov["axes_total"],
             ", ".join(cov["measured"]) or "none",
             "; partial: " + ", ".join(cov["partial"])
             if cov.get("partial") else ""), file=out)
    for ax in sc["axes"]:
        if ax["status"] == "not_measured":
            print("  [not measured] %-5s %s" % (ax["axis"], ax["reason"]),
                  file=out)
            continue
        if ax["status"] == "partial":
            print("  [partial     ] %-5s %s" % (ax["axis"], ax["reason"]),
                  file=out)
        heads = [m for m in ax["metrics"]
                 if m["headline"] and m["status"] == "measured"]
        for m in heads:
            fl = m["floors"][0] if m["floors"] else None
            print("  [measured    ] %-5s %-28s %s  ci95 %s  n=%s  floor %s=%s "
                  "margin %s"
                  % (ax["axis"], m["metric"], _fmt(m["est"]), _ci(m), m["n"],
                     fl["floor"] if fl else "—",
                     _fmt(fl["est"]) if fl else "—",
                     _fmt(fl["margin"]) if fl else "—"), file=out)
    for c in sc["provenance"]["caveats"]:
        print("  ! %s" % c, file=out)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite", nargs="?", default=None,
                    help="path to a suite JSON file (drives the STR axis; "
                         "omit to build a scorecard whose STR axis renders "
                         "'not measured')")
    ap.add_argument("--student", default="openrouter:default_model",
                    help="student model spec (recorded in the report header)")
    ap.add_argument("--arm-dir", action="append", default=[],
                    metavar="ARM=PATH",
                    help="override an arm's output dir (repeatable)")
    ap.add_argument("--puz-run", default=None, metavar="DIR",
                    help="eval/puzzle_run.py output dir (or its summary.json) "
                         "-> PUZ axis")
    ap.add_argument("--plan-run", default=None, metavar="DIR",
                    help="eval/plan_adherence.py output dir (or its "
                         "summary.json) -> PLAN axis")
    ap.add_argument("--blunder-run", default=None, metavar="DIR",
                    help="eval/blunder_report.py output dir (or its "
                         "summary.json) -> DQ and PUNT axes")
    ap.add_argument("--generated-utc", default=None,
                    help="pin the report timestamp (byte-reproducible runs)")
    ap.add_argument("--out", default=None,
                    help="report dir (default runs/reports/<suite>-<UTCts>)")
    ap.add_argument("--json", action="store_true",
                    help="print report.json to stdout as well")
    args = ap.parse_args()

    suite = {}
    name = "scorecard"
    if args.suite is not None:
        try:
            suite = json.loads(Path(args.suite).read_text())
        except Exception as e:
            print(f"error: cannot load suite {args.suite}: {e}",
                  file=sys.stderr)
            return 2
        name = suite.get("suite", "<unnamed>")
    elif not (args.puz_run or args.plan_run or args.blunder_run):
        print("error: nothing to report — give a suite path and/or at least "
              "one of --puz-run/--plan-run/--blunder-run", file=sys.stderr)
        return 2
    gates = suite.get("gates", {})
    mfr = gates.get("max_fallback_rate", 0.10)

    overrides = {}
    for kv in args.arm_dir:
        if "=" not in kv:
            print(f"error: --arm-dir wants ARM=PATH, got {kv!r}",
                  file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        overrides[k] = v

    ts = args.generated_utc or datetime.datetime.utcnow().strftime(
        "%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else (
        REPO / "runs" / "reports" / f"{name}-{ts}")
    out.mkdir(parents=True, exist_ok=True)

    # holdout audit (suite-level evidence; exit 1 there = contamination)
    holdouts, ho_rc, ho_err = run_json([PY, "eval/check_holdouts.py", "--json"])

    arms = []
    for arm in suite.get("arms", []):
        aname = arm.get("name", "<unnamed>")
        if not arm.get("enabled", False):
            continue
        entry = {"arm": aname, "role": arm.get("role"),
                 "deck": arm.get("deck"), "seeds": arm.get("seeds"),
                 "note": arm.get("note"), "status": "missing",
                 "gates": {}, "result": None}
        rng = parse_seed_range(arm.get("seeds", ""))
        entry["expected_games"] = 2 * (rng[1] - rng[0] + 1) if rng else None

        run_dir = Path(overrides.get(aname, f"runs/{name}-{aname}"))
        entry["dir"] = str(run_dir)
        if not (REPO / run_dir).is_dir():
            arms.append(entry)
            continue

        agg, agg_rc, agg_err = run_json(
            [PY, "eval/aggregate.py", str(run_dir), "--json",
             "--max-fallback-rate", str(mfr)])
        if agg is None or agg_rc not in (0, 2):
            entry["status"] = "error"
            entry["error"] = f"aggregate.py rc={agg_rc}: {agg_err}"
            arms.append(entry)
            continue

        def_rc = None
        if gates.get("defect_invariants", True):
            dp = subprocess.run(
                [PY, "eval/defect_analyzer.py", str(run_dir),
                 "--json", str(out / f"defects-{aname}.json"),
                 "--summary", str(out / f"defects-{aname}.txt")],
                capture_output=True, text=True, cwd=REPO)
            def_rc = dp.returncode
            if def_rc == 2:
                entry["status"] = "error"
                entry["error"] = ("defect_analyzer.py rc=2: "
                                  + dp.stderr.strip()[:200])
                arms.append(entry)
                continue

        paired = agg.get("paired") or {}
        focal = paired.get("focal_arm")
        focal_stats = (agg.get("arms") or {}).get(focal) or {}
        prov = agg.get("provenance") or {}
        prov_fields = prov.get("fields") or {}
        disciplines = prov_fields.get("discipline", [])

        entry["result"] = {
            "games_total": agg.get("games", {}).get("total"),
            "games_decisive": agg.get("games", {}).get("decisive"),
            "student_arm": focal,
            "student_win_rate": focal_stats.get("win_rate"),
            "student_wilson95": focal_stats.get("wilson95"),
            "paired_delta": paired.get("delta"),
            "paired_delta_ci95": paired.get("delta_ci95"),
            "n_pairs_used": paired.get("n_pairs_used"),
            "seat1_win_rate": (agg.get("games", {}).get("seat1_wins"),
                               agg.get("games", {}).get("decisive")),
            "provenance": {k: v for k, v in prov_fields.items()
                           if k in ("discipline", "engine_sha", "overlay_sha",
                                    "deck_sha", "mulligan_heuristic",
                                    "skip_heuristics")},
            "mixed_provenance": prov.get("mixed"),
        }

        g = entry["gates"]
        g["fallback_rate"] = {
            "limit": mfr,
            "pass": agg_rc == 0,
            "detail": ("within limit" if agg_rc == 0
                       else "FAILED FALLBACK GATE (aggregate.py exit 2)"),
        }
        if def_rc is not None:
            g["defect_invariants"] = {
                "pass": def_rc == 0,
                "detail": ("clean" if def_rc == 0
                           else f"defect_analyzer exit {def_rc} "
                                f"(see defects-{aname}.txt)"),
            }
        g["cited_discipline"] = {
            "pass": disciplines == ["cited"],
            "detail": f"discipline={disciplines}",
        }
        g["provenance_unmixed"] = {
            "pass": prov.get("mixed") is False,
            "detail": ("single provenance" if prov.get("mixed") is False
                       else f"MIXED: {prov.get('mixed_fields')}"),
        }
        # Resolved model identity: a cited arm whose serving endpoint never named
        # what answered cannot be attributed to a checkpoint (aggregate.py exit 3).
        # Legacy dirs predating the fields report pass=None and are not judged.
        ident = agg.get("model_identity") or {}
        if ident:
            g["model_identity"] = {
                "pass": (None if ident.get("legacy") or ident.get("vacuous")
                         else not ident.get("failed")),
                "detail": ("no llm arm — nothing to attribute" if ident.get("vacuous")
                           else "pre-provenance dir — which model played is unrecoverable"
                           if ident.get("legacy")
                           else "attributable: " + ", ".join(
                               "%s=%s" % (k, v[0] if len(v) == 1 else v)
                               for k, v in sorted(ident.get("served", {}).items()))
                           if not ident.get("failed")
                           else "UNATTRIBUTABLE: endpoint never named %s"
                                % ", ".join(ident.get("unresolved_fields", []))),
            }

        if gates.get("min_autonomy_pct") is not None:
            g["autonomy"] = {
                "pass": None,
                "detail": "UNENFORCEABLE: autonomy field not on game lines "
                          "yet (provenance workstream item 3)",
            }

        # pass=None means "not judged" (unenforceable or not applicable), which must
        # not read as failure — `all()` would treat None as falsy and fail the arm on
        # a gate whose own detail says it cannot be evaluated.
        entry["status"] = ("pass" if all(x["pass"] is not False for x in g.values())
                           else "fail")
        arms.append(entry)

    scorecard = build_scorecard(arms, args.suite, suite, args, ts)

    statuses = [a["status"] for a in arms]
    if any(s in ("missing", "error") for s in statuses):
        verdict = "INCOMPLETE"
        rc = 3
    elif any(s == "fail" for s in statuses):
        verdict = "FAIL"
        rc = 1
    elif holdouts is not None and holdouts.get("violations"):
        verdict = "FAIL"
        rc = 1
    else:
        verdict = "PASS"
        rc = 0
    # An unmeasured axis is an absence of evidence, not a broken gate: it can
    # only demote PASS to INCOMPLETE. It must never be able to read as PASS.
    if verdict == "PASS" and not scorecard["coverage"]["complete"]:
        verdict = "INCOMPLETE"
        rc = 3

    report = {
        "suite": name,
        "suite_version": suite.get("version"),
        "generated_utc": ts,
        "student": args.student,
        "verdict": verdict,
        "holdout_violations": (holdouts or {}).get("violations"),
        "holdout_audit_error": ho_err if holdouts is None else None,
        "arms": arms,
        "scorecard": scorecard,
    }

    (out / "suite.json").write_text(json.dumps(suite, indent=2))
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "scorecard.json").write_text(
        json.dumps(scorecard, indent=2, sort_keys=True) + "\n")
    (out / "report.md").write_text(render_md(report, mfr))

    print(f"verdict: {verdict}")
    for a in arms:
        line = f"  [{a['status']:9s}] {a['arm']}"
        r = a.get("result")
        if r and r.get("student_win_rate") is not None:
            lo, hi = r["student_wilson95"]
            line += (f"  win {pct(r['student_win_rate'])} "
                     f"CI[{pct(lo)}, {pct(hi)}] "
                     f"({r['games_decisive']}/{a['expected_games']} games)")
        elif a["status"] == "missing":
            line += f"  (no dir: {a['dir']})"
        elif a.get("error"):
            line += f"  ({a['error']})"
        print(line)
    print_scorecard(scorecard)
    print(f"report: {out}")
    if args.json:
        print(json.dumps(report, indent=2))
    return rc


def render_md(report, mfr):
    L = []
    L.append(f"# Eval report: {report['suite']} — {report['verdict']}")
    L.append("")
    L.append(f"- generated (UTC): {report['generated_utc']}")
    L.append(f"- student (identity A): `{report['student']}`")
    L.append(f"- gates: fallback_rate <= {mfr}, defect invariants, "
             f"cited discipline, unmixed provenance")
    L.append("")
    if report.get("scorecard"):
        L.append(render_scorecard_md(report["scorecard"]))
        L.append("")
    L.append("## STR suite detail")
    L.append("")
    if not report["arms"]:
        L.append("_No suite arms: build_report.py was run without a suite "
                 "path, so there is no match-strength evidence here._")
        L.append("")
        L.append("Citable only if verdict is PASS.")
        L.append("")
        return "\n".join(L)
    L.append("| arm | role | deck | win rate | Wilson 95% CI | paired Δ CI | "
             "games | gates |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a in report["arms"]:
        r = a.get("result")
        if r and r.get("student_win_rate") is not None:
            lo, hi = r["student_wilson95"]
            d = r.get("paired_delta_ci95")
            dci = (f"[{pct(d[0])}, {pct(d[1])}]"
                   if isinstance(d, list) else "n/a")
            gstr = " ".join("✓" if v["pass"] else ("✗" if v["pass"] is False
                                                   else "?")
                            for v in a["gates"].values())
            L.append(f"| {a['arm']} | {a['role']} | {a['deck']} "
                     f"| {pct(r['student_win_rate'])} "
                     f"| [{pct(lo)}, {pct(hi)}] | {dci} "
                     f"| {r['games_decisive']}/{a['expected_games']} "
                     f"| {gstr} |")
        else:
            L.append(f"| {a['arm']} | {a['role']} | {a['deck']} "
                     f"| — | — | — | 0/{a['expected_games']} "
                     f"| {a['status']} |")
    L.append("")
    for a in report["arms"]:
        r = a.get("result")
        L.append(f"## {a['arm']} ({a['role']}, deck {a['deck']})")
        if a.get("note"):
            L.append(f"_{a['note']}_")
        if not r:
            L.append(f"status: **{a['status']}** ({a.get('error') or a['dir']})")
            L.append("")
            continue
        p = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
             for k, v in r["provenance"].items()}
        L.append(f"- games: {r['games_decisive']} decisive of "
                 f"{r['games_total']} (expected {a['expected_games']})")
        L.append(f"- provenance: discipline={p.get('discipline')} "
                 f"engine={str(p.get('engine_sha', '?'))[:12]} "
                 f"overlay={p.get('overlay_sha')} deck={p.get('deck_sha')}"
                 + (" **MIXED-PROVENANCE**" if r["mixed_provenance"] else ""))
        for gname, gv in a["gates"].items():
            mark = ("PASS" if gv["pass"] else
                    ("FAIL" if gv["pass"] is False else "N/A"))
            L.append(f"- gate {gname}: **{mark}** — {gv['detail']}")
        L.append("")
    if report.get("holdout_violations"):
        L.append("## HOLDOUT VIOLATIONS")
        for slug, ds in report["holdout_violations"]:
            L.append(f"- held-out deck `{slug}` found in dataset `{ds}`")
        L.append("")
    L.append("Citable only if verdict is PASS. INCOMPLETE means at least one "
             "arm has no usable data; FAIL means a gate broke. Numbers from "
             "this report may not be quoted alongside arms added after the "
             "fact — rerun the whole suite per checkpoint.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
