"""scripts/mutate_scorers.py: the mutant generator and runner, and the planted self-checks the tool refuses to
report without (an equivalent mutant must survive, a bug must be killed, a hang is an error)."""
import ast
import contextlib
import io
import os
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

sys.path.insert(0, os.path.join(str(evalpath.REPO), "scripts"))
import mutate_scorers as ms  # noqa: E402

SRC = ("def f(a, b):\n    if a < b and b != 0:\n        return a + 1\n    for x in a:\n        if x:\n"
       "            continue\n    return not b\n")


def mutants(src):
    return [ms.mutant_source(src, i) for i in range(len(ms.sites(ast.parse(src))))]


def alive(pid):
    """Running, not a zombie: where PID 1 does not reap, a killed child stays in the table in state Z."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


class MutantGenerationTest(unittest.TestCase):
    def test_each_operator_yields_its_mutant(self):
        got = {(s.kind, s.desc) for _, s in mutants(SRC)}
        for want in [("compare", "Lt -> LtE"), ("compare", "NotEq -> Eq"), ("boolop", "And -> Or"),
                     ("arith", "Add -> Sub"), ("constant", "0 -> 1"), ("constant", "1 -> 0"),
                     ("not", "not x -> bool(x)"), ("return", "return v -> return None"),
                     ("guard", "if-body -> pass"), ("continue", "continue -> pass")]:
            self.assertIn(want, got)

    def test_a_mutant_changes_exactly_the_one_site(self):
        by = {s.desc: src for src, s in mutants(SRC) if s.kind in ("compare", "boolop")}
        base = ast.unparse(ast.parse(SRC))
        self.assertEqual(by["Lt -> LtE"], base.replace("a < b", "a <= b"))
        self.assertEqual(by["And -> Or"], base.replace("a < b and b != 0", "a < b or b != 0"))

    def test_every_mutant_compiles_and_differs_from_the_original(self):
        base = ast.unparse(ast.parse(SRC))
        for src, _ in mutants(SRC):
            compile(src, "<mutant>", "exec")
            self.assertNotEqual(src, base)

    def test_a_main_guard_body_and_docstrings_are_not_mutated(self):
        src = 'def f():\n    "doc 3"\n    return 1\nif __name__ == "__main__":\n    f()\n'
        kinds = sorted(s.kind for _, s in mutants(src))
        self.assertNotIn("guard", kinds)   # the `if __name__` body stays
        self.assertEqual(kinds.count("constant"), 1)   # `1`, not the "doc 3" string

    def test_sampling_is_seeded_and_capped(self):
        big = open(evalpath.EVAL / "sprt.py").read()
        a, total = ms.sample(big, 7, 3)
        self.assertEqual(a, ms.sample(big, 7, 3)[0])
        self.assertNotEqual(a, ms.sample(big, 7, 4)[0])
        self.assertEqual(len(a), 7)
        self.assertEqual(len(ms.sample(SRC, 999, 0)[0]), len(ms.sites(ast.parse(SRC))))
        self.assertGreater(total, 7)


class PlantedMutantsTest(unittest.TestCase):
    def test_the_self_check_planted_mutants_behave(self):
        with tempfile.TemporaryDirectory() as d:
            for name, want, got in ms.self_check(d):
                self.assertEqual(got, want, name)

    def test_a_weak_test_lets_a_real_bug_survive(self):
        """The runner reports a survivor when the test does not look: break the toy's clamp, test only the middle."""
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "toy.py"), "w").write(ms.TOY.replace("return hi", "return lo"))
            open(os.path.join(d, "t.py"), "w").write("import sys, toy\nsys.exit(0 if toy.clamp(2, 0, 3) == 2 else 1)\n")
            self.assertEqual(ms.run_test(d, "t.py", 30)[0], ms.SURVIVED)

    def test_a_run_leaves_no_temp_files_and_sees_only_its_own(self):
        """Tests leak temp dirs; each run gets its own TMPDIR under the copy and loses it (disk, and no cross-run clashes); the hash seed is fixed so a mutant gets one verdict."""
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "t.py"), "w").write(
                "import sys, tempfile, os\nx = tempfile.mkdtemp()\nopen(os.path.join(x, 'f'), 'w').write('1')\n"
                "sys.exit(0 if x.startswith(os.environ['TMPDIR']) and os.environ['TMPDIR'] != '/tmp' and os.environ['PYTHONHASHSEED'] == '0' else 1)\n")
            self.assertEqual(ms.run_test(d, "t.py", 30)[0], ms.SURVIVED)
            self.assertEqual(os.listdir(d), ["t.py"])

    def test_a_run_killed_by_a_signal_is_an_error_not_a_kill(self):
        """An out-of-memory kill is nobody's test failing: it must not raise the mutation score."""
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "t.py"), "w").write("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
            self.assertEqual(ms.run_test(d, "t.py", 30)[0], ms.ERROR)

    def test_a_verdict_that_does_not_repeat_is_an_error_and_one_that_does_stands(self):
        """A test that fails the first time only (a loaded machine) is not a kill; a pass that does not repeat is no
        survivor."""
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "t.py")
            open(t, "w").write("import os, sys\nif os.path.exists('seen'):\n    sys.exit(0)\nopen('seen', 'w').close()\nsys.exit(1)\n")
            self.assertEqual(ms.run_confirmed(d, "t.py", 30)[0], ms.ERROR)
            os.remove(os.path.join(d, "seen"))
            open(t, "w").write("import os, sys\nif os.path.exists('seen'):\n    sys.exit(1)\nopen('seen', 'w').close()\nsys.exit(0)\n")
            self.assertEqual(ms.run_confirmed(d, "t.py", 30)[0], ms.ERROR)
            open(t, "w").write("import sys\nsys.exit(1)\n")
            self.assertEqual(ms.run_confirmed(d, "t.py", 30)[0], ms.KILLED)
            open(t, "w").write("import sys\nsys.exit(0)\n")
            self.assertEqual(ms.run_confirmed(d, "t.py", 30)[0], ms.SURVIVED)

    def test_a_server_the_test_leaves_behind_neither_hangs_the_run_nor_outlives_it(self):
        """The fake model server of a crashed test holds stdout open; the run still ends and the server is reaped."""
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "child.pid")
            open(os.path.join(d, "t.py"), "w").write(
                "import subprocess, sys\nchild = subprocess.Popen(['sleep', '120'])\n"
                "open(%r, 'w').write(str(child.pid))\nsys.exit(0)\n" % pidfile)
            t0 = time.time()
            self.assertEqual(ms.run_test(d, "t.py", 60)[0], ms.SURVIVED)
            self.assertLess(time.time() - t0, 20)
            pid = int(open(pidfile).read())
            for _ in range(50):   # SIGKILL is delivered at once; the poll only waits for init to reap the zombie
                if not alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)   # do not leave the sleeper behind when the assertion fails
                self.fail("the child the test left behind (pid %d) is still running" % pid)

    def test_every_toy_mutant_the_checks_can_tell_apart_is_killed_or_equivalent(self):
        """On the toy: each generated mutant is killed except the ones that cannot change a result."""
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "t.py"), "w").write(ms.TOY_TEST)
            survived = []
            for i in range(len(ms.sites(ast.parse(ms.TOY)))):
                src, site = ms.mutant_source(ms.TOY, i)
                open(os.path.join(d, "toy.py"), "w").write(src)
                if ms.run_test(d, "t.py", 30)[0] == ms.SURVIVED:
                    survived.append(site.desc)
            # `x < lo` -> `x <= lo` and `x > hi` -> `x >= hi` return the same value at the boundary
            self.assertEqual(sorted(survived), ["Gt -> GtE", "Lt -> LtE"])


