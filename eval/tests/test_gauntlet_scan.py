"""gauntlet_scan.py: a clean run passes; each kind of harness defect in a game fails it."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import gauntlet_scan as gs

CLEAN = "[kw00] [TALLY] smart=28 fast=0 auto=9 ai=0 textParses=0\n"


def run_dir(games):
    root = tempfile.mkdtemp()
    for tag, (log, exit_code, winner) in games.items():
        d = os.path.join(root, "g-" + tag)
        os.makedirs(d)
        with open(os.path.join(d, "game.log"), "w") as f:
            f.write(log)
        with open(os.path.join(d, "exit_code"), "w") as f:
            f.write(exit_code)
        with open(os.path.join(d, "game-1.jsonl"), "w") as f:
            f.write(json.dumps({"t": "game", "turns": 12, "winner": winner}) + "\n")
    return root


def scan(root):
    with contextlib.redirect_stdout(io.StringIO()):
        return gs.scan(root)


class GauntletScanTest(unittest.TestCase):
    def test_a_clean_run_passes(self):
        self.assertEqual(scan(run_dir({"kw00": (CLEAN, "0", "B")})), 0)

    def test_an_empty_run_fails(self):
        self.assertEqual(scan(tempfile.mkdtemp()), 1)

    def test_each_defect_fails(self):
        for log in ("[kw00] [FALLBACK->AI] chooseX\n" + CLEAN,
                    "[kw00] [ROUTER] play: FAILED to pay for Braided Net -> nothing was played\n" + CLEAN,
                    "[HEADLESS] game thread threw: java.lang.StackOverflowError\n" + CLEAN,
                    "[kw00] [AUTO-LEGAL] order(unparsed)->as-is\n" + CLEAN,
                    "[PLACEHOLDER-ASKED] kw00/smart (openrouter:x) asked; a seat that asks no model reached it\n" + CLEAN,
                    "[kw00] [TALLY] smart=28 fast=0 auto=9 ai=2 textParses=0\n"):
            self.assertEqual(scan(run_dir({"kw00": (log, "0", "B")})), 1, log)

    def test_a_broken_lookup_fails(self):
        root = run_dir({"kw00": (CLEAN, "0", "B")})
        with open(os.path.join(root, "g-kw00", "game-1.jsonl"), "a") as f:
            f.write(json.dumps({"t": "context_tool", "request": 'TOOL CALL simulate {"option": 2}',
                                "result": "ERROR: preview failed: java.lang.NullPointerException"}) + "\n")
        self.assertEqual(scan(root), 1)

    def test_a_failed_target_is_set_apart_only_when_the_card_is_the_opponents_alone(self):
        # runs/gauntlet-forecast-first: g-kw28's seat failed Govern the Guildless (a harness defect), while in g-kw27 the
        # Forge AI opponent's Karlov's Crossbow failed. The decks the log names decide whose card it is; anything not
        # provably the opponent's alone counts (pr-review of #344: a creature's label, an effect's cast, a trigger).
        d = tempfile.mkdtemp()
        seat_deck, ai_deck = os.path.join(d, "kw27.txt"), os.path.join(d, "kw28.txt")
        with open(seat_deck, "w") as f:
            f.write("2 Goblin Welder\n2 Armor of Thorns\n4 Forest\n")
        with open(ai_deck, "w") as f:
            f.write("3 Karlov's Crossbow\n3 Armor of Thorns\n12 Plains\n")
        decks = ("[DECK] loaded 'kw27': 8 main, 0 sideboard  (%s)\n"
                 "[DECK] loaded 'kw28-ai': 18 main, 0 sideboard  (%s)\n" % (seat_deck, ai_deck))
        fail = "%s (82) - [Couldn't add to stack, failed to target] - [[]]\n  STACK_ADD: %s (82) - [Couldn't add to stack, failed to target] -\n"
        ai = decks + fail % ("Karlov's Crossbow", "Karlov's Crossbow") + CLEAN
        self.assertEqual(scan(run_dir({"kw27": (ai, "0", "B")})), 0, "the opponent's card alone")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gs.scan(run_dir({"kw27": (ai, "0", "B")}))
        self.assertIn("not the seat's", out.getvalue(), "the line is reported, not hidden")
        for card in ("Goblin Welder", "Armor of Thorns"):   # the seat's card (a creature), and a card both decks hold
            self.assertEqual(scan(run_dir({"kw27": (decks + fail % (card, card) + CLEAN, "0", "B")})), 1, card)
        # A card the seat played is the seat's, even from the opponent's deck (a stolen Crossbow).
        stolen = decks + "[kw27] [ROUTER] play: activates Karlov's Crossbow @40 [Equip {1}]\n" + fail % (
            "Karlov's Crossbow", "Karlov's Crossbow") + CLEAN
        self.assertEqual(scan(run_dir({"kw27": (stolen, "0", "B")})), 1, "a stolen card's failure is the seat's")
        # No deck lines to tell the side by: counted.
        self.assertEqual(scan(run_dir({"kw27": (fail % ("Karlov's Crossbow", "Karlov's Crossbow") + CLEAN, "0", "B")})), 1)

    def test_an_unfinished_game_fails(self):
        self.assertEqual(scan(run_dir({"kw00": (CLEAN, "0", "invalid")})), 1)
        self.assertEqual(scan(run_dir({"kw00": (CLEAN, "3", "B")})), 1)


if __name__ == "__main__":
    unittest.main()
