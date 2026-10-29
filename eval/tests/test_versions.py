"""versions.py: the version page matches the code, and every stamp it reads exists."""
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

    def test_the_page_matches_the_code(self):
        self.assertEqual(versions.main(["--check"]), 0, "run: python3 eval/versions.py --write")


if __name__ == "__main__":
    unittest.main()
