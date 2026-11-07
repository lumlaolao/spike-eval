#!/usr/bin/env python3
"""eval/research_stats.py on board and game rows built here."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import research_stats as rs  # noqa: E402

OPTS = ["A", "B", "C", "PASS"]


def decision(choice, reply="", options=OPTS, fallback=None):
    return {"t": "decision", "options": options, "choice": str(choice), "raw_reply": reply, "fallback": fallback}


def board(path, **extra):
    row = {"source_game": path, "regret_k": 1,
           "floor": {"options": {"A": False, "B": False, "C": True, "PASS": True}, "model_label": "B"},
           "positions": [{"k": 0, "winnable": True}, {"k": 1, "winnable": True}, {"k": 2, "winnable": False}]}
    row.update(extra)
    return row


class TestResearchStats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.game = os.path.join(self.dir, "game.jsonl")
        rows = [decision(3, "WHY: set up the win\nCHOICE: 4"), decision(1, "WHY: B\nCHOICE: 2"),
                decision(0, "WHY: this is lethal, so cast A"), decision(0, "WHY: x", fallback="forced")]
        with open(self.game, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_menu_counts_a_step_by_hand(self):
        out = rs.menu([board(self.game)])
        # B (position 1) is the earliest of the losing options A, B; kept are C and PASS (positions 2, 3)
        self.assertEqual((out["steps"], out["earliest_losing"], out["before_every_kept"], out["top_pick"]),
                         (1, 0, 1, 0))
        self.assertEqual((out["earliest_losing_chance"], out["before_every_kept_chance"], out["top_pick_chance"]),
                         (0.5, 1.0, 0.25))
        self.assertEqual((out["pick_pos"], out["kept_pos"]), (0.33, 0.83))

    def test_menu_counts_unmatched_and_missing_games(self):
        no_match = board(self.game, regret_k=1)
        no_match["floor"] = dict(no_match["floor"], model_label="C")
        out = rs.menu([no_match, board(os.path.join(self.dir, "gone.jsonl")), {"floor": None}])
        self.assertEqual((out["steps"], out["unmatched"], out["game_missing"]), (0, 1, 1))

    def test_loss_awareness_joins_the_kth_non_auto_row(self):
        out = rs.loss_awareness([board(self.game)])
        # k=0 "set up the win" (no claim), k=1 "B", k=2 "this is lethal" in a lost position
        self.assertEqual(out["winnable"], [2, 0, 0])
        self.assertEqual(out["lost"], [1, 1, 0])
        self.assertEqual((out["attempts"], out["failed_with_regret_step"], out["never_concede_after_step"]), (1, 1, 1))

    def test_a_concession_after_the_step_is_counted(self):
        rows = [decision(0, "WHY: a"), decision(1, "WHY: b"), decision(0, "WHY: it is impossible now")]
        with open(self.game, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        out = rs.loss_awareness([board(self.game)])
        self.assertEqual((out["lost"], out["never_concede_after_step"]), ([1, 0, 1], 0))

    def test_the_note_quotes_the_scripts_patterns(self):
        note = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research",
                            "2026-11-06-loss-awareness-regex-fails.md")
        with open(note, encoding="utf-8") as f:
            text = f.read()
        for pattern in (rs.CLAIMS_WIN, rs.CONCEDES):
            self.assertIn("`" + pattern.pattern + "`", text)

    def test_usage_error(self):
        self.assertEqual(rs.main([]), 2)


if __name__ == "__main__":
    unittest.main()
