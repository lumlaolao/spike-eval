"""puzzle_solve.py refuses a condition the seat would not honour."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puzzle_solve  # noqa: E402


class RulesToolNeedsMemoryToolsTest(unittest.TestCase):
    """The seat wires the rules lookup only with MEMORY_TOOLS=1, so --rules-tool alone ran puzzles that
    recorded RULES_TOOL=1 and never offered the tool (runs/puzzles/bench-pilot/on, 2026-10-20)."""

    def test_rules_tool_without_memory_tools_is_refused_before_anything_runs(self):
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "/nonexistent/suite.json", "--split", "open", "--model", "fake",
                               "--budget-usd", "0", "--out", "/nonexistent/out", "--rules-tool"])
        self.assertIn("--memory-tools", str(cm.exception.code))



class ResumeTest(unittest.TestCase):
    """A run stopped by a container restart is started again with --resume (owner, 2026-10-22): a puzzle whose
    k attempts on this suite are already recorded is not paid for twice; one cut off part way is played again."""

    def write(self, out, rows):
        out.mkdir(parents=True, exist_ok=True)
        (out / "puzzle-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_a_puzzle_with_its_k_attempts_on_this_suite_is_done(self):
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S"}, {"seed": 2, "suite_sha256": "S"}])
        self.assertTrue(puzzle_solve.already_done(out, 2, "S"))
        self.assertFalse(puzzle_solve.already_done(out, 3, "S"), "a third attempt is still owed")
        self.assertFalse(puzzle_solve.already_done(out, 2, "OTHER"), "rows of another suite version")
        self.assertFalse(puzzle_solve.already_done(Path(tempfile.mkdtemp()) / "none", 1, "S"))

    def test_a_puzzle_cut_off_part_way_keeps_its_paid_rows_aside_and_is_played_again(self):
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S"}])
        puzzle_solve.set_aside_partial(out)
        self.assertFalse((out / "puzzle-results.jsonl").exists(), "a scorer would read the partial rows")
        self.assertTrue(list(out.glob("puzzle-results.partial*.jsonl")))


if __name__ == "__main__":
    unittest.main()
