"""model_sweep.py: a game completes only when the model fully played it (#430)."""
import json
import os
import pathlib
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import model_sweep as ms


def out_dir(game_extra):
    d = pathlib.Path(tempfile.mkdtemp())
    rows = [{"t": "decision", "fallback": "bridge-down", "attempts": 3, "finish_reason": "stop"},
            dict({"t": "game", "seed": 1, "swap": False, "winner": "A"}, **game_extra)]
    (d / "game-1-1000.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


class PlumbingChecksTest(unittest.TestCase):
    def test_a_finished_game_completed_and_its_decisions_are_counted(self):
        c = ms.plumbing_checks(out_dir({}))
        self.assertTrue(c["game_completed"])
        self.assertEqual((c["decisions"], c["bridge_down_fallbacks"], c["max_attempts"]), (1, 1, 3))

    def test_a_cut_log_or_dead_bridge_game_did_not_complete(self):
        for extra in ({"eval_log_write_failed": True}, {"bridge_down_decisions_a": 1}):
            c = ms.plumbing_checks(out_dir(extra))
            self.assertFalse(c["game_completed"], extra)
            self.assertEqual(c["decisions"], 1, "the counts still read the rows")


if __name__ == "__main__":
    unittest.main()
