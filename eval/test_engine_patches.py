#!/usr/bin/env python3
"""Engine patches (docs/harness/ENGINE-PATCHES.md): the tool that materializes them, and the patches themselves.

MaterializeTest drives eval/engine_patches.py on a throwaway Forge repository: a patch is applied to the pinned
original read from git, never to the working tree (which may hold an installed patched copy); a patch that does not
apply fails loudly with the file named; `diff` re-makes a patch that `apply` turns back into the edited file.

EnginePatchesTest checks the real patches for every row of the table: the patch file exists and opens with the GPL
notice, the upstream original at FORGE_COMMIT (read with `git show`) has the recorded sha256, the patch applies to
it, and every hunk adds a line marked `HARNESS PATCH`. It also checks that the overlay ships no full copy of a Forge
file. The upstream checks need the Forge checkout ($FORGE_HOME, else ~/code/forge, else /root/forge); without one
they are skipped and say so.
"""

import contextlib
import hashlib
import io
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import engine_patches as ep

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs/harness/ENGINE-PATCHES.md"
ROW = re.compile(r"^\| `(engine-patches/[^`]+\.patch)` \| `([0-9a-f]{64})` \|", re.M)
# Overlay files that replace a Forge file whole and are not engine patches. The avatar sprite sheet is an owner
# decision still open (docs/harness/WORK-PLAN.md, "Owner decisions from a hygiene audit").
WHOLE_FILE_EXCEPTIONS = {"forge-gui/res/skins/default/sprite_avatars.png"}

