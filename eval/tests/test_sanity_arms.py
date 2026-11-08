#!/usr/bin/env python3
"""Tests for sanity_arms.py: the arms' seats and directories, the loopback server, and the check
that no sanity arm beats the floor (also through aggregate.py --sanity-arms).

Run: python3 eval/tests/test_sanity_arms.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

EVAL = evalpath.EVAL
import sanity_arms  # noqa: E402


def outcomes(results):
    """{seed: (winner unswapped, winner swapped)} -> sanity_arms.outcomes() shape."""
    games = {}
    for seed, (w0, w1) in results.items():
        games[(seed, False)] = w0
        games[(seed, True)] = w1
    return {"games": games, "crashed": 0}


FLOOR_LOSES = outcomes({1: ("B", "B"), 2: ("B", "B"), 3: ("B", "B")})


def write_arm(scored, arm, results, controller_a=None):
    d = sanity_arms.arm_dir(scored, arm)
    d.mkdir(parents=True)
    ov = sanity_arms.arm_overrides(arm)
    for seed, ws in results.items():
        for swap, w in zip((False, True), ws):
            g = {"t": "game", "seed": seed, "swap": swap, "winner": w, "deck_a": "Identity-A",
                 "deck_b": "Identity-B", "controller_a": controller_a or ov["controller_a"],
                 "controller_b": "ai", "smart_a": ov["smart_a"], "fast_a": ov["fast_a"]}
            (d / ("game-%d-%d.jsonl" % (seed, 1000 + swap))).write_text(json.dumps(g) + "\n")


class TestArms(unittest.TestCase):
    def test_arm_directories_are_siblings_of_the_scored_block(self):
        self.assertEqual(sanity_arms.arm_dir("/r/burn", "floor"), Path("/r/burn__sanity-floor"))
        self.assertEqual(sanity_arms.arm_dir("/r/burn/", "empty"), Path("/r/burn__sanity-empty"))

    def test_only_identity_a_changes(self):
        self.assertEqual(sanity_arms.arm_overrides("random")["controller_a"], "random")
        fake = sanity_arms.arm_overrides("garbage")
        self.assertEqual((fake["controller_a"], fake["smart_a"], fake["fake_policy"]),
                         ("llm", sanity_arms.FAKE_SPEC, "garbage"))
        self.assertTrue(all(k.endswith("_a") or k == "fake_policy" for k in fake))
        with self.assertRaises(ValueError):
            sanity_arms.arm_overrides("ai")

    def test_the_loopback_server_starts_and_answers_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, port = sanity_arms.start_fake_server("empty", Path(tmp) / "req.jsonl")
            try:
                body = json.dumps({"model": "fake/harness-model",
                                   "messages": [{"role": "user", "content": "Reply KEEP or MULLIGAN"}]})
                req = urllib.request.Request(sanity_arms.loopback_env(port)["OPENROUTER_BASE_URL"]
                                             + "/chat/completions", data=body.encode(),
                                             headers={"Content-Type": "application/json"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                reply = json.loads(opener.open(req, timeout=10).read())
                self.assertEqual(reply["choices"][0]["message"]["content"], "")
            finally:
                sanity_arms.stop_fake_server(proc)


class TestCheck(unittest.TestCase):
    def arms(self, **over):
        base = {"floor": FLOOR_LOSES, "random": FLOOR_LOSES, "empty": FLOOR_LOSES, "garbage": FLOOR_LOSES}
        base.update(over)
        return base

    def test_arms_that_never_win_pass(self):
        r = sanity_arms.check("x", arms=self.arms())
        self.assertTrue(r["ok"] and r["complete"])
        self.assertEqual(sanity_arms.exit_code(r), 0)

    def test_a_floor_win_is_a_violation(self):
        r = sanity_arms.check("x", arms=self.arms(floor=outcomes({1: ("A", "B"), 2: ("B", "B"), 3: ("B", "B")})))
        self.assertTrue(any(v.startswith("floor won 1") for v in r["violations"]))

    def test_a_garbage_seat_that_wins_where_the_floor_lost_is_a_violation(self):
        r = sanity_arms.check("x", arms=self.arms(garbage=outcomes({1: ("B", "A"), 2: ("B", "B"), 3: ("B", "B")})))
        self.assertTrue(any("garbage won 1 (seed, swap) the floor lost: 1/1" in v for v in r["violations"]))
        self.assertEqual(sanity_arms.exit_code(r), 1)

    def test_random_inside_the_no_skill_band_passes_and_above_it_fails(self):
        def losing_but(wins):
            res = {s: ("B", "B") for s in range(1, 21)}
            res.update(wins)
            return outcomes(res)
        many_floor = losing_but({})
        one_win = losing_but({1: ("A", "B")})                                       # 1/40 = 2.5%
        five_wins = losing_but({1: ("A", "A"), 2: ("A", "A"), 3: ("A", "B")})       # 5/40 = 12.5%
        ok = sanity_arms.check("x", arms=self.arms(floor=many_floor, random=one_win, empty=many_floor, garbage=many_floor))
        self.assertTrue(ok["ok"], ok["violations"])
        bad = sanity_arms.check("x", arms=self.arms(floor=many_floor, random=five_wins, empty=many_floor,
                                                    garbage=many_floor))
        self.assertTrue(any(v.startswith("random won 12.5%") for v in bad["violations"]))

    def test_a_missing_arm_is_incomplete_not_passed(self):
        r = sanity_arms.check("x", arms=self.arms(empty=None))
        self.assertEqual(r["missing"], ["empty"])
        self.assertEqual(sanity_arms.exit_code(r), 2)


class TestOnDisk(unittest.TestCase):
    def test_check_reads_the_arm_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            scored = Path(tmp) / "burn"
            scored.mkdir()
            losing = {1: ("B", "B"), 2: ("B", "B")}
            for arm in ("floor", "random", "empty"):
                write_arm(scored, arm, losing)
            write_arm(scored, "garbage", {1: ("A", "B"), 2: ("B", "B")})
            r = sanity_arms.check(scored)
            self.assertEqual(r["arms"]["garbage"]["wins"], 1)
            self.assertFalse(r["ok"])

    def test_an_unscorable_game_is_not_an_arm_win(self):
        # #430: outcomes() reads the scored winner, so a cut log or a dead bridge is "invalid", never an A win.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, seed, extra in (("game-1-1000.jsonl", 1, {}), ("game-2-1000.jsonl", 2, {"eval_log_write_failed": True}),
                                      ("game-3-1000.jsonl", 3, {"bridge_down_decisions_a": 1})):
                (d / name).write_text(json.dumps(dict({"t": "game", "seed": seed, "swap": False, "winner": "A",
                                                       "deck_a": "Identity-A", "deck_b": "Identity-B",
                                                       "controller_a": "llm", "controller_b": "ai"}, **extra)) + "\n")
            o = sanity_arms.outcomes(d)
        self.assertEqual(o["games"], {(1, False): "A", (2, False): "invalid", (3, False): "invalid"})

    def test_a_fake_arm_game_that_is_unscorable_fails_the_check(self):
        # #430: reading it as "invalid" must not hide "garbage won a game the floor lost"; the count fails the arm.
        with tempfile.TemporaryDirectory() as tmp:
            scored = Path(tmp) / "burn"
            scored.mkdir()
            losing = {1: ("B", "B"), 2: ("B", "B")}
            for arm in ("floor", "random", "empty"):
                write_arm(scored, arm, losing)
            write_arm(scored, "garbage", losing)
            clean = sanity_arms.check(scored)
            self.assertTrue(clean["ok"], clean["violations"])
            self.assertEqual(clean["arms"]["garbage"]["unscorable"], 0)
            g = sanity_arms.arm_dir(scored, "garbage") / "game-1-1000.jsonl"
            row = json.loads(g.read_text())
            g.write_text(json.dumps(dict(row, winner="A", bridge_down_decisions_a=3)) + "\n")
            r = sanity_arms.check(scored)
        self.assertEqual(r["arms"]["garbage"]["unscorable"], 1)
        self.assertEqual(r["arms"]["garbage"]["wins"], 0)               # not counted as a win
        self.assertFalse(r["ok"])
        self.assertTrue(any("unscorable" in v for v in r["violations"]), r["violations"])

    def test_aggregate_exits_5_when_an_arm_beats_the_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            scored = Path(tmp) / "burn"
            scored.mkdir()
            g = {"t": "game", "seed": 1, "swap": False, "winner": "A", "deck_a": "Identity-A", "deck_b": "Identity-B",
                 "controller_a": "random", "controller_b": "ai"}
            (scored / "game-1-1000.jsonl").write_text(json.dumps(g) + "\n")
            for arm in ("floor", "random", "empty"):
                write_arm(scored, arm, {1: ("B", "B")})
            write_arm(scored, "garbage", {1: ("A", "A")})
            p = subprocess.run([sys.executable, str(EVAL / "aggregate.py"), str(scored), "--sanity-arms"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
            self.assertIn("VIOLATION: garbage won", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
