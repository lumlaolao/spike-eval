"""demo_events: every model call, tool request and reasoning lands on the decision it produced, for one llm seat
(runs/loop/g33) and for two (a loopback mirror), and a live tail parses only complete lines."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
import demo_events as de

FIX = os.path.join(HERE, "testdata", "demo")


def events(*names):
    b = de.Builder()
    for name in names:
        with open(os.path.join(FIX, name)) as f:
            for line in f:
                if line.strip():
                    b.add(json.loads(line))
    return b


def decisions(b):
    return [e for e in b.events if e["kind"] == "decision"]


def tool_names(ev):
    return [t["name"] for c in ev["calls"] for t in c["tools"]]


class OneSeatTest(unittest.TestCase):
    def test_the_simulate_previews_and_the_answer_belong_to_the_t2_play(self):
        (ev,) = decisions(events("g33-simulate.jsonl"))
        self.assertEqual((ev["turn"], ev["phase"], ev["type"]), (2, "MAIN1", "play"))
        self.assertEqual(tool_names(ev), ["simulate", "simulate", "answer"])
        sim = ev["calls"][0]["tools"][0]
        self.assertEqual(sim["args"], {"option": 1, "target": "", "then": [2]})
        self.assertTrue(sim["result"].startswith("LOOKAHEAD"))
        self.assertEqual(ev["answer"]["option"], 6)
        self.assertEqual(ev["chosen"], [5])
        self.assertTrue(ev["options"][5].startswith("ACTIVATE Black Lotus"))
        self.assertEqual([bool(c["reasoning"]) for c in ev["calls"]], [True, True, True])

    def test_set_plan_is_the_decisions_plan_and_each_window_row_is_one_event(self):
        b = events("g33-set-plan.jsonl")
        (ev,) = decisions(b)
        self.assertEqual(tool_names(ev), ["set_plan", "answer"])
        self.assertTrue(ev["plan"]["goal"])
        self.assertIn("next", ev["plan"])
        windows = [e for e in b.events if e["kind"] == "window"]
        with open(os.path.join(FIX, "g33-set-plan.jsonl")) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        # One event per row and never changed later: a live client that fetched one already has it whole.
        self.assertEqual(len(windows), sum(1 for r in rows if r["t"] == "window"))
        self.assertTrue(windows)

    def test_the_inspect_and_the_providers_reasoning_are_on_the_mulligan_bottom(self):
        (ev,) = decisions(events("g33-inspect.jsonl"))
        self.assertEqual(ev["type"], "mulligan-bottom")
        self.assertEqual(ev["calls"][0]["tools"][0]["args"], {"target": "Bilbo, Thief in the Night"})
        self.assertTrue(ev["calls"][0]["tools"][0]["result"].startswith("CARD REFERENCE"))
        self.assertEqual(ev["chosen"], ev["picks"])

    def test_no_blocks_selects_no_option(self):
        (ev,) = decisions(events("g33-blockers.jsonl"))
        self.assertEqual(ev["type"], "blockers")
        self.assertEqual(ev["chosen"], [])
        self.assertEqual(ev["answer"]["blocks"], [])
        self.assertIn("Reckless Rage", ev["answer"]["why"])

    def test_a_pass_marks_the_pass_option(self):
        ev = decisions(events("g33-t5-upkeep.jsonl"))[-1]
        self.assertEqual(ev["choice"], -1)
        self.assertEqual([ev["options"][i] for i in ev["chosen"]], ["PASS (do nothing)"])

    def test_an_auto_row_is_classed_forced_taken_or_lost_and_never_called_forced_blindly(self):
        b = de.Builder()
        b.add({"t": "decision", "seat": "A", "turn": 1, "type": "target", "tier": "auto", "choice": 0,
               "options": ["x"], "fallback": "target(forced-only-legal)"})
        b.add({"t": "decision", "seat": "A", "turn": 1, "type": "play", "tier": "auto", "choice": 0,
               "options": ["x", "y"], "fallback": "ability-to-play(bridge-down)->first"})
        b.add({"t": "decision", "seat": "A", "turn": 1, "type": "play", "tier": "smart", "choice": 0, "options": ["x"]})
        self.assertEqual([e["auto_class"] for e in decisions(b)], ["forced", "LOST", None])

    def test_a_number_ask_selects_no_option(self):
        b = de.Builder()
        b.add({"t": "decision", "seat": "A", "turn": 1, "type": "number-value", "tier": "smart", "choice": 0,
               "n_options": -1, "options": ["integer range [0..3]"]})
        self.assertEqual(decisions(b)[0]["chosen"], [])

    def test_an_auto_row_has_no_prompt_and_borrows_the_last_state(self):
        b = events("g33-t5-upkeep.jsonl", "g33-damage-assign.jsonl")
        auto = decisions(b)[-1]
        self.assertEqual((auto["tier"], auto["prompt"], auto["state"], auto["calls"]), ("auto", None, None, []))
        self.assertEqual(b.events[auto["state_from"]]["turn"], 5)

    def test_the_game_row_is_the_summary(self):
        b = events("g33-game.jsonl")
        self.assertEqual(b.game["winner"], "A")
        self.assertEqual(b.game["turns"], 14)
        self.assertEqual(b.game["harness_epoch"], "harness-epoch-64")
        self.assertEqual(b.events[-1]["kind"], "game")


class TwoSeatTest(unittest.TestCase):
    def test_each_seats_calls_go_to_that_seats_next_decision(self):
        b = events("mirror-42-start.jsonl")
        seats = {e["seat"] for e in decisions(b)}
        self.assertEqual(seats, {"Grixis Reanimator", "Mardu Energy"})
        with open(os.path.join(FIX, "mirror-42-start.jsonl")) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        for seat in seats:
            calls = sum(1 for r in rows if r["t"] == "context_call" and r["seat"] == seat)
            got = sum(len([c for c in e["calls"] if not c.get("synthetic")]) for e in decisions(b) if e["seat"] == seat)
            pending = len(b.pending.get(seat, []))
            self.assertEqual(got + pending, calls, seat)

    def test_an_interleaved_row_of_the_other_seat_does_not_steal_the_ask(self):
        b = de.Builder()
        b.add({"t": "context_call", "seat": "A", "call": 1, "raw_reply": "TOOL CALL answer {}"})
        b.add({"t": "context_call", "seat": "B", "call": 1, "raw_reply": "TOOL CALL inspect {}"})
        b.add({"t": "context_tool", "seat": "A", "call": 1, "request": "TOOL CALL answer {\"option\": 2}",
               "result": "ANSWER ACCEPTED"})
        b.add({"t": "decision", "seat": "A", "turn": 1, "type": "play", "choice": 1, "options": ["x", "y"]})
        b.add({"t": "context_tool", "seat": "B", "call": 1, "request": "TOOL CALL inspect {\"target\": \"@3\"}",
               "result": "CARD"})
        b.add({"t": "decision", "seat": "B", "turn": 1, "type": "play", "choice": 0, "options": ["z"]})
        a, bb = decisions(b)
        self.assertEqual((tool_names(a), a["answer"], a["chosen"]), (["answer"], {"option": 2}, [1]))
        self.assertEqual(tool_names(bb), ["inspect"])
        self.assertIsNone(bb["answer"])


class FindGameTest(unittest.TestCase):
    def test_the_swapped_games_log_is_the_one_named_for_its_swap(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("game-7-0.log", "game-7-1.log"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x\n")
            path = os.path.join(d, "game-7-123.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"t": "game", "seed": 7, "swap": True}) + "\n")
            self.assertEqual(de.find_game(path), (path, os.path.join(d, "game-7-1.log")))

    def test_the_newest_game_is_the_one_started_last_not_the_one_written_last(self):
        with tempfile.TemporaryDirectory() as d:
            early, late = os.path.join(d, "game-1-100.jsonl"), os.path.join(d, "game-2-200.jsonl")
            for p in (late, early):  # the earlier game is written last
                with open(p, "w") as f:
                    f.write("{}\n")
            os.utime(early, (2e9, 2e9))
            self.assertEqual(de.find_game(d)[0], late)

    def test_a_negative_seeds_log_is_found_too(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("game--5-0.log", "game--5-1.log"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x\n")
            path = os.path.join(d, "game--5-100.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"t": "game", "seed": -5, "swap": True}) + "\n")
            self.assertEqual(de.find_game(d), (path, os.path.join(d, "game--5-1.log")))

    def test_a_bare_file_name_resolves_in_the_current_directory(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            try:
                os.chdir(d)
                with open("game-7-123.jsonl", "w") as f:
                    f.write("{}\n")
                self.assertEqual(de.find_game("game-7-123.jsonl"), ("game-7-123.jsonl", None))
            finally:
                os.chdir(cwd)


class ReaderTest(unittest.TestCase):
    def test_a_live_tail_parses_new_complete_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "game-1-1.jsonl")
            r = de.Reader(path)
            self.assertEqual(r.poll(), 0)  # the JVM has not opened the file yet
            row = json.dumps({"t": "decision", "seat": "A", "turn": 1, "type": "play", "choice": 0, "options": ["x"]})
            with open(path, "w") as f:
                f.write(row + "\n" + row[:20])
            self.assertEqual(r.poll(), 1)
            self.assertEqual(r.poll(), 0)  # the half-written line waits
            with open(path, "a") as f:
                f.write(row[20:] + "\n")
            self.assertEqual(r.poll(), 1)
            self.assertEqual([e["n"] for e in r.events], [0, 1])
            self.assertEqual(r.bad_lines, 0)

    def test_the_whole_g33_game_joins_every_call_to_a_decision(self):
        g33 = os.path.join(HERE, "..", "runs", "loop", "g33")
        jsonl, log = de.find_game(g33)
        if not jsonl:
            self.skipTest("runs/loop/g33 is not in this clone")
        r = de.load(jsonl)
        d = [e for e in r.events if e["kind"] == "decision"]
        self.assertEqual(len(d), 148)
        self.assertEqual(sum(len(e["calls"]) for e in d), 183)
        self.assertEqual(r.builder.pending, {})
        self.assertEqual(de.tally(log)["Seat"]["ai"], "0")


if __name__ == "__main__":
    unittest.main()
