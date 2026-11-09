#!/usr/bin/env python3
"""Tests for panel_score.py and the frozen panels file it reads.

Run: python3 eval/tests/test_panel_score.py

Panel runs are written synthetically against the real eval/suites/panels-v1.json, so the cell ids,
deck pins and seeds under test are the published ones.
"""

import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

EVAL = evalpath.EVAL
import deck_legality  # noqa: E402
import panel_score as ps  # noqa: E402
import sanity_arms  # noqa: E402
import seed_policy  # noqa: E402
import seed_registry  # noqa: E402

PANELS = EVAL / "suites" / "panels-v1.json"
DOC = json.loads(PANELS.read_text())
ENGINE = (evalpath.REPO / "FORGE_COMMIT").read_text().strip()
PILOT = "openrouter:deepseek/deepseek-v4.1-flash"
SEEDS = [7800, 7801, 7802, 7803]


def write_block(d, deck_sha, results, controller_a="llm", smart_a=PILOT, controller_b="ai", **kw):
    """results: {seed: (winner unswapped, winner swapped)}; 'crash' writes a file with no game line."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for seed, ws in results.items():
        for swap, w in zip((False, True), ws):
            f = d / ("game-%d-%d.jsonl" % (seed, 1000 + swap))
            if w == "crash":
                f.write_text(json.dumps({"t": "decision", "seat": "Identity-A"}) + "\n")
                continue
            g = {"t": "game", "seed": seed, "swap": swap, "winner": w, "deck_a": "Identity-A",
                 "deck_b": "Identity-B", "controller_a": controller_a, "controller_b": controller_b,
                 "smart_a": smart_a if controller_a == "llm" else None,
                 "fast_a": smart_a if controller_a == "llm" else None,
                 "deck_sha": deck_sha, "engine_sha": ENGINE, "overlay_sha": "o" * 12,
                 "context_profile": "v4-faithful", "context_sha": "c" * 12, "harness_epoch": "harness-epoch-5",
                 "plan_execute": False}
            if controller_a == "llm":
                g.update(DOC["pilot_condition"]["fields"])
            g.update(kw)
            f.write_text(json.dumps(g) + "\n")


def results(wins, n_seeds=4):
    """Identity A wins the first `wins` of 2*n_seeds games."""
    out, k = {}, 0
    for s in SEEDS[:n_seeds]:
        pair = []
        for _ in (0, 1):
            pair.append("A" if k < wins else "B")
            k += 1
        out[s] = tuple(pair)
    return out


def panel_run(root, name, wins_by_cell=None, default_wins=4, control_wins=4, sanity=None, skip=()):
    panel, pool, _ = ps.load_panel(PANELS, name)
    for c in panel["cells"]:
        if c["id"] in skip:
            continue
        dsha = ps.cell_deck_sha(c, pool)
        write_block(Path(root) / c["id"], dsha, results((wins_by_cell or {}).get(c["id"], default_wins)))
        if panel.get("control"):
            write_block(Path(str(Path(root) / c["id"]) + ps.CONTROL_SUFFIX), dsha, results(control_wins),
                        controller_a="ai")
        for arm, w in (sanity or {}).items():
            ov = sanity_arms.arm_overrides(arm)
            write_block(sanity_arms.arm_dir(Path(root) / c["id"], arm), dsha,
                        w(c["id"]) if callable(w) else results(w),
                        controller_a=ov["controller_a"], smart_a=ov["smart_a"])
    return panel, pool


ZERO_ARMS = {"floor": 0, "random": 0, "empty": 0, "garbage": 0}


class TestFrozenPanelsFile(unittest.TestCase):
    def test_the_mirror_panel_is_every_archetype_against_itself(self):
        # The Legacy pool: every deck but the hook test decks and the other formats' decks (prefixed by format).
        decks = sorted(p.stem for p in (EVAL / "decks").glob("*.txt")
                       if not p.stem.startswith("hook-") and not deck_legality.is_format_deck(p.stem))
        self.assertEqual(len(decks), 16)
        self.assertEqual([d["name"] for d in DOC["deck_pool"]], decks)
        cells = DOC["panels"]["mirror"]["cells"]
        self.assertEqual([(c["deck_a"], c["deck_b"]) for c in cells], [(d, d) for d in decks])

    def test_the_cross_panel_is_the_published_cyclic_latin_square(self):
        names = [d["name"] for d in DOC["deck_pool"]]
        panel = DOC["panels"]["cross"]
        k = panel["k"]
        self.assertEqual(k, 5)
        cells = panel["cells"]
        self.assertEqual([(c["deck_a"], c["deck_b"]) for c in cells],
                         [(names[i], names[(i + k) % 16]) for i in range(16)])
        self.assertEqual(sorted(c["deck_a"] for c in cells), names)
        self.assertEqual(sorted(c["deck_b"] for c in cells), names)
        self.assertTrue(all(c["deck_a"] != c["deck_b"] for c in cells))
        self.assertEqual(panel["control"]["controller_a"], "ai")

    def test_every_deck_pin_matches_its_file(self):
        for d in DOC["deck_pool"]:
            self.assertEqual(hashlib.sha256((evalpath.REPO / d["path"]).read_bytes()).hexdigest()[:12], d["sha12"],
                             d["name"])

    def test_the_seeds_are_unreserved_eval_only_and_the_control_covers_them(self):
        reg = seed_registry.load_registry()
        for spec in (DOC["panels"]["mirror"]["seeds"], DOC["panels"]["cross"]["seeds"],
                     DOC["panels"]["cross"]["control"]["seeds"]):
            seeds = ps.parse_seed_spec(spec)
            self.assertEqual(seed_policy.check_seeds(seeds), {})
            self.assertEqual(seed_registry.cited_seed_violations(reg, seeds), [])
        self.assertTrue(set(ps.parse_seed_spec(DOC["panels"]["cross"]["seeds"]))
                        <= set(ps.parse_seed_spec(DOC["panels"]["cross"]["control"]["seeds"])))

    def test_seed_specs_and_pair_shas(self):
        self.assertEqual(ps.parse_seed_spec("7800..7802"), [7800, 7801, 7802])
        self.assertEqual(ps.parse_seed_spec("9,3"), [3, 9])
        self.assertEqual(ps.pair_deck_sha("a", "a"), "a")
        self.assertEqual(ps.pair_deck_sha("a", "b"), "a+b")


class TestMirrorPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def score(self, **kw):
        return ps.score_panel(self.root, "mirror", PANELS, resamples=200, **kw)

    def test_every_cell_weighs_the_same_whatever_its_game_count(self):
        panel_run(self.root, "mirror", default_wins=4, sanity=ZERO_ARMS)
        # One cell gets 100 extra won games: a game-weighted pool would move far, the cell pool only by
        # that cell's share.
        burn = DOC["deck_pool"][1]
        extra = {7900 + i: ("A", "A") for i in range(50)}
        write_block(self.root / "burn", burn["sha12"], extra)
        r = ps.score_panel(self.root, "mirror", PANELS, resamples=50)
        cell = r["cells"]["burn"]["score"]
        self.assertAlmostEqual(cell, 104 / 108)
        self.assertAlmostEqual(r["pooled"]["score"], (15 * 0.5 + cell) / 16)
        self.assertIn("outside the panel's list", " ".join(r["problems"]))

    def test_pooled_ci_is_the_stratified_variance(self):
        panel_run(self.root, "mirror", default_wins=4, sanity=ZERO_ARMS)
        r = self.score()
        p = (4 + 0.5) / 9
        se = math.sqrt(16 * p * (1 - p) / 8) / 16
        self.assertAlmostEqual(r["pooled"]["se"], se)
        lo, hi = r["pooled"]["ci95_bootstrap"]
        self.assertTrue(lo <= 0.5 <= hi)
        self.assertTrue(r["valid"], r["problems"] + r["gate_failures"] + r["sanity"]["violations"])

    def test_worst_best_and_cells_below_half_are_reported(self):
        panel_run(self.root, "mirror", wins_by_cell={"doomsday": 0, "elves": 8}, sanity=ZERO_ARMS)
        p = self.score()["pooled"]
        self.assertEqual(p["worst_cell"], ["doomsday", 0.0])
        self.assertEqual(p["best_cell"], ["elves", 1.0])
        self.assertEqual(p["n_cells_ci_below_half"], 1)

    def test_str_norm_uses_the_random_arm_and_the_fifty_percent_mirror_and_is_uncapped(self):
        panel_run(self.root, "mirror", default_wins=6, sanity=dict(ZERO_ARMS))
        r = self.score()
        self.assertEqual(r["anchors"]["wr_ai_mirror"], 0.5)
        self.assertEqual(r["anchors"]["wr_random"], 0.0)
        self.assertAlmostEqual(r["anchors"]["str_norm"], 0.75 / 0.5)
        self.assertGreater(r["anchors"]["str_norm"], 1.0)

    def test_without_a_random_arm_str_norm_is_not_computed(self):
        panel_run(self.root, "mirror")
        r = self.score()
        self.assertIsNone(r["anchors"]["str_norm"])
        self.assertIn("no random sanity arm", r["anchors"]["wr_random_note"])

    def test_a_missing_cell_is_scored_but_not_valid(self):
        panel_run(self.root, "mirror", sanity=ZERO_ARMS, skip=("painter",))
        r = self.score()
        self.assertEqual(r["missing"], ["painter"])
        self.assertEqual(r["pooled"]["n_cells"], 15)
        self.assertFalse(r["valid"])

    def test_a_cell_played_on_the_wrong_decks_is_refused(self):
        panel_run(self.root, "mirror")
        write_block(self.root / "lands", "f" * 12, {7804: ("A", "A")})
        with self.assertRaises(ps.PanelError):
            self.score()

    def test_a_cell_whose_opponent_is_not_the_forge_ai_is_refused(self):
        panel_run(self.root, "mirror", skip=("lands",))
        write_block(self.root / "lands", DOC["deck_pool"][10]["sha12"], results(4), controller_b="random")
        with self.assertRaises(ps.PanelError) as cm:
            self.score()
        self.assertIn("identity B", str(cm.exception))

    def test_a_cell_played_against_a_named_forge_ai_profile_is_refused(self):
        # The benchmark's opponent is Forge's compiled defaults; an empty stamp (or none) is that opponent.
        panel_run(self.root, "mirror", skip=("lands",))
        for kw in ({"ai_profile_b": "Reckless"}, {"ai_profile_a": "Default", "ai_profile_b": ""}):
            with self.subTest(kw=kw):
                write_block(self.root / "lands", DOC["deck_pool"][10]["sha12"], results(4), **kw)
                with self.assertRaises(ps.PanelError) as cm:
                    self.score()
                self.assertIn("Forge AI profile", str(cm.exception))

    def test_a_development_lane_cell_is_refused(self):
        # Owner, 2026-10-24: a claude-cli: seat is local development, never part of a benchmark score.
        panel_run(self.root, "mirror", skip=("lands",))
        write_block(self.root / "lands", DOC["deck_pool"][10]["sha12"], results(4), dev_lane="claude-cli",
                    claude_cli_bridge_version_a="claude-cli-bridge-v1")
        with self.assertRaises(ps.PanelError) as cm:
            self.score()
        self.assertIn("development lane", str(cm.exception))

    def test_a_pilot_on_the_bare_cli_lane_is_refused_even_unstamped(self):
        panel_run(self.root, "mirror", skip=("lands",))
        write_block(self.root / "lands", DOC["deck_pool"][10]["sha12"], results(4), smart_a="opus")
        with self.assertRaises(ps.PanelError) as cm:
            self.score()
        self.assertIn("development lane", str(cm.exception))

    def test_a_cell_with_a_decision_lost_to_a_dead_bridge_is_refused(self):
        # Issue #369: a 402 from the provider left the pilot passing and the game scored as decisive.
        panel_run(self.root, "mirror", skip=("lands",))
        write_block(self.root / "lands", DOC["deck_pool"][10]["sha12"], results(4), bridge_down_decisions_a=3,
                    bridge_down_decisions_b=0)
        with self.assertRaises(ps.PanelError) as cm:
            self.score()
        self.assertIn("dead model bridge", str(cm.exception))

    def test_an_empty_sanity_arm_counts_no_dead_bridge_and_one_whose_transport_failed_is_refused(self):
        panel_run(self.root, "mirror", sanity=ZERO_ARMS)
        panel, pool, _ = ps.load_panel(PANELS, "mirror")
        for c in panel["cells"]:
            d = sanity_arms.arm_dir(self.root / c["id"], "empty")
            ov = sanity_arms.arm_overrides("empty")
            write_block(d, ps.cell_deck_sha(c, pool), results(0), controller_a=ov["controller_a"],
                        smart_a=ov["smart_a"], bridge_down_decisions_a=0, bridge_down_decisions_b=0)
            (d / "batch-manifest.json").write_text(json.dumps({"condition": {"fake_policy": "empty"}}))
        r = self.score()
        self.assertTrue(r["valid"], r["problems"] + r["gate_failures"] + r["sanity"]["violations"])
        c = panel["cells"][0]
        ov = sanity_arms.arm_overrides("empty")
        write_block(sanity_arms.arm_dir(self.root / c["id"], "empty"), ps.cell_deck_sha(c, pool), results(0),
                    controller_a=ov["controller_a"], smart_a=ov["smart_a"], bridge_down_decisions_a=3,
                    bridge_down_decisions_b=0)
        with self.assertRaises(ps.PanelError) as cm:
            self.score()
        self.assertIn("dead model bridge", str(cm.exception))

    def test_a_sanity_arm_that_beats_the_floor_invalidates_the_panel(self):
        arms = dict(ZERO_ARMS, garbage=lambda cid: results(1) if cid == "goblins" else results(0))
        panel_run(self.root, "mirror", sanity=arms)
        r = self.score()
        self.assertFalse(r["valid"])
        self.assertTrue(any(v.startswith("goblins: garbage won") for v in r["sanity"]["violations"]))

    def test_the_strict_score_counts_an_unfinished_game_as_zero(self):
        panel_run(self.root, "mirror", skip=("miracles",))
        write_block(self.root / "miracles", DOC["deck_pool"][12]["sha12"],
                    {7800: ("A", "crash"), 7801: ("A", "draw"), 7802: ("B", "B"), 7803: ("B", "B")})
        decisive = self.score()["cells"]["miracles"]
        strict = self.score(score="strict")["cells"]["miracles"]
        self.assertAlmostEqual(decisive["score"], 2 / 6)
        self.assertAlmostEqual(strict["score"], 2.5 / 8)

    def test_the_report_carries_a_version_and_mixed_epochs_are_refused(self):
        panel_run(self.root, "mirror")
        self.assertTrue(self.score()["benchmark_version"]["id"].startswith("bv1-"))
        write_block(self.root / "burn", DOC["deck_pool"][1]["sha12"], {7800: ("A", "A")},
                    harness_epoch="harness-epoch-4")
        with self.assertRaises(ps.PanelError):
            self.score()

    def test_a_pilot_off_the_pinned_condition_is_refused_whatever_the_flags(self):
        with tempfile.TemporaryDirectory() as d:
            panel_run(d, "mirror")
            panel, pool, _ = ps.load_panel(PANELS, "mirror")
            c = panel["cells"][0]
            write_block(Path(d) / c["id"], ps.cell_deck_sha(c, pool), results(4), lookahead=False)
            for allow in (False, True):
                with self.subTest(allow=allow), self.assertRaises(ps.PanelError) as cm:
                    ps.score_panel(d, "mirror", PANELS, resamples=20, allow_mixed=allow)
                self.assertIn("lookahead=false", str(cm.exception))

    def test_a_model_pilot_and_its_no_model_sanity_arms_are_one_environment(self):
        # A floor or random arm records answer_tool=false and the pilot true: a setting of the llm seat, not of
        # the environment. Read as the environment, every real panel with sanity arms was refused.
        with tempfile.TemporaryDirectory() as d:
            panel_run(d, "mirror", sanity=ZERO_ARMS)
            panel, pool, _ = ps.load_panel(PANELS, "mirror")
            for c in panel["cells"]:
                for arm in ("floor", "random"):
                    ov = sanity_arms.arm_overrides(arm)
                    write_block(sanity_arms.arm_dir(Path(d) / c["id"], arm), ps.cell_deck_sha(c, pool), results(0),
                                controller_a=ov["controller_a"], smart_a=ov["smart_a"], answer_tool=False)
            r = ps.score_panel(d, "mirror", PANELS, resamples=20)
            self.assertEqual(r["condition_problems"], [])

    def test_different_pilots_across_cells_are_a_problem(self):
        panel_run(self.root, "mirror", skip=("infect",))
        write_block(self.root / "infect", DOC["deck_pool"][9]["sha12"], results(4),
                    smart_a="openrouter:anthropic/claude-haiku-5.5")
        r = self.score()
        self.assertTrue(any("different pilots" in p for p in r["problems"]))


class TestRecordedPanelSmoke(unittest.TestCase):
    """eval/testdata/scoring/panel-smoke: run_batch.py --panel mirror --panel-cells burn --seeds 7800
    --controller-a floor --sanity-arms --exploratory against /root/forge on 2026-10-15 (game lines and
    each arm's fake_policy kept). Its first scoring refused: the empty and garbage arms read as one
    player whose openrouter_base_url (a fresh loopback port per launch) differed."""

    def test_a_real_one_cell_panel_with_all_four_arms_scores(self):
        r = ps.score_panel(EVAL / "testdata" / "scoring" / "panel-smoke", "mirror", PANELS, resamples=50)
        burn = r["cells"]["burn"]
        self.assertEqual((burn["wins"], burn["decisive"]), (0, 2))
        self.assertTrue(burn["sanity"]["ok"] and burn["sanity"]["complete"])
        self.assertEqual(len(r["missing"]), 15)
        self.assertFalse(r["valid"])
        self.assertEqual(r["condition_problems"], [])
        self.assertTrue(0.0 <= r["pooled"]["ci95"][0] <= r["pooled"]["ci95"][1] <= 1.0)


class TestCrossPanel(unittest.TestCase):
    def test_the_margin_subtracts_the_ai_vs_ai_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel_run(tmp, "cross", default_wins=6, control_wins=2, sanity=ZERO_ARMS)
            r = ps.score_panel(tmp, "cross", PANELS, resamples=100)
            self.assertAlmostEqual(r["margin"]["score"], 0.75 - 0.25)
            self.assertEqual(r["anchors"]["wr_ai_mirror"], 0.25)
            self.assertAlmostEqual(r["anchors"]["str_norm"], 0.75 / 0.25)
            self.assertTrue(r["valid"], r["problems"])

    def test_a_missing_control_makes_the_panel_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel_run(tmp, "cross")
            cid = DOC["panels"]["cross"]["cells"][0]["id"]
            ctl = Path(tmp) / (cid + ps.CONTROL_SUFFIX)
            for f in ctl.iterdir():
                f.unlink()
            ctl.rmdir()
            r = ps.score_panel(tmp, "cross", PANELS, resamples=20)
            self.assertIn(cid + ps.CONTROL_SUFFIX, r["missing"])
            self.assertFalse(r["valid"])


class TestScorerArithmetic(unittest.TestCase):
    """Pins found by mutation testing (eval/research/2026-11-08-scorer-mutation.md): each fails on the mutant named."""

    def test_the_strict_cluster_score_weighs_a_win_one_a_draw_half_and_a_loss_zero(self):
        # 0.0 -> 1 (a loss worth a point) survived: only the decisive score was pinned
        self.assertAlmostEqual(ps._cluster_score([["A", "draw", "B", "B"]], "strict"), 1.5 / 4)
        self.assertAlmostEqual(ps._cluster_score([["A", "draw", "B", "B"]], "decisive"), 1 / 3)
        self.assertIsNone(ps._cluster_score([["draw"]], "decisive"))

    def test_a_pool_of_cells_with_no_score_or_no_games_has_no_score(self):
        # `if not pts: return ...` -> pass: a divide by zero instead of an empty pool
        for pts in ([], [(None, 8)], [(0.5, 0)]):
            self.assertEqual(ps.stratified(pts), {"score": None, "ci95": None, "se": None, "n_cells": 0})

    def test_a_cell_that_played_other_seeds_names_at_most_eight_of_them(self):
        # `stray[:8]` -> `[:9]`
        with tempfile.TemporaryDirectory() as tmp:
            panel_run(tmp, "mirror", sanity=ZERO_ARMS)
            write_block(Path(tmp) / "burn", DOC["deck_pool"][1]["sha12"], {7900 + i: ("A", "A") for i in range(12)})
            r = ps.score_panel(tmp, "mirror", PANELS, resamples=20)
            msg = [p for p in r["problems"] if "outside the panel's list" in p]
            self.assertEqual(len(msg), 1)
            self.assertIn("7907]", msg[0])
            self.assertNotIn("7908", msg[0])

    def test_str_norm_is_undefined_unless_the_ai_anchor_beats_the_random_anchor(self):
        # wr_ai - wr_random <= 0 as a sum survived: an AI mirror below the random arm must not give a number
        self.assertIsNone(ps.str_norm(0.5, 0.6, 0.5))
        self.assertIsNone(ps.str_norm(0.5, 0.5, 0.5))
        self.assertAlmostEqual(ps.str_norm(0.5, 0.2, 0.6), 0.75)

    def test_str_norm_needs_a_random_arm_in_every_cell_not_in_one(self):
        # `all(...) and randoms` -> `or` survived: one random arm used to stand for the panel
        with tempfile.TemporaryDirectory() as tmp:
            panel_run(tmp, "mirror", sanity=ZERO_ARMS)
            arm = sanity_arms.arm_dir(Path(tmp) / "burn", "random")
            for f in arm.iterdir():
                f.unlink()
            arm.rmdir()
            r = ps.score_panel(tmp, "mirror", PANELS, resamples=20)
            self.assertIsNone(r["anchors"]["wr_random"])
            self.assertIsNone(r["anchors"]["str_norm"])

    def test_the_margin_interval_is_the_laplace_smoothed_variance_of_both_arms(self):
        # (p*n + .5)/(n + 1), mean -/+ Z*se, and the bootstrap's model - control each survived as a mutant
        with tempfile.TemporaryDirectory() as tmp:
            panel_run(tmp, "cross", default_wins=6, control_wins=2, sanity=ZERO_ARMS)
            r = ps.score_panel(tmp, "cross", PANELS, resamples=60)
            m, cells = r["margin"], r["margin"]["n_cells"]
            pm, pc = (6 + 0.5) / 9, (2 + 0.5) / 9
            se = math.sqrt(cells * (pm * (1 - pm) / 8 + pc * (1 - pc) / 8)) / cells
            self.assertAlmostEqual(m["se"], se)
            self.assertAlmostEqual(m["ci95"][0], 0.5 - ps.Z * se)
            self.assertAlmostEqual(m["ci95"][1], 0.5 + ps.Z * se)
            lo, hi = m["ci95_bootstrap"]   # a model - control margin of 0.5, not the sum (1.0)
            self.assertTrue(0.3 < lo <= 0.5 <= hi < 0.7, m["ci95_bootstrap"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
