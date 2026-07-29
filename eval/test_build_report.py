#!/usr/bin/env python3
"""Self-test for build_report.py's v1.0 SCORECARD assembly.

Run: python3 eval/test_build_report.py

Scope: the scorecard layer only (axis construction, floors, not-measured
rendering, provenance collection, exit code). The suite/gate half of
build_report.py is exercised by the arms it consumes (aggregate.py,
defect_analyzer.py, check_holdouts.py) and by their own tests; nothing here
launches a game, touches the network, or shells out to the engine.

Fixtures are hand-built axis summaries, NOT hand-built scores: they exist to
drive the missing-data and floor-attachment paths that a real corpus cannot be
made to exhibit on demand (there is no way to ask a real blunder_report run to
have exactly zero rollout adjudications AND nonzero textlane ones without
constructing it).

WHAT WOULD MAKE THIS FAIL:
  * THE CENTRAL ONE: an axis with no input data rendering as 0/0.0/absent
    instead of an explicit "not measured" row. test_missing_axis_never_zero
    scans the emitted stdout, report.md and scorecard.json for the axis and
    asserts the literal string "not measured" is present and that no metric
    row for that axis carries est=0.0. Deleting the axis_not_measured() branch
    (say, by returning an empty measured axis) fails it.
  * A measured metric losing its trivial floor: every measured row must carry
    >=1 floor entry with a margin. Dropping the `floors=` argument from any
    metric_row call fails test_every_measured_metric_has_a_floor.
  * The n/se/ci95 contract: any measured row missing n, se or a 2-element
    ci95 fails test_every_measured_metric_has_n_se_ci.
  * PARTIAL demotion: an axis whose only headline metric has n=0 being counted
    as "measured" (inflating coverage) fails test_partial_axis_demoted.
  * Provenance false alarm: _collect treating scalar "abc" and ["abc"] as two
    different values (the aggregate.py-vs-puzzle_run.py shape mismatch) fails
    test_collect_flattens_lists — that bug fabricates a MIXED-provenance
    caveat out of agreeing runs.
  * Verdict laundering: a scorecard with an unmeasured axis exiting 0/PASS
    fails test_incomplete_scorecard_cannot_pass.
  * Contract drift going unreported: an axis artifact stamped with a
    contract_sha other than the running one and no caveat fails
    test_contract_drift_is_caveated.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
sys.path.insert(0, str(EVAL))

import build_report as br            # noqa: E402
import prompt_contract               # noqa: E402

PY = sys.executable or "python3"
FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class Args(object):
    """Stand-in for the argparse namespace build_scorecard reads."""

    def __init__(self, **kw):
        self.student = "test-student"
        self.puz_run = None
        self.plan_run = None
        self.blunder_run = None
        for k, v in kw.items():
            setattr(self, k, v)


# --------------------------------------------------------------- fixtures

def blk(est, se=0.1, ci=(0.0, 1.0), n=10, nc=5, method="wilson", **extra):
    d = {"est": est, "se": se, "ci95": list(ci), "n": n, "n_clusters": nc,
         "method": method}
    d.update(extra)
    return d


def puz_summary(sha=None, skew=None, n_pos=4):
    """A minimal but shape-faithful puzzle_run summary.json.

    `skew` is the pass_1 of the best constant-index policy. Default 0.90 on a
    60-position suite is unambiguously skewed against a 0.35 random baseline;
    pass skew=0.36 (or n_pos=4) to get a suite the caveat must stay quiet on.
    """
    floors = {}
    for name, est in (("random_choice", 3.5), ("always_pass", 0.0),
                      ("always_first", 4.0), ("majority_class", 5.0),
                      ("oracle_ceiling", 10.0)):
        floors[name] = blk(est, n=n_pos, nc=2, method="cluster-bootstrap")
        floors[name]["pass_1"] = blk(est / 10.0, n=n_pos, nc=2)
        floors[name]["pass_k"] = blk(est / 20.0, n=n_pos, nc=2)
    floors["always_first"]["best_index_counts"] = {"1": 3, "2": 1}
    if skew is not None:
        mci = blk(skew * 10.0, n=n_pos, nc=2, method="cluster-bootstrap")
        mci["pass_1"] = blk(skew, n=n_pos, nc=2)
        mci["pass_k"] = blk(skew, n=n_pos, nc=2)
        mci["index"] = 2
        mci["best_index_counts"] = {"1": 3, "2": 1}
        floors["max_constant_index"] = mci
    return {
        "schema": "puzzle-run-summary.v1",
        "contract": prompt_contract.CONTRACT_VERSION,
        "contract_sha": sha or prompt_contract.contract_sha(),
        "engine_sha": "ENGINESHA", "overlay_sha": "OVERLAYSHA",
        "n_positions": 4, "n_samples": 8, "n_clusters": 2,
        "split": "open", "split_counts": {"open": 4},
        "seed_blocks": {"unreserved": 4},
        "model": "offline:random", "backend": "offline",
        "offline_policy": "random", "k": 2, "strictness": "strict",
        "skips": {}, "caveats": ["fixture caveat"], "warnings": [],
        "baselines": floors,
        "metrics": {
            "puz_score_incl": blk(6.0, se=0.5, ci=(5.0, 7.0), n=8, nc=2,
                                  method="cluster-bootstrap",
                                  margin_over_random_choice=2.5),
            "puz_score_excl": blk(6.5, se=0.5, ci=(5.5, 7.5), n=7, nc=2,
                                  method="cluster-bootstrap"),
            "pass_1": blk(0.5, n=8, nc=None),
            "pass_k": blk(0.25, n=4, nc=None, k_used=2),
            "interface_failure_rate": blk(0.125, n=8, nc=None),
        },
    }


def blunder_summary(rollout_n=0):
    """blunder_report summary with rollout adjudication switched on/off. With
    rollout_n=0 (the state of every corpus in this repo today) DQ has no
    observations at all and PUNT's headline has none either."""
    return {
        "t": "summary", "summary_schema": "blunder-summary.v1",
        "contract": prompt_contract.CONTRACT_VERSION,
        "contract_sha": prompt_contract.contract_sha(),
        "params": {"driver": "engine" if rollout_n else "none",
                   "adjudicate": "rollout" if rollout_n else "textlane"},
        "counts": {"n_games": 5, "n_decisions": 100, "n_scoreable": 80,
                   "n_rollout_adjudicated": rollout_n,
                   "n_textlane_verified": 3},
        "dq_wpl_mean": blk(0.04 if rollout_n else None,
                           se=0.01 if rollout_n else None,
                           ci=(0.02, 0.06) if rollout_n else (None, None),
                           n=rollout_n, nc=5 if rollout_n else 0,
                           method="cluster-bootstrap",
                           baseline_random_choice_wpl=0.09 if rollout_n
                           else None,
                           margin_over_random_choice=-0.05 if rollout_n
                           else None),
        "punt_rate": blk(0.05 if rollout_n else None,
                         se=0.01 if rollout_n else None,
                         ci=(0.03, 0.07) if rollout_n else (None, None),
                         n=rollout_n, nc=5 if rollout_n else 0,
                         method="cluster-bootstrap"),
        "punt_rate_textlane": blk(0.0375, se=0.005, ci=(0.02, 0.05), n=80,
                                  nc=5, method="cluster-bootstrap"),
        "per_game_worst_p95": blk(0.2 if rollout_n else None, se=None,
                                  ci=(None, None), n=5 if rollout_n else 0,
                                  nc=5 if rollout_n else 0, method="exact"),
        "flag_rate": blk(0.2, se=0.02, ci=(0.16, 0.25), n=80, nc=5,
                         method="cluster-bootstrap"),
        "audit_lane_punt_rate": blk(None, se=None, ci=(None, None), n=0, nc=0,
                                    method="cluster-bootstrap"),
        "flagged_lane_punt_rate": blk(None, se=None, ci=(None, None), n=0,
                                      nc=0, method="cluster-bootstrap"),
        "flagger_lift": blk(None, se=None, ci=(None, None), n=0, nc=0,
                            method="cluster-bootstrap"),
        "flaggers_leaky": None,
        "flaggers_leaky_note": "audit lane unadjudicated",
        "n_shadow_rows_available": 0,
        "shadow_lane_note": "shadow lane vacuous",
        "baselines": {"always_good_baseline": {"est": 0.95}},
        "warnings": [],
    }


