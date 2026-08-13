#!/usr/bin/env python3
"""Tests for eval/pzl_floors.py -- folding executed floor runs into the manifest.

The thing this module must never do is publish a floor that is lower than reality,
because the floor is what a model's score gets compared against. Every check below
guards that direction: taint stays out of both halves of the rate, unscoreable rows
never count as solves, the quoted floor is the STRONGEST arm rather than the average,
and a row only earns the "unscoreable" label if it failed that way in every arm.

Run: python3 eval/test_pzl_floors.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pzl_floors as F   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FAILURES = []


def check(name, cond, detail=""):
    print("[%s] %s%s" % ("ok" if cond else "FAIL", name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def row(rid, status, solved=False, tainted=False, bounded=True, installed=True):
    return {"id": rid, "status": status, "solved": solved,
            "tainted_by_deckout": tainted, "goal_bounded": bounded,
            "goal_enforcement_installed": installed}


def test_arm_stats_excludes_taint_from_both_halves():
    rows = [row("a", "solved", solved=True),
            row("b", "failed"),
            row("free", "solved", solved=True, tainted=True),
            row("denied", "failed", tainted=True),
            row("crash", "unscoreable", installed=False),
            row("boom", "invalid")]
    s = F.arm_stats(rows)
    check("scoreable_is_two", s["n_scoreable"] == 2, str(s["n_scoreable"]))
    check("solved_excludes_free_win", s["n_solved"] == 1, str(s["n_solved"]))
    check("rate_over_untainted_only", s["solve_rate"] == 0.5, str(s["solve_rate"]))
    check("free_wins_reported", s["n_free_wins_excluded"] == 1)
    check("tainted_total_reported", s["n_excluded_tainted"] == 2)
    check("unscoreable_reported", s["n_excluded_unscoreable"] == 1)
    check("invalid_reported", s["n_invalid"] == 1)
    check("attempted_is_everything", s["n_attempted"] == 6)


def test_unscoreable_never_counts_as_a_solve():
    """An unbounded goal can produce solved=True -- the game really was won, just not
    within any limit. It must not reach the numerator."""
    rows = [row("unbounded", "unscoreable", solved=True, bounded=False),
            row("real", "failed")]
    s = F.arm_stats(rows)
    check("unbounded_win_not_solved", s["n_solved"] == 0, str(s["n_solved"]))
    check("unbounded_win_not_scoreable", s["n_scoreable"] == 1, str(s["n_scoreable"]))
    check("rate_is_zero_not_half", s["solve_rate"] == 0.0, str(s["solve_rate"]))


def test_scoreability_is_the_intersection_across_arms():
    """A row that failed to score in ONE arm may just have hit a timeout there. Only a
    row unscoreable in EVERY arm is a property of the file plus the engine."""
    by_arm = {
        "floor": [row("always", "unscoreable", bounded=False), row("sometimes", "failed")],
        "random": [row("always", "unscoreable", bounded=False),
                   row("sometimes", "unscoreable", bounded=False)],
        "ai-log": [row("always", "unscoreable", bounded=False), row("sometimes", "failed")],
    }
    out = F.scoreability(by_arm)
    ids = [u["id"] for u in out]
    check("intersection_only", ids == ["always"], str(ids))


def test_reason_distinguishes_crash_from_unbounded():
    by_arm = {a: [row("crash", "unscoreable", installed=False),
                  row("unbnd", "unscoreable", bounded=False)]
              for a in ("floor", "random", "ai-log")}
    out = {u["id"]: u["reason"] for u in F.scoreability(by_arm)}
    check("crash_reason", "crash on load" in out["crash"], out.get("crash", ""))
    check("unbounded_reason", "never fire" in out["unbnd"], out.get("unbnd", ""))
    # An unscoreable row matching neither must not be silently given a plausible reason.
    by_arm2 = {a: [row("weird", "unscoreable")] for a in ("floor", "random", "ai-log")}
    check("unknown_reason_flagged",
          "investigate" in F.scoreability(by_arm2)[0]["reason"])


def test_quoted_floor_is_the_strongest_arm():
    """The floor a model must beat is the best non-LLM agent, not the mean of the arms
    and not the do-nothing arm -- quoting either of those understates it."""
    with tempfile.TemporaryDirectory() as tmp:
        runs = os.path.join(tmp, "runs")
        rates = {"floor": 1, "random": 2, "ai": 5}   # solves out of 10 scoreable
        for d, nsolved in rates.items():
            os.makedirs(os.path.join(runs, d))
            rows = ([row("s%d" % i, "solved", solved=True) for i in range(nsolved)]
                    + [row("f%d" % i, "failed") for i in range(10 - nsolved)])
            with open(os.path.join(runs, d, "results.jsonl"), "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
        suite = os.path.join(tmp, "s")
        with open(suite + ".json", "w", encoding="utf-8") as fh:
            json.dump({"floors": {"status": "NOT MEASURED"}, "known_limits": []}, fh)
        F.main(["--runs", runs, "--suite", suite])
        man = json.load(open(suite + ".json", encoding="utf-8"))
    fl = man["floors"]
    check("status_flipped_to_measured", fl["status"] == "MEASURED")
    check("method_is_executed", fl["method"] == "executed")
    check("quotes_strongest_arm", fl["quote_floor_as"] == 0.5, str(fl["quote_floor_as"]))
    check("all_three_arms_kept", sorted(fl["arms"]) == ["ai-log", "floor", "random"])
    check("weak_arm_still_recorded", fl["arms"]["floor"]["solve_rate"] == 0.1)


def test_shipped_manifest_agrees_with_shipped_runs():
    """The manifest must not drift from the artifacts it claims to summarize."""
    man_p = REPO / "eval/suites/pzl-v1.json"
    if not man_p.is_file():
        check("manifest_present", False, str(man_p))
        return
    man = json.loads(man_p.read_text(encoding="utf-8"))
    fl = man.get("floors", {})
    if fl.get("status") != "MEASURED":
        print("[skip] floors not measured yet — nothing to cross-check")
        return
    for dirname, label in F.ARMS:
        p = REPO / "runs/pzl" / dirname / "results.jsonl"
        if not p.is_file():
            check("run_artifact_present_%s" % label, False, str(p))
            continue
        live = F.arm_stats(F.load(str(p)))
        stored = fl["arms"][label]
        check("manifest_matches_run_%s" % label, live == stored,
              "stored=%s live=%s" % (stored.get("solve_rate"), live.get("solve_rate")))
    check("quoted_floor_is_max_of_arms",
          fl["quote_floor_as"] == max(a["solve_rate"] for a in fl["arms"].values()))
    check("floor_below_ai_arm",
          fl["arms"]["floor"]["solve_rate"] <= fl["arms"]["ai-log"]["solve_rate"],
          "a do-nothing arm outscoring Forge's AI would mean the axis is broken")


def main():
    test_arm_stats_excludes_taint_from_both_halves()
    test_unscoreable_never_counts_as_a_solve()
    test_scoreability_is_the_intersection_across_arms()
    test_reason_distinguishes_crash_from_unbounded()
    test_quoted_floor_is_the_strongest_arm()
    test_shipped_manifest_agrees_with_shipped_runs()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all pzl_floors checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
