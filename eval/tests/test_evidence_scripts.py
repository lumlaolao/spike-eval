"""A script kept in an evidence folder is the record of how its data was made (docs/STANDARDS.md section 3): it
parses, opens with a docstring that says what it does and what it cannot, and names no machine's own paths, so a
reader of a fresh clone can tell what produced the rows and rerun it from there."""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

EVIDENCE = evalpath.REPO / "docs" / "harness" / "evidence"
# An absolute path into one machine's home, root or temp folder; a placeholder such as <forge> is not one.
MACHINE_PATH = re.compile(r"(?<![\w.<])/(home|root|Users|tmp)/")


def problems(name, text):
    """What keeps one evidence script from being a record: empty when it is one."""
    try:
        tree = ast.parse(text, name)
    except SyntaxError as e:
        return ["%s does not parse: %s" % (name, e)]
    out = []
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
        good = '"""Does x; cannot y."""\nimport os\nBUILD = os.environ["BUILD"]  # e.g. <forge>/build\n'
        self.assertEqual(problems("ok.py", good), [])
        self.assertIn("does not parse", problems("bad.py", "def (:\n")[0])
        self.assertIn("no module docstring", problems("bare.py", "# a comment is not a docstring\nx = 1\n")[0])
        for path in ("/home/user/wt", "/root/forge", "/Users/me/x", "/tmp/claude-0/s"):
            got = problems("p.py", '"""d."""\nB = "%s"\n' % path)
            self.assertEqual(len(got), 1, path)
            self.assertIn("p.py:2 names a machine path", got[0])
        self.assertEqual(problems("rel.py", '"""d."""\nP = "eval/puzzles/home/x"\n'), [])


if __name__ == "__main__":
    unittest.main()