def plan_summary(empty=False):
    return {
        "schema": "plan-adherence-summary.v1",
        "generated_utc": "20260728T000000Z",
        "dirs": ["runs/fixture"], "n_games": 5,
        "n_seats_resolved": 0 if empty else 5,
        "n_seats_unresolved": 5 if empty else 0,
        "caveats": [], "warnings": [],
        "by_deck": {} if empty else {
            "burn": {"n_games": 5, "n_seats": 5, "milestones": {
                "threat_by_2": {"kind": "deploy_by_turn", "weight": 1.0,
                                "n_seat_games": 5,
                                "baselines": {"never_attained": 0.0,
                                              "always_attained": 1.0},
                                "attain_rate": blk(0.6, n=5, nc=None)},
                "burn_split": {"kind": "target_split", "weight": 1.0,
                               "n_seat_games": 5,
                               "baselines": {
                                   "uniform_random_face_rate": 0.55,
                                   "n_rows": 20},
                               "face_rate": blk(0.7, n=20, nc=5,
                                                method="cluster-bootstrap")},
                "protect_held": {"kind": "protection_usage", "weight": 1.0,
                                 "n_seat_games": 5, "n_opportunities": 12,
                                 "numerator": 9,
                                 "baselines": {"never_take_rate": 0.0,
                                               "always_take_rate": 1.0,
                                               "n_opportunities": 12},
                                 "rate": blk(0.75, n=12, nc=4,
                                             method="cluster-bootstrap")},
            }},
        },
    }


