"""puzzle_tool_use.py: lookups counted by name, decisions left out, conditions compared puzzle by puzzle."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_tool_use as ptu  # noqa: E402


def write(root, cond, pid, solves, tool_rows=()):
    d = os.path.join(root, cond, pid)
    os.makedirs(d)
    with open(os.path.join(d, "puzzle-results.jsonl"), "w") as f:
        for s in solves:
            f.write(json.dumps({"schema": "pzl-result-v1", "solved": s}) + "\n")
    for i in range(len(solves)):  # one game log per attempt, as the runner writes them; the lookups in the first
        with open(os.path.join(d, "game-%d-1.jsonl" % (i + 1)), "w") as f:
            for req in (tool_rows if i == 0 else ()):
                f.write(json.dumps({"t": "context_tool", "request": req}) + "\n")
            f.write(json.dumps({"t": "context_call", "elapsed_ms": 4000}) + "\n")


class ToolUseTest(unittest.TestCase):
    def test_lookups_are_counted_and_decisions_are_not(self):
        root = tempfile.mkdtemp()
        write(root, "off", "rules-RP_1", [True, False])
        write(root, "on", "rules-RP_1", [True, True], ['TOOL CALL inspect {"target":"@1"}',
                                                     'TOOL CALL answer {"option":1}', 'TOOL CALL set_plan {}',
                                                     'TOOL CALL rules_lookup {"query":"x"}'])
        write(root, "off", "rules-RP_2", [True])
        write(root, "on", "rules-RP_2", [True], ['TOOL CALL inspect {"target":"@2"}'])  # a lookup, not the rules
        text, _ = ptu.report(root)
        self.assertIn("== off: 2/3 attempts solved (67%), 2 puzzles", text)
        self.assertIn("lookups: {'inspect': 2, 'rules_lookup': 1} | attempts that called rules_lookup: 1", text)
        self.assertNotIn("answer", text.split("== on")[1].split("\n")[1])
        self.assertIn("model time per call: median 4.0s", text)
        self.assertIn("rules-RP_1     1/2 -> 2/2", text)
        self.assertNotIn("rules-RP_2", text, "a puzzle both conditions solve alike is not listed")
        self.assertEqual(ptu.main([root]), 0)

    def test_results_without_game_logs_say_so_and_fail(self):
        root = tempfile.mkdtemp()
        write(root, "off", "rules-RP_1", [True, False])
        write(root, "on", "rules-RP_1", [True])
        os.remove(os.path.join(root, "on", "rules-RP_1", "game-1-1.jsonl"))
        text, _ = ptu.report(root)
        self.assertIn("no game JSONL for 1 of 1 attempts", text)
        self.assertNotIn("attempts that called rules_lookup: 0", text.split("== on")[1])
        self.assertEqual(ptu.main([root]), 2, "lookup counts without the logs would read as none made")
        self.assertEqual(ptu.main([os.path.join(root, "nonexistent")]), 2)

    def test_an_empty_condition_says_so(self):
        root = tempfile.mkdtemp()
        write(root, "off", "rules-RP_1", [True])
        os.makedirs(os.path.join(root, "on"))
        text, complete = ptu.report(root)
        self.assertIn("no attempts in on/", text)
        self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
