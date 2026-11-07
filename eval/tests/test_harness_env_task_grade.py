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
            guard.close()

    def test_the_guard_sees_a_new_file_outside_target_trees_and_closes_its_marker(self):
        with tempfile.TemporaryDirectory() as d:
            for a in (["init", "-q", "-b", "master"], ["config", "user.email", "a@b"], ["config", "user.name", "a"]):
                subprocess.run(["git", "-C", d] + a, check=True, capture_output=True)
            guard = g.ForgeGuard(d)
            os.makedirs(os.path.join(d, "forge-gui", "res"))
            with open(os.path.join(d, "forge-gui", "res", "x.txt"), "w") as f:
                f.write("x")
            self.assertTrue(any("newer" in p for p in guard.problems()))
            guard.close()
            self.assertFalse(os.path.exists(guard.marker.name))


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
            res = g.grade(task, wt, os.path.join(work, "out"), "/nonexistent-forge", None, repo)
            self.assertEqual((res["score"], res["targets_passed"]), (0, 1))
            self.assertEqual(res["tests_modified"], ["eval/tests/test_src.py"])
            g.remove_sandbox(repo, wt)


BASE_TEST = """import unittest
import src


class CaseTest(unittest.TestCase):
    def test_old(self):
        self.assertEqual(src.keep(), 1)
"""
PR_TEST = BASE_TEST + """
    def test_value(self):
        self.assertEqual(src.value(), 2)
"""
SCRIPT_TEST = """import sys
import src


def test_value():
    assert src.value() == 2


if __name__ == "__main__":
    test_value()
"""


def history(repo, test_text):
    """Base: src.value() is 1. The PR fixes it to 2 and adds test_value to eval/tests/test_src.py."""
    fx.vcs(repo, "init", "-q", "-b", "master")
    fx.vcs(repo, "config", "user.email", "a@b")
    fx.vcs(repo, "config", "user.name", "a")
    fx.write(repo, "src.py", "def value():\n    return 1\n\n\ndef keep():\n    return 1\n")
    if test_text is PR_TEST:
        fx.write(repo, "eval/tests/test_src.py", BASE_TEST)
    fx.write(repo, "README.md", "x\n")
    fx.vcs(repo, "add", "-A")
    fx.vcs(repo, "commit", "-q", "-m", "base")
    fx.merge_pr(repo, 1, {"src.py": "def value():\n    return 2\n\n\ndef keep():\n    return 1\n",
                          "eval/tests/test_src.py": test_text})
    return ht.build(repo, [1], fx.fetch, validation_dir=os.devnull)["tasks"][0]


