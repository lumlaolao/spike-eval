"""match_review: the rows are the shapes found by reading runs/lines/blocks (seed 50 and 51)."""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
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

    def test_a_yield_that_passed_the_seats_own_main_phase_is_a_skip(self):
        # runs/luna-v8/g3: a yield given at the seat's own upkeep passed its main phases (15 of them).
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [{"t": "window", "turn": 9, "phase": "MAIN1", "active": True, "stack": 0,
                     "reason": "yield:end-of-turn", "castable": 3},
                    {"t": "window", "turn": 10, "phase": "MAIN1", "active": False, "stack": 0,
                     "reason": "yield:end-of-turn", "castable": 1},
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            r = mr.review(p)
            self.assertEqual((r["counts"]["SKIP"], r["counts"]["windows the seat yielded"]), (1, 1))
            self.assertIn("own MAIN1 passed by a yield", mr.render(r))

    def test_an_engine_id_in_a_prompt_is_a_leak(self):
        # runs/luna-v9/g5: "Aether Vial (4) - ..." in the stack, read as four charge counters.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [dec("confirm", "smart", prompt="STACK: #1 ability of Aether Vial @9 (you): Aether Vial (4) - You may"),
                    dec("play", "smart", prompt="NOT VISIBLE: ForgeAI's hand (7 cards) and library (50); your library (48)"),
                    dec("mana-sources", "smart", prompt="  2) Lotus Petal [untapped] @36 — {T}, Sacrifice CARDNAME: Add one mana"),
                    dec("play", "smart", prompt="  1) CAST Force of Will @1 — cost: Pay 1 life, Exile a Card.Blue+Other card"),
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            r = mr.review(p)
            self.assertEqual(r["counts"]["LEAK"], 3, "an id, a placeholder and a filter; the library counts are not ids")
            self.assertIn("Card.Bl", mr.render(r))
            self.assertIn("Sacrifice CARDNAME", mr.render(r))
            self.assertIn("Vial (4)", mr.render(r))

    def test_a_pass_with_mana_floating_is_a_broken_line_not_a_harness_defect(self):
        # Review C2: a ritual cast, then the seat passed with its mana still in the pool.
        menu = "YOU [Seat] life 20 | mana pool {B:3} | spells cast this turn 1\nDECISION: You have priority\n  1) CAST Tendrils of Agony\n  2) PASS (do nothing)"
        empty = menu.replace("{B:3}", "empty")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            rows = [dec("play", "smart", prompt=menu, choice=1, n_options=2),      # passed, floating: a broken line
                    dec("play", "smart", prompt=menu, choice=0, n_options=2),      # cast: fine
                    dec("play", "smart", prompt=empty, choice=1, n_options=2),     # passed with nothing made: fine
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            r = mr.review(p)
            self.assertEqual(r["counts"]["LINE"], 1)
            self.assertIn("mana floating {B:3}", mr.render(r))
            self.assertEqual(mr.defects(r), 0, "a broken line is the model's to answer for until a read says otherwise")

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

    def test_a_stray_choice_beside_the_picks_of_a_subset_question_is_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            row = dec("discard", "smart", choice=1, n_options=4)
            row.update(prompt="DECISION: discard\nReason briefly, then end with EXACTLY: PICKS: <comma-separated numbers>",
                       raw_reply="Take Force.\nCHOICE: 1\nPICKS: 1")
            with open(p, "w") as f:
                f.write(json.dumps(row) + "\n" + json.dumps({"t": "game", "seed": 1, "swap": False, "winner": "A"}) + "\n")
            self.assertEqual(mr.review(p)["counts"]["LOST"], 0)

    def test_a_clean_game_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            with open(p, "w") as f:
                f.write(json.dumps(dec("play", "smart")) + "\n" + json.dumps({"t": "game", "seed": 1, "swap": False, "winner": "A"}) + "\n")
            self.assertIn("nothing taken, lost or unserved", mr.render(mr.review(p)))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mr.main([d, "--fail-on-defect"]), 0)



class TimeSplitTest(unittest.TestCase):
    def test_answers_deadline_waits_and_errors_are_counted(self):
        import tempfile, time
        text = ("[OPENROUTER S/smart] replied in 1000 ms with 1 tool call(s)\n"
                "[OPENROUTER S/smart] wall-clock timeout after 300000 ms (deadline 300000 ms)\n"
                "[OPENROUTER S/smart] HTTP 502: upstream request failed\n"
                "[OPENROUTER S/smart] replied in 2000 ms with 1 tool call(s)\n")
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
        try:
            start = int(time.time() * 1000) - 600000
            t = mr.time_split(text, "/x/game-7-%d.jsonl" % start, f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual((t["answers"], t["answers_ms"], t["deadline_waits"], t["deadline_ms"], t["provider_errors"]),
                         (2, 3000, 1, 300000, 1))
        self.assertAlmostEqual(t["elapsed_ms"] / 60000.0, 10, delta=0.5)

    def test_the_game_rows_duration_beats_the_log_mtime(self):
        # runs/loop/g30 read "TIME 803 min" in a fresh clone: the log's mtime is the checkout, not the game's end.
        # The game row's duration_ms (736192, 12 min) is what the game took.
        import tempfile, time
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("")
        try:
            start = int(time.time() * 1000) - 803 * 60000
            t = mr.time_split("", "/x/game-7-%d.jsonl" % start, f.name, duration_ms=736192)
        finally:
            os.unlink(f.name)
        self.assertEqual(t["elapsed_ms"], 736192)



class LookupRoundsTest(unittest.TestCase):
    def test_lookups_are_not_retries(self):
        # runs/loop/g08 T8: the model read its archive three times before answering; 5 attempts, 1 a real retry.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1-0.jsonl")
            look = {"t": "context_call", "raw_reply": "TOOL CALL memory_read {\"evidence_id\":1}"}
            rows = [look, look, look, {"t": "context_call", "raw_reply": "TOOL CALL answer {\"option\":1}"},
                    dict(dec("zone-change", "smart"), attempts=4, retry_ms=9000),
                    look, dict(dec("zone-change", "smart"), attempts=3, retry_ms=7000),
                    {"t": "game", "seed": 1, "swap": False, "winner": "A"}]
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            r = mr.review(p)
            self.assertEqual(r["counts"]["SLOW"], 1, mr.render(r))
            self.assertIn("1 lookups, 1 retried", mr.render(r))

class EngineFilterLintTest(unittest.TestCase):
    """The filter-syntax lint is one regex in three places: the sheet, AskContract and the harness (which says the
    filter in words). They had drifted apart once; this keeps them equal and pins what they catch."""

    def test_the_three_copies_are_one_regex(self):
        root = str(evalpath.REPO)
        spike = os.path.join(root, "forge-overlay", "forge-gui-desktop", "src")
        ask = open(os.path.join(spike, "test", "java", "forge", "spike", "AskContract.java"), encoding="utf-8").read()
        gui = open(os.path.join(spike, "main", "java", "forge", "spike", "LlmSpikeGui.java"), encoding="utf-8").read()
        java_ask = re.search(r'ENGINE_FILTER = Pattern\.compile\("((?:[^"\\]|\\.)*)"\)', ask).group(1)
        java_gui = re.search(r'ENGINE_FILTER_REGEX = "((?:[^"\\]|\\.)*)"', gui).group(1)
        unescape = lambda j: j.replace("\\\\", "\\")
        self.assertEqual(unescape(java_ask), mr.ENGINE_FILTER.pattern)
        self.assertEqual(unescape(java_gui), mr.ENGINE_FILTER.pattern)

    def test_what_it_catches(self):
        for leak in ("Exile a Card.Blue+Other card", "(must match: Card.nonLand)", "Sacrifice an Eldrazi.Spawn",
                     "Creature.cmcEQX"):
            self.assertTrue(mr.ENGINE_FILTER.search(leak), leak)
        for prose in ("Pay 1 life. Exile a blue card", "your hand (7 cards). Library 50", "Aether Vial @9"):
            self.assertFalse(mr.ENGINE_FILTER.search(prose), prose)

if __name__ == "__main__":
    unittest.main()
