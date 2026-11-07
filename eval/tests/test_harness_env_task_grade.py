"""harness_env_task_grade.py: the reward rule, the TestNG reader, the Forge guard, and red-then-green on a fixture."""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_task_grade as g
import harness_env_tasks as ht
import test_harness_env_tasks as fx

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


class ReporterTest(unittest.TestCase):
    def test_the_reporters_rows_become_per_method_results_and_config_failures_are_kept(self):
        rows = [["PASS", False, "forge.spike.T", "old"], ["FAIL", False, "forge.spike.T", "fresh"],
                ["PASS", True, "forge.spike.T", "setUp"], ["FAIL", True, "forge.spike.T", "tearDown"],
                ["PASS", False, "forge.spike.T", "twice"], ["FAIL", False, "forge.spike.T", "twice"]]
        r = g.parse_reporter({"results": rows})
        self.assertEqual(r["forge.spike.T.old"], "PASS")
        self.assertEqual(r["forge.spike.T.fresh"], "FAIL")
        self.assertEqual(r["forge.spike.T.tearDown<config>"], "FAIL")
        self.assertEqual(r["forge.spike.T.twice"], "FAIL")
        self.assertNotIn("forge.spike.T.setUp<config>", r)

    def test_the_jvm_flags_are_the_acceptance_scripts(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness_acceptance.sh")) as f:
            sh = f.read()
        block = re.search(r'^ADDOPENS="(.*?)"$', sh, re.M | re.S).group(1)
        self.assertEqual(re.findall(r"--add-opens (\S+)=ALL-UNNAMED", block), g.ADDOPENS)


class NoteTest(unittest.TestCase):
    """The research note quotes the number of tests of the grader's and the task builder's files: they must be true."""

    def count(self, module):
        suite = unittest.TestLoader().loadTestsFromModule(sys.modules[module])  # not the -k filtered default loader
        return suite.countTestCases()

    def test_the_notes_test_counts_are_the_unittest_counts(self):
        note = os.path.join(os.path.dirname(os.path.dirname(__file__)), "research", "2026-11-07-harness-env-tasks.md")
        with open(note, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"(\d+) grader tests and (\d+) task tests", text)
        self.assertIsNotNone(m, "the note must say 'N grader tests and N task tests'")
        self.assertEqual((int(m.group(1)), int(m.group(2))),
                         (self.count("test_harness_env_task_grade"), self.count("test_harness_env_tasks")))


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

    def test_the_validation_records_the_graders_hash_the_engine_head_and_the_time(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as work, \
                tempfile.TemporaryDirectory() as forge:
            fx.make_history(repo)
            for a in (["init", "-q", "-b", "master"], ["config", "user.email", "a@b"], ["config", "user.name", "a"],
                      ["commit", "-q", "--allow-empty", "-m", "engine"]):
                subprocess.run(["git", "-C", forge] + a, check=True, capture_output=True)
            head = subprocess.run(["git", "-C", forge, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            task = ht.build(repo, [1], fx.fetch)["tasks"][0]
            res = g.validate(task, repo, work, forge, None)
            with open(os.path.join(work, "pr1-validation.json")) as f:
                on_disk = json.load(f)
            with open(g.__file__, "rb") as f:
                digest = __import__("hashlib").sha256(f.read()).hexdigest()
            for doc in (res, on_disk):
                self.assertEqual(doc["grader_sha256"], digest)
                self.assertEqual(doc["engine_head"], head)
                self.assertRegex(doc["validated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

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
            shutil.rmtree(wt)


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
SCRIPT_TEST = """import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import src


def test_value():
    assert src.value() == 2


if __name__ == "__main__":
    test_value()
"""


def history(repo, test_text, base_extra=None, merge_extra=None):
    """Base: src.value() is 1. The PR fixes it to 2 and adds test_value to eval/tests/test_src.py."""
    fx.vcs(repo, "init", "-q", "-b", "master")
    fx.vcs(repo, "config", "user.email", "a@b")
    fx.vcs(repo, "config", "user.name", "a")
    fx.write(repo, "src.py", "def value():\n    return 1\n\n\ndef keep():\n    return 1\n")
    if test_text is PR_TEST:
        fx.write(repo, "eval/tests/test_src.py", BASE_TEST)
    fx.write(repo, "README.md", "x\n")
    for path, text in (base_extra or {}).items():
        fx.write(repo, path, text)
    fx.vcs(repo, "add", "-A")
    fx.vcs(repo, "commit", "-q", "-m", "base")
    fx.merge_pr(repo, 1, dict({"src.py": "def value():\n    return 2\n\n\ndef keep():\n    return 1\n",
                              "eval/tests/test_src.py": test_text}, **(merge_extra or {})))
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

    def sandbox(self, test_text=PR_TEST, base_extra=None, merge_extra=None):
        self.task = history(self.repo, test_text, base_extra, merge_extra)
        self.wt = os.path.join(self.work, "wt")
        g.prepare(self.task, self.repo, self.wt)
        self.test_path = os.path.join(self.wt, "eval/tests/test_src.py")
        return self.task

    def fix(self):
        """The gold patch, applied by hand (git show), so this works against any version of the grader."""
        for path in self.task["gold_files"]:
            blob = ht.read_blob(self.repo, self.task["merge_sha"], path)
            full = os.path.join(self.wt, path)
            if blob is None:
                os.unlink(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(blob)

    def write(self, path, text):
        full = os.path.join(self.wt, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)

    def grade(self):
        return g.grade(self.task, self.wt, os.path.join(self.work, "out"), "/nonexistent-forge", None, self.repo)

    def read(self):
        with open(self.test_path) as f:
            return f.read()

    def test_baseline_the_unfixed_sandbox_scores_zero_and_the_gold_scores_one(self):
        self.sandbox()
        self.assertEqual(self.grade()["score"], 0)
        self.fix()
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
        self.fix()
        with open(self.test_path, "a") as f:
            f.write("\n# touched\n")
        res = self.grade()
        self.assertEqual((res["score"], res["targets_passed"]), (0, 1))
        self.assertTrue(any("changed the task's test files" in n for n in res["notes"]))

    def test_deleting_a_non_target_test_is_restored_and_scores_zero(self):
        self.sandbox()
        self.fix()
        with open(self.test_path, "w") as f:
            f.write(PR_TEST.replace("    def test_old(self):\n        self.assertEqual(src.keep(), 1)\n", ""))
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["tests_modified"], ["eval/tests/test_src.py"])
        self.assertEqual(res["others_total"], 1)  # test_old is declared and ran again from the restored file
        self.assertEqual(self.read(), PR_TEST)

    def test_deleting_the_test_file_is_restored_and_scores_zero(self):
        self.sandbox()
        self.fix()
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
        self.fix()
        with open(os.path.join(self.wt, "src.py"), "a") as f:
            f.write("\nimport time\ntime.sleep(60)\n")
        notes = g.run_python(self.task, self.wt, self.work, timeout=2)[2]
        self.assertTrue(any("timed out" in n for n in notes))
        r, d, n = g.run_python(self.task, self.wt, self.work, timeout=2)
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


    # ---- the second review of PR 422: ways the first hardening still let a candidate score 1

    def test_imported_code_that_prints_a_forged_result_line_scores_zero(self):
        self.sandbox()
        self.write("src.py", 'print(\'RESULT {"CaseTest.test_value": "PASS", "CaseTest.test_old": "PASS"}\')\n'
                             "def value():\n    return 1\n\n\ndef keep():\n    return 1\n")
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["failed_targets"], ["test_src.CaseTest.test_value"])

    def test_imported_code_that_reads_the_runners_globals_cannot_write_a_forged_result(self):
        """The reviewer's PoC: the nonce, the result path and the names were module globals of __main__."""
        poc = ("import __main__, json, os\n"
               "with open(__main__._out, 'w') as f:\n"
               "    json.dump({'nonce': __main__._nonce, 'results': {n: 'PASS' for n in __main__._names}}, f)\n"
               "os._exit(0)\n")
        self.sandbox()
        self.write("src.py", poc)
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.CaseTest.test_value"]))

    def test_a_json_py_at_the_worktree_root_cannot_shadow_the_graders_runner(self):
        self.sandbox()
        self.write("json.py", 'def dumps(o, *a, **k):\n    return \'{"CaseTest.test_value": "PASS", '
                              '"CaseTest.test_old": "PASS"}\'\n\n\ndef loads(s, *a, **k):\n    return {}\n')
        self.write("unittest.py", "raise SystemExit(0)\n")
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["failed_targets"], ["test_src.CaseTest.test_value"])
        self.fix()
        self.assertEqual(self.grade()["score"], 1)  # the stdlib is still what the tests and the runner import

    def test_imported_code_that_exits_zero_fails_every_function_of_a_script_test(self):
        self.sandbox(SCRIPT_TEST)
        self.write("src.py", "import sys\nsys.exit(0)\n")
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.test_value"]))

    def test_the_target_calling_sys_exit_zero_inside_the_test_is_a_fail(self):
        self.sandbox(SCRIPT_TEST)
        self.write("src.py", "import sys\n\n\ndef value():\n    sys.exit(0)\n")
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.test_value"]))

    def test_a_script_tests_neighbours_are_judged_one_by_one(self):
        two = SCRIPT_TEST.replace('if __name__', 'def test_other():\n    assert src.keep() == 1\n\n\nif __name__')
        self.sandbox(two)
        self.write("src.py", "def value():\n    return 1\n\n\ndef keep():\n    return 1\n")
        res = self.grade()
        self.assertEqual((res["targets_passed"], res["failed_targets"], res["regressions"]), (1, ["test_src.test_value"], []))

    def test_a_script_tests_own_failure_tally_counts(self):
        tally = ("FAILURES = []\n\n\ndef check(ok):\n    if not ok:\n        FAILURES.append(1)\n\n\n"
                 "def test_value():\n    check(__import__('src').value() == 2)\n")
        self.sandbox(tally)
        self.assertEqual(self.grade()["score"], 0)
        self.fix()
        self.assertEqual(self.grade()["score"], 1)

    def planted_pyc(self, source_path, code_text):
        import importlib.util
        import importlib._bootstrap_external as be
        code = compile(code_text, source_path, "exec")
        pyc = importlib.util.cache_from_source(source_path)
        os.makedirs(os.path.dirname(pyc), exist_ok=True)
        with open(pyc, "wb") as f:  # flags 0b01: a hash-based pyc that is never checked against the source
            f.write(be._code_to_hash_pyc(code, b"\0" * 8, False))
        return pyc

    def test_an_unchecked_hash_pyc_cannot_stand_in_for_the_source_or_the_test(self):
        self.sandbox()
        pyc = self.planted_pyc(os.path.join(self.wt, "src.py"), "def value():\n    return 2\n\n\ndef keep():\n    return 1\n")
        self.assertEqual(self.grade()["score"], 0)  # src.py is unfixed; its planted bytecode must not run
        self.assertFalse(os.path.exists(pyc))
        pyc = self.planted_pyc(self.test_path, PR_TEST.replace("self.assertEqual(src.value(), 2)", "self.assertTrue(True)"))
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.CaseTest.test_value"]))
        self.assertFalse(os.path.exists(pyc))

    def test_a_sourceless_pyc_next_to_the_tests_is_deleted(self):
        self.sandbox()
        self.write("extra.pyc", "junk")
        self.grade()
        self.assertFalse(os.path.exists(os.path.join(self.wt, "extra.pyc")))

    def test_the_python_child_sees_no_api_key_or_egress_flag(self):
        self.sandbox()
        self.write("src.py", "import os\n\n\ndef value():\n    return 2 if os.environ.get('OPENROUTER_API_KEY') or "
                             "os.environ.get('OPENROUTER_ALLOW_EGRESS') or os.environ.get('SOME_TOKEN') else 1\n\n\n"
                             "def keep():\n    return 1\n")
        env = dict(OPENROUTER_API_KEY="sk-x", OPENROUTER_ALLOW_EGRESS="1", SOME_TOKEN="t")
        with unittest.mock.patch.dict(os.environ, env):
            self.assertEqual(self.grade()["score"], 0)
            self.assertEqual(sorted(set(g.clean_env()) & set(env)), [])

    def test_a_read_only_input_the_candidate_edited_is_restored_before_the_tests_read_it(self):
        reader = PR_TEST.replace("self.assertEqual(src.value(), 2)",
                                 "self.assertEqual(src.value(), int(open('README.md').read()))")
        self.sandbox(reader, base_extra={"README.md": "1\n"}, merge_extra={"README.md": "2\n"})
        self.write("README.md", "1\n")  # src.value() is 1 at the base: the test now compares 1 with 1
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.CaseTest.test_value"]))
        with open(os.path.join(self.wt, "README.md")) as f:
            self.assertEqual(f.read(), "2\n")

    def data_case(self, path, base_text, merge_text, hacked):
        """A test that reads `path` and expects src.value() to equal it: at the base src.value() is 1, after the fix 2.
        The candidate edits the file to `hacked` (1), so an unfixed tree would pass unless the file is restored."""
        reader = PR_TEST.replace("self.assertEqual(src.value(), 2)",
                                 "self.assertEqual(src.value(), int(open(%r).read().split()[0]))" % path)
        self.sandbox(reader, base_extra={path: base_text}, merge_extra={path: merge_text})
        self.write(path, hacked)
        res = self.grade()
        self.assertEqual((res["score"], res["failed_targets"]), (0, ["test_src.CaseTest.test_value"]))
        with open(os.path.join(self.wt, path)) as f:
            self.assertEqual(f.read(), merge_text)

    def test_a_json_data_file_under_eval_the_candidate_edited_is_restored(self):
        self.data_case("eval/data/x.json", "2\n", "2\n", "1\n")

    def test_a_txt_data_file_under_eval_the_candidate_edited_is_restored(self):
        self.data_case("eval/suites/x.txt", "2\n", "2\n", "1\n")

    def test_a_tsv_data_file_under_docs_the_candidate_edited_is_restored(self):
        self.data_case("docs/harness/x.tsv", "2\tz\n", "2\tz\n", "1\tz\n")

    def test_the_inputs_the_gold_patch_changes_are_not_restored(self):
        src = ("def value():\n    return int(open('eval/data/x.json').read())\n\n\ndef keep():\n    return 1\n")
        self.sandbox(PR_TEST, base_extra={"src.py": src, "eval/data/x.json": "1\n"},
                     merge_extra={"src.py": src, "eval/data/x.json": "2\n"})
        self.assertEqual(self.task["gold_files"], ["eval/data/x.json"])
        self.assertEqual(self.grade()["score"], 0)  # the json is the fix: the grader must not hand it over
        with open(os.path.join(self.wt, "eval/data/x.json")) as f:
            self.assertEqual(f.read(), "1\n")
        self.fix()
        self.assertEqual(self.grade()["score"], 1)

    def test_a_symlinked_parent_directory_is_refused_and_nothing_is_written_through_it(self):
        self.sandbox()
        outside = os.path.join(self.work, "outside")
        os.makedirs(outside)
        shutil.rmtree(os.path.join(self.wt, "eval/tests"))
        os.symlink(outside, os.path.join(self.wt, "eval/tests"))
        self.fix()
        res = self.grade()
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("leaves the worktree" in n for n in res["notes"]))
        self.assertEqual(os.listdir(outside), [])

    def test_the_graders_files_from_an_earlier_grade_are_deleted_first(self):
        self.sandbox()
        out = os.path.join(self.work, "out")
        os.makedirs(out)
        for name in ("javac.log", "result.json"):
            with open(os.path.join(out, name), "w") as f:
                f.write('{"score": 1}')
        res = self.grade()
        self.assertFalse(os.path.exists(os.path.join(out, "javac.log")))
        with open(os.path.join(out, "result.json")) as f:
            self.assertEqual(json.load(f)["score"], res["score"])
        self.assertEqual(res["score"], 0)

    def test_the_pin_for_the_engine_patches_is_the_graders_not_the_candidates(self):
        self.sandbox(base_extra={"FORGE_COMMIT": "base\n"}, merge_extra={"FORGE_COMMIT": "good\n"})
        self.write("FORGE_COMMIT", "evil\n")
        self.write("engine-patches/forge-game/src/main/java/forge/game/A.java.patch", "diff\n")
        pin = os.path.join(self.work, "pin")
        g.pin_repo(self.task, self.repo, self.wt, pin)
        with open(os.path.join(pin, "FORGE_COMMIT")) as f:
            self.assertEqual(f.read(), "good\n")
        self.assertTrue(os.path.isfile(os.path.join(pin, "engine-patches/forge-game/src/main/java/forge/game/A.java.patch")))


class PrebuiltClassTest(unittest.TestCase):
    """javac must not fall back to the installed build of a class whose source the candidate deleted."""
    SPIKE = "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/Foo.java"
    GUI = "forge-overlay/forge-gui-desktop/src/main/java/forge/gui/Baz.java"
    GONE = "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/Gone.java"
    PATCH = "engine-patches/forge-game/src/main/java/forge/game/Bar.java.patch"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        fx.vcs(self.repo, "init", "-q", "-b", "master")
        fx.vcs(self.repo, "config", "user.email", "a@b")
        fx.vcs(self.repo, "config", "user.name", "a")
        for p in (self.SPIKE, self.GONE, self.PATCH):
            fx.write(self.repo, p, "x\n")
        fx.vcs(self.repo, "add", "-A")
        fx.vcs(self.repo, "commit", "-q", "-m", "base")
        self.base = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        os.unlink(os.path.join(self.repo, self.GONE))
        fx.write(self.repo, self.GUI, "x\n")
        fx.vcs(self.repo, "add", "-A")
        fx.vcs(self.repo, "commit", "-q", "-m", "merge")
        self.task = {"base_sha": self.base, "merge_sha": subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()}
        self.forge = os.path.join(self.tmp.name, "forge")
        for rel in ("forge-gui-desktop/target/classes/forge/spike/Foo.class",
                    "forge-gui-desktop/target/classes/forge/spike/Gone.class",
                    "forge-gui-desktop/target/classes/forge/gui/Baz.class",
                    "forge-gui-desktop/target/classes/forge/gui/Baz$1.class",
                    "forge-gui-desktop/target/classes/forge/gui/Other.class",
                    "forge-game/target/classes/forge/game/Bar.class",
                    "forge-game/target/classes/forge/game/Keep.class",
                    "forge-core/target/classes/forge/util/U.class", "forge-ai/target/classes/forge/ai/A.class",
                    "forge-gui/target/classes/forge/gui/G.class"):
            fx.write(self.forge, rel, "class")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_classpath_holds_no_class_the_overlay_or_an_engine_patch_supplies(self):
        dest = os.path.join(self.tmp.name, "cp")
        supplied = g.supplied_sources(self.repo, self.task["base_sha"]) | g.supplied_sources(self.repo, self.task["merge_sha"])
        dirs = g.filtered_engine_classes(self.forge, supplied, dest)
        have = sorted(os.path.relpath(os.path.join(r, f), dest) for r, _, fs in os.walk(dest) for f in fs)
        self.assertEqual(have, ["forge-ai/forge/ai/A.class", "forge-core/forge/util/U.class",
                                "forge-game/forge/game/Keep.class", "forge-gui-desktop/forge/gui/Other.class",
                                "forge-gui/forge/gui/G.class"])
        self.assertEqual(len(dirs), 5)

    def test_a_deleted_source_the_gold_patch_keeps_fails_the_grade_one_the_gold_deletes_does_not(self):
        wt = os.path.join(self.tmp.name, "wt")
        for p in (self.SPIKE, self.PATCH):
            fx.write(wt, p, "x\n")
        keep = g.supplied_sources(self.repo, self.task["base_sha"]) & g.supplied_sources(self.repo, self.task["merge_sha"])
        self.assertEqual(g.missing_supplied_sources(keep, wt), [])  # Gone.java: deleted by the gold
        os.unlink(os.path.join(wt, self.SPIKE))
        self.assertEqual(g.missing_supplied_sources(keep, wt), [self.SPIKE])
        os.unlink(os.path.join(wt, self.PATCH))
        self.assertEqual(g.missing_supplied_sources(keep, wt), [self.PATCH, self.SPIKE])

    def test_the_reporter_the_grader_compiles_is_the_one_in_the_repository(self):
        self.assertTrue(os.path.isfile(g.REPORTER_SRC))
        with open(g.REPORTER_SRC) as f:
            self.assertIn("class " + g.REPORTER_CLASS, f.read())




def find_testng_jar():
    """TestNG's jar from ~/.m2, else from the newest acceptance run's deps.txt; None when this machine has neither."""
    import glob
    found = sorted(glob.glob(os.path.expanduser("~/.m2/repository/org/testng/testng/*/testng-*.jar")))
    if not found:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for deps in sorted(glob.glob(os.path.join(g.common_root(repo) or repo, "runs/harness-acceptance/*/deps.txt")),
                           key=os.path.getmtime)[::-1]:
            with open(deps) as f:
                found = [e for e in f.read().strip().split(":") if "/testng-" in e and os.path.isfile(e)]
            if found:
                break
    return found[-1] if found else None


TESTNG_JAR = find_testng_jar()
A_JAVA = "forge-game/src/main/java/forge/game/A.java"
A_V1 = "package forge.game;\n\npublic class A {\n}\n"
A_V2 = "package forge.game;\n\npublic class A {\n    public int x() { return 1; }\n}\n"
A_PATCH_PATH = "engine-patches/" + A_JAVA + ".patch"
SPIKE_DIR = "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/"
TEST_DIR = "forge-overlay/forge-gui-desktop/src/test/java/forge/spike/"
FOO_V1 = "package forge.spike;\n\npublic class Foo {\n    public static int v() { return 1; }\n}\n"
FOO_V2 = FOO_V1.replace("return 1", "return 2")
# `Test` is declared in the file so javac needs no TestNG here; the tests' regex sees an @Test annotation all the same
FOO_TEST_V1 = ("package forge.spike;\n\npublic class FooTest {\n    @Test\n    public void old() { }\n}\n\n"
               "@interface Test { }\n")
FOO_TEST_V2 = FOO_TEST_V1.replace("    public void old() { }\n", "    public void old() { }\n\n    @Test\n    public void fresh() { }\n")
EVIL_TESTNG = r"""package org.testng;

import java.io.*;
import java.util.*;

public class TestNG {
    public static void main(String[] args) throws Exception {
        Properties p = new Properties();
        p.load(new FileReader(System.getProperty("harness.grader.spec")));
        try (Writer w = new FileWriter(p.getProperty("result"))) {
            w.write("{\"nonce\":\"" + p.getProperty("nonce") + "\",\"results\":[[\"PASS\",false,\"forge.spike.FooTest\",\"old\"],"
                    + "[\"PASS\",false,\"forge.spike.FooTest\",\"fresh\"]]}");
        }
    }
}
"""


class JavaGradeTest(unittest.TestCase):
    """grade() of a Java task against a fake engine checkout and the real javac. Every test but the one marked ends
    before the JVM starts (the refusal, or a deps file with no TestNG), so the suite starts no JVM."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.repo, self.wt, self.out = (os.path.join(t, n) for n in ("repo", "wt", "out"))
        os.makedirs(self.repo)
        fx.vcs(self.repo, "init", "-q", "-b", "master")
        fx.vcs(self.repo, "config", "user.email", "a@b")
        fx.vcs(self.repo, "config", "user.name", "a")
        self.forge = os.path.join(t, "forge")  # the fake engine: a git checkout holding the upstream original of A.java
        fx.write(self.forge, A_JAVA, A_V1)
        for a in (["init", "-q", "-b", "master"], ["config", "user.email", "a@b"], ["config", "user.name", "a"],
                  ["add", "-A"], ["commit", "-q", "-m", "upstream"]):
            fx.vcs(self.forge, *a)
        pin = subprocess.run(["git", "-C", self.forge, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        fx.write(self.repo, "FORGE_COMMIT", pin)
        fx.write(self.repo, A_PATCH_PATH, "".join(difflib.unified_diff(
            A_V1.splitlines(True), A_V2.splitlines(True), "a/" + A_JAVA, "b/" + A_JAVA)))
        fx.write(self.repo, SPIKE_DIR + "Foo.java", FOO_V1)
        fx.write(self.repo, TEST_DIR + "FooTest.java", FOO_TEST_V1)
        fx.vcs(self.repo, "add", "-A")
        fx.vcs(self.repo, "commit", "-q", "-m", "base")
        fx.merge_pr(self.repo, 1, {SPIKE_DIR + "Foo.java": FOO_V2, TEST_DIR + "FooTest.java": FOO_TEST_V2})
        self.task = ht.build(self.repo, [1], fx.fetch, validation_dir=os.devnull)["tasks"][0]
        for m in ("forge-gui-desktop", "forge-core", "forge-game", "forge-ai", "forge-gui"):
            os.makedirs(os.path.join(self.forge, m, "target", "classes"))
        fx.write(self.forge, "forge-gui/res/x.txt", "x\n")
        g.prepare(self.task, self.repo, self.wt)
        self.assertTrue(self.task["needs_jvm"])
        self.assertEqual(self.task["java_test_classes"], ["forge.spike.FooTest"])

    def tearDown(self):
        self.tmp.cleanup()

    def deps(self, *jars):
        path = os.path.join(self.tmp.name, "deps.txt")
        with open(path, "w") as f:
            f.write(":".join(jars) or os.path.join(self.tmp.name, "no-such.jar"))
        return path

    def grade(self, deps):
        return g.grade(self.task, self.wt, self.out, self.forge, deps, self.repo)

    def candidate_file(self, rel, text):
        fx.write(self.wt, rel, text)

    def fixed(self):
        self.candidate_file(SPIKE_DIR + "Foo.java", FOO_V2)

    def log(self):
        with open(os.path.join(self.out, "javac.log")) as f:
            return f.read()

    def test_a_deleted_source_the_gold_patch_keeps_scores_zero_with_a_note(self):
        os.unlink(os.path.join(self.wt, SPIKE_DIR + "Foo.java"))
        res = self.grade(self.deps())
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("deleted source files" in n and "Foo.java" in n for n in res["notes"]), res["notes"])

    def test_the_pin_the_engine_patches_use_is_the_graders_not_the_candidates(self):
        self.fixed()
        self.candidate_file("FORGE_COMMIT", "0123456789abcdef\n")
        try:
            notes = self.grade(self.deps())["notes"]
        except g.SetupError:  # the patched A.java compiled; the run then stops at the reporter (no TestNG in the deps)
            notes = []
        self.assertEqual([n for n in notes if "engine patches" in n], [])
        self.assertNotIn("error:", self.log())

    def test_a_source_path_with_a_space_in_it_reaches_javac(self):
        self.candidate_file("forge-overlay/my dir/src/main/java/forge/util/Spaced.java",
                            "package forge.util;\n\npublic class Spaced { }\n")
        try:
            self.grade(self.deps())  # no TestNG in the deps: it stops at the reporter, after javac
        except g.SetupError:
            pass
        self.assertNotIn("error:", self.log())

    def test_a_candidate_class_in_a_package_outside_forge_is_refused_before_any_test_runs(self):
        self.fixed()
        self.candidate_file("forge-overlay/x/src/main/java/org/testng/TestNG.java", EVIL_TESTNG)
        res = self.grade(self.deps())  # an unrefused tree would go on to compile the reporter and fail here
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("org/testng/TestNG.class" in n for n in res["notes"]), res["notes"])

    def test_a_package_declaration_the_path_does_not_show_is_refused_too(self):
        self.fixed()
        self.candidate_file("forge-overlay/x/src/main/java/forge/util/Sneaky.java",
                            "package org.testng.annotations;\n\npublic class Sneaky { }\n")
        res = self.grade(self.deps())
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("org/testng/annotations/Sneaky.class" in n for n in res["notes"]), res["notes"])

    def test_a_candidate_class_with_the_name_of_a_dependency_class_is_refused(self):
        jar = os.path.join(self.tmp.name, "dep.jar")
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("forge/util/Dup.class", b"x")
        self.fixed()
        self.candidate_file("forge-overlay/x/src/main/java/forge/util/Dup.java", "package forge.util;\n\npublic class Dup { }\n")
        res = self.grade(self.deps(jar))
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("forge/util/Dup.class" in n for n in res["notes"]), res["notes"])

    @unittest.skipUnless(TESTNG_JAR, "no TestNG jar under ~/.m2")
    def test_a_candidate_testng_main_cannot_write_an_all_pass_result(self):
        """The one test here that starts a JVM (run it under the shared lock): the exploit, end to end."""
        self.candidate_file("forge-overlay/x/src/main/java/org/testng/TestNG.java", EVIL_TESTNG)
        res = self.grade(self.deps(TESTNG_JAR))
        self.assertEqual(res["score"], 0)  # Foo is unfixed; an all-PASS forgery would score 1

    @unittest.skipUnless(TESTNG_JAR, "no TestNG jar under ~/.m2")
    def test_the_grader_reporter_compiles_against_testng(self):
        javac = os.path.join(g.java_home(), "bin", "javac")
        dest = g.compile_reporter(javac, TESTNG_JAR, self.tmp.name)
        self.assertTrue(os.path.isfile(os.path.join(dest, g.REPORTER_CLASS + ".class")))

    def test_the_standards_name_the_reporter_as_the_one_java_file_outside_the_overlay(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "docs", "STANDARDS.md"), encoding="utf-8") as f:
            self.assertIn("eval/harness_env/HarnessGraderReporter.java", f.read())
        javas = [os.path.relpath(os.path.join(r, n), root) for r, _, ns in os.walk(os.path.join(root, "eval"))
                 for n in ns if n.endswith(".java")]
        self.assertEqual(javas, ["eval/harness_env/HarnessGraderReporter.java"])

    def test_the_runtime_classpath_puts_the_dependencies_before_the_candidates_classes(self):
        cp = g.runtime_classpath("/r", "/d1.jar:/d2.jar", "/classes", ["/e1", "/e2"]).split(":")
        self.assertEqual(cp, ["/r", "/d1.jar", "/d2.jar", "/classes", "/e1", "/e2"])


class ResultChannelTest(unittest.TestCase):
    """The JVM's results are what the grader's reporter wrote with this run's nonce, and nothing else."""
    FORGED_XML = ('<testng-results><suite name="s"><test name="t"><class name="forge.spike.T">'
                  '<test-method status="PASS" signature="x()" name="x" is-config="false"/></class></test></suite>'
                  '</testng-results>')
    SPEC = "import json, sys; spec = dict(l.strip().split('=', 1) for l in open(sys.argv[1].split('=', 1)[1]));"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(os.path.join(self.out, "testng"))

    def tearDown(self):
        self.tmp.cleanup()

    def jvm(self, script, timeout=30):
        """A stand-in JVM: a python program that gets the spec property as its first argument."""
        return g.run_jvm([sys.executable, "-c", script, "@SPEC@"], self.tmp.name, dict(os.environ), self.out, timeout, self.tmp.name)

    def write_result(self, nonce_expr, results_expr):
        return self.SPEC + "open(spec['result'], 'w').write(json.dumps({'nonce': %s, 'results': %s}))" % (nonce_expr, results_expr)

    def test_a_testng_xml_the_candidate_left_is_not_read(self):
        script = ("import os; p = os.path.join(%r, 'testng', 'testng-results.xml'); open(p, 'w').write(%r)"
                  % (self.out, self.FORGED_XML))
        results, notes = self.jvm(script)
        self.assertEqual(results, {})
        self.assertTrue(any("no usable result" in n for n in notes))
        self.assertEqual(g.score(["forge.spike.T.x"], results)["score"], 0)

    def test_a_result_file_with_another_nonce_is_not_read(self):
        results, notes = self.jvm(self.write_result("'guess'", "[['PASS', False, 'forge.spike.T', 'x']]"))
        self.assertEqual(results, {})
        self.assertTrue(any("nonce" in n for n in notes))

    def test_a_malformed_payload_is_a_zero_with_a_note_not_an_exception(self):
        results, notes = self.jvm(self.write_result("spec['nonce']", "'garbage'"))
        self.assertEqual(results, {})
        self.assertTrue(any("malformed" in n for n in notes))
        results, notes = self.jvm(self.SPEC + "open(spec['result'], 'w').write('{not json')")
        self.assertEqual(results, {})
        self.assertTrue(any("no usable result" in n for n in notes))

    def test_the_reporters_file_with_this_runs_nonce_is_read(self):
        results, notes = self.jvm(self.write_result("spec['nonce']", "[['PASS', False, 'forge.spike.T', 'x']]"))
        self.assertEqual((results, notes), ({"forge.spike.T.x": "PASS"}, []))

    def test_a_timed_out_jvm_has_no_result(self):
        results, notes = self.jvm("import time; time.sleep(30)", timeout=1)
        self.assertEqual(results, {})
        self.assertTrue(any("timed out" in n for n in notes))

    def test_a_crashed_jvm_has_no_result(self):
        results, notes = g.run_jvm(["false"], self.out, dict(os.environ), self.out, 30, self.tmp.name)
        self.assertEqual(results, {})
        self.assertTrue(any("no usable result" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
