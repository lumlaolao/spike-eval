"""Every eval and scripts/ Python file still starts after a move (docs/STANDARDS.md section 3): each module
it imports resolves from its own folder, the standard library, or (only if it imports evalpath) the folders
evalpath puts on sys.path; and each script with a command line answers --help with exit 0, run from outside
the repository as a user would. Nothing is executed beyond argument parsing: no game, no network."""
import ast
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import test_untested_list  # noqa: E402  (one rule for what an eval script is)

REPO = evalpath.REPO
MAIN_GUARD = re.compile(r"__name__\s*==\s*['\"]__main__['\"]")


def scripts():
    return sorted(test_untested_list.scripts()
                  + [p for p in (REPO / "scripts").rglob("*.py") if "__pycache__" not in p.parts])


def imported_modules(path):
    """The first component of every absolute import in a file, at module level or in a function."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def outside_repo(name):
    """The standard library (or anything installed), never a file of this repository."""
    if name in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(name)
    return spec is not None and not (spec.origin or "").startswith(str(REPO) + os.sep)


def has_command_line(path):
    src = path.read_text(encoding="utf-8")
    return "argparse" in src and MAIN_GUARD.search(src) is not None


class ScriptsStartTest(unittest.TestCase):

    def test_every_import_resolves(self):
        unresolved = []
        for path in scripts():
            names = imported_modules(path)
            folders = [str(path.parent)] + ([str(d) for d in evalpath.DIRS] if "evalpath" in names else [])
            for name in sorted(names - {"evalpath"}):
                if importlib.machinery.PathFinder.find_spec(name, folders) is None and not outside_repo(name):
                    unresolved.append("%s: %s" % (path.relative_to(REPO), name))
        self.assertEqual(unresolved, [])

    def test_every_command_line_answers_help(self):
        clis = [p for p in scripts() if has_command_line(p)]
        self.assertGreater(len(clis), 40, "the scan found almost no scripts: is the pattern still right?")
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENROUTER_ALLOW_EGRESS")}
        failed = []
        with tempfile.TemporaryDirectory() as cwd:
            for path in clis:
                p = subprocess.run([sys.executable, str(path), "--help"], cwd=cwd, env=env,
                                   capture_output=True, text=True, timeout=60)
                if p.returncode != 0 or "usage" not in p.stdout.lower():
                    failed.append("%s: rc=%d %s" % (path.relative_to(REPO), p.returncode,
                                                    (p.stderr.strip().splitlines() or [""])[-1][:120]))
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
