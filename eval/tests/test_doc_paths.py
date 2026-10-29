"""Every repository path named in backticks in the files agents read first exists (docs/STANDARDS.md
section 7). A dead path there sends every agent that reads it nowhere; layout moves left several."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

REPO = evalpath.REPO
DOCS = (["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "README.md", "docs/STANDARDS.md"]
        + sorted(str(p.relative_to(REPO)) for p in REPO.glob(".claude/rules/*.md"))
        + sorted(str(p.relative_to(REPO)) for p in REPO.glob(".claude/skills/*/SKILL.md")))
# Only paths under the repository's own folders; placeholders (<name>, *, $VAR, ...) are not paths.
TOP = ("eval/", "docs/", "scripts/", "forge-overlay/", "engine-patches/", ".claude/", "data/", "datasets/")
TOKEN = re.compile(r"`([^`\s]+)`")


def named_paths():
    for doc in DOCS:
        for n, line in enumerate((REPO / doc).read_text(encoding="utf-8").splitlines(), 1):
            for t in TOKEN.findall(line):
                p = t.rstrip(".,:;)").split("#")[0].split(":")[0]
                if p.startswith(TOP) and not any(c in p for c in "<>*$") and "..." not in p:
                    yield doc, n, p


class DocPathsTest(unittest.TestCase):

    def test_every_named_path_exists(self):
        found = list(named_paths())
        self.assertGreater(len(found), 50, "the scan found almost nothing: is the pattern still right?")
        dead = ["%s:%d %s" % (d, n, p) for d, n, p in found if not (REPO / p).exists()]
        self.assertEqual(dead, [])


if __name__ == "__main__":
    unittest.main()