REL = "forge-game/src/main/java/forge/game/A.java"
UPSTREAM = "".join("    line %d\n" % i for i in range(1, 21))
OURS = UPSTREAM.replace("    line 10\n", "    line 10\n    // HARNESS PATCH: added\n")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, check=True).stdout


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class MaterializeTest(unittest.TestCase):
    """A throwaway Forge (one commit) and a throwaway repository pinned to it, with one patch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.forge, self.repo, self.out = root / "forge", root / "repo", root / "out"
        write(self.forge / REL, UPSTREAM)
        git(self.forge, "init", "-q")
        git(self.forge, "add", ".")
        git(self.forge, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "upstream")
        write(self.repo / "FORGE_COMMIT", git(self.forge, "rev-parse", "HEAD"))
        write(root / "edited" / REL, OURS)
        self.assertEqual(ep.rediff(root / "edited", self.forge, self.repo, new=[REL]), [REL])

    def tearDown(self):
        self.tmp.cleanup()

    def patch_path(self):
        return self.repo / ep.PATCH_DIR / (REL + ep.SUFFIX)

    def cli(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = ep.main(list(args) + ["--forge", str(self.forge), "--repo", str(self.repo)])
        return rc, err.getvalue()

    def test_a_patch_opens_with_the_gpl_notice_and_holds_only_the_change(self):
        text = self.patch_path().read_text()
        self.assertTrue(text.startswith("Modified from Forge (https://github.com/Card-Forge/forge), GPL-3.0-or-later"))
        self.assertIn("HARNESS PATCH", text.split("\n--- ")[0])
        self.assertRegex(text, r"Changes dated \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}\.")
        self.assertNotIn("line 1\n", text)  # three lines of context, not the file
        self.assertEqual(ep.hunks(text), [["    // HARNESS PATCH: added"]])

    def test_the_pinned_original_is_patched_never_the_working_tree(self):
        write(self.forge / REL, OURS + "an installed copy, edited by hand\n")
        rc, err = self.cli("apply", "--out", str(self.out))
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.out / REL).read_text(), OURS)

    def test_a_patch_that_does_not_apply_fails_naming_the_file_and_writes_nothing(self):
        text = self.patch_path().read_text().replace("     line 9\n", "     line 9 upstream moved\n")
        self.patch_path().write_text(text)
        rc, err = self.cli("apply", "--out", str(self.out))
        self.assertNotEqual(rc, 0)
        self.assertIn(REL, err)
        self.assertFalse(self.out.exists())

    def test_a_patch_whose_original_is_not_at_the_pin_fails_naming_the_file(self):
        self.patch_path().rename(self.repo / ep.PATCH_DIR / "forge-game/src/main/java/forge/game/Gone.java.patch")
        rc, err = self.cli("apply", "--out", str(self.out))
        self.assertNotEqual(rc, 0)
        self.assertIn("forge-game/src/main/java/forge/game/Gone.java", err)

    def test_a_rediff_round_trips_and_keeps_the_first_date(self):
        first = re.search(r"Changes dated (\S+) to", self.patch_path().read_text()).group(1)
        self.patch_path().write_text(self.patch_path().read_text().replace(first, "2026-01-01", 1))
        again = OURS.replace("    line 19\n", "    line 19 // HARNESS PATCH: a second change\n")
        edited = write(Path(self.tmp.name) / "again" / REL, again)
        self.assertEqual(ep.rediff(Path(self.tmp.name) / "again", self.forge, self.repo), [REL])
        self.assertIn("Changes dated 2026-01-01 to", self.patch_path().read_text())
        self.assertEqual(ep.patched_sources(self.forge, self.repo), {REL: edited.read_bytes()})
        self.assertEqual(len(ep.hunks(self.patch_path().read_text())), 2)

    def test_a_tree_without_patches_materializes_nothing_and_needs_no_forge(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(ep.patched_sources(None, empty), {})


class EnginePatchesTest(unittest.TestCase):

    def rows(self):
        rows = ROW.findall(DOC.read_text(encoding="utf-8"))
        self.assertTrue(rows, "no patch rows in %s" % DOC)
        return rows

    def test_every_patch_is_in_the_table_and_opens_with_the_gpl_notice(self):
        self.assertEqual(sorted(p for p, _ in self.rows()),
                         sorted("%s/%s%s" % (ep.PATCH_DIR, rel, ep.SUFFIX) for rel in ep.patch_files(REPO)))
        for path, _ in self.rows():
            head = (REPO / path).read_text(encoding="utf-8").split("\n--- ")[0]
            for part in ("Modified from Forge (https://github.com/Card-Forge/forge)", "GPL-3.0-or-later",
                         "HARNESS PATCH", "docs/harness/ENGINE-PATCHES.md", "Changes dated"):
                self.assertIn(part, head, path)

    def test_every_hunk_adds_a_marked_line(self):
        for path, _ in self.rows():
            groups = ep.hunks((REPO / path).read_text(encoding="utf-8"))
            self.assertTrue(groups, "%s changes nothing: not a patch" % path)
            for added in groups:
                self.assertTrue(any("HARNESS PATCH" in ln for ln in added),
                                "%s: a hunk without a HARNESS PATCH marker:\n%s" % (path, "\n".join(added[:8])))

    def test_the_upstream_original_is_the_recorded_one_and_the_patch_applies_to_it(self):
        forge = ep.forge_home()
        if forge is None:
            self.skipTest("no Forge checkout: upstream originals not checked")
        commit = ep.pinned_commit(REPO)
        for path, sha in self.rows():
            rel = path[len(ep.PATCH_DIR) + 1:-len(ep.SUFFIX)]
            orig = ep.original(forge, commit, rel)
            self.assertEqual(hashlib.sha256(orig).hexdigest(), sha,
                             "%s: the upstream file at %s is not the one the patch was made from; re-diff it"
                             % (rel, commit[:12]))
            self.assertNotEqual(ep.apply_patch(orig, REPO / path, rel), orig, path)

    def test_the_overlay_ships_no_whole_forge_file(self):
        forge = ep.forge_home()
        if forge is None:
            self.skipTest("no Forge checkout: upstream paths not checked")
        commit = ep.pinned_commit(REPO)
        root = REPO / "forge-overlay"
        rels = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        listed = subprocess.run(["git", "-C", str(forge), "ls-tree", "-r", "--name-only", commit] + rels,
                                capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(set(listed) - WHOLE_FILE_EXCEPTIONS), [],
                         "whole Forge files in forge-overlay/: carry each as a diff in %s/" % ep.PATCH_DIR)


if __name__ == "__main__":
    unittest.main()
