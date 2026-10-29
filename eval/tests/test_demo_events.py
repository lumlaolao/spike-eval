"""demo_events: every model call, tool request and reasoning lands on the decision it produced, for one llm seat
(runs/loop/g33) and for two (a loopback mirror), and a live tail parses only complete lines."""
import json
import os
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

EVAL = str(evalpath.EVAL)
import demo_events as de

FIX = os.path.join(EVAL, "testdata", "demo")


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


class FeedTest(unittest.TestCase):
    def test_each_decision_gets_the_feed_snapshot_written_before_its_row(self):
        with tempfile.TemporaryDirectory() as d:
            game, feed = os.path.join(d, "game-7-1.jsonl"), os.path.join(d, "feed-7-1.jsonl")
            rows = [{"t": "decision", "seat": "A", "turn": t, "type": "play", "choice": 0, "options": ["x"]}
                    for t in (1, 2)]
            lines = [json.dumps(r) + "\n" for r in rows]
            with open(game, "w") as f:
                f.write("".join(lines))
            second = len(lines[0])  # the byte offset where the second decision row starts
            snaps = [{"t": "feed", "seq": 0, "jsonl_offset": 0, "players": [{"name": "B", "hand": [{"name": "Bolt"}]}],
                      "handles": {"A": {"1": 40}}},
                     {"t": "feed", "seq": 1, "jsonl_offset": second, "players": [{"name": "B", "hand": []}]},
                     {"t": "feed", "seq": 2, "jsonl_offset": second + 5, "players": []}]
            with open(feed, "w") as f:
                f.write("".join(json.dumps(s) + "\n" for s in snaps))
            d1, d2 = [e for e in de.load(game).events if e["kind"] == "decision"]
            self.assertEqual((d1["feed"]["seq"], d2["feed"]["seq"]), (0, 1))
            self.assertEqual(d1["feed"]["players"][0]["hand"], [{"name": "Bolt"}])
            self.assertEqual(d2["feed_handles"], {"1": 40})  # carried from the last row that wrote A's map

    def test_the_handles_row_written_at_a_decision_maps_its_new_cards_and_carries_no_board(self):
        with tempfile.TemporaryDirectory() as d:
            game, feed = os.path.join(d, "game-7-1.jsonl"), os.path.join(d, "feed-7-1.jsonl")
            with open(game, "w") as f:
                f.write(json.dumps({"t": "decision", "seat": "A", "turn": 1, "type": "play", "choice": 0,
                                    "options": ["x"]}) + "\n")
            snaps = [{"t": "feed", "seq": 0, "jsonl_offset": 0, "players": [{"name": "B"}], "handles": {"A": {"1": 40}}},
                     {"t": "feed_handles", "seq": 1, "jsonl_offset": 0, "handles": {"A": {"1": 40, "2": 41}}}]
            with open(feed, "w") as f:
                f.write("".join(json.dumps(s) + "\n" for s in snaps))
            (ev,) = [e for e in de.load(game).events if e["kind"] == "decision"]
            self.assertEqual(ev["feed"]["seq"], 0)                   # the board, not the handles-only row
            self.assertEqual(ev["feed_handles"], {"1": 40, "2": 41})  # the card first shown in this prompt

    def test_a_live_poll_never_gives_a_decision_a_board_older_than_its_row(self):
        with tempfile.TemporaryDirectory() as d:
            game, feed = os.path.join(d, "game-7-1.jsonl"), os.path.join(d, "feed-7-1.jsonl")
            open(game, "w").close()
            with open(feed, "w") as f:
                f.write(json.dumps({"t": "feed", "seq": 0, "jsonl_offset": 0, "players": []}) + "\n")
            r = de.Reader(game)
            real = r.feed.poll

            def late_rows():  # a feed row and its decision row land just after the feed was read
                real()
                with open(feed, "a") as f:
                    f.write(json.dumps({"t": "feed", "seq": 1, "jsonl_offset": 0, "players": []}) + "\n")
                with open(game, "a") as f:
                    f.write(json.dumps({"t": "decision", "seat": "A", "turn": 1, "type": "play", "choice": 0,
                                        "options": ["x"]}) + "\n")
            r.feed.poll = late_rows
            r.poll()
            r.feed.poll = real
            r.poll()
            (ev,) = [e for e in r.events if e["kind"] == "decision"]
            self.assertEqual(ev["feed"]["seq"], 1)

    def test_without_a_feed_file_a_decision_has_no_feed(self):
        (ev,) = decisions(events("g33-simulate.jsonl"))
        self.assertIsNone(ev["feed"])


def dec(turn, options, chosen, phase="MAIN1", type_="play"):
    return {"kind": "decision", "seat": "Seat", "turn": turn, "phase": phase, "type": type_, "options": options,
            "chosen": chosen}


