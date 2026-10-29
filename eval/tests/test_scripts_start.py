"""Every eval and scripts/ Python file still starts after a move (docs/STANDARDS.md section 3): each module
it imports resolves from the folders evalpath puts on sys.path (and its own folder), and each script with
a command line answers --help with exit 0, run from outside the repository as a user would. Nothing is
executed beyond argument parsing: no game, no network."""
import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

REPO = evalpath.REPO


def scripts():
    found = list(evalpath.EVAL.rglob("*.py")) + list((REPO / "scripts").rglob("*.py"))
    return sorted(p for p in found if "__pycache__" not in p.parts and evalpath.EVAL / "tests" not in p.parents)


def top_level_imports(path):
    """Absolute imports a file makes at module level or in any function (relative imports aside)."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def has_command_line(path):
    src = path.read_text(encoding="utf-8")
    return "argparse" in src and ("__name__ == \"__main__\"" in src or "__name__ == '__main__'" in src)


class ScriptsStartTest(unittest.TestCase):

    def test_every_import_resolves(self):
        unresolved = []
        for path in scripts():
            sys.path.insert(0, str(path.parent))
            try:
                for name in sorted(top_level_imports(path)):
                    if importlib.util.find_spec(name) is None:
                        unresolved.append("%s: %s" % (path.relative_to(REPO), name))
            finally:
                sys.path.remove(str(path.parent))
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
