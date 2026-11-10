"""docs/STANDARDS.md section 5: a test that runs a root script or a game launcher gives it a scratch HOME.

Every one of them falls back to a place under $HOME when a variable is unset (install.sh and play-vs-llm.sh to
$HOME/code/forge), so a test that passes the real HOME can overwrite or launch the owner's own Forge build: #463's
test opened a real game window on the owner's Mac. This scan finds each subprocess call in eval/tests that runs one
of them and fails when the call passes no env=, or when the function it is in never names HOME. It reads the source
only; whether the HOME it sets is a scratch one is left to review.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

SCRIPTS = ("setup.sh", "play-vs-llm.sh", "install.sh", "run-llm-headless.sh", "run-llm-spike.sh", "launcher")
CALLS = ("run", "Popen", "call", "check_call", "check_output")


def offenders(source, name="<test>"):
    """-> ["<name>:<line>: <why>"] for each call of a root script or launcher that does not pass a HOME."""
    tree = ast.parse(source)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, fn) or ""
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in CALLS
                    and call.args and isinstance(call.args[0], ast.List) and call.args[0].elts):
                continue
            first = call.args[0].elts[0]
            if not (isinstance(first, ast.Constant) and first.value in ("bash", "sh")):
                continue
            argv = ast.get_source_segment(source, call.args[0]) or ""
            if "-n" in [e.value for e in call.args[0].elts if isinstance(e, ast.Constant)]:
                continue   # bash -n only parses the script
            if not any(s in argv for s in SCRIPTS):
                continue
            if not any(k.arg == "env" for k in call.keywords):
                out.append("%s:%d: runs %s with this machine's environment (no env=)" % (name, call.lineno, argv))
            elif "HOME" not in body.replace("FORGE_HOME", "").replace("JAVA_HOME", ""):
                out.append("%s:%d: runs %s and %s never sets HOME" % (name, call.lineno, argv, fn.name))
    return out


class ScratchHomeTest(unittest.TestCase):
    def test_every_test_that_runs_a_root_script_or_a_launcher_gives_it_a_home(self):
        tests = os.path.join(str(evalpath.EVAL), "tests")
        found = []
        for f in sorted(os.listdir(tests)):
            if f.startswith("test_") and f.endswith(".py"):
                with open(os.path.join(tests, f), encoding="utf-8") as fh:
                    found += offenders(fh.read(), f)
        self.assertEqual(found, [])

    def test_the_scan_sees_a_call_that_passes_no_home(self):
        bad = ("import subprocess\n\ndef f(env):\n    subprocess.run(['bash', 'install.sh'])\n"
               "    subprocess.run(['bash', 'play-vs-llm.sh'], env=dict(env, FORGE_HOME='x'))\n")
        got = offenders(bad)
        self.assertEqual(len(got), 2, got)
        self.assertIn("no env=", got[0])
        self.assertIn("never sets HOME", got[1])

    def test_a_call_with_a_home_and_a_syntax_check_pass(self):
        good = ("import subprocess\n\ndef f(env, tmp):\n    env['HOME'] = tmp\n"
                "    subprocess.run(['bash', 'setup.sh'], env=env)\n    subprocess.run(['bash', '-n', 'setup.sh'])\n")
        self.assertEqual(offenders(good), [])


class AcceptanceForgeHomeTest(unittest.TestCase):
    def test_the_pytests_stage_hands_the_tests_this_run_s_forge(self):
        """Without it a test that reads FORGE_HOME falls back to its own default and may skip (test_rollout)."""
        with open(os.path.join(str(evalpath.EVAL), "harness_acceptance.sh"), encoding="utf-8") as f:
            line = next(ln for ln in f if "unittest discover -s eval/tests" in ln and ">\"$OUT/pytests.log\"" in ln)
        self.assertIn('FORGE_HOME="$FORGE" python3 -m unittest', line)


if __name__ == "__main__":
    unittest.main()
