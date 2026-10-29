"""Tests for human_decisions.py: the human controller defines what a decision is."""
import os
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import human_decisions as hd

SRC = '''public class PlayerControllerHuman {
    @Override
    public boolean confirmAction(SpellAbility sa, String q) {
        return getGui().confirm(null, q);
    }
    @Override
    public int chooseNumber(SpellAbility sa, String t, int min, int max) {
        return pickNumber(min, max);
    }
    private int pickNumber(int min, int max) {
        return getGui().getInteger("n", min, max);
    }
    @Override
    public void resetAtEndOfTurn() {
        counter = 0;
    }
}
'''


class HumanDecisions(unittest.TestCase):
    def test_a_method_that_asks_directly_or_through_a_helper_is_a_decision(self):
        asks = hd.prompting(SRC)
        self.assertIn("confirmAction", asks)
        self.assertIn("chooseNumber", asks)          # through pickNumber
        self.assertNotIn("resetAtEndOfTurn", asks)

    def test_the_pinned_engine_has_no_core_decision_the_seat_does_not_own(self):
        home = os.environ.get("FORGE_HOME", os.path.expanduser("~/code/forge"))
        if not os.path.isfile(os.path.join(home, hd.HUMAN)):
            self.skipTest("no Forge checkout at %s" % home)
        res = hd.audit(home)
        self.assertGreater(len(res["owned"]), 30)
        self.assertEqual([n for n, _ in res["unowned"]], [])


if __name__ == "__main__":
    unittest.main()