class DivergenceTest(unittest.TestCase):
    MENU = ["CAST Brainstorm @12", "PLAY Tundra @7", "PASS"]

    def test_the_first_different_answer_to_the_same_question_is_the_divergence(self):
        a = [dec(1, self.MENU, [1]), {"kind": "window"}, dec(1, self.MENU, [0]), dec(2, ["PASS"], [0])]
        b = [dec(1, self.MENU, [1]), dec(1, self.MENU, [2]), dec(3, ["PASS"], [0])]
        self.assertEqual(de.divergence(a, b),
                         {"a": 1, "b": 1, "reason": "same question, different answer"})

    def test_handles_and_notes_rendered_differently_by_two_harnesses_are_the_same_option(self):
        older = ["CAST Brainstorm @3 [Hand]", "PLAY Tundra @1 [Hand, yours]", "PASS"]
        self.assertIsNone(de.divergence([dec(1, self.MENU, [1])], [dec(1, older, [1])]))

    def test_two_games_of_different_seeds_are_not_called_a_divergence(self):
        a = [dec(1, self.MENU, [1]), {"kind": "game", "seed": 1, "deck_a": "x", "deck_b": "y"}]
        b = [dec(1, self.MENU, [1]), {"kind": "game", "seed": 2, "deck_a": "x", "deck_b": "y"}]
        self.assertTrue(de.divergence(a, b)["unpaired"])

    def test_two_picks_that_differ_only_in_a_note_are_different_answers(self):
        menu = ["Grizzly Bears @3 [Battlefield, yours]", "Grizzly Bears @7 [Battlefield, opponent's]"]
        dv = de.divergence([dec(1, menu, [0])], [dec(1, menu, [1])])
        self.assertEqual(dv["reason"], "same question, different answer")

    def test_a_menu_only_difference_is_noted_and_the_first_different_answer_is_the_divergence(self):
        newer = ["ACTIVATE Mox Ruby @11 [{T}: Add {R}.]", "PLAY LAND Urza's Saga @8", "PASS (do nothing)"]
        older = ["PLAY LAND Urza's Saga @5", "PASS (do nothing)"]
        a = [dec(1, newer, [1]), dec(2, self.MENU, [0])]
        b = [dec(1, older, [0]), dec(2, self.MENU, [1])]
        self.assertEqual(de.divergence(a, b), {"a": 1, "b": 1, "menus_differ_from": 0,
                                               "reason": "same question, different answer"})
        dv = de.divergence([dec(1, newer, [0])], [dec(1, older, [0])])  # Mox Ruby against Urza's Saga
        self.assertEqual(dv["reason"], "same spot, different menus, different answer")
        dv = de.divergence([dec(1, newer, [1])], [dec(1, older, [0])])  # the same play: only the menus differ
        self.assertEqual((dv["a"], dv["menus_differ_from"]), (0, 0))
        self.assertIn("same answers throughout", dv["reason"])

    def test_two_abilities_of_one_card_across_different_menus_are_different_answers(self):
        newer = ["ACTIVATE Mutavault @9 [{T}: Add {C}.]", "ACTIVATE Mutavault @9 [{1}: Mutavault becomes a 2/2]", "PASS"]
        older = ["ACTIVATE Mutavault @4 [{1}: Mutavault becomes a 2/2]", "PASS"]
        dv = de.divergence([dec(1, newer, [0])], [dec(1, older, [0])])  # tap for mana against animate
        self.assertEqual(dv["reason"], "same spot, different menus, different answer")
        both_animate = de.divergence([dec(1, newer, [1]), dec(2, self.MENU, [0])],
                                     [dec(1, older, [0]), dec(2, self.MENU, [1])])
        self.assertEqual((both_animate["a"], both_animate["menus_differ_from"]), (1, 0))

    def test_one_of_two_identical_copies_is_the_same_answer_across_different_menus(self):
        # The two versions word the copies' notes differently, so only the name, unambiguous in each menu, matches.
        newer = ["ACTIVATE Mountain @2 [{T}: Add {R}.]", "PLAY LAND Mountain @5 [Hand, yours]",
                 "PLAY LAND Mountain @6 [Hand, yours]", "PASS"]
        older = ["PLAY LAND Mountain @3 [Hand]", "PLAY LAND Mountain @4 [Hand]", "PASS"]
        dv = de.divergence([dec(1, newer, [2]), dec(2, self.MENU, [0])], [dec(1, older, [0]), dec(2, self.MENU, [1])])
        self.assertEqual((dv["a"], dv["menus_differ_from"]), (1, 0))  # the scan went on to the real divergence

    def test_a_different_spot_is_reported_as_already_parted(self):
        dv = de.divergence([dec(1, self.MENU, [2])], [dec(2, self.MENU, [2])])
        self.assertIn("already parted", dv["reason"])


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

    def test_a_run_that_is_not_in_this_clone_is_no_game_not_an_error(self):
        # runs/ is git-ignored, so a fresh clone has no runs/loop at all.
        missing = os.path.join(tempfile.mkdtemp(), "runs", "loop", "g33")
        self.assertEqual(de.find_game(missing), (None, None))

    def test_the_whole_g33_game_joins_every_call_to_a_decision(self):
        g33 = os.path.join(EVAL, "..", "runs", "loop", "g33")
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