def write_run(tmp, name, summary):
    d = Path(tmp) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary, indent=1))
    return str(d)


def axes_by_name(sc):
    return {a["axis"]: a for a in sc["axes"]}


# ----------------------------------------------------------------- tests

def test_missing_axis_never_zero():
    """THE central invariant: no input -> explicit 'not measured', in the
    struct, in the markdown, and in the printed summary. Never a 0."""
    args = Args()
    sc = br.build_scorecard([], None, {}, args, "20260728-000000")
    axes = axes_by_name(sc)
    check("all five axes present",
          sorted(axes) == sorted(br.AXES), sorted(axes))
    for name in br.AXES:
        ax = axes[name]
        check("%s: status not_measured" % name,
              ax["status"] == "not_measured", ax["status"])
        check("%s: has a reason" % name, bool(ax.get("reason")))
        check("%s: has the command that would measure it" % name,
              bool(ax.get("how_to_measure")))
        check("%s: emits NO metric rows (nothing to zero)" % name,
              ax["metrics"] == [], str(ax["metrics"])[:120])
    check("coverage: 0 measured", sc["coverage"]["axes_measured"] == 0)
    check("coverage: not complete", sc["coverage"]["complete"] is False)

    md = br.render_scorecard_md(sc)
    for name in br.AXES:
        check("md: %s rendered as 'not measured'" % name,
              ("| **%s** | _not measured_" % name) in md
              and ("### %s" % name) in md)
    check("md: contains no fabricated 0.000 cell",
          "| 0.000 |" not in md)
    txt = _print_to_string(sc)
    for name in br.AXES:
        check("stdout: %s marked [not measured]" % name,
              ("[not measured] %-5s" % name) in txt, txt[:200])


def _print_to_string(sc):
    import io
    buf = io.StringIO()
    br.print_scorecard(sc, out=buf)
    return buf.getvalue()


