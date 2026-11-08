"""analyze_ab_stage0.py: a game the model did not fully play is counted apart and never makes a pair (#430)."""
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import analyze_ab_stage0 as ab


def arm(games):
    d = tempfile.mkdtemp()
    for seed, extra in games.items():
        g = dict({"t": "game", "seed": seed, "swap": False, "winner": "A", "turns": 8, "deck_a": "X", "deck_b": "Y",
                  "controller_a": "llm", "controller_b": "ai"}, **extra)
        with open(os.path.join(d, "game-%d-1000.jsonl" % seed), "w") as f:
            f.write(json.dumps(g) + "\n")
    return d


class LoadGamesTest(unittest.TestCase):
    def test_scorable_games_are_keyed_by_seed_and_swap(self):
        by_key, meta = ab.load_games_by_key(arm({1: {}, 2: {"winner": "B"}}))
        self.assertEqual(sorted(by_key), [(1, False), (2, False)])
        self.assertEqual((meta["total"], meta["nondecisive"], meta["unscorable"]), (2, 0, 0))

    def test_a_cut_log_or_dead_bridge_game_is_counted_apart_and_never_paired(self):
        by_key, meta = ab.load_games_by_key(arm({1: {}, 2: {"eval_log_write_failed": True},
                                                 3: {"bridge_down_decisions_a": 5}}))
        self.assertEqual(sorted(by_key), [(1, False)])
        self.assertEqual((meta["total"], meta["nondecisive"], meta["unscorable"]), (3, 0, 2))


if __name__ == "__main__":
    unittest.main()
