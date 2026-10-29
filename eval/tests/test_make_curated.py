"""Tests for eval/puzzles/modern/tools/make_curated.py: every hand-curated item becomes a valid corpus record."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
sys.path.insert(0, str(evalpath.EVAL / "puzzles" / "modern"))
sys.path.insert(0, str(evalpath.EVAL / "puzzles" / "modern" / "tools"))
import harvest  # noqa: E402
import make_curated  # noqa: E402


class MakeCuratedTest(unittest.TestCase):

    def test_every_curated_item_normalises_to_a_valid_record(self):
        items = make_curated.CI_ITEMS + make_curated.TAJ_ITEMS + make_curated.OR_ITEMS
        self.assertGreater(len(items), 0)
        for it in items:
            self.assertEqual(harvest.validate(harvest.normalise(it)), [], it.get("title"))

    def test_it_writes_into_the_corpus_raw_items(self):
        self.assertEqual(os.path.normpath(make_curated.OUT),
                         os.path.normpath(str(evalpath.EVAL / "puzzles" / "modern" / "raw" / "items")))


if __name__ == "__main__":
    unittest.main()
