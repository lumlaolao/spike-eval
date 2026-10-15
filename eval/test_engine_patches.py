#!/usr/bin/env python3
"""Engine patches (docs/harness/ENGINE-PATCHES.md) stay tied to the pinned engine.

For every row of the table: the overlay file exists, the upstream original at FORGE_COMMIT (read from the
Forge checkout with `git show`) has the recorded sha256, and every hunk that differs from it carries a
`HARNESS PATCH` marker. Needs the Forge checkout ($FORGE_HOME, else ~/code/forge, else /root/forge); without
one the upstream checks are skipped and say so.
"""

import difflib
import hashlib
import os
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs/harness/ENGINE-PATCHES.md"
ROW = re.compile(r"^\| `(forge-overlay/[^`]+)` \| `([0-9a-f]{64})` \|", re.M)


def forge_home():
    for c in (os.environ.get("FORGE_HOME"), str(Path.home() / "code/forge"), "/root/forge"):
        if c and (Path(c) / ".git").exists():
            return Path(c)
    return None


def upstream(forge, commit, rel):
    return subprocess.run(["git", "-C", str(forge), "show", "%s:%s" % (commit, rel)],
                          capture_output=True, check=True).stdout


class EnginePatchesTest(unittest.TestCase):

    def rows(self):
        rows = ROW.findall(DOC.read_text(encoding="utf-8"))
        self.assertTrue(rows, "no patch rows in %s" % DOC)
        return rows

    def test_every_patched_file_exists_and_is_marked(self):
        for path, _ in self.rows():
            text = (REPO / path).read_text(encoding="utf-8")
            self.assertIn("HARNESS PATCH", text, path)

    def test_the_upstream_original_is_the_recorded_one_and_every_change_is_marked(self):
        forge = forge_home()
        if forge is None:
            self.skipTest("no Forge checkout: upstream originals not checked")
        commit = (REPO / "FORGE_COMMIT").read_text().strip()
        for path, sha in self.rows():
            rel = path[len("forge-overlay/"):]
            orig = upstream(forge, commit, rel)
            self.assertEqual(hashlib.sha256(orig).hexdigest(), sha,
                             "%s: the upstream file at %s is not the one the patch was made from; re-diff it"
                             % (rel, commit[:12]))
            ours = (REPO / path).read_text(encoding="utf-8").splitlines()
            theirs = orig.decode("utf-8").splitlines()
            groups = list(difflib.SequenceMatcher(None, theirs, ours, autojunk=False).get_grouped_opcodes(3))
            self.assertTrue(groups, "%s is identical to upstream: not a patch" % path)
            for g in groups:
                changed = [ln for tag, i1, i2, j1, j2 in g if tag != "equal" for ln in ours[j1:j2] + theirs[i1:i2]]
                self.assertTrue(any("HARNESS PATCH" in ln for ln in changed),
                                "%s: a change without a HARNESS PATCH marker:\n%s" % (path, "\n".join(changed[:8])))


if __name__ == "__main__":
    unittest.main()