class CopyTest(unittest.TestCase):
    def test_a_copy_of_the_tree_never_holds_a_secret(self):
        """The copy is a scratch directory; credentials in the clone's root (secrets.env, .env*, .forge-home) stay out."""
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as parent:
            repo = ms.Path(repo)
            for d in ms.COPY_DIRS:
                (repo / d).mkdir()
            for name in ("secrets.env", ".env.local", ".forge-home"):
                (repo / "eval" / name).write_text("planted")
            for name in ("secrets.env", ".env", ".env.local", ".forge-home", "my_secret_token.txt", "FORGE_COMMIT"):
                (repo / name).write_text("planted")
            root = ms.make_copy(parent, repo)
            try:
                self.assertEqual(sorted(p.name for p in root.iterdir() if p.is_file()), ["FORGE_COMMIT"])
                self.assertEqual(os.listdir(root / "eval"), [])
            finally:
                ms.shutil.rmtree(root)


class MainRefusalTest(unittest.TestCase):
    """main() reports nothing and exits 2 when the tool or the baseline cannot be trusted."""

    def run_main(self, *, self_check, run_confirmed, with_test=True):
        saved = ms.self_check, ms.run_confirmed, ms.make_copy
        tiny = tempfile.mkdtemp()
        os.makedirs(os.path.join(tiny, "eval", "tests"))
        if with_test:
            open(os.path.join(tiny, "eval", "tests", "test_sprt.py"), "w").close()
        ms.self_check, ms.run_confirmed, ms.make_copy = (
            lambda parent: self_check, run_confirmed, lambda parent, repo=None: ms.Path(tiny))
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ms.main(["eval/sprt.py"])
        finally:
            ms.self_check, ms.run_confirmed, ms.make_copy = saved
            ms.shutil.rmtree(tiny, ignore_errors=True)
        return rc, err.getvalue(), out.getvalue()

    def test_a_failed_self_check_exits_2_before_any_mutant(self):
        calls = []
        rc, err, out = self.run_main(self_check=[("planted bug is killed", ms.KILLED, ms.SURVIVED)],
                                     run_confirmed=lambda *a: calls.append(a))
        self.assertEqual(rc, 2)
        self.assertIn("self-check FAILED", err)
        self.assertEqual((calls, out), ([], ""), "nothing ran and nothing was reported")

    def test_a_test_file_that_fails_unmutated_exits_2_before_any_mutant(self):
        """Otherwise every mutant would look killed."""
        rc, err, out = self.run_main(self_check=[], run_confirmed=lambda *a: (ms.KILLED, 0.1, "boom"))
        self.assertEqual(rc, 2)
        self.assertIn("fails unmutated", err)
        self.assertEqual(out, "")

    def test_a_module_with_no_test_file_exits_2_before_any_mutant(self):
        calls = []
        rc, err, out = self.run_main(self_check=[], run_confirmed=lambda *a: calls.append(a), with_test=False)
        self.assertEqual(rc, 2)
        self.assertIn("no test file", err)
        self.assertEqual((calls, out), ([], ""))


