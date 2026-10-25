"""gauntlet_scan.py: a clean run passes; each kind of harness defect in a game fails it."""
import contextlib
import io
import json
import os
import tempfile
import unittest

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

    def test_an_unfinished_game_fails(self):
        self.assertEqual(scan(run_dir({"kw00": (CLEAN, "0", "invalid")})), 1)
        self.assertEqual(scan(run_dir({"kw00": (CLEAN, "3", "B")})), 1)


if __name__ == "__main__":
    unittest.main()
