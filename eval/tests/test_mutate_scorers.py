"""scripts/mutate_scorers.py: the mutant generator and runner, and the planted self-checks the tool refuses to
report without (an equivalent mutant must survive, a bug must be killed, a hang is an error)."""
import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

sys.path.insert(0, os.path.join(str(evalpath.REPO), "scripts"))
import mutate_scorers as ms  # noqa: E402

SRC = ("def f(a, b):\n    if a < b and b != 0:\n        return a + 1\n    for x in a:\n        if x:\n"
       "            continue\n    return not b\n")


def mutants(src):
    return [ms.mutant_source(src, i) for i in range(len(ms.sites(ast.parse(src))))]


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
        import time
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "t.py"), "w").write(
                "import subprocess, sys\nsubprocess.Popen(['sleep', '120'])\nsys.exit(0)\n")
            t0 = time.time()
            self.assertEqual(ms.run_test(d, "t.py", 60)[0], ms.SURVIVED)
            self.assertLess(time.time() - t0, 20)

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


class WilsonTest(unittest.TestCase):
    def test_interval(self):
        lo, hi = ms.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.490, places=2)
        self.assertAlmostEqual(hi, 0.943, places=2)
        self.assertEqual(ms.wilson(0, 0), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