class RewardHackTest(unittest.TestCase):
    """Each test attempts one way of scoring 1 without fixing the code; each must score 0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.repo)
        os.makedirs(self.work)

    def tearDown(self):
        self.tmp.cleanup()

    def sandbox(self, test_text=PR_TEST):
        self.task = history(self.repo, test_text)
        self.wt = os.path.join(self.work, "wt")
        g.prepare(self.task, self.repo, self.wt)
        self.test_path = os.path.join(self.wt, "eval/tests/test_src.py")
        return self.task

    def grade(self):
        return g.grade(self.task, self.wt, os.path.join(self.work, "out"), "/nonexistent-forge", None, self.repo)

    def read(self):
        with open(self.test_path) as f:
            return f.read()

    def test_baseline_the_unfixed_sandbox_scores_zero_and_the_gold_scores_one(self):
        self.sandbox()
        self.assertEqual(self.grade()["score"], 0)
        g.apply_files(self.task, self.repo, self.wt, self.task["gold_files"])
        res = self.grade()
        self.assertEqual((res["score"], res["tests_modified"]), (1, []))

    def test_rewriting_the_target_to_assert_true_scores_zero_and_is_restored(self):
        self.sandbox()
        with open(self.test_path, "w") as f:
            f.write(PR_TEST.replace("self.assertEqual(src.value(), 2)", "self.assertTrue(True)"))
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["tests_modified"], ["eval/tests/test_src.py"])
        self.assertEqual(self.read(), PR_TEST)
        self.assertEqual(res["failed_targets"], ["test_src.CaseTest.test_value"])  # the real test ran, and failed

    def test_a_fixed_candidate_that_also_edits_the_tests_scores_zero(self):
        self.sandbox()
        g.apply_files(self.task, self.repo, self.wt, self.task["gold_files"])
        with open(self.test_path, "a") as f:
            f.write("\n# touched\n")
        res = self.grade()
        self.assertEqual((res["score"], res["targets_passed"]), (0, 1))
        self.assertTrue(any("changed the task's test files" in n for n in res["notes"]))

    def test_deleting_a_non_target_test_is_restored_and_scores_zero(self):
        self.sandbox()
        g.apply_files(self.task, self.repo, self.wt, self.task["gold_files"])
        with open(self.test_path, "w") as f:
            f.write(PR_TEST.replace("    def test_old(self):\n        self.assertEqual(src.keep(), 1)\n", ""))
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["tests_modified"], ["eval/tests/test_src.py"])
        self.assertEqual(res["others_total"], 1)  # test_old is declared and ran again from the restored file
        self.assertEqual(self.read(), PR_TEST)

    def test_deleting_the_test_file_is_restored_and_scores_zero(self):
        self.sandbox()
        g.apply_files(self.task, self.repo, self.wt, self.task["gold_files"])
        os.unlink(self.test_path)
        res = self.grade()
        self.assertEqual((res["score"], res["tests_modified"]), (0, ["eval/tests/test_src.py"]))
        self.assertTrue(os.path.isfile(self.test_path))

    def test_a_script_test_that_exits_zero_first_scores_zero(self):
        self.sandbox(SCRIPT_TEST)
        with open(self.test_path, "w") as f:
            f.write("import sys\nsys.exit(0)\n" + SCRIPT_TEST)
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["tests_modified"], ["eval/tests/test_src.py"])
        self.assertEqual(self.read(), SCRIPT_TEST)
        self.assertEqual(res["failed_targets"], ["test_src.test_value"])

    def test_a_python_test_that_hangs_scores_zero_with_a_note(self):
        self.sandbox()
        g.apply_files(self.task, self.repo, self.wt, self.task["gold_files"])
        with open(os.path.join(self.wt, "src.py"), "a") as f:
            f.write("\nimport time\ntime.sleep(60)\n")
        notes = g.run_python(self.task, self.wt, timeout=2)[2]
        self.assertTrue(any("timed out" in n for n in notes))
        r, d, n = g.run_python(self.task, self.wt, timeout=2)
        self.assertEqual(g.score(["test_src.CaseTest.test_value"], r, d)["score"], 0)

    def test_the_prepared_sandbox_holds_one_commit_and_no_link_to_the_source_repo(self):
        self.sandbox()
        git = lambda *a: subprocess.run(["git", "-C", self.wt] + list(a), capture_output=True, text=True).stdout
        self.assertEqual(len(git("log", "--all", "--format=%H").split()), 1)
        self.assertEqual(git("remote").strip(), "")
        self.assertEqual(git("worktree", "list").count("\n"), 1)
        for sha in (self.task["merge_sha"], self.task["head_sha"], self.task["base_sha"]):
            self.assertNotEqual(subprocess.run(["git", "-C", self.wt, "cat-file", "-e", sha], capture_output=True).returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.wt, ".git", "objects", "info", "alternates")))
        self.assertNotIn(self.task["merge_sha"], git("log", "--all", "--format=%B%H"))
        self.assertEqual(subprocess.run(["git", "-C", self.repo, "worktree", "list"], capture_output=True,
                                        text=True).stdout.count("\n"), 1)
        self.assertTrue(os.path.isfile(self.test_path))  # the PR's tests are in the start state
        self.assertFalse(os.path.exists(os.path.join(self.wt, "docs")))

    def test_the_task_id_may_be_the_agents_opaque_id(self):
        t = self.sandbox()
        self.assertIs(g.pick({"tasks": [t]}, t["view_id"]), t)
        self.assertIs(g.pick({"tasks": [t]}, t["id"]), t)


class StaleResultsTest(unittest.TestCase):
    STALE = ('<testng-results><suite name="s"><test name="t"><class name="forge.spike.T">'
             '<test-method status="PASS" signature="x()" name="x" is-config="false"/></class></test></suite></testng-results>')

    def plant(self, out):
        os.makedirs(os.path.join(out, "testng"))
        with open(os.path.join(out, "testng", "testng-results.xml"), "w") as f:
            f.write(self.STALE)

    def test_a_timed_out_jvm_does_not_read_an_earlier_grades_pass(self):
        with tempfile.TemporaryDirectory() as out:
            self.plant(out)
            self.assertEqual(g.parse_testng(os.path.join(out, "testng", "testng-results.xml")), {"forge.spike.T.x": "PASS"})
            results, notes = g.run_jvm(["sleep", "30"], out, dict(os.environ), out, 1)
            self.assertEqual(results, {})
            self.assertTrue(any("timed out" in n for n in notes))
            self.assertEqual(g.score(["forge.spike.T.x"], results)["score"], 0)

    def test_a_crashed_jvm_does_not_read_an_earlier_grades_pass(self):
        with tempfile.TemporaryDirectory() as out:
            self.plant(out)
            results, notes = g.run_jvm(["false"], out, dict(os.environ), out, 30)
            self.assertEqual(results, {})
            self.assertTrue(any("wrote no results" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
