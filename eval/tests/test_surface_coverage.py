"""surface_coverage.py: what makes the gate fail, and that a listed gap does not."""
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import surface_coverage as sc

LEDGER = ("signature\timpl\tkind\townership\tlegacy_reach\tnotes\n"
          "chooseBinary[x]\tLLM\tstrategic\tmodel\tcore\tn\n"
          "orderBlocker[x]\tLLM\tstrategic\tmodel\trare\tn\n"
          "reveal[x]\tLLM\tinformation\tmodel\tcore\tn\n"
          "applyManaToCost[x]\tAI\tpayment\tai-inherited\tnone\tn\n")


def write(text):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


class SurfaceCoverageTest(unittest.TestCase):
    def run_check(self, report, gaps):
        return sc.check(sc.read_report(write(report)), sc.decision_hooks(write(LEDGER)), sc.read_gaps(write(gaps)))

    def test_only_llm_decision_hooks_are_gated(self):
        self.assertEqual(sc.decision_hooks(write(LEDGER)), {"chooseBinary", "orderBlocker"})

    def test_an_unproven_hook_fails_and_says_why(self):
        problems, asked, _ = self.run_check("chooseBinary\tasked\norderBlocker\tauto\n", "")
        self.assertEqual(asked, ["chooseBinary"])
        self.assertEqual(problems, ["UNPROVEN: orderBlocker (only forced or fallback picks) has no test that asks the model"])

    def test_a_listed_gap_passes_and_is_reported(self):
        problems, _, open_gaps = self.run_check("chooseBinary\tasked\n", "# c\norderBlocker\tneeds a test\n")
        self.assertEqual((problems, open_gaps), ([], ["orderBlocker"]))

    def test_a_gap_that_is_now_asked_fails(self):
        problems, _, _ = self.run_check("chooseBinary\tasked\norderBlocker\tasked\n", "orderBlocker\tx\n")
        self.assertEqual(problems, ["STALE GAP: orderBlocker is now asked by a test; remove it from the gap file"])

    def test_a_gap_that_is_no_decision_hook_fails(self):
        problems, _, _ = self.run_check("chooseBinary\tasked\norderBlocker\tasked\n", "reveal\tx\n")
        self.assertEqual(problems, ["UNKNOWN GAP: reveal is not a decision hook in the ledger"])


    def test_every_known_way_to_play_is_played_or_a_listed_gap(self):
        report = sc.read_report(write("known\tplay:alt:Evoke\nknown\tplay:alt:Flashback\nknown\tplay:opt:Kicker1\n"
                                      "play:alt:Flashback\tasked\n"))
        problems, played, open_gaps = sc.check_plays(report, {"play:opt:Kicker1": "x", "play:alt:Dash": "x"})
        self.assertEqual(played, ["play:alt:Flashback"])
        self.assertEqual(open_gaps, ["play:opt:Kicker1"])
        self.assertEqual(problems, ["UNPROVEN: play:alt:Evoke is a way to play a card that no test played from the menu",
                                    "UNKNOWN GAP: play:alt:Dash is not a way to play a card the engine lists"])

    def test_a_played_gap_is_stale(self):
        report = sc.read_report(write("known\tplay:alt:Evoke\nplay:alt:Evoke\tasked\n"))
        self.assertEqual(sc.check_plays(report, {"play:alt:Evoke": "x"})[0],
                         ["STALE GAP: play:alt:Evoke is now played by a test; remove it from the play gap file"])

if __name__ == "__main__":
    unittest.main()
