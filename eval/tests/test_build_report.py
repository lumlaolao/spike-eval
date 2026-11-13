#!/usr/bin/env python3
"""build_report.py: the suite verdict. aggregate.py, check_holdouts.py and defect_analyzer.py are stubbed with the
output they give, so each test is the judging, not the statistics (those are aggregate.py's own tests)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import build_report as br  # noqa: E402

CLEAN_HOLDOUTS = ({"violations": []}, 0, "")


def agg_json(total=20, fallback_failed=False, harness_failed=False, identity_failed=False, no_game_line=0):
    """What aggregate.py --json says about one cited arm of `total` game files (`no_game_line` of them with none)."""
    return {"games": {"total": total, "decisive": total - no_game_line, "seat1_wins": total // 2,
                      "no_game_line": no_game_line},
            "arms": {"llm[x]": {"win_rate": 0.6, "wilson95": [0.4, 0.8]}},
            "paired": {"focal_arm": "llm[x]", "delta": 0.1, "delta_ci95": [0.0, 0.2], "n_pairs_used": total // 2},
            "provenance": {"fields": {"discipline": ["cited"]}, "mixed": False},
            "experiments": {"n": 1, "order": ["e1"], "by_id": {"e1": {"label": "one"}}},
            "tool_accounting": {},
            "model_identity": {"failed": identity_failed, "unresolved_fields": ["served_model_a"]
                               if identity_failed else [], "served": {"served_model_a": ["x"]}},
            "fallback_gate": {"failed": fallback_failed, "harness_failed": harness_failed,
                              "harness_decided_rate": 0.05 if harness_failed else 0.0}}


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.arm_dir = d / "arm"
        self.arm_dir.mkdir()
        self.suite = d / "suite.json"
        self.suite.write_text(json.dumps({
            "suite": "t", "version": 1, "gates": {"max_fallback_rate": 0.1, "defect_invariants": True},
            "arms": [{"name": "a1", "enabled": True, "role": "headline", "deck": "burn", "seeds": "9000..9009"}]}))
        self.out = d / "out"

    def run_report(self, agg, agg_rc=0, holdouts=CLEAN_HOLDOUTS):
        def fake_json(cmd):
            return holdouts if "check_holdouts.py" in cmd[1] else (agg, agg_rc, "")
        argv = ["build_report.py", str(self.suite), "--arm-dir", "a1=%s" % self.arm_dir, "--out", str(self.out)]
        with mock.patch.object(br, "run_json", fake_json), \
                mock.patch.object(br.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as printed:
            rc = br.main()
        self.printed, self.md = printed.getvalue(), (self.out / "report.md").read_text()
        return rc, json.loads((self.out / "report.json").read_text())

    def test_a_clean_full_arm_passes(self):
        rc, rep = self.run_report(agg_json())
        self.assertEqual((rc, rep["verdict"]), (0, "PASS"))

    def test_a_holdout_audit_that_did_not_run_earns_no_verdict_and_says_why(self):
        # An audit with no JSON, or an exit other than 0 or 1, is no audit: the verdict is INCOMPLETE, never PASS.
        for holdouts in ((None, 1, "Traceback: no datasets/"), ({"violations": []}, 2, "bad registry")):
            with self.subTest(holdouts=holdouts):
                rc, rep = self.run_report(agg_json(), holdouts=holdouts)
                self.assertEqual((rc, rep["verdict"]), (3, "INCOMPLETE"))
                self.assertTrue(rep["holdout_audit_error"])
                self.assertIn("holdout audit did not run: " + rep["holdout_audit_error"], self.printed)
                self.assertIn("holdout audit did not run: " + rep["holdout_audit_error"], self.md)

    def test_a_holdout_violation_fails(self):
        rc, rep = self.run_report(agg_json(), holdouts=({"violations": [["burn", "ds1"]]}, 1, ""))
        self.assertEqual((rc, rep["verdict"]), (1, "FAIL"))

    def test_the_model_identity_and_harness_decided_gates_fail_the_arm_not_error_it(self):
        # aggregate.py exits 3 and 4 for these: each is a judged arm that fails its own gate and no other.
        for agg, agg_rc, gate in ((agg_json(identity_failed=True), 3, "model_identity"),
                                  (agg_json(harness_failed=True), 4, "harness_decided")):
            with self.subTest(gate=gate):
                rc, rep = self.run_report(agg, agg_rc)
                self.assertEqual((rc, rep["verdict"]), (1, "FAIL"))
                arm = rep["arms"][0]
                self.assertEqual(arm["status"], "fail")
                self.assertIs(arm["gates"][gate]["pass"], False)
                self.assertIs(arm["gates"]["fallback_rate"]["pass"], True)
                self.assertIn("status: **fail**", self.md)

    def test_the_fallback_gate_reads_the_json_whatever_gate_the_exit_code_names(self):
        # aggregate.py names only the first gate that failed; exit 3 can hide a failed fallback gate.
        rc, rep = self.run_report(agg_json(fallback_failed=True, identity_failed=True), 3)
        gates = rep["arms"][0]["gates"]
        self.assertEqual((gates["fallback_rate"]["pass"], gates["model_identity"]["pass"]), (False, False))
        self.assertEqual(rc, 1)

    def test_an_aggregate_exit_outside_the_gate_codes_is_an_error_even_with_json(self):
        rc, rep = self.run_report(agg_json(), 1)
        self.assertEqual((rc, rep["arms"][0]["status"]), (3, "error"))

    def test_a_failing_arm_stays_failed_when_it_is_also_short(self):
        rc, rep = self.run_report(agg_json(total=2, identity_failed=True), 3)
        self.assertEqual((rc, rep["arms"][0]["status"]), (1, "fail"))

    def test_files_with_no_game_line_are_not_played_games(self):
        rc, rep = self.run_report(agg_json(total=20, no_game_line=3))
        self.assertEqual((rc, rep["arms"][0]["status"], rep["arms"][0]["error"]), (3, "short", "17 of 20 games"))
        self.assertIn("(17 of 20 games)", self.printed)
        self.assertIn("status: **short** (17 of 20 games)", self.md)

    def test_an_arm_with_fewer_games_than_its_seeds_earns_no_verdict(self):
        rc, rep = self.run_report(agg_json(total=2))
        self.assertEqual((rc, rep["verdict"]), (3, "INCOMPLETE"))
        self.assertEqual((rep["arms"][0]["status"], rep["arms"][0]["error"]), ("short", "2 of 20 games"))

    def test_an_aggregate_that_could_not_read_the_dir_is_an_error(self):
        rc, rep = self.run_report(None, 1)
        self.assertEqual((rc, rep["arms"][0]["status"]), (3, "error"))


if __name__ == "__main__":
    unittest.main()
