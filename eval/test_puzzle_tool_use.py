"""puzzle_tool_use.py: lookups counted by name, decisions left out, conditions compared puzzle by puzzle."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puzzle_tool_use as ptu  # noqa: E402


def write(root, cond, pid, solves, tool_rows=()):
    d = os.path.join(root, cond, pid)
    os.makedirs(d)
    with open(os.path.join(d, "puzzle-results.jsonl"), "w") as f:
        for s in solves:
            f.write(json.dumps({"schema": "pzl-result-v1", "solved": s}) + "\n")
    with open(os.path.join(d, "game-1-1.jsonl"), "w") as f:
        for req in tool_rows:
            f.write(json.dumps({"t": "context_tool", "request": req}) + "\n")
        f.write(json.dumps({"t": "context_call", "elapsed_ms": 4000}) + "\n")


class ToolUseTest(unittest.TestCase):
    def test_lookups_are_counted_and_decisions_are_not(self):
        root = tempfile.mkdtemp()
        write(root, "off", "rules-RP_1", [True, False])
        write(root, "on", "rules-RP_1", [True, True], ['TOOL CALL inspect {"target":"@1"}',
                                                     'TOOL CALL answer {"option":1}', 'TOOL CALL set_plan {}',
                                                     'TOOL CALL rules_lookup {"query":"x"}'])
        text = ptu.report(root)
        self.assertIn("== off: 1/2 attempts solved (50%), 1 puzzles", text)
        self.assertIn("lookups: {'inspect': 1, 'rules_lookup': 1} | attempts that called rules_lookup: 1", text)
        self.assertNotIn("answer", text.split("== on")[1].split("\n")[1])
        self.assertIn("model time per call: median 4.0s", text)
        self.assertIn("rules-RP_1     1/2 -> 2/2", text)


if __name__ == "__main__":
    unittest.main()
