#!/usr/bin/env python3
"""Tests for run_batch.py's games of more than two seats and Commander games: the seats C and D it passes, what it
refuses, how it finds a Commander precon, the cap it derives, and that a directory holds one format and one seat
count. Nothing here launches a JVM or contacts a model.

Run: python3 eval/tests/test_run_batch_multiplayer.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import module_tmp  # noqa: E402
import run_batch  # noqa: E402
from test_run_batch import make_args  # noqa: E402


def setUpModule():
    module_tmp.isolate(__name__)


def four(**kw):
    base = dict(players=4, format="commander", exploratory=True, pairs=False,
                controller_a="random", controller_b="ai", smart_a=None, fast_a=None,
                deck_c="/p/c.dck", deck_d="/p/d.dck", name_c="Coven", name_d=None,
                controller_c=None, controller_d="random", smart_c=None, smart_d=None, fast_c=None, fast_d=None,
                ai_profile_c=None, ai_profile_d=None)
    base.update(kw)
    return make_args(**base)


class SeatsTest(unittest.TestCase):

    def test_a_two_seat_batch_passes_no_seat_count_and_keeps_its_condition(self):
        args = make_args()
        self.assertEqual(run_batch.multiplayer_env(args), {})
        self.assertFalse(run_batch.placed(args))
        self.assertEqual(run_batch.seats_cd(args), {})
        env = run_batch.build_env(args, 7100, False, "/tmp/out")
        for k in ("PLAYERS", "DECK_C", "CONTROLLER_C", "AI_PROFILE_C"):
            self.assertNotIn(k, env)
        self.assertEqual(run_batch.resume_expectation(args)["n_players"], [run_batch.ABSENT])
        self.assertIn(run_batch.ABSENT, run_batch.resume_expectation(args)["game_format"])

    def test_four_seats_pass_seats_c_and_d(self):
        env = run_batch.multiplayer_env(four())
        self.assertEqual(env["PLAYERS"], "4")
        self.assertEqual((env["DECK_C"], env["DECK_C_NAME"], env["CONTROLLER_C"]), ("/p/c.dck", "Coven", "ai"))
        self.assertEqual((env["DECK_D"], env["CONTROLLER_D"], env["AI_PROFILE_D"]), ("/p/d.dck", "random", ""))
        self.assertNotIn("DECK_D_NAME", env)   # the JVM names it after its deck file
        full = run_batch.build_env(four(), 1, False, "/tmp/out")
        self.assertEqual((full["PLAYERS"], full["GAME_FORMAT"]), ("4", "commander"))
        exp = run_batch.resume_expectation(four())
        self.assertEqual((exp["n_players"], exp["game_format"]), ([4], ["commander"]))
        self.assertEqual(run_batch.resume_expectation(four(players=2, deck_c=None, deck_d=None, name_c=None,
                                                           controller_d=None))["n_players"], [2])

    def test_every_seat_flag_and_variable_is_run_defining(self):
        for k in ("PLAYERS", "DECK_C", "DECK_D", "CONTROLLER_C", "CONTROLLER_D", "AI_PROFILE_C", "DECK_D_NAME"):
            self.assertIn(k, run_batch.RUN_DEFINING_KEYS)
        ns = run_batch.build_parser().parse_args(["--seeds", "1", "--players", "3", "--deck-c", "x", "--controller-c",
                                                  "random", "--ai-profile-d", "Default"])
        self.assertEqual((ns.players, ns.deck_c, ns.controller_c, ns.ai_profile_d), (3, "x", "random", "Default"))


class RefusalTest(unittest.TestCase):

    def test_what_a_multiplayer_batch_refuses(self):
        self.assertIsNone(run_batch.multiplayer_refusal(four()))
        self.assertIsNone(run_batch.multiplayer_refusal(make_args(players=None, format=None)))
        cases = {
            "--players must be": four(players=5),
            "is exploratory": four(exploratory=False),
            "--pairs": four(pairs=True),
            "needs --deck-d": four(deck_d=None),
            "zero-model seats": four(controller_c="llm"),
            "seats no D": four(players=3),
        }
        for why, args in cases.items():
            msg = run_batch.multiplayer_refusal(args)
            self.assertIsNotNone(msg, why)
            self.assertIn(why, msg)
        # A two-seat Commander game is placed too, so exploratory as well.
        self.assertIn("is exploratory", run_batch.multiplayer_refusal(
            make_args(players=None, format="commander", exploratory=False)))

    def test_a_profile_on_seat_c_needs_a_forge_ai_seat(self):
        self.assertIsNone(run_batch.ai_profile_refusal(four(ai_profile_c="Default")))
        self.assertIn("--controller-d random", run_batch.ai_profile_refusal(four(ai_profile_d="Default")))


class CommanderDeckTest(unittest.TestCase):

    def setUp(self):
        self.forge = Path(tempfile.mkdtemp())
        d = self.forge / run_batch.PRECON_DIR
        d.mkdir(parents=True)
        for f in ("Abzan Armor [TDC] [2025].dck", "Breed Lethality [C16] [2016].dck",
                  "Breed Lethality [CM2] [2018].dck"):
            (d / f).write_text("[metadata]\nName=%s\n[Commander]\n1 X\n[Main]\n99 Forest\n" % f[:-4])

    def test_a_precon_is_found_by_its_name_and_an_ambiguous_one_is_refused(self):
        path, why = run_batch.commander_deck("Abzan Armor", self.forge)
        self.assertIsNone(why)
        self.assertTrue(path.endswith("Abzan Armor [TDC] [2025].dck"))
        self.assertTrue(run_batch.commander_deck("breed lethality [cm2] [2018]", self.forge)[0]
                        .endswith("/Breed Lethality [CM2] [2018].dck"))
        _, why = run_batch.commander_deck("Breed Lethality", self.forge)
        self.assertIn("names 2 precons", why)
        _, why = run_batch.commander_deck("Nothing Like It", self.forge)
        self.assertIn("no Commander precon", why)
        mine = self.forge / "mine.dck"
        mine.write_text("[Commander]\n1 Y\n")
        self.assertEqual(run_batch.commander_deck(str(mine), self.forge), (str(mine), None))

    def test_the_format_guard_wants_a_dck_with_a_commander(self):
        good = run_batch.commander_deck("Abzan Armor", self.forge)[0]
        bad = self.forge / "burn.txt"
        bad.write_text("4 Lightning Bolt\n")
        run_batch.format_guard(four(deck_a=good, deck_b=good, deck_c=good, deck_d=good))
        with self.assertRaises(SystemExit):
            run_batch.format_guard(four(deck_a=good, deck_b=str(bad), deck_c=good, deck_d=good))


class CapAndResumeTest(unittest.TestCase):

    def test_the_cap_a_commander_game_derives(self):
        self.assertEqual(run_batch.derive_cap_minutes(four()), 30.0)
        self.assertEqual(run_batch.derive_cap_minutes(four(controller_a="llm", smart_a="claude-cli:sonnet",
                                                           fast_a="claude-cli:sonnet")), 210.0)
        self.assertEqual(run_batch.derive_cap_minutes(make_args(controller_a="ai", controller_b="ai")), 15.0)

    def test_a_directory_holds_one_format_and_one_seat_count(self):
        out = Path(tempfile.mkdtemp())
        two = run_batch.batch_condition(make_args(format=None), cited=False)
        self.assertEqual((two["game_format"], two["players"], two["seats_cd"]), ("legacy", 2, {}))
        many = run_batch.batch_condition(four(), cited=False)
        self.assertEqual(sorted(many["seats_cd"]), ["c", "d"])
        run_batch.write_manifest(out, two, seeds=[1], pairs=False, jobs=1)
        problem = run_batch.check_resume_condition(out, many, cited=False)
        self.assertIsNotNone(problem)
        for field in ("game_format", "players", "seats_cd"):
            self.assertIn(field, problem)
        self.assertIsNone(run_batch.check_resume_condition(out, two, cited=False))

    def test_a_manifest_from_before_the_fields_is_read_by_its_game_lines(self):
        out = Path(tempfile.mkdtemp())
        old = run_batch.batch_condition(make_args(format="modern"), cited=False)
        for k in ("game_format", "players", "seats_cd"):
            del old[k]
        run_batch.write_manifest(out, old, seeds=[1], pairs=False, jobs=1)
        (out / "game-1-0.jsonl").write_text(json.dumps({"t": "game", "seed": 1, "game_format": "modern"}) + "\n")
        now = run_batch.batch_condition(make_args(format="modern"), cited=False)
        self.assertIsNone(run_batch.check_resume_condition(out, now, cited=False), "the same format resumes")
        cmdr = run_batch.batch_condition(make_args(format="commander"), cited=False)
        self.assertIn("game_format", run_batch.check_resume_condition(out, cmdr, cited=False))


if __name__ == "__main__":
    unittest.main()
