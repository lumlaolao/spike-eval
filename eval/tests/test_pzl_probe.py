"""The misreading probe (PZL_PROBE): one fixed line played to game end against a puzzle's [acceptance] state.

A rules puzzle is good only if the play a solver makes when it got the rule wrong does NOT reach the
acceptance state. The JVM writes a `kind: probe` row; these tests pin what the Python side decides from it:
the line's grammar (checked before a JVM starts), the env that asks for it, the verdict, the exit code, and that
a scoring reader never takes a probe row for an attempt. Each test names the tweak that flips it red.
"""
import os
import re
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


JAVA_TEST = Path(__file__).resolve().parents[2] / (
    "forge-overlay/forge-gui-desktop/src/test/java/forge/spike/PuzzleSearchTest.java")


def java_strings(name):
    """The string literals of `static final String[] <name> = {...};` in PuzzleSearchTest.java."""
    m = re.search(r"String\[\]\s+%s\s*=\s*\{(.*?)\};" % name, JAVA_TEST.read_text(encoding="utf-8"), re.S)
    if not m:
        raise AssertionError("PuzzleSearchTest.java has no String[] %s" % name)
    return [bytes(x, "utf-8").decode("unicode_escape") for x in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]


class ProbeLine(unittest.TestCase):
    def test_a_line_is_seat_bound_value_steps(self):
        # Tweak: accept a step with no value, or a value at or past its bound -> red.
        self.assertEqual(pb.check_probe_line("Solver:2:1 Opponent:3:0"), "Solver:2:1 Opponent:3:0")
        for bad in ("", "  ", "Solver:2", "Solver:2:2", "Solver:0:0", "Solver:a:1", "Judge:2:1", "Solver:2:-1"):
            with self.assertRaises(SystemExit, msg=bad):
                pb.check_probe_line(bad)

    def test_the_python_and_java_grammars_agree(self):
        # PuzzleSearch.parseLine is the grammar the JVM enforces; its test pins the strings it refuses and reads.
        # Tweak: loosen check_probe_line (isdigit, no leading-zero rule, a trailing ':' ) -> a bad string is accepted.
        bad, good = java_strings("LINE_BAD"), java_strings("LINE_GOOD")
        self.assertGreaterEqual(len(bad), 10)
        self.assertGreaterEqual(len(good), 3)
        for s in bad:
            with self.assertRaises(SystemExit, msg=repr(s)):
                pb.check_probe_line(s)
        for s in good:
            self.assertEqual(pb.check_probe_line(s), " ".join(s.split()), msg=repr(s))


class ProbeEnv(unittest.TestCase):
    def test_the_env_asks_the_search_runner_for_one_line_on_one_seed(self):
        # probe_env holds only what run_arm does not set; run_arm sets the rest, so the JVM sees all of it.
        # Tweak: drop PZL_PROBE from probe_env, or stop run_arm setting CONTROLLER_A / PZL_SEEDS -> red.
        self.assertEqual(pb.probe_env("Solver:2:1", completion_seed=3, opponent="search"),
                         {"CONTROLLER_B": "search", "PZL_PROBE": "Solver:2:1", "PZL_PROBE_COMPLETION_SEED": "3"})
        seen = {}
        build = mock.Mock()
        build.run.side_effect = lambda env, out: seen.update(env) or 0
        with tempfile.TemporaryDirectory() as d:
            pb.run_arm(build, "/p/RP_X.pzl", "search", "7", d, pb.probe_env("Solver:2:1", 3, "ai"))
        self.assertEqual((seen["CONTROLLER_A"], seen["CONTROLLER_B"], seen["PZL_SEEDS"]), ("search", "ai", "7"))
        self.assertEqual((seen["PZL_PROBE"], seen["PZL_PROBE_COMPLETION_SEED"]), ("Solver:2:1", "3"))
        self.assertTrue(seen["PUZZLE"].endswith("RP_X.pzl"))
        self.assertIn("GAME_CAP_MS", seen)


