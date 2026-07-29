#!/usr/bin/env python3
"""Self-test for select_suite.py. Pure, offline, no engine, no network.

Run: python3 eval/test_select_suite.py

WHAT WOULD MAKE THIS FAIL (per test, the code change that breaks it):
  * test_answer_index: taking the LAST 10 instead of the first. A key may mark
    several options 10 (score_from_wp's CI-overlap tie rule), and the earliest
    correct slot is the one a constant policy exploits, so that is the index
    the positional floor must be measured against.
  * test_balance_math: any drift in k <= t*M/(1-t). Off-by-one here silently
    leaves the suite beatable by "always answer 1", which is the exact defect
    this module exists to remove.
  * test_select_lowers_always_first: selection that does not actually move the
    floor, or that reports a floor it did not achieve.
  * test_selection_never_edits_a_key: any mutation of answer_key/option_scores.
    This is a SELECTOR; a key that changes under it is a silently different
    benchmark.
  * test_determinism: selection depending on input order rather than id.
  * test_impossible_target_is_reported: a fully skewed pool being reported as
    balanced instead of warning that the target was unreachable.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import select_suite as ss  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("[ok] %s" % name)
    else:
        FAILURES.append(name)
        print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


def pos(pid, n_options, answer_idx, pass_last=True):
    opts = ["CAST %s Spell %d" % (pid, i) for i in range(n_options - 1)]
    opts.append("PASS (do nothing)" if pass_last else "CAST %s last" % pid)
    scores = [0] * n_options
    scores[answer_idx] = 10
    return {"id": pid, "status": "verified", "options": opts,
            "answer_key": {"option_scores": scores, "verified_by": "rollout",
                           "caveats": []}}


def test_answer_index():
    check("answer_index: finds the scoring slot",
          ss.answer_index(pos("a", 3, 2)) == 2)
    check("answer_index: takes the FIRST 10 when a key marks several",
          ss.answer_index({"answer_key": {"option_scores": [0, 10, 10]}}) == 1)
    check("answer_index: no key -> None",
          ss.answer_index({"answer_key": {}}) is None
          and ss.answer_index({}) is None)
    check("answer_index: a key with no 10 -> None",
          ss.answer_index({"answer_key": {"option_scores": [0, 7, 3]}}) is None)


def test_balance_math():
    # k / (M + k) <= t
    check("balance: t=0.5, M=10 -> 10", ss.max_first_allowed(10, 0.5) == 10)
    check("balance: t=1/3, M=10 -> 5", ss.max_first_allowed(10, 1 / 3.0) == 5)
    check("balance: t=0.25, M=9 -> 3", ss.max_first_allowed(9, 0.25) == 3)
    check("balance: t=0 admits nothing", ss.max_first_allowed(10, 0.0) == 0)
    check("balance: no non-first positions -> admit none",
          ss.max_first_allowed(0, 0.35) == 0)
    check("balance: t>=1 is unbounded", ss.max_first_allowed(5, 1.0) is None)
    # the resulting ratio really does satisfy the constraint
    for m, t in ((10, 1 / 3.0), (9, 0.25), (7, 0.4)):
        k = ss.max_first_allowed(m, t)
        check("balance: k/(M+k) <= t for M=%d t=%.2f" % (m, t),
              k / float(m + k) <= t + 1e-12, "k=%d" % k)


def test_select_lowers_always_first():
    # 20 positions, 16 with the answer at slot 0 -> always_first 0.80
    rows = ([pos("f%02d" % i, 4, 0) for i in range(16)]
            + [pos("n%02d" % i, 4, 2) for i in range(4)])
    sel, rep = ss.select(rows)
    before = rep["before"]["always_first_baseline"]
    after = rep["after"]["always_first_baseline"]
    check("select: pool really was skewed", abs(before - 0.80) < 1e-9,
          str(before))
    check("select: always_first drops to the random baseline or below",
          after <= rep["after"]["random_baseline"] + 1e-9,
          "after=%r random=%r" % (after, rep["after"]["random_baseline"]))
    check("select: the reported floor matches the selected rows",
          abs(after - sum(1 for r in sel if ss.answer_index(r) == 0)
              / float(len(sel))) < 1e-9)
    check("select: every non-first position is kept",
          sum(1 for r in sel if ss.answer_index(r) != 0) == 4)
    check("select: dropping is counted, not silent",
          rep["n_dropped_for_balance"] == 16 - sum(
              1 for r in sel if ss.answer_index(r) == 0))
    check("select: suite shrank (the honest cost)", len(sel) < len(rows))


def test_pass_floor_is_balanced_too():
    """Balancing position must not smuggle the PASS floor upward.

    WHAT WOULD MAKE THIS FAIL: dropping the always_pass constraint and keeping
    only the positional one. Measured on the first real suite, that combination
    moved always_first 0.63 -> 0.27 while dragging always_pass 0.23 -> 0.33 --
    PASS is the last option, so discarding answer-at-slot-0 positions
    preferentially keeps PASS answers, and a suite that is a third PASS
    re-creates the original 88% failure. Trading one floor for another is not
    a fix.
    """
    # 10 first-answer, 10 PASS-answer, 4 genuine middle-answer positions
    rows = ([pos("f%02d" % i, 4, 0) for i in range(10)]
            + [pos("p%02d" % i, 4, 3) for i in range(10)]
            + [pos("m%02d" % i, 4, 1) for i in range(4)])
    sel, rep = ss.select(rows)
    a = rep["after"]
    check("pass-floor: suite is non-empty", len(sel) > 0, str(len(sel)))
    check("pass-floor: always_first meets its target",
          a["always_first_baseline"] <= rep["target_always_first"] + 1e-9,
          "%r vs %r" % (a["always_first_baseline"], rep["target_always_first"]))
    check("pass-floor: always_pass ALSO meets its target",
          a["always_pass_baseline"] <= rep["target_always_pass"] + 1e-9,
          "%r vs %r" % (a["always_pass_baseline"], rep["target_always_pass"]))
    check("pass-floor: always_pass did not get worse than the pool",
          a["always_pass_baseline"] <= rep["before"]["always_pass_baseline"] + 1e-9,
          "%r -> %r" % (rep["before"]["always_pass_baseline"],
                        a["always_pass_baseline"]))
    check("pass-floor: both kinds of drop are reported separately",
          rep["n_dropped_first"] > 0 and rep["n_dropped_pass"] > 0,
          "first=%r pass=%r" % (rep["n_dropped_first"], rep["n_dropped_pass"]))


def test_is_pass_answer_matches_the_floor_definition():
    """The selector's PASS test must be the SAME rule suite_composition uses.

    WHAT WOULD MAKE THIS FAIL: going back to "the FIRST option scoring 10".
    Keys can mark several options 10 (the CI-overlap tie rule), and a model
    that always replies PASS banks the 10 whenever PASS is among them. When the
    two definitions disagreed, the selector counted 2 PASS answers where the
    floor counted 5 and shipped a suite at always_pass 0.33 against a 0.27
    target while reporting success.
    """
    import verify_keys as vk
    # answer_index is 1, but PASS (slot 2) ALSO scores 10 -> always-PASS wins
    tie = {"id": "t", "status": "verified",
           "options": ["CAST A", "CAST B", "PASS (do nothing)"],
           "answer_key": {"option_scores": [0, 10, 10]}}
    check("pass-def: a tie that includes PASS counts as a PASS answer",
          ss.is_pass_answer(tie) is True)
    comp = vk.suite_composition([{ "verified": True, "options": tie["options"],
                                   "option_scores": [0, 10, 10]}])
    check("pass-def: selector agrees with suite_composition's floor",
          (comp["always_pass_baseline"] == 1.0) == ss.is_pass_answer(tie))
    check("pass-def: a non-PASS answer is not counted",
          ss.is_pass_answer({"options": ["CAST A", "PASS (do nothing)"],
                             "answer_key": {"option_scores": [10, 0]}}) is False)


def test_selection_never_edits_a_key():
    rows = [pos("a", 3, 0), pos("b", 3, 1), pos("c", 3, 2)]
    snapshot = json.dumps(rows, sort_keys=True)
    sel, _ = ss.select(rows)
    check("select: input rows are not mutated",
          json.dumps(rows, sort_keys=True) == snapshot)
    for r in sel:
        src = [x for x in rows if x["id"] == r["id"]][0]
        check("select: key for %s is byte-identical" % r["id"],
              json.dumps(r["answer_key"], sort_keys=True)
              == json.dumps(src["answer_key"], sort_keys=True))


def test_dedupe_collapses_repeated_decisions():
    a = pos("a", 2, 0)
    b = pos("b", 2, 0)          # identical option text -> same decision
    b["options"] = list(a["options"])
    c = pos("c", 3, 1)
    sel, rep = ss.select([a, b, c], target=1.0)
    check("dedupe: repeated decision counted once",
          rep["n_after_dedupe"] == 2, str(rep["n_after_dedupe"]))
    check("dedupe: --no-dedupe path keeps both",
          ss.select([a, b, c], target=1.0, dedupe_first=False)[1]
          ["n_after_dedupe"] == 3)


def test_quality_filters():
    """min_options / require_single_answer are measured suite-quality gates.

    WHAT WOULD MAKE THIS FAIL: letting 2-option positions or multi-10 keys
    through. Measured on the shuffled 39-position suite: 12 rows had
    n_options==2 (one action + PASS, so with PASS pinned there is a single
    movable slot and shuffling cannot move the answer -- always_first 0.917
    permanently), and 17 rows had MORE THAN ONE option scoring 10 via
    score_from_wp's CI-overlap tie rule, which is both nearly free for any
    policy and not a puzzle (no single best play to find). Filtering to
    single-answer + n>=3 took always_first 0.692 -> 0.400.
    """
    two = pos("two", 2, 0)
    three = pos("three", 3, 1)
    tie = pos("tie", 4, 1)
    tie["answer_key"]["option_scores"] = [0, 10, 10, 0]

    check("quality: n_top_options counts the 10s",
          ss.n_top_options(tie) == 2 and ss.n_top_options(three) == 1)
    sel, rep = ss.select([two, three, tie], target=1.0, min_options=3)
    check("quality: min_options drops 2-option positions",
          all(len(r["options"]) >= 3 for r in sel) and len(sel) == 2,
          str([r["id"] for r in sel]))
    sel2, rep2 = ss.select([two, three, tie], target=1.0,
                           require_single_answer=True)
    check("quality: require_single_answer drops multi-10 keys",
          [r["id"] for r in sel2] == ["three", "two"], str([r["id"] for r in sel2]))
    sel3, rep3 = ss.select([two, three, tie], target=1.0, min_options=3,
                           require_single_answer=True)
    check("quality: both filters compose",
          [r["id"] for r in sel3] == ["three"], str([r["id"] for r in sel3]))
    check("quality: the filters are recorded in the report",
          rep3["min_options"] == 3 and rep3["require_single_answer"] is True)
    check("quality: default is unfiltered (no silent behaviour change)",
          len(ss.select([two, three, tie], target=1.0)[0]) == 3)


def test_determinism():
    rows = [pos("f%02d" % i, 4, 0) for i in range(10)] + \
           [pos("n%02d" % i, 4, 1) for i in range(5)]
    first = [r["id"] for r in ss.select(rows)[0]]
    second = [r["id"] for r in ss.select(list(reversed(rows)))[0]]
    check("determinism: selection is independent of input order",
          first == second, "%r vs %r" % (first[:4], second[:4]))


def test_impossible_target_is_reported():
    """A pool with NO non-first answers cannot be balanced at any target below
    1.0. It must come back empty rather than quietly returning a skewed suite."""
    rows = [pos("f%02d" % i, 2, 0) for i in range(8)]
    sel, rep = ss.select(rows)
    check("impossible: nothing is selected from a fully skewed pool",
          len(sel) == 0, str(len(sel)))
    check("impossible: the drop is reported",
          rep["n_dropped_for_balance"] == 8)
    check("impossible: after-floors degrade to None, not a fake 0.0",
          rep["after"]["always_first_baseline"] is None)


def test_cli_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # answer at slot 1: non-first AND non-PASS (PASS is slot 3 here), so
        # both floors can actually be satisfied. An earlier version put every
        # non-first answer on the PASS slot, which is unbalanceable by
        # construction and correctly selected nothing.
        rows = ([pos("f%02d" % i, 4, 0) for i in range(12)]
                + [pos("n%02d" % i, 4, 1) for i in range(6)])
        src = td / "positions.jsonl"
        src.write_text("".join(json.dumps(r) + "\n" for r in rows),
                       encoding="utf-8")
        out = td / "suite.jsonl"
        rc = ss.main([str(src), "--out", str(out),
                      "--report", str(td / "sel.json")])
        check("cli: exit 0", rc == 0, "rc=%r" % rc)
        got = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        rep = json.loads((td / "sel.json").read_text(encoding="utf-8"))
        check("cli: wrote the selected rows",
              len(got) == rep["n_selected"] and got)
        check("cli: selected suite meets its target",
              rep["after"]["always_first_baseline"]
              <= rep["after"]["random_baseline"] + 1e-9, json.dumps(rep["after"]))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print("--- %s ---" % name)
            fn()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
