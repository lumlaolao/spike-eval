"""eval/research/README.md catalogues the research notes (docs/STANDARDS.md, the eval/research/ row): every dated
note has a row in the Notes table, every note it links exists, and every other file in the folder is a dated note.
It does not check the PR numbers or code names the catalogue cites; those are read in review."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402

FOLDER = evalpath.REPO / "eval" / "research"
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
LINK = re.compile(r"\]\(([^)#]+\.md)\)")


class ResearchIndexTest(unittest.TestCase):
    def setUp(self):
        self.index = (FOLDER / "README.md").read_text(encoding="utf-8")
        self.notes_table = self.index.split("## Ideas")[0]

    def test_every_dated_note_has_a_row(self):
        linked = set(LINK.findall(self.notes_table))
        missing = sorted(p.name for p in FOLDER.glob("*.md") if DATED.match(p.name) and p.name not in linked)
        self.assertEqual(missing, [], "add a row to eval/research/README.md for each")

    def test_every_linked_note_exists(self):
        dead = sorted(n for n in set(LINK.findall(self.index)) if not (FOLDER / n).is_file())
        self.assertEqual(dead, [])

    def test_the_index_is_the_only_undated_file(self):
        odd = sorted(p.name for p in FOLDER.iterdir() if p.is_file() and p.name != "README.md"
                     and not DATED.match(p.name))
        self.assertEqual(odd, [])


if __name__ == "__main__":
    unittest.main()