class WilsonTest(unittest.TestCase):
    def test_interval(self):
        lo, hi = ms.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.490, places=2)
        self.assertAlmostEqual(hi, 0.943, places=2)
        self.assertEqual(ms.wilson(0, 0), (0.0, 1.0))


class WeightedTest(unittest.TestCase):
    @staticmethod
    def rows(k, s, e=0):
        return [{"status": ms.KILLED}] * k + [{"status": ms.SURVIVED}] * s + [{"status": ms.ERROR}] * e

    def test_a_file_counts_in_proportion_to_its_sites_not_its_sample(self):
        results = [{"file": "a.py", "sampled": 10, "sites": 900, "rows": self.rows(2, 8)},
                   {"file": "b.py", "sampled": 10, "sites": 100, "rows": self.rows(10, 0)}]
        est, lo, hi = ms.weighted(results)
        self.assertAlmostEqual(est, (900 * 0.2 + 100 * 1.0) / 1000)   # the pooled sample says 12/20 = 60%
        self.assertLess(lo, est)
        self.assertGreater(hi, est)
        self.assertIn("| all (weighted by each file's sites) | 1000 |", ms.markdown(results))

    def test_the_interval_is_the_stratified_normal_one_with_a_file_of_only_errors_in_the_total(self):
        """est = sum(N_i p_i) / sum(N_i); se = sqrt(sum(N_i^2 p_i (1 - p_i) / n_i)) / sum(N_i); a file with no
        killed or survived mutant adds its sites to the total and nothing to the sums."""
        results = [{"file": "a.py", "sampled": 10, "sites": 900, "rows": self.rows(6, 4)},    # p = 0.6, n = 10
                   {"file": "b.py", "sampled": 5, "sites": 100, "rows": self.rows(4, 1)},     # p = 0.8, n = 5
                   {"file": "c.py", "sampled": 3, "sites": 50, "rows": self.rows(0, 0, 3)}]   # only errors
        total = 900 + 100 + 50
        est = (900 * 0.6 + 100 * 0.8) / total                                   # 620 / 1050
        var = 900 ** 2 * 0.6 * 0.4 / 10 + 100 ** 2 * 0.8 * 0.2 / 5              # 19440 + 320
        se = var ** 0.5 / total
        got = ms.weighted(results)
        self.assertAlmostEqual(got[0], est)
        self.assertAlmostEqual(got[0], 0.590476, places=6)
        self.assertAlmostEqual(se, 0.133876, places=6)
        self.assertAlmostEqual(got[1], est - 1.96 * se)
        self.assertAlmostEqual(got[2], est + 1.96 * se)
        self.assertAlmostEqual(got[1], 0.32808, places=4)
        self.assertAlmostEqual(got[2], 0.85287, places=4)


if __name__ == "__main__":
    unittest.main()
