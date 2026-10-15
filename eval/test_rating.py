#!/usr/bin/env python3
"""Tests for rating.py: the Bradley-Terry fit, its anchors, bootstrap, checks and condition guard.

Run: python3 eval/test_rating.py

Synthetic games are drawn from a known model so recovery can be checked; the recorded games in
eval/testdata/scoring/ (runs/compare and runs/frontier) check the guards on real game lines.
"""

import math
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aggregate  # noqa: E402
import rating  # noqa: E402

FIX = HERE / "testdata" / "scoring"
ENGINE = (HERE.parent / "FORGE_COMMIT").read_text().strip()
LLM = "openrouter:anthropic/claude-haiku-5.5"


def seat(ctrl):
    """(controller, smart, fast) for a player label used in these tests."""
    if ctrl.startswith("llm:"):
        return "llm", ctrl[4:], ctrl[4:]
    return ctrl, None, None


def game(seed, swap, a, b, winner, **kw):
    ca, sa, fa = seat(a)
    cb, sb, fb = seat(b)
    g = {"t": "game", "seed": seed, "swap": swap, "winner": winner, "deck_a": "A", "deck_b": "B",
         "controller_a": ca, "controller_b": cb, "smart_a": sa, "fast_a": fa, "smart_b": sb, "fast_b": fb,
         "engine_sha": ENGINE, "overlay_sha": "o" * 12, "context_profile": "v4-faithful",
         "context_sha": "c" * 12, "harness_epoch": "harness-epoch-5", "deck_sha": "d" * 12,
         "_dir": "synthetic"}
    g.update(kw)
    return g


def simulate(pairs, h=0.0, seed=1):
    """pairs: [(a, b, true elo a - elo b, n seeds)] -> both seat orders per seed, drawn from BT."""
    rng = random.Random(seed)
    out, s = [], 0
    for a, b, diff, n in pairs:
        d = diff / rating.ELO_PER_LOGIT
        for _ in range(n):
            s += 1
            for swap in (False, True):
                p = rating._sigmoid(d + (h if not swap else -h))
                out.append(game(s, swap, a, b, "A" if rng.random() < p else "B"))
    return out


class TestPartition(unittest.TestCase):
    def test_every_condition_field_is_in_exactly_one_group(self):
        groups = [set(aggregate.CONDITION_ARM_FIELDS), set(rating.PLAYER_FIELDS), set(rating.MATCHUP_FIELDS),
                  set(rating.PLAYER_FIELDS_BY_IDENT["a"]) | set(rating.PLAYER_FIELDS_BY_IDENT["b"]),
                  set(rating.ENV_FIELDS)]
        for f in aggregate.CONDITION_FIELDS:
            self.assertEqual(sum(f in g for g in groups), 1, f)
        self.assertIn("harness_epoch", rating.ENV_FIELDS)
        self.assertIn("one_tier", rating.PLAYER_FIELDS)


class TestFit(unittest.TestCase):
    def test_a_known_gap_to_the_anchor_is_recovered(self):
        games = simulate([("llm:" + LLM, "ai", 200, 1500)])
        rep = rating.rate([], resamples=0, games=games)
        self.assertAlmostEqual(rep["players"]["llm:" + LLM]["elo"], 1700, delta=30)
        self.assertEqual(rep["players"]["ai"]["elo"], 1500)
        self.assertTrue(rep["players"]["ai"]["pinned"])

    def test_the_first_player_term_is_recovered_from_mirror_games(self):
        games = simulate([("ai", "ai", 0, 2000)], h=math.log(0.7 / 0.3))
        rep = rating.rate([], resamples=0, games=games)
        self.assertAlmostEqual(rep["first_player"]["win_prob_between_equals"], 0.7, delta=0.02)

    def test_players_that_never_win_stay_finite_and_ordered(self):
        games = simulate([("floor", "ai", -3000, 10), ("random", "ai", -3000, 10), ("random", "floor", 0, 1)])
        games[-2]["winner"], games[-1]["winner"] = "A", "A"   # random beats floor twice
        rep = rating.rate([], resamples=200, games=games)
        f, r = rep["players"]["floor"]["elo"], rep["players"]["random"]["elo"]
        self.assertTrue(math.isfinite(f) and f < r < 1500)
        self.assertTrue(rep["sanity"]["valid"])

    def test_a_floor_that_beats_random_invalidates_the_run(self):
        games = simulate([("floor", "random", 400, 30), ("random", "ai", -400, 30), ("floor", "ai", -400, 30)])
        rep = rating.rate([], resamples=50, games=games)
        self.assertFalse(rep["sanity"]["valid"])
        self.assertIn("floor above random", rep["sanity"]["point_violations"])

    def test_bootstrap_cis_contain_the_point_and_are_reproducible(self):
        games = simulate([("llm:" + LLM, "ai", 100, 40), ("random", "ai", -800, 20)])
        r1 = rating.rate([], resamples=200, bootstrap_seed=3, games=games)
        r2 = rating.rate([], resamples=200, bootstrap_seed=3, games=games)
        self.assertEqual(r1["players"], r2["players"])
        for p in r1["players"].values():
            lo, hi = p["elo_ci95"]
            self.assertTrue(lo <= p["elo"] + 1e-6 and p["elo"] - 1e-6 <= hi)
            self.assertTrue(p["rank_ci95"][0] <= p["rank"] <= p["rank_ci95"][1])

    def test_both_seat_orders_of_a_seed_are_one_cluster(self):
        recs = rating.decisive_records([game(5, False, "random", "ai", "B"), game(5, True, "random", "ai", "A")])
        self.assertEqual(recs[0][4], recs[1][4])
        self.assertEqual((recs[0][2], recs[1][2]), (1, -1))

    def test_draws_and_unfinished_games_are_not_rated(self):
        recs = rating.decisive_records([game(1, False, "random", "ai", "draw"),
                                        game(2, False, "random", "ai", "invalid")])
        self.assertEqual(recs, [])


