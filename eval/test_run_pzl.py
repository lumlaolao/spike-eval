#!/usr/bin/env python3
"""Tests for eval/run_pzl.py -- the PZL floor driver.

The failure this module exists to prevent is a solve rate that flatters the solver.
Two distinct ways that happens, and both are checked below:

  1. A puzzle decided by an empty-library deck-out is counted as a solve. The floor
     smoke run hit this on its FIRST batch: 1 of 8 rows handed a do-nothing arm a
     "win" because the opponent drew from a zero-card library. Taint must leave the
     numerator AND the denominator.
  2. A crashed or refused run is counted as a failure rather than as invalid, which
     silently converts harness breakage into evidence about the model.

Run: python3 eval/test_run_pzl.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_pzl as R   # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print("[%s] %s%s" % ("ok" if cond else "FAIL", name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def res(rid, status, solved, tainted=False, side="", loss="", enforced=True):
    return {"id": rid, "status": status, "solved": solved,
            "tainted_by_deckout": tainted, "taint_side": side,
            "solver_loss_reason": loss, "goal_enforcement_installed": enforced}


def test_taint_leaves_both_numerator_and_denominator():
    rows = [
        res("clean_solve", "solved", True),
        res("clean_fail", "failed", False, loss="SpellEffect"),
        res("clean_fail2", "failed", False, loss="SpellEffect"),
        # free win: opponent decked out, solver did nothing
        res("free_win", "solved", True, tainted=True, side="opponent"),
        # denied: solver decked out before it could act
        res("denied", "failed", False, tainted=True, side="solver", loss="Milled"),
    ]
    s = R.summarize(rows, "floor", quiet=True)
    check("scoreable_excludes_tainted", s["n_scoreable"] == 3, str(s["n_scoreable"]))
    check("solved_excludes_free_win", s["n_solved"] == 1, str(s["n_solved"]))
    # Rates are stored rounded to 4dp; 1/300 = 0.0033, so 4dp resolves single rows.
    check("solve_rate_is_1_of_3", s["solve_rate"] == round(1 / 3.0, 4), str(s["solve_rate"]))
    check("free_wins_counted_separately", s["n_free_wins_from_taint"] == 1)
    check("taint_total", s["n_tainted_by_deckout"] == 2)
    check("taint_sides_split", s["taint_side"] == {"opponent": 1, "solver": 1},
          str(s["taint_side"]))


def test_all_tainted_reports_no_rate_rather_than_zero():
    """Zero scoreable rows must yield None, not 0.0 -- 'we measured nothing' and
    'the solver scored nothing' are different claims."""
    rows = [res("a", "solved", True, tainted=True, side="opponent"),
            res("b", "failed", False, tainted=True, side="solver", loss="Milled")]
    s = R.summarize(rows, "floor", quiet=True)
    check("no_scoreable_rows", s["n_scoreable"] == 0)
    check("rate_is_none_not_zero", s["solve_rate"] is None, str(s["solve_rate"]))


def test_invalid_and_unscoreable_never_count_as_failures():
    rows = [res("ok", "failed", False, loss="SpellEffect"),
            res("crash", "invalid", False),
            res("nogoal", "unscoreable", False, enforced=False)]
    s = R.summarize(rows, "floor", quiet=True)
    check("scoreable_only_real_rows", s["n_scoreable"] == 1, str(s["n_scoreable"]))
    check("status_counts_kept", s["status"] == {"failed": 1, "invalid": 1, "unscoreable": 1},
          str(s["status"]))
    check("missing_enforcement_surfaced", s["n_goal_enforcement_missing"] == 1)


def test_parse_result_reads_the_result_line():
    row = {"id": "sid", "source_file": "X.pzl", "difficulty": "Mythic", "recency_band": 0}
    out = ("noise\n"
           '[PZL] goal enforcement installed: true\n'
           'PZL_RESULT {"id":"X","status":"solved","solved":true}\n'
           "trailing noise\n")
    r = R.parse_result(out, row)
    check("parsed_status", r["status"] == "solved")
    check("suite_id_attached", r["suite_id"] == "sid")
    check("difficulty_joined", r["difficulty"] == "Mythic")


def test_missing_result_line_is_invalid_not_failed():
    row = {"id": "sid", "source_file": "X.pzl"}
    r = R.parse_result("Exception in thread main\nboom\n", row)
    check("no_line_is_invalid", r["status"] == "invalid", str(r))
    check("no_line_not_solved", r["solved"] is False)
    check("error_keeps_tail", "boom" in r["error"], r.get("error", ""))


def test_unparseable_result_line_is_invalid():
    row = {"id": "sid", "source_file": "X.pzl"}
    r = R.parse_result("PZL_RESULT {not json\n", row)
    check("bad_json_is_invalid", r["status"] == "invalid")
    check("bad_json_not_solved", r["solved"] is False)


def test_refused_unenforced_goal_does_not_become_a_failure():
    """LlmSpikePuzzle throws on a goal Forge would silently downgrade. That exits
    before any PZL_RESULT, so it must land as invalid -- if it read as 'failed', a
    mis-adjudicated goal would look like a hard puzzle."""
    row = {"id": "sid", "source_file": "X.pzl"}
    r = R.parse_result("goal 'Mill the Opponent' has no case in Puzzle.addGoalEnforcement\n", row)
    check("refusal_is_invalid", r["status"] == "invalid")
    check("refusal_not_scoreable",
          R.summarize([r], "floor", quiet=True)["n_scoreable"] == 0)


def test_suite_loads_and_ids_are_unique():
    suite = os.path.join(R.REPO, "eval/suites/pzl-v1.jsonl")
    if not os.path.isfile(suite):
        check("suite_present", False, suite)
        return
    rows = R.load_suite(suite)
    check("suite_has_300", len(rows) == 300, str(len(rows)))
    check("ids_unique", len({r["id"] for r in rows}) == len(rows))
    check("every_row_has_source", all(r.get("source_file", "").endswith(".pzl") for r in rows))


def main():
    test_taint_leaves_both_numerator_and_denominator()
    test_all_tainted_reports_no_rate_rather_than_zero()
    test_invalid_and_unscoreable_never_count_as_failures()
    test_parse_result_reads_the_result_line()
    test_missing_result_line_is_invalid_not_failed()
    test_unparseable_result_line_is_invalid()
    test_refused_unenforced_goal_does_not_become_a_failure()
    test_suite_loads_and_ids_are_unique()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all run_pzl checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
