"""versions.py: the version page matches the code, and every stamp it reads exists."""
import collections
import re
import unittest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import versions


class VersionsTest(unittest.TestCase):
    def test_every_stamp_is_read_from_the_source(self):
        rows = versions.read_stamps()
        self.assertEqual(len(rows), len(versions.STAMPS) + 1)
        self.assertTrue(all(value for _, value, _, _ in rows))

    # Constants named like a version that are not result stamps, each with why (STANDARDS section 4).
    NOT_STAMPED = {
        ("DemoFeed.java", "VERSION"): "the demo's audience feed: printed to the log, never on a game line or result row, "
                                      "never read by the seat",
    }

    def test_every_version_constant_is_a_stamp_or_says_why(self):
        # A VERSION constant missing from STAMPS leaves VERSIONS.md ("every version stamp the harness carries") false;
        # ReplayBridge.VERSION reached review that way (standards review of #365).
        # Either modifier order, a simple, generic or array type, any spacing or a value on the next line; VERSION or
        # EPOCH (HARNESS_EPOCH is the headline stamp). Comments are removed first: a commented-out line is not a constant.
        pattern = re.compile(r'\b(?:static\s+final|final\s+static)\s+[\w.<>\[\],\s]+?\s+(\w*(?:VERSION|EPOCH)\w*)\s*=')
        seen = collections.Counter()
        for name in sorted(os.listdir(versions.SPIKE)):
            if name.endswith(".java"):
                with open(os.path.join(versions.SPIKE, name), encoding="utf-8") as f:
                    code = re.sub(r'/\*.*?\*/|//[^\n]*', '', f.read(), flags=re.S)
                for const in pattern.findall(code):
                    seen[(name, const)] += 1
        found = set(seen)
        # versions.read_stamps takes a file's first match, so a second constant of one name (a nested class) would
        # be hidden or misread.
        self.assertEqual(sorted(k for k, n in seen.items() if n > 1), [], "one constant of each name per file")
        stamped = {(fname, const) for fname, const, _, _ in versions.STAMPS}
        self.assertLessEqual(stamped, found, "the scan misses a listed stamp: widen the pattern")
        self.assertEqual(sorted(found - stamped - set(self.NOT_STAMPED)), [],
                         "add it to versions.STAMPS (then --write) or to NOT_STAMPED with why")
        self.assertEqual(sorted(set(self.NOT_STAMPED) - found), [], "a NOT_STAMPED entry names no constant")
        self.assertTrue(all(why.strip() for why in self.NOT_STAMPED.values()))

    # Stamps whose value no acceptance stage names, each with the file that pins it instead, checked below (#372).
    PINNED_ELSEWHERE = {
        "ReplayBridge.VERSION": "forge-overlay/forge-gui-desktop/src/test/java/forge/spike/ReplayBridgeTest.java",
        "FORGE_COMMIT": None,  # the engine pin: preflight compares the checkout's HEAD with the FORGE_COMMIT file
    }

    @staticmethod
    def asserted(text, value):
        """Whether `value` is a whole token (not part of a longer version) on a line that is not a comment: a stage
        asserts a stamp as a quoted value or inside the log line it greps for ("[OBSERVATION] observation-...")."""
        token = re.compile(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])")
        return any(token.search(line) for line in text.splitlines() if not line.lstrip().startswith(("#", "//")))

    def test_every_stamp_is_pinned_by_value_in_acceptance(self):
        # STANDARDS section 4: each stamp is asserted by literal value in a stage of eval/harness_acceptance.sh, so a
        # version that moves without the gate moving turns acceptance red; a stamp no stage asserts is caught (#372).
        def read(rel):
            with open(os.path.join(versions.REPO, rel), encoding="utf-8") as f:
                return f.read()
        gate = read("eval/harness_acceptance.sh")
        stamps = versions.read_stamps()
        unpinned = [name for name, value, _, _ in stamps
                    if not self.asserted(gate, value) and name not in self.PINNED_ELSEWHERE]
        self.assertEqual(unpinned, [], "pin each value in a stage of eval/harness_acceptance.sh, or say where else")
        values = {name: value for name, value, _, _ in stamps}
        self.assertEqual(sorted(set(self.PINNED_ELSEWHERE) - set(values)), [], "a PINNED_ELSEWHERE entry names no stamp")
        for name, where in self.PINNED_ELSEWHERE.items():
            if where:
                self.assertTrue(self.asserted(read(where), values[name]), "%s no longer pins %s" % (where, name))
        self.assertIn('"$REPO/FORGE_COMMIT"', gate, "preflight no longer reads the engine pin")

    def test_a_comment_does_not_pin_a_stamp(self):
        self.assertFalse(self.asserted('# move it WITH "one-chat-v3"\n// "one-chat-v3"', "one-chat-v3"))
        self.assertTrue(self.asserted('elif game.get("v")!="one-chat-v3":', "one-chat-v3"))
        self.assertFalse(self.asserted('x = "one-chat-v30"', "one-chat-v3"))

    def test_the_page_matches_the_code(self):
        self.assertEqual(versions.main(["--check"]), 0, "run: python3 eval/versions.py --write")


if __name__ == "__main__":
    unittest.main()
