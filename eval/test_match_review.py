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

    def test_a_choice_the_seat_was_not_asked_about_is_taken(self):
        # runs/loop/g06: four Painter's Servant colour choices with five colours legal, recorded as
        # "color(default)->first" -- neither "forced" nor a rule of thumb, so the sheet called them
        # other-automatic and TAKEN was 0 while the seat had lost the decision.
        self.assertEqual(mr.classify_auto(dec("color", "auto", "color(default)->first of 5 legal")), "TAKEN")
        self.assertEqual(mr.classify_auto(dec("some-type", "auto", "some-type(default)->first of 12 legal")), "TAKEN")
        # ONE legal colour is genuinely forced and stays free.
        self.assertEqual(mr.classify_auto(dec("color", "auto", "color(forced)->first")), "forced")

    def test_one_candidate_is_forced_only_in_a_pick_one_menu(self):
        self.assertEqual(mr.classify_auto(dec("attackers", "auto", "attackers(forced-only-legal)->attack X")), "TAKEN")
        self.assertEqual(mr.classify_auto(dec("target", "auto", "forced(only-legal)")), "forced")
        self.assertEqual(mr.classify_auto(dec("discard", "auto", "discard(forced) -> X")), "forced")
        self.assertEqual(mr.classify_auto(dec("scry", "auto", "scry(unparsed)->keep-all-top")), "LOST")

    def test_log_anomalies_are_reported(self):
        _, text, _ = self.review("[Seat] [FALLBACK->AI] x\n[LOOKAHEAD] preview failed: java.lang.RuntimeException: Couldn't map\n")
        self.assertIn("AI fallback x1", text)
        self.assertIn("preview failed x1", text)

    def test_a_window_passed_with_something_usable_is_a_skip(self):
        # The play loop's shortcuts used to pass without any row, so the sheet read "nothing taken"
        # in games where the harness had declined dozens of windows for the seat.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [{"t": "window", "turn": 4, "phase": "UPKEEP", "reason": "window-gate", "castable": 2},
                    {"t": "window", "turn": 4, "phase": "DRAW", "reason": "window-gate", "castable": 0},
                    {"t": "window", "turn": 4, "phase": "END_OF_TURN", "reason": "yield:end-of-turn", "castable": 3},
                    {"t": "window", "turn": 4, "phase": "UPKEEP", "reason": "last-sig", "castable": 1},
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            r = mr.review(p)
            self.assertEqual((r["counts"]["SKIP"], r["counts"]["windows with nothing usable"],
                              r["counts"]["windows the seat yielded"],
                              r["counts"]["windows repeating a spot the seat passed"]), (1, 1, 1, 1))
            self.assertIn("UPKEEP window passed without asking (window-gate) with 2 usable", mr.render(r))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mr.main([d, "--fail-on-defect"]), 1)

    def test_a_reply_that_was_not_the_option_used_is_lost(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [dec("target", "smart", choice=0, n_options=3, raw_reply="Bolt the Lions.\nCHOICE: 2"),
                    dec("target", "smart", choice=1, n_options=3, raw_reply="CHOICE: 2"),
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(x) + "\n" for x in rows))
            r = mr.review(p)
            self.assertEqual(r["counts"]["LOST"], 1)
            self.assertIn("the reply said CHOICE 2 but option 1 was used", mr.render(r))

    def test_a_model_row_whose_answer_was_not_used_is_lost(self):
        # The play loop turned an unparsed reply into a PASS row identical to a real PASS, and block
        # pairs that named no legal block vanished; both rows now carry a fallback and are LOST.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [dec("play", "smart", choice=-1, n_options=3, fallback="play(unparsed)->PASS"),
                    dec("blockers", "smart", choice=1, n_options=2, fallback="blockers(unparsed-pairs:1)"),
                    dec("play", "auto", choice=-1, n_options=-1, fallback="play(turn-cap)->PASS"),
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(x) + "\n" for x in rows))
            r = mr.review(p)
            self.assertEqual((r["counts"]["LOST"], r["counts"]["TAKEN"]), (2, 1))

    def test_a_report_from_the_seat_is_listed_and_is_not_a_defect(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            req = 'TOOL CALL report_unclear_decision {"problem":"option 2 names a Mountain I do not control"}'
            rows = [{"t": "context_tool", "turn": 4, "request": req, "result": "Recorded for the harness maintainers."},
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(x) + "\n" for x in rows))
            r = mr.review(p)
            self.assertEqual((r["counts"]["REPORT"], r["counts"]["TOOL"], mr.defects(r)), (1, 0, 0))
            self.assertRegex(mr.render(r), r"REPORT x1 +line 1 T4  option 2 names a Mountain")

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
