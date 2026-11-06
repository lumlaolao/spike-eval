#!/usr/bin/env python3
"""eval/fact_citations.py: the groups, the citation pattern and the yield-skip filter, on rows built here."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fact_citations as fc  # noqa: E402

LETHAL = "FACTS (engine estimates, not truth): your next combat: 2 attacker(s) dealing 7 unblocked; their life 4 -> 0 = LETHAL"
CLOCK = "FACTS (engine estimates, not truth): your next combat: 1 attacker(s) dealing 2 unblocked; their life 9 -> 7"


def decision(prompt, reply, phase="MAIN2", turn=1):
    return {"t": "decision", "turn": turn, "phase": phase, "prompt": prompt, "raw_reply": reply}


class FactCitationsTest(unittest.TestCase):

    def write_game(self, root, name, rows):
        d = os.path.join(root, name)
        os.makedirs(d)
        with open(os.path.join(d, "game-1-1.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_a_game_is_grouped_by_its_clock_after_combat(self):
        self.assertEqual(fc.clock_group([decision(LETHAL, "")]), "lethal")
        self.assertEqual(fc.clock_group([decision(CLOCK, "")]), "clock")
        self.assertEqual(fc.clock_group([decision("no facts", "")]), "none")
        # one lethal prompt among the late decisions makes the game lethal
        self.assertEqual(fc.clock_group([decision(CLOCK, ""), decision(LETHAL, "")]), "lethal")

    def test_only_turn_one_after_combat_counts(self):
        rows = [decision(LETHAL, "x", phase="MAIN1"), decision(LETHAL, "x", turn=2),
                {"t": "decision", "turn": 1, "phase": "MAIN2"}, decision(LETHAL, "x", phase="END_OF_TURN")]
        self.assertEqual(len(fc.late_decisions(rows)), 1)

    def test_the_citation_pattern(self):
        self.assertTrue(fc.cites_later_combat([decision("", "WHY: the next combat is lethal per the estimate")]))
        self.assertTrue(fc.cites_later_combat([decision("", "attack for lethal next turn")]))
        self.assertFalse(fc.cites_later_combat([decision("", "WHY: sacrifice Bloodghast to drain 1")]))

    def test_a_yield_skip_needs_own_main_a_stack_and_a_castable(self):
        base = {"t": "window", "reason": "yield:end-of-turn", "active": True, "phase": "MAIN2", "stack": 2,
                "castable": 3}
        self.assertTrue(fc.is_own_main_yield_skip(base))
        for change in ({"stack": 0}, {"castable": 0}, {"active": False}, {"phase": "END_OF_TURN"},
                       {"reason": "last-sig"}):
            self.assertFalse(fc.is_own_main_yield_skip(dict(base, **change)), change)

    def test_scan_counts_games_once_and_by_group(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_game(root, "a", [decision(LETHAL, "the next combat is lethal"),
                                        {"t": "window", "reason": "yield:end-of-turn", "active": True,
                                         "phase": "MAIN2", "stack": 1, "castable": 1}])
            self.write_game(root, "b", [decision(CLOCK, "pass")])
            self.write_game(root, "c", [decision("no facts", "pass", phase="MAIN1")])
            self.write_game(root, "d", [{"t": "window"}])           # no decision with a prompt: not a game
            out = fc.scan([root, root])                              # the same root twice counts each game once
        self.assertEqual(out["games"], 3)
        self.assertEqual(out["games_with_late_decision"], 2)
        self.assertEqual(out["clock"]["lethal"], {"games": 1, "cite": 1})
        self.assertEqual(out["clock"]["clock"], {"games": 1, "cite": 0})
        self.assertEqual(out["clock"]["none"], {"games": 0, "cite": 0})
        self.assertEqual((out["yield_windows"], out["yield_games"]), (1, 1))

    def test_no_argument_is_a_usage_error(self):
        self.assertEqual(fc.main([]), 2)


if __name__ == "__main__":
    unittest.main()
