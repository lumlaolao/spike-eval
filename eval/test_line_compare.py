"""line_compare: the prompt fixture is the turn-13 flashback of
runs/lines/ctl/planexec/game-48-1791912207547.jsonl (row 132), where the seat sacrificed Ajani."""
import json
import os
import tempfile
import unittest

import line_compare as lc

STATE = ("TURN 13\nYOU [MarduLLM] life 16 | poison 0/10 | energy 2 | hand 1\n"
         "  hand(1): Swords to Plowshares {W} @10 (castable)\n"
         "  board: Ajani, Nacatl Pariah 1/2 [tapped] @68 (was @6), Guide of Souls 1/2 [tapped] @90 (was @77)\n"
         "  lands: 1 untapped / 2 total\n")
AFTER = STATE.replace("Ajani, Nacatl Pariah 1/2 [tapped] @68 (was @6), ", "")
FLASHBACK = "CAST Cabal Therapy @103 from your GRAVEYARD (Flashback) — cost: Sacrifice a creature [Choose a nonland card name.]"


def play(prompt, options, choice, turn=13):
    return {"t": "decision", "type": "play", "tier": "smart", "turn": turn, "prompt": prompt,
            "options": options, "choice": choice}


def game(d, name, rows, seed, swap, winner):
    with open(os.path.join(d, name), "w") as f:
        for r in rows + [{"t": "game", "seed": seed, "swap": swap, "winner": winner, "turns": 14,
                          "win_condition": "life", "context_sha": "44f97a930af3"}]:
            f.write(json.dumps(r) + "\n")


class LineCompareTest(unittest.TestCase):
    def test_wilson_and_sign_test_known_values(self):
        lo, hi = lc.wilson(6, 16)
        self.assertAlmostEqual(lo, 0.185, places=2)
        self.assertAlmostEqual(hi, 0.614, places=2)
        self.assertEqual(lc.wilson(0, 0), (0.0, 0.0))
        self.assertEqual(lc.sign_test(0, 0), 1.0)
        self.assertEqual(lc.sign_test(3, 3), 1.0)
        self.assertAlmostEqual(lc.sign_test(8, 0), 2 / 256.0)       # 0.0078: eight discordant pairs, all one way
        self.assertAlmostEqual(lc.sign_test(6, 1), 16 / 128.0)

    def test_option_labels_are_reduced_to_the_card(self):
        self.assertEqual(lc.name_of(FLASHBACK), "Cabal Therapy")
        self.assertEqual(lc.name_of("CAST Ajani, Nacatl Pariah 1/2 @3 1{W} [Ajani - Creature 1 / 2]"), "Ajani, Nacatl Pariah")
        self.assertEqual(lc.name_of("ACTIVATE Goblin Bombardment @27 [Sacrifice a creature: deals 1 damage]"), "Goblin Bombardment")
        self.assertEqual(lc.name_of(None), "")

    def test_a_flashback_that_sacrifices_ajani_is_seen_as_ajani_lost(self):
        with tempfile.TemporaryDirectory() as d:
            game(d, "game-48-1.jsonl", [
                play(STATE, [FLASHBACK, "PASS (do nothing)"], 0),
                {"t": "decision", "type": "sacrifice", "tier": "smart", "turn": 13, "prompt": STATE, "options": [], "choice": 1},
                play(AFTER, ["PASS (do nothing)"], 0),
            ], 48, False, "B")
            a = lc.arm(d)
        self.assertEqual((a["games"], a["wins"]), (1, 0))
        self.assertEqual(a["flash"], [("game-48-1.jsonl", 13, True, False)])
        self.assertEqual(a["nonhand"]["Cabal Therapy"], 1)          # in the graveyard, not the hand
        self.assertEqual(a["result"], {(48, False): False})

    def test_a_play_from_hand_is_not_a_non_hand_play_and_plan_rows_are_counted(self):
        with tempfile.TemporaryDirectory() as d:
            game(d, "game-42-1.jsonl", [
                play(STATE, ["CAST Swords to Plowshares @10 {W} [Exile target creature.]", "PASS (do nothing)"], 0),
                {"t": "decision", "type": "play", "tier": "plan", "turn": 13, "options": [], "choice": 0},
            ], 42, True, "A")
            a = lc.arm(d)
        self.assertEqual(sum(a["nonhand"].values()), 0)
        self.assertEqual((a["smart"], a["plan"], a["wins"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
