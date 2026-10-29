"""Tests for evalpath.py: every folder of eval scripts is importable by module name."""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

# Folders whose scripts import nothing from the others and are run by path, never imported.
STANDALONE = {"tests", "puzzles/modern", "puzzles/modern/tools"}


class EvalPathTest(unittest.TestCase):

    def test_every_script_folder_is_on_the_path(self):
        script_dirs = {p.parent for p in evalpath.EVAL.rglob("*.py") if "__pycache__" not in p.parts}
        missing = sorted(str(d.relative_to(evalpath.EVAL)) for d in script_dirs
                         if d not in evalpath.DIRS and str(d.relative_to(evalpath.EVAL)) not in STANDALONE)
        self.assertEqual(missing, [], "a folder of eval scripts that evalpath.DIRS does not list")

    def test_a_script_in_each_folder_imports_from_a_fresh_interpreter(self):
        # One module per folder, imported the way a script outside eval/ does it.
        code = ("import sys; sys.path.insert(0, %r); import evalpath; "
                "import aggregate, demo_events, harness_env_spec, puzzle_format" % str(evalpath.EVAL))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_the_repo_is_the_folder_above_eval(self):
        self.assertTrue((evalpath.REPO / "forge-overlay").is_dir())
        self.assertEqual(evalpath.EVAL.name, "eval")


if __name__ == "__main__":
    unittest.main()
