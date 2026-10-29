"""Tests for eval/puzzles/modern/harvest.py: normalising and validating a raw corpus item."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401
sys.path.insert(0, str(evalpath.EVAL / "puzzles" / "modern"))
import harvest  # noqa: E402

RAW = {"source_name": "Cranial Insertion", "source_url": "https://example.org/a", "title": "",
       "question": "Does  Lightning Bolt\nkill it?", "answer": "Yes.", "rules_areas": "Damage; damage, Stack",
       "date": "2024-01-02", "notes": "test fixture"}


class HarvestTest(unittest.TestCase):

    def test_a_raw_item_normalises_to_a_valid_record(self):
        rec = harvest.normalise(RAW)
        self.assertEqual(harvest.validate(rec), [])
        self.assertEqual(rec["title"], "does lightning bolt kill it?")
        self.assertEqual(rec["rules_areas"], [harvest.canon_area("damage"), harvest.canon_area("stack")])
        self.assertTrue(rec["id"].startswith("cranial-insertion-"))

    def test_the_dedup_key_ignores_case_and_whitespace_in_the_question(self):
        other = dict(RAW, question="does lightning bolt kill it?")
        self.assertEqual(harvest.dedup_key(RAW), harvest.dedup_key(other))
        self.assertNotEqual(harvest.dedup_key(RAW), harvest.dedup_key(dict(RAW, source_url="https://example.org/b")))

    def test_a_record_without_an_answer_is_invalid(self):
        self.assertIn("missing answer", harvest.validate(harvest.normalise(dict(RAW, answer=""))))


if __name__ == "__main__":
    unittest.main()
