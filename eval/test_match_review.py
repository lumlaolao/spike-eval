"""match_review: the rows are the shapes found by reading runs/lines/blocks (seed 50 and 51)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import match_review as mr


def dec(dtype, tier, fallback=None, **kw):
    r = {"t": "decision", "type": dtype, "tier": tier, "turn": 3, "fallback": fallback, "attempts": 1}
    r.update(kw)
    return r


ROWS = [
    dec("mulligan", "auto", "mulligan(heuristic-keep 3/7 lands)"),
    dec("attackers", "auto", "attackers(forced-only-legal)->attack Guide of Souls"),      # taken: not attacking is legal
    dec("surveil", "auto", "surveil(forced-only-legal)->keep-all-top"),                   # taken
    dec("choose-entity", "auto", "choose-entity(forced-only-legal) [Voice of Victory]"),  # a real forced pick
    dec("play", "auto", "play(bridge-down)->PASS", attempts=5, retry_ms=52109),           # lost after asking
    dec("play", "smart"),
    dec("play", "smart", attempts=2, retry_ms=900),
    dec("sacrifice", "ai", "sacrifice(ai)->Krenko"),
    {"t": "context_tool", "turn": 6, "request": "TOOL CALL simulate {\"option\":1}",
     "result": "LOOKAHEAD (option 1): (preview failed: RuntimeException)"},
    {"t": "context_tool", "turn": 6, "request": "TOOL CALL inspect {\"target\":\"@4\"}", "result": "@4 (current): Guide of Souls"},
    {"t": "game", "seed": 50, "swap": True, "winner": "B", "turns": 14, "win_condition": "AllOpponentsLost"},
]


class MatchReviewTest(unittest.TestCase):
    def review(self, log=None):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-50-1.jsonl")
            with open(p, "w") as f:
                for r in ROWS:
                    f.write(json.dumps(r) + "\n")
            if log is not None:
                with open(os.path.join(d, "game-50-1.log"), "w") as f:
                    f.write(log)
            r = mr.review(p)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mr.main([d, "--fail-on-defect"])
            return r, mr.render(r), rc

    def test_taken_lost_and_forced_are_told_apart(self):
        r, text, rc = self.review()
        c = r["counts"]
        self.assertEqual((c["TAKEN"], c["LOST"], c["forced picks"], c["model decisions"]), (3, 2, 1, 2))
        self.assertEqual((c["tool requests"], c["tool FAILED"], c["SLOW"]), (2, 1, 1))
        self.assertEqual(rc, 1)
        self.assertIn("decided by the FORGE AI", text)
        self.assertNotIn("Voice of Victory", text, "a forced pick is counted, not listed")
        self.assertLess(text.index("LOST"), text.index("TAKEN x"), "lost decisions are listed first")

    def test_one_candidate_is_forced_only_in_a_pick_one_menu(self):
        self.assertEqual(mr.classify_auto(dec("attackers", "auto", "attackers(forced-only-legal)->attack X")), "TAKEN")
        self.assertEqual(mr.classify_auto(dec("target", "auto", "forced(only-legal)")), "forced")
        self.assertEqual(mr.classify_auto(dec("discard", "auto", "discard(forced) -> X")), "forced")
        self.assertEqual(mr.classify_auto(dec("scry", "auto", "scry(unparsed)->keep-all-top")), "LOST")

    def test_log_anomalies_are_reported(self):
        _, text, _ = self.review("[Seat] [FALLBACK->AI] x\n[LOOKAHEAD] preview failed: java.lang.RuntimeException: Couldn't map\n")
        self.assertIn("AI fallback x1", text)
        self.assertIn("preview failed x1", text)

    def test_a_clean_game_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            with open(p, "w") as f:
                f.write(json.dumps(dec("play", "smart")) + "\n" + json.dumps({"t": "game", "seed": 1, "swap": False, "winner": "A"}) + "\n")
            self.assertIn("nothing taken, lost or unserved", mr.render(mr.review(p)))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mr.main([d, "--fail-on-defect"]), 0)


if __name__ == "__main__":
    unittest.main()
