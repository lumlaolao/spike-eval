"""Schema + integrity checks for the harvested modern-MTG puzzle corpus.

Runs as part of the acceptance `pytests` stage (unittest discovery over eval/).
The corpus itself lives in eval/puzzles/modern/puzzles.jsonl and is produced by
eval/puzzles/modern/harvest.py; this file is the guard that keeps it trustworthy as
other agents and humans extend it. Stdlib only.

What it does NOT do: judge whether any answer is correct. `machine_verifiable`
records only that the outcome is a deterministic Comprehensive-Rules result, not
that a rules engine checked it (see eval/puzzles/modern/README.md).
"""

import json
import os
import unittest
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

_HERE = str(evalpath.EVAL)
CORPUS = os.path.join(_HERE, "puzzles", "modern", "puzzles.jsonl")

REQUIRED = {
    "id", "title", "source_url", "source_name", "date", "era", "format",
    "question", "answer", "answer_authority", "rules_areas", "difficulty",
    "machine_verifiable", "verified",
}
AUTHORITIES = {"judge-article", "official-ruling", "consensus", "unverified"}
DIFFICULTIES = {"easy", "medium", "hard", "expert"}


def _load():
    with open(CORPUS, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class ModernPuzzleCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert os.path.exists(CORPUS), "missing corpus: %s" % CORPUS
        cls.rows = _load()

    def test_corpus_is_large_enough_to_matter(self):
        self.assertGreaterEqual(len(self.rows), 100, "corpus shrank below 100 puzzles")

    def test_every_row_has_the_required_fields_and_a_real_question_and_answer(self):
        for r in self.rows:
            missing = REQUIRED - set(r)
            self.assertFalse(missing, "%s missing %s" % (r.get("id"), missing))
            self.assertTrue(str(r["question"]).strip(), "%s has an empty question" % r["id"])
            self.assertTrue(str(r["answer"]).strip(), "%s has an empty answer" % r["id"])

    def test_ids_are_unique(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate puzzle ids")

    def test_authority_and_difficulty_are_from_the_declared_vocabularies(self):
        for r in self.rows:
            self.assertIn(r["answer_authority"], AUTHORITIES, r["id"])
            self.assertIn(r["difficulty"], DIFFICULTIES, r["id"])

    def test_rules_areas_are_a_nonempty_list_of_strings(self):
        for r in self.rows:
            areas = r["rules_areas"]
            self.assertIsInstance(areas, list, r["id"])
            self.assertTrue(areas, "%s has no rules area" % r["id"])
            for a in areas:
                self.assertIsInstance(a, str)

    def test_flags_are_booleans_and_the_bar_is_met(self):
        for r in self.rows:
            self.assertIsInstance(r["machine_verifiable"], bool, r["id"])
            self.assertIsInstance(r["verified"], bool, r["id"])
        # The corpus must actually be hard and engine-addressable, not a pile of easy trivia.
        harder = sum(1 for r in self.rows if r["difficulty"] in ("medium", "hard", "expert"))
        mv = sum(1 for r in self.rows if r["machine_verifiable"])
        areas = {a for r in self.rows for a in r["rules_areas"]}
        self.assertGreaterEqual(harder, 50, "too few medium+ puzzles")
        self.assertGreaterEqual(mv, 50, "too few machine_verifiable puzzles")
        self.assertGreaterEqual(len(areas), 8, "coverage collapsed to <8 rules areas")

    def test_no_duplicate_source_question_pairs(self):
        seen = set()
        for r in self.rows:
            key = (r["source_url"], r["question"])
            self.assertNotIn(key, seen, "duplicate (source, question): %s" % r["id"])
            seen.add(key)


if __name__ == "__main__":
    unittest.main()