def test_measured_axes_from_real_shaped_summaries():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(puz_run=write_run(tmp, "puz", puz_summary()),
                    plan_run=write_run(tmp, "plan", plan_summary()),
                    blunder_run=write_run(tmp, "bl", blunder_summary(60)))
        sc = br.build_scorecard([], None, {}, args, "T")
        axes = axes_by_name(sc)
        for name in ("PUZ", "PLAN", "DQ", "PUNT"):
            check("%s measured" % name, axes[name]["status"] == "measured",
                  "%s / %s" % (axes[name]["status"], axes[name].get("reason")))
        check("STR still not measured (no suite)",
              axes["STR"]["status"] == "not_measured")
        check("coverage counts 4", sc["coverage"]["axes_measured"] == 4,
              str(sc["coverage"]))
        check("coverage incomplete while STR is absent",
              sc["coverage"]["complete"] is False)
        names = [m["metric"] for m in axes["PUZ"]["metrics"]]
        # CHANGED 2026-07-29, deliberately: pass_1 leads, not score_incl.
        # Measured on a suite balanced so the pass_1 floors sit at chance, a
        # constant "answer 1" policy still scored 5.17/10 on score_incl while
        # solving 25% of positions. Partial credit gives score_incl a trivial
        # floor above 50%; the project rule is to prefer near-zero floors.
        # Asserted on the `headline` FLAG, not just row order -- ordering alone
        # would let a reordering slip through without changing what the
        # scorecard actually calls the headline.
        check("PUZ headline is pass_1",
              names[0] == "puz.pass_1", str(names))
        hl = [m["metric"] for m in axes["PUZ"]["metrics"] if m.get("headline")]
        check("PUZ headline FLAG is on pass_1 and not on score_incl",
              "puz.pass_1" in hl and "puz.score_incl" not in hl, str(hl))
        check("score_incl is still reported, just demoted",
              "puz.score_incl" in names, str(names))
        check("DQ wpl carries its random-choice floor",
              axes["DQ"]["metrics"][0]["floors"][0]["floor"]
              == "random_choice_wpl")


def test_every_measured_metric_has_a_floor():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(puz_run=write_run(tmp, "puz", puz_summary()),
                    plan_run=write_run(tmp, "plan", plan_summary()),
                    blunder_run=write_run(tmp, "bl", blunder_summary(60)))
        sc = br.build_scorecard([], None, {}, args, "T")
        bad = []
        for ax in sc["axes"]:
            for m in ax["metrics"]:
                if m["status"] != "measured":
                    continue
                if not m["floors"]:
                    bad.append(m["metric"] + ":no-floor")
                    continue
                if not any(f.get("margin") is not None for f in m["floors"]):
                    bad.append(m["metric"] + ":no-margin")
        check("every measured metric carries a floor AND a margin",
              not bad, ", ".join(bad))


def test_every_measured_metric_has_n_se_ci():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(puz_run=write_run(tmp, "puz", puz_summary()),
                    plan_run=write_run(tmp, "plan", plan_summary()),
                    blunder_run=write_run(tmp, "bl", blunder_summary(60)))
        sc = br.build_scorecard([], None, {}, args, "T")
        bad = []
        for ax in sc["axes"]:
            for m in ax["metrics"]:
                if m["status"] != "measured":
                    continue
                if not isinstance(m.get("n"), int) or m["n"] <= 0:
                    bad.append(m["metric"] + ":n")
                if "se" not in m:
                    bad.append(m["metric"] + ":se")
                if not (isinstance(m.get("ci95"), list) and len(m["ci95"]) == 2):
                    bad.append(m["metric"] + ":ci95")
        check("every measured metric carries n/se/ci95", not bad,
              ", ".join(bad))


