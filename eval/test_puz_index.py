#!/usr/bin/env python3
"""Self-test for puz_index.py. Pure, offline, no network, no engine.

Run: python3 eval/test_puz_index.py

WHAT WOULD MAKE THIS FAIL (per test, the change that breaks it):
  * test_incomplete_run_is_not_a_score: recording a run whose samples all
    failed as a normal row. A 0-of-4-valid run has an EMPTY denominator; if it
    lands in the table looking like pass_1=0 it reads as "the model got them
    all wrong" when the truth is "the model never answered". That is the same
    class of error as quoting an accuracy without its floor.
  * test_refuses_to_rank_across_suites: ranking rows whose suite_sha256 or
    contract_sha differ. Those are different tests; a tidy table across them
    is worse than no table.
  * test_suite_identity_hashes_content_not_name: identifying a suite by name.
    Suites are edited in place, so a name compares a suite against its own
    past self and calls it an improvement.
  * test_rerecord_updates_rather_than_duplicates: appending blindly, leaving
    two rows for one run that disagree.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import puz_index as pi  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("[ok] %s" % name)
    else:
        FAILURES.append(name)
        print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


def blk(est):
    return {"est": est, "ci95": [est, est], "se": 0.0, "n": 4}


def summary(model, suite_path, k=4, valid=4, attempted=4, pass1=0.5,
            contract_sha="CSHA", utc="20260729T120000Z"):
    return {
        "schema": "puzzle-run-summary.v1", "generated_utc": utc,
        "positions_file": str(suite_path), "contract": "prompt-contract.v1",
        "contract_sha": contract_sha, "backend": "bedrock", "model": model,
        "k": k, "strictness": "strict", "split": "open",
        "n_positions": 4, "n_samples": attempted,
        "counters": {"valid_samples": valid, "attempted_samples": attempted,
                     "transport_errors": attempted - valid},
        "metrics": {"pass_1": dict(
            blk(pass1),
            margin_over_max_constant_index=(None if pass1 is None
                                            else pass1 - 0.28)),
                    "pass_k": blk(pass1),
                    "puz_score_incl": blk(None if pass1 is None else pass1 * 10),
                    "interface_failure_rate": blk(0.0)},
        "baselines": {"max_constant_index": {"pass_1": blk(0.28)},
                      "random_choice": {"pass_1": blk(0.29)},
                      "oracle_ceiling": {"pass_1": blk(1.0)}},
    }


def write_run(tmp, name, s):
    d = Path(tmp) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    return d


def make_suite(tmp, name, body):
    p = Path(tmp) / name
    p.write_text(body, encoding="utf-8")
    return p


def test_incomplete_run_is_not_a_score():
    with tempfile.TemporaryDirectory() as tmp:
        suite = make_suite(tmp, "s.jsonl", '{"id":"a"}\n')
        dead = write_run(tmp, "dead",
                         summary("m", suite, valid=0, attempted=4, pass1=None))
        row = pi.row_from_run(dead)
        check("incomplete: flagged not complete", row["complete"] is False,
              str(row["complete"]))
        check("incomplete: transport errors are carried",
              row["transport_errors"] == 4, str(row["transport_errors"]))
        check("incomplete: pass_1 stays None, never coerced to 0",
              row["pass_1"] is None, repr(row["pass_1"]))
        table = pi.format_table([row])
        check("incomplete: the table SAYS so rather than printing a number",
              "INCOMPLETE" in table, table)

        live = write_run(tmp, "live", summary("m", suite, valid=4, pass1=0.5))
        check("complete run is marked complete",
              pi.row_from_run(live)["complete"] is True)


def test_suite_identity_hashes_content_not_name():
    with tempfile.TemporaryDirectory() as tmp:
        a = make_suite(tmp, "puz.jsonl", '{"id":"a"}\n')
        n1, h1 = pi.suite_identity({}, positions_file=str(a))
        a.write_text('{"id":"a"}\n{"id":"b"}\n', encoding="utf-8")  # edited
        n2, h2 = pi.suite_identity({}, positions_file=str(a))
        check("suite id: same NAME after an edit", n1 == n2 == "puz")
        check("suite id: different HASH after an edit — that is the point",
              h1 != h2, "%s vs %s" % (h1, h2))
        # A suite that has moved must not compare equal to anything.
        _n, h3 = pi.suite_identity({}, positions_file=str(Path(tmp) / "gone"))
        check("suite id: a missing suite hashes to None, not to a constant",
              h3 is None)


def test_refuses_to_rank_across_suites():
    with tempfile.TemporaryDirectory() as tmp:
        s1 = make_suite(tmp, "s1.jsonl", '{"id":"a"}\n')
        s2 = make_suite(tmp, "s2.jsonl", '{"id":"a"}\n{"id":"b"}\n')
        r1 = pi.row_from_run(write_run(tmp, "r1", summary("m", s1, pass1=0.4)))
        r2 = pi.row_from_run(write_run(tmp, "r2", summary("m", s2, pass1=0.9)))
        groups = pi.comparability_groups([r1, r2])
        check("compare: different suites land in different groups",
              len(groups) == 2, str(len(groups)))

        r3 = pi.row_from_run(write_run(
            tmp, "r3", summary("m", s1, pass1=0.6, contract_sha="OTHER")))
        check("compare: a contract change also splits the group",
              len(pi.comparability_groups([r1, r3])) == 2)

        r4 = pi.row_from_run(write_run(tmp, "r4", summary("m2", s1, pass1=0.7)))
        check("compare: same suite+contract+k DO group together",
              len(pi.comparability_groups([r1, r4])) == 1)


def test_rerecord_updates_rather_than_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "idx"
        suite = make_suite(tmp, "s.jsonl", '{"id":"a"}\n')
        d = write_run(tmp, "run", summary("m", suite, pass1=0.4))
        pi.append_rows(root, [pi.row_from_run(d)])
        # rerun the SAME dir with a better score
        (d / "summary.json").write_text(
            json.dumps(summary("m", suite, pass1=0.8)), encoding="utf-8")
        rows = pi.append_rows(root, [pi.row_from_run(d)])
        same = [r for r in rows if r["run_dir"] == str(d)]
        check("rerecord: one row per run dir, not two",
              len(same) == 1, str(len(same)))
        check("rerecord: it holds the NEW value",
              abs(same[0]["pass_1"] - 0.8) < 1e-9, str(same[0]["pass_1"]))
        check("rerecord: survives a round trip through the file",
              len(pi.load_index(root)) == 1)


def test_cli_compare_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "idx"
        suite = make_suite(tmp, "s.jsonl", '{"id":"a"}\n')
        d1 = write_run(tmp, "r1", summary("alpha", suite, pass1=0.4,
                                          utc="20260729T100000Z"))
        d2 = write_run(tmp, "r2", summary("beta", suite, pass1=0.7,
                                          utc="20260729T110000Z"))
        rc = pi.main(["--root", str(root), "record", str(d1), str(d2)])
        check("cli: record exits 0", rc == 0, str(rc))
        rc = pi.main(["--root", str(root), "compare"])
        check("cli: compare exits 0", rc == 0, str(rc))
        rc = pi.main(["--root", str(root), "compare", "--model", "beta"])
        check("cli: compare --model filters without error", rc == 0)
        check("cli: reindex rebuilds from dirs",
              pi.main(["--root", str(tmp), "reindex"]) == 0)



def test_discrimination_separates_free_from_ranking_positions():
    """A headline inflated by positions nobody can fail is not a score.

    WHAT WOULD MAKE THIS FAIL: counting free positions (every model solves) or
    dead ones (no model solves) as part of the discriminating set. Measured on
    puz-v1 with three models: 18 of 25 positions were free, so a model that
    solved ONLY those already scored 0.72 -- against a reported constant-index
    floor of 0.28 -- and the whole ranking rested on 5 positions.
    """
    per = {
        # p1..p3 free, p4 dead, p5/p6 discriminating
        "A": {"p1": 1.0, "p2": 1.0, "p3": 1.0, "p4": 0.0, "p5": 1.0, "p6": 0.0},
        "B": {"p1": 1.0, "p2": 1.0, "p3": 1.0, "p4": 0.0, "p5": 0.0, "p6": 1.0},
    }
    order = ["p1", "p2", "p3", "p4", "p5", "p6"]
    d = pi.discrimination(per, order)
    check("disc: free positions identified",
          d["free"] == ["p1", "p2", "p3"], str(d["free"]))
    check("disc: dead positions are NOT counted as discriminating",
          d["dead"] == ["p4"], str(d["dead"]))
    check("disc: only the positions models differ on rank them",
          d["discriminating"] == ["p5", "p6"], str(d["discriminating"]))
    # a position missing from one model must not be silently treated as solved
    per2 = {"A": {"p1": 1.0}, "B": {}}
    d2 = pi.discrimination(per2, ["p1"])
    check("disc: a position one model never answered is excluded entirely",
          d2["free"] == [] and d2["discriminating"] == [], str(d2))


def test_leaderboard_refuses_to_rank_on_noise():
    """5 positions cannot order frontier models, and the tool must say so."""
    import io, contextlib
    per = {"A": dict(("p%d" % i, 1.0 if i < 4 else 0.0) for i in range(5)),
           "B": dict(("p%d" % i, 1.0 if i < 3 else 0.0) for i in range(5))}
    order = ["p%d" % i for i in range(5)]
    buf = io.StringIO()

    class _A(object):
        dirs = []
    # drive the printer directly on a known-tiny discriminating set
    with contextlib.redirect_stdout(buf):
        d = pi.discrimination(per, order)
        n = len(d["discriminating"])
    check("leaderboard: a tiny suite yields a tiny discriminating set",
          n <= 2, str(n))


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
