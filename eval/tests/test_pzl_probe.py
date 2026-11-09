"""The misreading probe (PZL_PROBE): one fixed line played to game end against a puzzle's [acceptance] state.

A rules puzzle is good only if the play a solver makes when it got the rule wrong does NOT reach the
acceptance state. The JVM writes a `kind: probe` row; these tests pin what the Python side decides from it:
the line's grammar (checked before a JVM starts), the env that asks for it, the verdict, the exit code, and that
a scoring reader never takes a probe row for an attempt. Each test names the tweak that flips it red.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401
import puzzle_baselines as pb  # noqa: E402
import puzzle_score as ps  # noqa: E402


def row(**kw):
    base = {"id": "RP_X", "kind": "probe", "status": "probe", "probe_line": "Solver:2:1 Opponent:3:0",
            "acceptance": "rejected", "acceptance_failed": ["zone p0 Exile Bear == 1 (was 0)"]}
    base.update(kw)
    return base


class ProbeLine(unittest.TestCase):
    def test_a_line_is_seat_bound_value_steps(self):
        # Tweak: accept a step with no value, or a value at or past its bound -> red.
        self.assertEqual(pb.check_probe_line("Solver:2:1 Opponent:3:0"), "Solver:2:1 Opponent:3:0")
        for bad in ("", "  ", "Solver:2", "Solver:2:2", "Solver:0:0", "Solver:a:1", "Judge:2:1", "Solver:2:-1"):
            with self.assertRaises(SystemExit, msg=bad):
                pb.check_probe_line(bad)


class ProbeEnv(unittest.TestCase):
    def test_the_env_asks_the_search_runner_for_one_line_on_one_seed(self):
        # Tweak: drop PZL_PROBE or let PZL_SEEDS carry a range -> red.
        env = pb.probe_env("/p/RP_X.pzl", "Solver:2:1", seed=7, completion_seed=3, opponent="ai")
        self.assertEqual(env["PZL_PROBE"], "Solver:2:1")
        self.assertEqual(env["CONTROLLER_A"], "search")
        self.assertEqual(env["CONTROLLER_B"], "ai")
        self.assertEqual(env["PZL_SEEDS"], "7")
        self.assertEqual(env["PZL_PROBE_COMPLETION_SEED"], "3")
        self.assertTrue(env["PUZZLE"].endswith("RP_X.pzl"))


class ProbeVerdict(unittest.TestCase):
    def test_only_a_rejected_read_passes(self):
        # Tweak: treat a capped or unreadable probe as rejected -> red (a torn-down state is no evidence).
        self.assertEqual(pb.probe_verdict(row()), "rejected")
        self.assertIn("reaches the acceptance state", pb.probe_verdict(row(acceptance="accepted", acceptance_failed=[])))
        self.assertIn("capped", pb.probe_verdict(row(status="invalid", acceptance="")))
        self.assertIn("no acceptance", pb.probe_verdict(row(acceptance="")))
        self.assertEqual(pb.probe_verdict(None), "no result row")
        self.assertIn("not a probe row", pb.probe_verdict({"status": "proven"}))
        self.assertIn("drift", pb.probe_verdict(row(search_drift="step 2 asked a different seat")))


class ProbeCommand(unittest.TestCase):
    def run_main(self, result_row):
        with tempfile.TemporaryDirectory() as d:
            pzl = Path(d) / "RP_X.pzl"
            pzl.write_text("[metadata]\nName=x\n", encoding="utf-8")
            with mock.patch.object(pb, "Build"), \
                    mock.patch.object(pb, "run_arm", return_value=[result_row] if result_row else []) as run:
                code = pb.main(["probe", "--pzl", str(pzl), "--line", "Solver:2:1", "--out-dir", d])
            return code, run

    def test_exit_zero_only_when_the_misreading_is_rejected(self):
        # Tweak: return 0 whatever the verdict -> the accepted case goes red.
        self.assertEqual(self.run_main(row())[0], 0)
        self.assertEqual(self.run_main(row(acceptance="accepted", acceptance_failed=[]))[0], 1)
        self.assertEqual(self.run_main(None)[0], 1)

    def test_the_run_carries_the_probe_env(self):
        _, run = self.run_main(row())
        self.assertEqual(run.call_args.args[2], "search")
        self.assertEqual(run.call_args.args[-1]["PZL_PROBE"], "Solver:2:1")


class NeverScored(unittest.TestCase):
    def test_a_probe_row_is_no_attempt(self):
        # Tweak: drop the probe refusal -> the row reads as an unknown status, a different error, or a failure.
        with self.assertRaises(ps.Refused) as cm:
            ps.attempt_success(row())
        self.assertIn("misreading probe", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
