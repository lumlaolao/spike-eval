"""demo_puzzle: a puzzle run's directory gives the viewer the brief the seat was told, who played (the written line
or a model), how it ended, and the intended line behind the reveal; a directory with no result row is no puzzle."""
import os
import tempfile
import unittest

import demo_puzzle

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(HERE, "testdata", "demo", "puzzle")


class PuzzleTest(unittest.TestCase):
    def test_a_written_line_run_shows_its_brief_its_result_and_the_intended_line(self):
        i = demo_puzzle.info(RUN)
        self.assertEqual((i["id"], i["solved"], i["solver"]["kind"]), ("drain_0026d851c85f", True, "written line"))
        self.assertEqual(i["pzl"], "eval/puzzles/hard/gen/drain_0026d851c85f.pzl")  # found by id: the path moved
        self.assertEqual(i["family"], "drain")
        self.assertTrue(i["brief"].startswith("It is your second main phase."))
        self.assertIn("exhaustive maximum", i["rule"])
        self.assertEqual(i["solution"][:3], ["cast Gravecrawler", "pass", "activate Carrion Feeder"])
        self.assertNotIn("# steps: 14", i["solution"])  # comments are not steps

    def test_a_model_attempt_names_the_model(self):
        self.assertEqual(demo_puzzle.solver_of({"solver_model": "openrouter:openai/gpt-6-luna"}),
                         {"kind": "model", "detail": "openrouter:openai/gpt-6-luna"})

    def test_a_directory_without_a_result_row_is_not_a_puzzle(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(demo_puzzle.info(d))


if __name__ == "__main__":
    unittest.main()
