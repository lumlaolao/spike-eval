#!/usr/bin/env python3
"""Tests for benchmark_version.py: what moves the id, and what refuses to pool.

Run: python3 eval/test_benchmark_version.py

Fixtures: eval/testdata/scoring/ holds the game lines of runs/compare (harness-epoch-4) and
runs/frontier (harness-epoch-5), plans replaced by their hash.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import benchmark_version as bv  # noqa: E402

FIX = HERE / "testdata" / "scoring"


def make_repo(root):
    """A minimal checkout holding every file the version hashes."""
    root = Path(root)
    (root / "eval" / "decks").mkdir(parents=True)
    (root / "eval" / "suites").mkdir(parents=True)
    (root / "eval" / "tooluse").mkdir(parents=True)
    spike = root / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
    spike.mkdir(parents=True)
    (root / "FORGE_COMMIT").write_text("e" * 40 + "\n")
    for n in ("LlmSpikeGui.java", "AnswerTool.java"):
        (spike / n).write_text("class %s {}" % n)
    patch = root / "forge-overlay/forge-game/src/main/java/forge/game/player"
    patch.mkdir(parents=True)
    (patch / "Player.java").write_text("class Player {}")
    (root / "eval" / "decks" / "burn.txt").write_text("4 Lightning Bolt\n")
    (root / "eval" / "seed_registry.json").write_text('{"version": 2, "banks": []}')
    (root / "eval" / "tooluse" / "tools.json").write_text('{"tools": []}')
    write_panels(root)
    return root


def write_panels(root, anchors=None, sha=None):
    deck = root / "eval" / "decks" / "burn.txt"
    doc = {"suite": "t", "deck_pool": [{"name": "burn", "path": "eval/decks/burn.txt",
                                        "sha12": sha or bv._sha12(deck.read_bytes())}],
           "panels": {}, "anchors": anchors or {"forge_ai_elo": 1500}}
    (root / "eval" / "suites" / "panels-v1.json").write_text(json.dumps(doc))


def game(**kw):
    g = {"t": "game", "engine_sha": "e" * 40, "overlay_sha": "o" * 12, "context_profile": "v4-faithful",
         "context_sha": "c" * 12, "harness_epoch": "harness-epoch-5"}
    g.update(kw)
    return g


class TestVersionId(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def vid(self, games=None):
        return bv.stamp(self.repo, games=games)["id"]

    def test_the_id_is_stable_for_unchanged_inputs(self):
        self.assertEqual(self.vid(), self.vid())
        self.assertTrue(self.vid().startswith("bv1-"))

    def test_every_hashed_input_moves_the_id(self):
        base = self.vid()
        edits = [
            lambda: (self.repo / "eval" / "tooluse" / "tools.json").write_text('{"tools": [1]}'),
            lambda: (self.repo / "eval" / "seed_registry.json").write_text('{"version": 3, "banks": []}'),
            lambda: (self.repo / "FORGE_COMMIT").write_text("f" * 40),
            lambda: (self.repo / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/AnswerTool.java").write_text("class X {}"),
            # an engine patch is part of the harness too (2026-10-16 review H3)
            lambda: (self.repo / "forge-overlay/forge-game/src/main/java/forge/game/player/Player.java").write_text("class P {}"),
            lambda: (self.repo / "eval" / "suites" / "puzzles-v1.json").write_text("{}"),
            lambda: write_panels(self.repo, anchors={"forge_ai_elo": 1600}),
        ]
        seen = {base}
        for edit in edits:
            edit()
            now = self.vid()
            self.assertNotIn(now, seen, "an edit did not move the id")
            seen.add(now)

    def test_a_deck_edited_after_the_panels_were_frozen_is_refused(self):
        (self.repo / "eval" / "decks" / "burn.txt").write_text("4 Chain Lightning\n")
        with self.assertRaises(bv.VersionError) as cm:
            self.vid()
        self.assertIn("changed since the panels file was frozen", str(cm.exception))

    def test_observed_fields_come_from_the_game_lines(self):
        comp = bv.components(self.repo, games=[game(), game()])
        self.assertEqual(comp["observed"]["harness_epoch"], "harness-epoch-5")
        self.assertEqual(comp["overlay"], "o" * 12)
        self.assertNotEqual(self.vid([game()]), self.vid([game(context_sha="d" * 12)]))

    def test_games_that_disagree_on_the_harness_epoch_do_not_pool(self):
        with self.assertRaises(bv.VersionError) as cm:
            self.vid([game(), game(harness_epoch="harness-epoch-4")])
        self.assertIn("harness_epoch", str(cm.exception))

    def test_games_played_on_another_engine_are_refused(self):
        with self.assertRaises(bv.VersionError):
            self.vid([game(engine_sha="a" * 40)])

    def test_crashed_placeholders_carry_no_condition_and_are_ignored(self):
        self.assertEqual(self.vid([game(), {"t": "game", "_no_game_line": True}]), self.vid([game()]))


class TestPooling(unittest.TestCase):
    def test_one_version_pools(self):
        s = {"id": "bv1-x", "components": {"a": 1}}
        self.assertEqual(bv.check_poolable([s, dict(s)]), "bv1-x")

    def test_different_versions_refuse_and_name_what_differs(self):
        a = {"id": "bv1-x", "components": {"tools": "1", "engine": "e"}}
        b = {"id": "bv1-y", "components": {"tools": "2", "engine": "e"}}
        with self.assertRaises(bv.VersionError) as cm:
            bv.check_poolable([a, b])
        self.assertIn("tools", str(cm.exception))
        self.assertNotIn("engine", str(cm.exception).split("components:")[1])

    def test_a_report_without_a_version_cannot_pool(self):
        with self.assertRaises(bv.VersionError):
            bv.check_poolable([{"id": "bv1-x"}, None])

    def test_cli_check_pool_exits_nonzero_on_mixed_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = Path(tmp) / "a.json", Path(tmp) / "b.json"
            p1.write_text(json.dumps({"benchmark_version": {"id": "bv1-x", "components": {}}}))
            p2.write_text(json.dumps({"benchmark_version": {"id": "bv1-y", "components": {}}}))
            self.assertEqual(bv.main(["--check-pool", str(p1), str(p1)]), 0)
            self.assertEqual(bv.main(["--check-pool", str(p1), str(p2)]), 1)


class TestRecordedGames(unittest.TestCase):
    def test_this_checkout_has_a_version(self):
        s = bv.stamp()
        self.assertTrue(s["id"].startswith("bv1-"))
        self.assertEqual(len(s["components"]["deck_pool"]), 16)

    def test_compare_and_frontier_games_are_different_versions(self):
        one = bv.games_of_dirs([FIX / "compare-two-tier", FIX / "compare-baseline-floor"])
        self.assertEqual(bv.components(games=one)["observed"]["harness_epoch"], "harness-epoch-4")
        with self.assertRaises(bv.VersionError):
            bv.components(games=bv.games_of_dirs([FIX / "compare-two-tier", FIX / "frontier-opus"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