def test_partial_axis_demoted():
    """rollout_n=0: DQ has nothing at all; PUNT has real textlane numbers but
    its HEADLINE (rollout punt rate) has none. PUNT must NOT count as
    measured — that is the coverage-inflation bug."""
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(blunder_run=write_run(tmp, "bl", blunder_summary(0)))
        sc = br.build_scorecard([], None, {}, args, "T")
        axes = axes_by_name(sc)
        check("DQ not_measured with no rollouts",
              axes["DQ"]["status"] == "not_measured", axes["DQ"]["status"])
        check("DQ reason names rollouts",
              "rollout" in (axes["DQ"].get("reason") or ""))
        check("PUNT demoted to partial",
              axes["PUNT"]["status"] == "partial", axes["PUNT"]["status"])
        check("PUNT not counted as measured",
              "PUNT" not in sc["coverage"]["measured"])
        check("PUNT listed as partial",
              sc["coverage"]["partial"] == ["PUNT"],
              str(sc["coverage"]["partial"]))
        head = [m for m in axes["PUNT"]["metrics"]
                if m["metric"] == "punt.rate_rollout"][0]
        check("punt.rate_rollout is not_measured, not 0.0",
              head["status"] == "not_measured" and head["est"] is None,
              json.dumps(head)[:160])
        check("punt.rate_textlane survives as non-headline evidence",
              any(m["metric"] == "punt.rate_textlane"
                  and m["status"] == "measured"
                  for m in axes["PUNT"]["metrics"]))


def test_plan_is_a_profile_not_a_score():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(plan_run=write_run(tmp, "plan", plan_summary()))
        sc = br.build_scorecard([], None, {}, args, "T")
        plan = axes_by_name(sc)["PLAN"]
        names = sorted(m["metric"] for m in plan["metrics"])
        check("one row per milestone, none collapsed",
              names == ["plan.burn.burn_split", "plan.burn.protect_held",
                        "plan.burn.threat_by_2"], str(names))
        check("no aggregate plan score exists",
              not any(m["metric"] in ("plan.score", "plan.total")
                      for m in plan["metrics"]))
        check("profile caveat is emitted",
              any("PROFILE" in c for c in plan["caveats"]))
        floors = {m["metric"]: m["floors"][0]["floor"]
                  for m in plan["metrics"]}
        check("target_split floor is the uniform-random face rate",
              floors["plan.burn.burn_split"] == "uniform_random_face",
              str(floors))


def test_plan_unresolved_is_not_measured():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(plan_run=write_run(tmp, "plan", plan_summary(empty=True)))
        sc = br.build_scorecard([], None, {}, args, "T")
        plan = axes_by_name(sc)["PLAN"]
        check("PLAN with no resolved deck is not_measured",
              plan["status"] == "not_measured", plan["status"])
        check("reason names the unresolved seats",
              "unresolved" in (plan["reason"] or ""))


