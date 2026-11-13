#!/usr/bin/env python3
"""Runs the script-style test files under unittest, so the acceptance gate sees them.

Ten eval/tests/test_*.py files predate unittest here: their checks are module-level `test_` functions
driven by their own `main()`, which exits non-zero on a failure. `unittest discover` collects
none of them, so `harness_acceptance.sh`'s pytests stage could not see them fail. This file runs
each such script in a subprocess and requires exit 0.

A file is "script-style" when it defines a module-level `test_` function and no TestCase class
(test_defect_analyzer.py has its own TestCase adapter, so it is collected already). It cannot see a
check a script's `main()` forgets to call; every such main today iterates globals() or names each
function.
"""

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

HERE = Path(__file__).resolve().parent


def script_style_tests():
    found = []
    for path in sorted(HERE.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        has_fn = any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
                     for n in tree.body)
        has_case = any(isinstance(n, ast.ClassDef)
                       and any("TestCase" in ast.unparse(b) for b in n.bases)
                       for n in tree.body)
        if has_fn and not has_case:
            found.append(path)
    return found


class ScriptStyleSuites(unittest.TestCase):

    def test_the_known_script_style_files_are_all_found(self):
        # A rename or a conversion to unittest moves this list on purpose; an empty or shrunken
        # list from a broken detector would otherwise make the next test pass vacuously.
        names = {p.name for p in script_style_tests()}
        for expected in ("test_aggregate.py", "test_make_sft_data.py", "test_replay_match.py",
                         "test_seed_policy.py", "test_sprt.py", "test_tournament.py"):
            self.assertIn(expected, names)
        self.assertNotIn("test_defect_analyzer.py", names)

    def test_every_script_style_file_exits_zero(self):
        for path in script_style_tests():
            with self.subTest(script=path.name):
                with tempfile.TemporaryDirectory() as tmp:  # what a script leaves in its temp dir goes with it
                    proc = subprocess.run([sys.executable, str(path)], cwd=str(HERE.parent), capture_output=True,
                                          text=True, timeout=600, env=dict(os.environ, TMPDIR=tmp))
                self.assertEqual(proc.returncode, 0,
                                 "%s exited %d:\n%s%s" % (path.name, proc.returncode,
                                                          proc.stdout[-2000:], proc.stderr[-2000:]))


if __name__ == "__main__":
    unittest.main()
