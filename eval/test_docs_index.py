"""docs/harness/INDEX.md lists every document under docs/harness/, so none is read as current by mistake."""
import os
import re
import unittest

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "harness")


class DocsIndexTest(unittest.TestCase):
    def test_every_document_is_listed(self):
        index = open(os.path.join(DOCS, "INDEX.md"), encoding="utf-8").read()
        listed = set(re.findall(r"[\w.-]+\.(?:md|tsv)", index))
        here = {f for f in os.listdir(DOCS) if f.endswith((".md", ".tsv"))}
        self.assertEqual(sorted(here - listed), [], "add these to docs/harness/INDEX.md (current or historical)")

    def test_the_agent_entry_points_exist(self):
        root = os.path.dirname(os.path.dirname(DOCS))
        for path in ("CLAUDE.md", ".claude/rules/forge-overlay.md", ".claude/rules/eval.md", ".claude/rules/seat-prompts.md"):
            self.assertTrue(os.path.isfile(os.path.join(root, path)), path)
        self.assertLessEqual(sum(1 for _ in open(os.path.join(root, "CLAUDE.md"), encoding="utf-8")), 200,
                             "CLAUDE.md is loaded into every session: keep it under 200 lines (move detail to .claude/rules/)")


if __name__ == "__main__":
    unittest.main()