class TestChecks(unittest.TestCase):
    def test_a_rock_paper_scissors_cycle_is_found_and_misfit(self):
        x, y = "llm:" + LLM, "llm:openrouter:deepseek/deepseek-v4.1-flash"
        games = simulate([("ai", x, 3000, 15), (x, y, 3000, 15), (y, "ai", 3000, 15)])
        rep = rating.rate([], resamples=0, games=games)
        self.assertEqual(len(rep["intransitivity"]["three_cycles"]), 1)
        self.assertEqual(rep["goodness_of_fit"]["n_flagged"], 3)

    def test_a_transitive_field_has_no_cycle(self):
        games = simulate([("ai", "random", 3000, 15), ("random", "floor", 3000, 15), ("ai", "floor", 3000, 15)])
        rep = rating.rate([], resamples=0, games=games)
        self.assertEqual(rep["intransitivity"]["three_cycles"], [])

    def test_chi_square_tail_matches_tables(self):
        self.assertAlmostEqual(rating.chi2_sf(3.841, 1), 0.05, places=3)
        self.assertAlmostEqual(rating.chi2_sf(18.307, 10), 0.05, places=3)
        self.assertAlmostEqual(rating.chi2_sf(1.0, 30), 1.0, places=6)


class TestConditionGuard(unittest.TestCase):
    def test_games_from_two_harness_epochs_are_refused(self):
        games = simulate([("random", "ai", -800, 3)])
        games[0]["harness_epoch"] = "harness-epoch-4"
        with self.assertRaises(rating.MixedConditionError):
            rating.rate([], resamples=0, games=games)
        rep = rating.rate([], resamples=0, games=games, allow_mixed=True)
        self.assertTrue(rep["mixed_allowed"])
        self.assertIsNone(rep["benchmark_version"])

    def test_one_player_under_two_settings_is_refused(self):
        with self.assertRaises(rating.MixedConditionError) as cm:
            rating.rate([FIX / "compare-one-tier", FIX / "compare-two-tier"], resamples=0)
        self.assertIn("one_tier", str(cm.exception))

    def test_different_decks_do_not_count_as_different_conditions(self):
        games = simulate([("random", "ai", -800, 3)])
        games[0]["deck_sha"] = "e" * 12
        rating.rate([], resamples=0, games=games)

    def test_no_forge_ai_means_no_scale(self):
        with self.assertRaises(rating.MixedConditionError):
            rating.rate([], resamples=0, games=simulate([("random", "floor", 100, 3)]))


class TestRecordedGames(unittest.TestCase):
    def test_compare_baselines_and_deepseek_rate_in_the_expected_order(self):
        rep = rating.rate([FIX / "compare-baseline-floor", FIX / "compare-baseline-random",
                           FIX / "compare-two-tier"], resamples=100)
        p = rep["players"]
        ds = p["llm:openrouter:deepseek/deepseek-v4.1-flash"]
        self.assertEqual((ds["wins"], ds["decisive_games"]), (2, 6))
        self.assertTrue(p["floor"]["elo"] <= p["random"]["elo"] < ds["elo"] < 1500)
        self.assertTrue(rep["sanity"]["valid"])
        self.assertTrue(rep["benchmark_version"]["id"].startswith("bv1-"))

    def test_the_fake_model_arms_are_two_players_and_their_ports_do_not_split_them(self):
        smoke = FIX / "panel-smoke"
        rep = rating.rate([smoke / "burn__sanity-empty", smoke / "burn__sanity-garbage",
                           smoke / "burn__sanity-random", smoke / "burn__sanity-floor"], resamples=20)
        fakes = sorted(p for p in rep["players"] if "fake" in p)
        self.assertEqual(fakes, ["llm:openrouter:fake/harness-model[empty]",
                                 "llm:openrouter:fake/harness-model[garbage]"])
        a = game(1, False, "llm:x", "ai", "B", openrouter_base_url="http://127.0.0.1:41000/v1")
        b = dict(a, openrouter_base_url="http://127.0.0.1:52000/v1")
        c = dict(a, openrouter_base_url="https://openrouter.ai/api/v1")
        self.assertEqual(rating.player_condition(a, "a"), rating.player_condition(b, "a"))
        self.assertNotEqual(rating.player_condition(a, "a"), rating.player_condition(c, "a"))

    def test_compare_and_frontier_epochs_do_not_pool(self):
        with self.assertRaises(rating.MixedConditionError):
            rating.rate([FIX / "compare-baseline-floor", FIX / "frontier-opus"], resamples=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