def test_positional_skew_caveat_discriminates():
    """A caveat that fires on every suite tells the reader nothing.

    WHAT WOULD MAKE THIS FAIL: restoring the unconditional
    `if best_index_counts: caveats.append(...)`, which raised POSITIONAL SKEW
    even on a suite whose floors sat at chance; or pointing the reader back at
    margin_over_always_first, which measures slot 1 alone and reads as chance
    while the skew sits one slot over -- exactly the case that made this floor
    necessary (8 of 17 answers in slot 2, always_first 0.29, true floor 0.47).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # 60 positions, best constant policy scores 0.90 vs 0.35 random:
        # far outside sampling noise, so this must be flagged.
        args = Args(puz_run=write_run(tmp, "puz",
                                      puz_summary(skew=0.90, n_pos=60)))
        puz = axes_by_name(br.build_scorecard([], None, {}, args, "T"))["PUZ"]
        skew = [c for c in puz["caveats"] if "POSITIONAL SKEW" in c]
        check("skewed suite is flagged", len(skew) == 1, str(puz["caveats"]))
        check("the caveat names the guilty slot, not just slot 1",
              "always answer 2" in skew[0], skew[0])
        check("it points at the floor that actually binds",
              "margin_over_max_constant_index" in skew[0], skew[0])
        # A caveat naming a floor the scorecard does not print is a dead end
        # for the reader, so the floor itself must reach the metric rows.
        incl = [m for m in puz["metrics"]
                if m["metric"] == "puz.score_incl"][0]
        got = {f["floor"] for f in incl["floors"]}
        check("max_constant_index reaches the scorecard floors",
              "max_constant_index" in got, str(sorted(got)))
        check("always_first is kept beside it for older artifacts",
              "always_first" in got, str(sorted(got)))

    with tempfile.TemporaryDirectory() as tmp:
        # Same suite size, best constant policy at 0.36 vs 0.35 random --
        # a balanced suite. Silence is the correct output.
        args = Args(puz_run=write_run(tmp, "puz",
                                      puz_summary(skew=0.36, n_pos=60)))
        puz = axes_by_name(br.build_scorecard([], None, {}, args, "T"))["PUZ"]
        check("a balanced suite raises NO skew caveat",
              not any("POSITIONAL SKEW" in c for c in puz["caveats"]),
              str(puz["caveats"]))


def test_selective_refusal_is_flagged():
    """interface_failure_rate 0.125 > the 0.10 bar: score_excl must be
    demoted out of the headline and the lift must be reported."""
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(puz_run=write_run(tmp, "puz", puz_summary()))
        sc = br.build_scorecard([], None, {}, args, "T")
        puz = axes_by_name(sc)["PUZ"]
        check("selective_refusal_lift computed",
              abs(puz["selective_refusal_lift"] - 0.5) < 1e-9,
              str(puz.get("selective_refusal_lift")))
        excl = [m for m in puz["metrics"] if m["metric"] == "puz.score_excl"][0]
        check("score_excl is never a headline",
              excl["headline"] is False)
        check("selective-refusal caveat raised above the bar",
              any("SELECTIVE REFUSAL" in c for c in puz["caveats"]),
              str(puz["caveats"]))
        check("no skew caveat when the run has no max_constant_index floor",
              not any("POSITIONAL SKEW" in c for c in puz["caveats"]),
              str(puz["caveats"]))
        incl = [m for m in puz["metrics"]
                if m["metric"] == "puz.score_incl"][0]
        got = {f["floor"] for f in incl["floors"]}
        check("all four floors + ceiling on the headline",
              got == {"random_choice", "always_pass", "always_first",
                      "majority_class", "oracle_ceiling"}, str(sorted(got)))
        af = [f for f in incl["floors"] if f["floor"] == "always_first"][0]
        check("always_first margin computed (6.0 - 4.0)",
              abs(af["margin"] - 2.0) < 1e-9, str(af))


def test_collect_flattens_lists():
    """aggregate.py hands back ["sha"]; puzzle_run.py hands back "sha". If
    _collect does not flatten, two AGREEING axes fabricate a MIXED caveat."""
    axes = {"STR": {"axis": "STR", "provenance": {"engine_sha": ["sha1"]}},
            "PUZ": {"axis": "PUZ", "provenance": {"engine_sha": "sha1"}}}
    got = br._collect(axes, "engine_sha")
    check("scalar and 1-element list collapse",
          got == {"sha1": ["STR", "PUZ"]}, json.dumps(got))
    axes["PLAN"] = {"axis": "PLAN", "provenance": {"engine_sha": "sha2"}}
    got = br._collect(axes, "engine_sha")
    check("a genuine disagreement still shows two keys",
          sorted(got) == ["sha1", "sha2"], json.dumps(got))
    check("empty/None provenance is skipped",
          br._collect({"X": {"axis": "X", "provenance": {"engine_sha": None}}},
                      "engine_sha") == {})


def test_contract_drift_is_caveated():
    with tempfile.TemporaryDirectory() as tmp:
        args = Args(puz_run=write_run(tmp, "puz",
                                      puz_summary(sha="deadbeefdeadbeef")))
        sc = br.build_scorecard([], None, {}, args, "T")
        cav = sc["provenance"]["caveats"]
        check("contract drift raises a caveat",
              any("CONTRACT DRIFT" in c for c in cav), str(cav))
        check("running contract_sha recorded",
              sc["provenance"]["contract_sha_running"]
              == prompt_contract.contract_sha())
        args2 = Args(puz_run=write_run(tmp, "puz2", puz_summary()))
        sc2 = br.build_scorecard([], None, {}, args2, "T")
        check("matching contract_sha raises no drift caveat",
              not any("CONTRACT DRIFT" in c
                      for c in sc2["provenance"]["caveats"]),
              str(sc2["provenance"]["caveats"]))


def test_bad_path_is_not_measured_not_a_crash():
    args = Args(puz_run="/nonexistent/definitely/not/here",
                plan_run="/nonexistent/plan",
                blunder_run="/nonexistent/blunder")
    sc = br.build_scorecard([], None, {}, args, "T")
    axes = axes_by_name(sc)
    for name in ("PUZ", "PLAN", "DQ", "PUNT"):
        check("%s: bad path -> not_measured" % name,
              axes[name]["status"] == "not_measured", axes[name]["status"])
        check("%s: reason names the path" % name,
              "nonexistent" in (axes[name]["reason"] or ""))


def test_incomplete_scorecard_cannot_pass():
    """End-to-end through the CLI: a scorecard missing an axis must exit 3
    (INCOMPLETE), never 0 (PASS), and must write scorecard.json."""
    with tempfile.TemporaryDirectory() as tmp:
        puz = write_run(tmp, "puz", puz_summary())
        out = Path(tmp) / "report"
        p = subprocess.run(
            [PY, str(EVAL / "build_report.py"), "--puz-run", puz,
             "--out", str(out), "--generated-utc", "20260728-000000"],
            capture_output=True, text=True, cwd=str(REPO))
        check("exit 3 INCOMPLETE, not 0", p.returncode == 3,
              "rc=%s stderr=%s" % (p.returncode, p.stderr[:200]))
        check("verdict line says INCOMPLETE",
              "verdict: INCOMPLETE" in p.stdout, p.stdout[:200])
        check("scorecard.json written", (out / "scorecard.json").is_file())
        check("report.md written", (out / "report.md").is_file())
        sc = json.loads((out / "scorecard.json").read_text())
        check("scorecard.json schema", sc["schema"] == br.SCORECARD_SCHEMA)
        check("scorecard.json still lists every axis",
              [a["axis"] for a in sc["axes"]] == list(br.AXES))
        md = (out / "report.md").read_text()
        check("md names the unmeasured axes in the header",
              "NOT MEASURED: PLAN, DQ, PUNT" in md
              or "NOT MEASURED: STR, PLAN, DQ, PUNT" in md, md[:600])
        check("md tells you the command to measure a missing axis",
              "eval/plan_adherence.py" in md)
        check("report.json embeds the scorecard",
              "scorecard" in json.loads((out / "report.json").read_text()))


def test_no_inputs_is_a_usage_error():
    p = subprocess.run([PY, str(EVAL / "build_report.py")],
                       capture_output=True, text=True, cwd=str(REPO))
    check("no suite and no axis input -> exit 2", p.returncode == 2,
          "rc=%s" % p.returncode)
    check("usage error explains what to pass",
          "--puz-run" in p.stderr, p.stderr[:200])


def test_num_rejects_non_numbers():
    """Everything printed goes through _num: a bool or a string from an
    upstream summary must not become a score."""
    check("_num rejects True", br._num(True) is None)
    check("_num rejects a string", br._num("5") is None)
    check("_num rejects None", br._num(None) is None)
    check("_num rejects NaN", br._num(float("nan")) is None)
    check("_num keeps 0.0 (a real zero is a real number)",
          br._num(0.0) == 0.0)
    check("_fmt renders a missing value as an em dash",
          br._fmt(None) == "—")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print("--- %s ---" % t.__name__)
        t()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
