"""puzzle_solve.py refuses a condition the seat would not honour."""
import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
