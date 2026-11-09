"""A script kept in an evidence folder is the record of how its data was made (docs/STANDARDS.md section 3), so a
reader of a fresh clone can tell what produced the rows and rerun it from there. Checked here: it parses, has a
module docstring (present; what it says is a reviewer's read), imports only the standard library and the
repository's own modules, and names no absolute path under a machine's home, temp or mount folders."""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

EVIDENCE = evalpath.REPO / "docs" / "harness" / "evidence"
# An absolute path into one machine's home, temp or mount folders (Linux, macOS, Windows). A placeholder such as
# <forge>, a $VAR, a URL's path and a repository-relative path are not one.
# C:\Users\ is matched as written or escaped in a literal.
MACHINE_PATH = re.compile(r"(?<![\w.<$~:/])/(home|root|Users|tmp|var|mnt|opt|private|Volumes)/"
                          r"|\b[A-Za-z]:(\\){1,2}Users(\\){1,2}")
# Python 3.10+ lists its own modules; on the 3.9 floor the import check is skipped (the rest still runs).
STDLIB = getattr(sys, "stdlib_module_names", None)
REPO_MODULES = ({p.stem for p in evalpath.REPO.glob("eval/**/*.py")}
                | {p.stem for p in evalpath.REPO.glob("scripts/*.py")})


def problems(name, text):
    """What keeps one evidence script from being a record: empty when it is one."""
    try:
        tree = ast.parse(text, name)
    except SyntaxError as e:
        return ["%s does not parse: %s" % (name, e)]
    out = []
    for node in (ast.walk(tree) if STDLIB else ()):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
        for top in (n.split(".")[0] for n in names):
            if top not in STDLIB and top not in REPO_MODULES:
                out.append("%s:%d imports %s, neither the standard library nor a repository module"
                           % (name, node.lineno, top))
    if not ast.get_docstring(tree):
        out.append("%s has no module docstring saying what it does and what it cannot" % name)
    for n, line in enumerate(text.splitlines(), 1):
        if MACHINE_PATH.search(line):
            out.append("%s:%d names a machine path: %s" % (name, n, line.strip()))
    return out


class EvidenceScriptsTest(unittest.TestCase):

    def test_every_evidence_script_is_a_record(self):
        scripts = sorted(EVIDENCE.rglob("*.py"))
        self.assertTrue(scripts, "no script found under %s: the check reads nothing" % EVIDENCE)
        found = [p for s in scripts for p in problems(str(s.relative_to(evalpath.REPO)), s.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])

    def test_each_problem_is_caught(self):
        good = ('"""Does x; cannot y."""\nimport os, json\nimport puzzle_baselines as pb\nfrom pathlib import Path\n'
                'BUILD = os.environ["BUILD"]  # e.g. <forge>/build or $FORGE_HOME/tmp/x\n'
                'URL = "https://example.com/home/x/"\nREL = "eval/puzzles/home/x"\nHOME = "~/tmp/x"\n')
        self.assertEqual(problems("ok.py", good), [])
        self.assertIn("does not parse", problems("bad.py", "def (:\n")[0])
        self.assertIn("no module docstring", problems("bare.py", "# a comment is not a docstring\nx = 1\n")[0])
        if STDLIB:
            self.assertIn("imports numpy", problems("np.py", '"""d."""\nimport numpy\n')[0])
            self.assertIn("imports requests", problems("rq.py", '"""d."""\nfrom requests.auth import x\n')[0])
        for path in ("/home/user/wt", "/root/forge", "/Users/me/x", "/tmp/claude-0/s", "/var/folders/a/T/x",
                     "/mnt/data/x", "/opt/forge/x", "/private/var/x", "/Volumes/ext/x", "C:\\\\Users\\\\me\\\\x"):
            got = problems("p.py", '"""d."""\nB = "%s"\n' % path)
            self.assertEqual(len(got), 1, path)
            self.assertIn("p.py:2 names a machine path", got[0])


if __name__ == "__main__":
    unittest.main()
