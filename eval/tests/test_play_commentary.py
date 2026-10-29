"""play_commentary.py: the live line-per-decision feed of the model's seat in a play-vs-llm.sh game."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import play_commentary as pc  # noqa: E402

PLAY = {"t": "decision", "seat": "Seat", "tier": "smart", "type": "play", "turn": 3, "phase": "MAIN1", "choice": 1,
        "options": ["PLAY LAND Mountain @4", "CAST Goblin Guide 2/2 @7 {R} [Haste]", "PASS (do nothing)"],
        "raw_reply": "WHY: Two damage now and every turn.\nCHOICE: 2"}
PASS = dict(PLAY, choice=-1, raw_reply="WHY: Nothing worth doing.\nCHOICE: 3")
PICKS = {"t": "decision", "seat": "Seat", "tier": "smart", "type": "sacrifice", "turn": 5, "phase": "MAIN2",
         "options": ["Krenko, Mob Boss 3/3 [untapped] @8", "Skirk Prospector 1/1 [untapped] @10",
                     "Mogg War Marshal 1/1 @11"], "picks": [0, 2],
         "raw_reply": "WHY: The token comes back.\nPICKS: 1,3"}
AUTO = dict(PLAY, tier="auto", raw_reply=None)
OTHER_SEAT = dict(PLAY, seat="Human")
WINDOW = {"t": "window", "seat": "Seat", "reason": "pass-only"}


class LineTest(unittest.TestCase):

    def test_a_play_names_the_card_without_handles_or_rules_text(self):
        self.assertEqual(pc.line(PLAY), "T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}")

    def test_a_pass_reads_as_a_pass(self):
        self.assertEqual(pc.line(PASS), "T3 MAIN1  Seat: PASS (do nothing)")

    def test_several_picks_are_each_named(self):
        self.assertEqual(pc.line(PICKS), "T5 MAIN2  Seat sacrifice: Krenko, Mob Boss 3/3 + Mogg War Marshal 1/1")

    def test_the_move_the_row_records_is_shown_not_the_one_the_reply_asked_for(self):
        # The model's reply was not used (refused, then a fixed legal option): the game did option 1, not 2.
        lost = dict(PLAY, choice=0, raw_reply="WHY: Haste.\nCHOICE: 2")
        self.assertEqual(pc.line(lost), "T3 MAIN1  Seat: PLAY LAND Mountain")

    def test_a_declined_ask_reads_as_declined_not_as_the_reply(self):
        # An optional target declined (choice -1, no PASS option): the game took nothing, whatever the reply asked.
        target = dict(PLAY, type="target", choice=-1, options=["Goblin Guide @7", "Seat @1"],
                      raw_reply="WHY: Kill it.\nCHOICE: 1")
        self.assertEqual(pc.line(target), "T3 MAIN1  Seat target: (declined)")

    def test_the_reasoning_only_when_asked(self):
        self.assertNotIn("why", pc.line(PLAY))
        self.assertEqual(pc.line(PLAY, why=True), "T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}\n"
                                                  "          why: Two damage now and every turn.")

    def test_an_answer_with_no_option_number_shows_its_answer_line(self):
        keep = dict(PLAY, type="mulligan", options=[], raw_reply="WHY: Two lands and three threats.\nKEEP: yes")
        self.assertEqual(pc.line(keep), "T3 MAIN1  Seat mulligan: KEEP: yes")

    def test_a_decision_before_the_first_turn_has_no_phase(self):
        for phase in (None, "null"):          # the game row writes the string "null" (runs/human smoke, 2026-10-25)
            keep = dict(PLAY, type="mulligan", turn=0, phase=phase, options=[], raw_reply="KEEP: yes")
            self.assertEqual(pc.line(keep), "T0  Seat mulligan: KEEP: yes")

    def test_rows_that_are_not_the_models_decisions_are_skipped(self):
        for r in (AUTO, OTHER_SEAT, WINDOW):
            self.assertIsNone(pc.line(r), r)


class FeedTest(unittest.TestCase):

    def test_once_prints_every_model_decision_of_the_newest_game_and_skips_a_torn_last_line(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "game-42-1.jsonl"), "w") as f:
                for r in (PLAY, WINDOW, AUTO, PASS):
                    f.write(json.dumps(r) + "\n")
                f.write('{"t":"decision","seat":"Se')       # still being written
            out = io.StringIO()
            pc.feed(d, seat="Seat", why=False, follow=False, out=out)
            self.assertEqual(out.getvalue().splitlines(),
                             ["T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}", "T3 MAIN1  Seat: PASS (do nothing)"])

    def _thinking(self, rows):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "game-42-1.jsonl"), "w") as f:
                f.write(json.dumps(PLAY) + "\n")
            os.mkdir(os.path.join(d, "transcripts"))
            with open(os.path.join(d, "transcripts", "http-1-2-0.jsonl"), "w") as f:
                for r in rows:
                    f.write(json.dumps(dict({"t": "http", "body": '{"phase":"response"}'}, **r)) + "\n")
            out = io.StringIO()
            pc.feed(d, seat="Seat", why=False, follow=False, out=out)
            return out.getvalue().splitlines()

    def test_a_call_the_seat_is_waiting_on_says_it_is_thinking(self):
        lines = self._thinking([{"seq": 1, "phase": "sent", "label": "Seat/smart"},
                                {"seq": 1, "phase": "response", "label": "Seat/smart"},
                                {"seq": 2, "phase": "sent", "label": "Seat/smart"}])
        self.assertEqual(lines[-1], "          ... Seat is thinking")

    def test_no_thinking_line_once_every_call_is_answered_or_for_another_seat(self):
        done = [{"seq": 1, "phase": "sent", "label": "Seat/smart"},
                {"seq": 1, "phase": "response", "label": "Seat/smart"}]
        self.assertNotIn("thinking", "\n".join(self._thinking(done)))
        other = [{"seq": 1, "phase": "sent", "label": "Human/smart"}]
        self.assertNotIn("thinking", "\n".join(self._thinking(other)))

    def _game(self, d, rows=(), raw=b""):
        with open(os.path.join(d, "game-42-1.jsonl"), "ab") as f:
            for r in rows:
                f.write(json.dumps(r).encode() + b"\n")
            f.write(raw)

    def test_each_poll_prints_only_what_is_new_and_a_torn_row_once_it_is_whole(self):
        with tempfile.TemporaryDirectory() as d:
            c = pc.Commentary(d, "Seat", why=False)
            self.assertEqual(c.poll(), [])
            row = json.dumps(PLAY).encode()
            self._game(d, raw=row[:20])
            self.assertEqual(c.poll(), [])
            self._game(d, raw=row[20:] + b"\n")
            self.assertEqual(c.poll(), ["T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}"])
            self._game(d, [PASS])
            self.assertEqual(c.poll(), ["T3 MAIN1  Seat: PASS (do nothing)"])
            self.assertEqual(c.poll(), [])

    def test_a_card_name_split_inside_a_character_between_writes_reads_whole(self):
        with tempfile.TemporaryDirectory() as d:
            c = pc.Commentary(d, "Seat", why=False)
            row = json.dumps(dict(PLAY, options=["PASS (do nothing)", "CAST \u00c6ther Vial @3"]),
                             ensure_ascii=False).encode()
            cut = row.index("\u00c6".encode()) + 1
            self._game(d, raw=row[:cut])
            c.poll()
            self._game(d, raw=row[cut:] + b"\n")
            self.assertEqual(c.poll(), ["T3 MAIN1  Seat: CAST \u00c6ther Vial"])

    def test_a_match_s_next_game_is_marked_when_the_turns_start_again(self):
        with tempfile.TemporaryDirectory() as d:
            self._game(d, [dict(PLAY, turn=9), dict(PLAY, turn=0, phase="null", type="mulligan", options=[],
                                                     raw_reply="KEEP: yes")])
            self.assertEqual(pc.Commentary(d, "Seat", why=False).poll(),
                             ["T9 MAIN1  Seat: CAST Goblin Guide 2/2 {R}", "-- next game --",
                              "T0  Seat mulligan: KEEP: yes"])

    def test_the_seat_s_own_play_draw_choice_and_mulligan_mark_one_game_not_two(self):
        # Real row shapes: a game begins at T0 with the seat's play/draw choice (when it won the toss) and mulligan.
        with tempfile.TemporaryDirectory() as d:
            first = dict(PLAY, turn=0, phase="null", type="starting-player", options=["Play", "Draw"], choice=0)
            mull = dict(PLAY, turn=0, phase="null", type="mulligan", options=["KEEP", "MULLIGAN"], choice=0)
            self._game(d, [first, mull, dict(PLAY, turn=3), first, mull])
            self.assertEqual(pc.Commentary(d, "Seat", why=False).poll(),
                             ["T0  Seat starting-player: Play", "T0  Seat mulligan: KEEP",
                              "T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}", "-- next game --",
                              "T0  Seat starting-player: Play", "T0  Seat mulligan: KEEP"])

    def test_a_response_written_to_the_next_transcript_file_still_ends_the_thinking(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "transcripts"))
            for name, row in (("http-1-2-0.jsonl", {"seq": 4, "phase": "sent", "label": "Seat/smart"}),
                              ("http-1-2-1.jsonl", {"seq": 4, "phase": "response", "label": "Seat/smart"})):
                with open(os.path.join(d, "transcripts", name), "w") as f:
                    f.write(json.dumps(dict(row, t="http")) + "\n")
            self.assertEqual(pc.Commentary(d, "Seat", why=False).poll(), [])

    def test_an_empty_directory_prints_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            pc.feed(d, seat="Seat", why=False, follow=False, out=out)
            self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