class BuildEnv(unittest.TestCase):
    def test_a_probe_in_the_shell_never_reaches_a_puzzle_game(self):
        # A PZL_PROBE exported in the operator's shell would turn every search run into a probe.
        # Tweak: remove "PZL_" from the prefix tuple Build.run strips -> both names reach the JVM.
        build = object.__new__(pb.Build)
        with tempfile.TemporaryDirectory() as d:
            build.dir, build.classes, build.classpath = Path(d), Path(d) / "classes", "/x.jar"
            build.overlay_sha = build.checkout_sha = "aaaaaaaaaaaa"
            build.stale, build.forge, build.java = False, Path(d), "java"
            seen = {}
            def call(cmd, **kw):
                seen.update(kw["env"])
                return 0
            shell = {"PZL_PROBE": "Solver:2:1", "PZL_PROBE_COMPLETION_SEED": "9"}
            with mock.patch.dict(os.environ, shell), mock.patch.object(pb.subprocess, "call", call):
                self.assertEqual(build.run({}, d), 0)
                self.assertEqual([n for n in shell if n in seen], [])
                build.run({"PZL_PROBE": "Solver:3:0"}, d)
            self.assertEqual(seen.get("PZL_PROBE"), "Solver:3:0", "a caller's own setting still reaches the game")


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
        self.assertIn("line not consumed", pb.probe_verdict(row(search_drift="line not consumed: 1 of 3 steps")))
        # Tweak: drop the goal check -> a game run without the turn limit reads as rejected.
        for r in (row(goal_enforcement_installed=False), row(goal_bounded=False)):
            self.assertIn("goal not enforced", pb.probe_verdict(r))

    def test_a_game_stopped_at_the_draw_cap_is_no_evidence(self):
        # Tweak: read the cap's Draw as a state -> a stopped row that says "rejected" passes. Checked before the
        # status, since the JVM writes such a row "invalid" with probe_stopped.
        for r in (row(status="invalid", probe_stopped=True, acceptance=""), row(probe_stopped=True)):
            self.assertEqual(pb.probe_verdict(r), "stopped at the draw cap: no evidence")


class ProbeCommand(unittest.TestCase):
    def run_main(self, result_row, text="[metadata]\nName=x\n[acceptance]\nzone p0 Exile Bear == 1\n"):
        with tempfile.TemporaryDirectory() as d:
            pzl = Path(d) / "RP_X.pzl"
            pzl.write_text(text, encoding="utf-8")
            with mock.patch.object(pb, "Build") as build, \
                    mock.patch.object(pb, "run_arm", return_value=[result_row] if result_row else []) as run:
                code = pb.main(["probe", "--pzl", str(pzl), "--line", "Solver:2:1", "--out-dir", d])
            return code, run, build

    def test_exit_zero_only_when_the_misreading_is_rejected(self):
        # Tweak: return 0 whatever the verdict -> the accepted case goes red.
        self.assertEqual(self.run_main(row())[0], 0)
        self.assertEqual(self.run_main(row(acceptance="accepted", acceptance_failed=[]))[0], 1)
        self.assertEqual(self.run_main(None)[0], 1)

    def test_the_run_carries_the_probe_env(self):
        _, run, _ = self.run_main(row())
        self.assertEqual(run.call_args.args[2], "search")
        self.assertEqual(run.call_args.args[-1]["PZL_PROBE"], "Solver:2:1")

    def test_a_puzzle_with_no_acceptance_section_is_refused_before_a_jvm(self):
        # The JVM throws for it. Tweak: drop the check in cmd_probe -> run_arm is reached.
        with mock.patch.object(pb, "Build"), mock.patch.object(pb, "run_arm") as run:
            with tempfile.TemporaryDirectory() as d:
                pzl = Path(d) / "RP_X.pzl"
                pzl.write_text("[metadata]\nName=x\n[solution]\nplay\n", encoding="utf-8")
                with self.assertRaises(SystemExit) as cm:
                    pb.main(["probe", "--pzl", str(pzl), "--line", "Solver:2:1", "--out-dir", d])
        self.assertIn("no [acceptance] section", str(cm.exception.code))
        run.assert_not_called()


class NeverScored(unittest.TestCase):
    def test_a_probe_row_is_no_attempt(self):
        # Tweak: drop the probe refusal -> the row reads as an unknown status, a different error, or a failure.
        with self.assertRaises(ps.Refused) as cm:
            ps.attempt_success(row())
        self.assertIn("misreading probe", str(cm.exception))

    def test_a_capped_probe_row_is_still_no_attempt(self):
        # A capped probe is status "invalid", which a scoring reader counts as a failed attempt ("capped").
        # Tweak: move the probe check below the invalid check -> (False, "capped") instead of a refusal.
        with self.assertRaises(ps.Refused) as cm:
            ps.attempt_success(row(status="invalid", acceptance=""))
        self.assertIn("misreading probe", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
