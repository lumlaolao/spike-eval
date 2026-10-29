"""demo_puzzle: a puzzle run's directory gives the viewer the brief the seat was told, who played (the written line,
another script or a model), how it ended, and the intended line behind the reveal. Steps are mapped onto decisions
only for a clean written-line run of the file that was played; another seed's result is never borrowed."""
import json
import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import demo_puzzle

EVAL = str(evalpath.EVAL)
RUN = os.path.join(EVAL, "testdata", "demo", "puzzle")


def row():
    with open(os.path.join(RUN, "puzzle-results.jsonl")) as f:
        return json.loads(f.readline())


class PuzzleTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_with(self, **changes):
        r = dict(row(), **changes)
        with open(os.path.join(self.d, "puzzle-results.jsonl"), "w") as f:
            f.write(json.dumps(r) + "\n")
        return demo_puzzle.info(self.d, r["seed"])

    def test_a_written_line_run_shows_the_brief_the_seat_got_its_result_and_the_intended_line(self):
        i = demo_puzzle.info(RUN, 1)
        self.assertEqual((i["id"], i["solved"], i["solver"]["kind"]), ("drain_3c09484e5e54", True, "written line"))
        self.assertEqual(i["pzl"], "eval/puzzles/hard/gen/drain_3c09484e5e54.pzl")  # found by id: the path moved
        self.assertTrue(i["brief"].startswith("Turns Limit: 1"))  # the seat's own brief (goal_oracle)
        self.assertEqual((i["family"], i["pzl_changed"]), ("drain", False))
        self.assertIn("exhaustive maximum", i["rule"])
        self.assertEqual(i["solution"][:2], ["play land Swamp", "cast Carrion Feeder"])
        self.assertEqual(i["script_lines"], row()["script_lines"])  # clean: steps map onto decisions

    def test_another_seeds_result_is_never_borrowed(self):
        self.assertIsNone(demo_puzzle.info(RUN, 2))

    def test_steps_are_not_mapped_when_the_script_waited_drifted_or_was_not_the_written_line(self):
        self.assertIsNone(self.run_with(script_waits=1)["script_lines"])
        self.assertIsNone(self.run_with(script_drift="cast X")["script_lines"])
        self.assertIsNone(self.run_with(script_untaps_left=1)["script_lines"])
        self.assertIsNone(self.run_with(script_exhausted=True)["script_lines"])
        self.assertIsNone(self.run_with(script_played=False)["script_lines"])
        other = self.run_with(solver_model="script:/tmp/L.txt")
        self.assertEqual((other["solver"]["kind"], other["script_lines"]), ("script", None))

    def test_a_pzl_changed_since_the_run_is_flagged_and_its_steps_not_mapped(self):
        i = self.run_with(pzl_sha256="0" * 64)
        self.assertEqual((i["pzl_changed"], i["script_lines"]), (True, None))
        unverified = self.run_with(pzl_sha256=None)  # an older row without the hash: today's file is not proven
        self.assertEqual((unverified["pzl_changed"], unverified["script_lines"]), (False, None))

    def test_a_deck_out_solve_is_flagged_as_puzzle_score_counts_it(self):
        self.assertTrue(self.run_with(tainted_by_deckout=True)["tainted_by_deckout"])  # a miss to puzzle_score
        self.assertFalse(self.run_with()["tainted_by_deckout"])

    def test_a_model_attempt_names_the_model(self):
        self.assertEqual(demo_puzzle.solver_of({"solver_model": "openrouter:openai/gpt-6-luna"}),
                         {"kind": "model", "detail": "openrouter:openai/gpt-6-luna"})

    def test_a_directory_without_a_result_row_is_not_a_puzzle(self):
        self.assertIsNone(demo_puzzle.info(self.d))


if __name__ == "__main__":
    unittest.main()
