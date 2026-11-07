"""harness_env_task_grade.py: the reward rule, the TestNG reader, the Forge guard, and red-then-green on a fixture."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_task_grade as g
import harness_env_tasks as ht
import test_harness_env_tasks as fx

XML = '''<testng-results><suite name="s"><test name="t">
<class name="forge.spike.T">
 <test-method status="PASS" signature="old()" name="old" is-config="false"/>
 <test-method status="FAIL" signature="fresh()" name="fresh"/>
 <test-method status="PASS" signature="setUp()" name="setUp" is-config="true"/>
 <test-method status="FAIL" signature="tearDown()" name="tearDown" is-config="true"/>
</class></test></suite></testng-results>'''


class ScoreTest(unittest.TestCase):
    def test_one_needs_every_target_and_no_regression_in_the_classes(self):
        t = ["A.x", "A.y"]
        full = {"A.x": "PASS", "A.y": "PASS", "A.z": "PASS"}
        self.assertEqual(g.score(t, full)["score"], 1)
        self.assertEqual(g.score(t, dict(full, **{"A.y": "FAIL"}))["score"], 0)
        self.assertEqual(g.score(t, dict(full, **{"A.z": "FAIL"}))["score"], 0)
        self.assertEqual(g.score(t, dict(full, **{"A.z": "SKIP"}))["score"], 0)

    def test_a_declared_test_that_never_ran_is_a_regression_not_a_pass(self):
        s = g.score(["A.x"], {"A.x": "PASS"}, declared=["A.x", "A.gone"])
        self.assertEqual((s["score"], s["regressions"]), (0, ["A.gone"]))

    def test_nothing_run_and_no_build_both_score_zero(self):
        self.assertEqual(g.score(["A.x"], {})["score"], 0)
        self.assertEqual(g.score([], {})["score"], 0)
        s = g.score(["A.x"], {"A.x": "PASS"}, compile_ok=False)
        self.assertEqual((s["score"], s["partial"]), (0, 0.0))

    def test_partial_credit_fields(self):
        s = g.score(["A.x", "A.y"], {"A.x": "PASS", "A.y": "FAIL", "A.z": "PASS", "A.w": "FAIL"})
        self.assertEqual((s["targets_passed"], s["target_fraction"], s["others_fraction"]), (1, 0.5, 0.5))
        self.assertEqual(s["partial"], 0.5)
        self.assertEqual(s["failed_targets"], ["A.y"])


class ReaderTest(unittest.TestCase):
    def test_testng_results_are_read_per_method_and_config_failures_are_kept(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(XML)
        try:
            r = g.parse_testng(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(r["forge.spike.T.old"], "PASS")
        self.assertEqual(r["forge.spike.T.fresh"], "FAIL")
        self.assertEqual(r["forge.spike.T.tearDown<config>"], "FAIL")
        self.assertNotIn("forge.spike.T.setUp<config>", r)
        self.assertEqual(g.parse_testng("/no/such.xml"), {})


class ForgeGuardTest(unittest.TestCase):
    def test_the_grader_refuses_to_write_inside_the_engine_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "forge", "x"))
            with self.assertRaises(g.SetupError):
                g.refuse_forge_paths(os.path.join(d, "forge"), os.path.join(d, "forge", "x"))
            g.refuse_forge_paths(os.path.join(d, "forge"), os.path.join(d, "forge-other"))

    def test_the_guard_sees_a_change_to_the_checkout_and_to_its_target_trees(self):
        with tempfile.TemporaryDirectory() as d:
            for a in (["init", "-q", "-b", "master"], ["config", "user.email", "a@b"], ["config", "user.name", "a"]):
                subprocess.run(["git", "-C", d] + a, check=True, capture_output=True)
            os.makedirs(os.path.join(d, "forge-core", "target"))
            guard = g.ForgeGuard(d)
            self.assertEqual(guard.problems(), [])
            guard = g.ForgeGuard(d)
            with open(os.path.join(d, "stray.txt"), "w") as f:
                f.write("x")
            with open(os.path.join(d, "forge-core", "target", "A.class"), "w") as f:
                f.write("x")
            self.assertEqual(len(guard.problems()), 2)


class RedGreenTest(unittest.TestCase):
    def test_base_plus_tests_is_red_and_base_plus_gold_is_green(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as work:
            fx.make_history(repo)
            task = ht.build(repo, [1], fx.fetch)["tasks"][0]
            res = g.validate(task, repo, work, "/nonexistent-forge", None)
            self.assertEqual((res["red"]["score"], res["green"]["score"]), (0, 1))
            self.assertTrue(res["well_posed"])
            self.assertEqual(res["red"]["failed_targets"], ["test_src.CaseTest.test_value"])
            self.assertFalse(os.path.exists(os.path.join(work, "pr1-wt")))
            wl = subprocess.run(["git", "-C", repo, "worktree", "list"], capture_output=True, text=True).stdout
            self.assertEqual(len(wl.strip().splitlines()), 1)

    def test_a_candidate_that_breaks_a_neighbour_in_the_file_scores_zero(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as work:
            fx.make_history(repo)
            task = ht.build(repo, [1], fx.fetch)["tasks"][0]
            wt = os.path.join(work, "wt")
            g.prepare(task, repo, wt, gold=True)
            with open(os.path.join(wt, "eval/tests/test_src.py"), "a") as f:
                f.write("\n    def test_neighbour(self):\n        self.assertEqual(src.value(), 99)\n")
            res = g.grade(task, wt, os.path.join(work, "out"), "/nonexistent-forge")
            self.assertEqual((res["score"], res["targets_passed"]), (0, 1))
            self.assertEqual(res["regressions"], ["test_src.CaseTest.test_neighbour"])
            g.remove_worktree(repo, wt)


if __name__ == "__main__":
    unittest.main()
