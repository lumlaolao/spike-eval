"""docs/STANDARDS.md section 5: a test that runs a root script or a game launcher gives it a scratch HOME.

Every one of them falls back to a place under $HOME when a variable is unset (install.sh and play-vs-llm.sh to
$HOME/code/forge), so a test that passes the real HOME can overwrite or launch the owner's own Forge build: #463's
test opened a real game window on the owner's Mac. This scan finds each subprocess call in eval/tests that runs one
of them (a list literal, or a name assigned one in the same function) and fails when it passes no env= (or None),
when its function sets no HOME key, or when every HOME it sets is this machine's (os.environ, expanduser). It reads
the source only: an argv built some other way, or a HOME that is real by another route, is left to review.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

SCRIPTS = ("setup.sh", "play-vs-llm.sh", "install.sh", "run-llm-headless.sh", "run-llm-spike.sh", "launcher")
CALLS = ("run", "Popen", "call", "check_call", "check_output")
REAL_HOME = ("environ", "expanduser", "Path.home", "getenv")   # a HOME taken from this machine is not a scratch one


def _scopes(tree):
    """(scope node, its calls): each function's own calls, and the module's (class bodies included) for the rest."""
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    inner = {id(c) for f in funcs for c in ast.walk(f) if isinstance(c, ast.Call)}
    yield tree, [c for c in ast.walk(tree) if isinstance(c, ast.Call) and id(c) not in inner]
    for f in funcs:
        nested = {id(c) for g in ast.walk(f) if g is not f and isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for c in ast.walk(g) if isinstance(c, ast.Call)}
        yield f, [c for c in ast.walk(f) if isinstance(c, ast.Call) and id(c) not in nested]


def _home_values(scope):
    """Every value the scope gives a HOME key: HOME=x, {"HOME": x}, d["HOME"] = x, d.setdefault("HOME", x)."""
    out = []
    for n in ast.walk(scope):
        if isinstance(n, ast.Call):
            out += [k.value for k in n.keywords if k.arg == "HOME"]
            if (isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault" and len(n.args) == 2
                    and isinstance(n.args[0], ast.Constant) and n.args[0].value == "HOME"):
                out.append(n.args[1])
        elif isinstance(n, ast.Dict):
            out += [v for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "HOME"]
        elif isinstance(n, ast.Assign):
            out += [n.value for t in n.targets if isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant) and t.slice.value == "HOME"]
    return out


def _argv(scope, node):
    """The list a call runs: a literal, or the last list literal assigned to the name it passes."""
    if isinstance(node, ast.List):
        return node
    if isinstance(node, ast.Name):
        lists = [n.value for n in ast.walk(scope) if isinstance(n, ast.Assign) and isinstance(n.value, ast.List)
                 and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets)]
        return lists[-1] if lists else None
    return None


def offenders(source, name="<test>"):
    """-> ["<name>:<line>: <why>"] for each call of a root script or launcher that does not pass a scratch HOME."""
    tree = ast.parse(source)
    seg = lambda n: ast.get_source_segment(source, n) or ""   # noqa: E731
    out = []
    for scope, calls in _scopes(tree):
        homes = None
        for call in calls:
            if not (isinstance(call.func, ast.Attribute) and call.func.attr in CALLS and call.args):
                continue
            argv = _argv(scope, call.args[0])
            if argv is None or not any(s in seg(e) for e in argv.elts for s in SCRIPTS):
                continue
            if "-n" in [e.value for e in argv.elts if isinstance(e, ast.Constant)]:
                continue   # bash -n only parses the script
            where = "%s:%d: runs %s" % (name, call.lineno, seg(argv))
            env = next((k.value for k in call.keywords if k.arg == "env"), None)
            if env is None or (isinstance(env, ast.Constant) and env.value is None):
                out.append(where + " with this machine's environment (no env=)")
                continue
            homes = _home_values(scope) if homes is None else homes
            if not homes:
                out.append(where + " and never sets HOME")
            elif all(any(r in seg(v) for r in REAL_HOME) for v in homes):
                out.append(where + " with this machine's own HOME")
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

    def test_the_scan_sees_each_way_a_script_gets_this_machine_s_home(self):
        bad = ("import os, subprocess\n\n"
               "def f(env):\n    subprocess.run(['bash', 'install.sh'])\n"
               "    subprocess.run(['bash', 'play-vs-llm.sh'], env=dict(env, FORGE_HOME='x'))  # HOME in a comment\n\n"
               "def g():\n    cmd = ['/bin/bash', 'setup.sh']\n    subprocess.run(cmd, env=None)\n\n"
               "def h():\n    subprocess.run([str(P / 'run-llm-spike.sh')], env=dict(os.environ, HOME=os.environ['HOME']))\n\n"
               "subprocess.run(['bash', 'setup.sh'])\n")
        got = offenders(bad)
        self.assertEqual(len(got), 5, got)
        self.assertEqual(sum("no env=" in g for g in got), 3, got)
        self.assertEqual(sum("never sets HOME" in g for g in got), 1, got)
        self.assertEqual(sum("own HOME" in g for g in got), 1, got)

    def test_a_call_with_a_scratch_home_and_a_syntax_check_pass(self):
        good = ("import subprocess\n\n"
                "def f(env, tmp):\n    env['HOME'] = tmp\n    subprocess.run(['bash', 'setup.sh'], env=env)\n"
                "    subprocess.run(['bash', '-n', 'setup.sh'])\n\n"
                "def g(tmp):\n    base = {'HOME': tmp}\n    subprocess.run(['bash', 'install.sh'], env=base)\n\n"
                "def h(tmp, base):\n    base.setdefault('HOME', tmp)\n    subprocess.run(['bash', 'install.sh'], env=base)\n")
        self.assertEqual(offenders(good), [])


class AcceptanceForgeHomeTest(unittest.TestCase):
    def test_the_pytests_stage_hands_the_tests_this_run_s_forge(self):
        """Without it a test that reads FORGE_HOME falls back to its own default and may skip (test_rollout)."""
        with open(os.path.join(str(evalpath.EVAL), "harness_acceptance.sh"), encoding="utf-8") as f:
            line = next(ln for ln in f if "unittest discover -s eval/tests" in ln and ">\"$OUT/pytests.log\"" in ln)
        self.assertIn('FORGE_HOME="$FORGE" python3 -m unittest', line)


if __name__ == "__main__":
    unittest.main()
