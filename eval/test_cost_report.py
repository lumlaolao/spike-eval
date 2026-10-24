"""cost_report: token counts priced from the price file; the figures are ones recomputed by hand."""
import json
import os
import tempfile
import unittest

import cost_report as cr

OR = "openrouter:deepseek/deepseek-v4.1-flash"
PRICES = {"deepseek/deepseek-v4.1-flash": {"prompt": 0.0000000441, "completion": 0.000000765, "checked": "2026-10-13"}}


def write(d, name, rows):
    with open(os.path.join(d, name), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class CostReportTest(unittest.TestCase):
    def test_context_calls_are_priced_and_decision_rows_are_not_added_on_top(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [
                {"t": "context_call", "tier": "smart", "prompt_tokens": 1000000, "completion_tokens": 100000},
                {"t": "context_call", "tier": "fast", "prompt_tokens": 1000000, "completion_tokens": 0},
                {"t": "decision", "tier": "smart", "prompt_tokens": 999999, "completion_tokens": 999999},   # same call again
                {"t": "game", "smart_a": OR, "fast_a": OR}])
            r = cr.report_dir(d, PRICES)
        self.assertEqual((r["games"], r["calls"], r["prompt_tokens"], r["completion_tokens"]), (1, 2, 2000000, 100000))
        self.assertAlmostEqual(r["usd"], 2 * 0.0441 + 0.0765, places=6)

    def test_cached_and_cache_written_prompt_tokens_are_priced_at_their_own_rates(self):
        p = {"prompt": 1.0, "completion": 10.0, "input_cache_read": 0.1, "input_cache_write": 1.25}
        # 1000 prompt tokens: 800 read from cache, 100 written to it, 100 at the full rate; 10 completion.
        self.assertAlmostEqual(cr.usd_of(p, 1000, 10, 800, 100), 100 * 1.0 + 800 * 0.1 + 100 * 1.25 + 10 * 10.0)
        # No cache rates in the price file: every prompt token at the full rate, as before.
        self.assertAlmostEqual(cr.usd_of({"prompt": 1.0, "completion": 10.0}, 1000, 10, 800, 100), 1000 + 100)
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [
                {"t": "context_call", "tier": "smart", "prompt_tokens": 1000, "completion_tokens": 10, "cached_tokens": 800,
                 "cache_write_tokens": 100},
                {"t": "context_call", "tier": "smart", "prompt_tokens": 1000, "completion_tokens": 10},   # a row from before
                {"t": "game", "smart_a": "openrouter:x/y", "fast_a": "openrouter:x/y"}])
            r = cr.report_dir(d, {"x/y": dict(p, checked="t")})
        self.assertEqual(r["cached_tokens"], 800)
        self.assertAlmostEqual(r["usd"], (100 + 80 + 125 + 100) + (1000 + 100))
        self.assertIn("40% of prompt tokens read from cache", cr.fmt(r))

    def test_a_game_without_tool_rounds_is_priced_from_its_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [
                {"t": "decision", "tier": "smart", "prompt_tokens": 2000, "completion_tokens": 100},
                {"t": "decision", "tier": "auto", "prompt_tokens": None, "completion_tokens": None},     # no model call
                {"t": "game", "smart_a": OR, "fast_a": OR}])
            r = cr.report_dir(d, PRICES)
        self.assertEqual(r["calls"], 1)
        self.assertAlmostEqual(r["usd"], 2000 * 0.0000000441 + 100 * 0.000000765, places=9)

    def test_a_killed_game_is_still_billed_and_counted_as_unfinished(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [{"t": "context_call", "tier": "smart", "prompt_tokens": 1000000, "completion_tokens": 0}])
            write(d, "batch-manifest.json", [{"condition": {"smart_a": OR, "fast_a": OR}}])
            r = cr.report_dir(d, PRICES)
        self.assertEqual((r["games"], r["unfinished"]), (0, 1))
        self.assertAlmostEqual(r["usd"], 0.0441, places=6)
        self.assertIn("1 unfinished", cr.fmt(dict(r, dir=d)))

    def test_a_model_with_no_price_is_named_not_priced_at_zero_silently(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [{"t": "context_call", "tier": "smart", "prompt_tokens": 10, "completion_tokens": 10},
                                      {"t": "game", "smart_a": "openrouter:acme/unknown", "fast_a": "haiku"}])
            r = cr.report_dir(d, PRICES)
        self.assertEqual(r["unpriced"], {"acme/unknown"})
        self.assertIn("NOT PRICED: acme/unknown", cr.fmt(dict(r, dir=d)))
        self.assertIsNone(cr.model_id("haiku"))

    def test_each_seat_is_priced_by_its_own_model(self):
        # Each call is priced by its own seat's model: two different models first, then a killed game
        # where only B is a model.
        dear = "openrouter:acme/dear"
        prices = dict(PRICES, **{"acme/dear": {"prompt": 0.000001, "completion": 0.000002, "checked": "2026-10-14"}})
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1.jsonl", [
                {"t": "context_call", "seat": "Burn", "tier": "smart", "prompt_tokens": 1000000, "completion_tokens": 0},
                {"t": "context_call", "seat": "Delver", "tier": "fast", "prompt_tokens": 0, "completion_tokens": 1000},
                {"t": "game", "deck_a": "Delver", "deck_b": "Burn", "controller_a": "llm", "controller_b": "llm",
                 "smart_a": dear, "fast_a": dear, "smart_b": OR, "fast_b": OR}])
            r = cr.report_dir(d, prices)
            self.assertAlmostEqual(r["usd"], 0.0441 + 1000 * 0.000002, places=9)
            self.assertEqual(r["models"], {"acme/dear", "deepseek/deepseek-v4.1-flash"})
            # A killed game names its seats through the manifest.
            write(d, "game-1.jsonl", [{"t": "context_call", "seat": "Burn", "tier": "smart",
                                       "prompt_tokens": 1000000, "completion_tokens": 0}])
            write(d, "batch-manifest.json", [{"condition": {"name_a": "Delver", "name_b": "Burn",
                                                            "smart_a": "ai", "smart_b": OR}}])
            self.assertAlmostEqual(cr.report_dir(d, prices)["usd"], 0.0441, places=9)
            # Both identities under one name (an exploratory batch allows it): the dearer spec, never $0.
            write(d, "batch-manifest.json", [{"condition": {"name_a": "Burn", "name_b": "Burn",
                                                            "smart_a": "ai", "smart_b": OR}}])
            self.assertAlmostEqual(cr.report_dir(d, prices)["usd"], 0.0441, places=9)
            write(d, "batch-manifest.json", [{"condition": {"name_a": "Burn", "name_b": "Burn",
                                                            "smart_a": dear, "smart_b": OR}}])
            self.assertAlmostEqual(cr.report_dir(d, prices)["usd"], 1.0, places=9)

    def test_the_price_file_has_the_authorised_model(self):
        p = cr.load_prices()["deepseek/deepseek-v4.1-flash"]
        self.assertTrue(0 < p["prompt"] < p["completion"] < 0.001)
        self.assertRegex(p["checked"], r"^\d{4}-\d\d-\d\d$")

    def test_a_budget_is_checked_at_the_dearest_endpoint_and_a_report_at_the_list_price(self):
        # Latency routing may land on a priority tier: gpt-6.1-sol's "ultrafast" endpoint lists 6x its list price
        # (OpenRouter /models/.../endpoints, 2026-10-24), so a budget priced at the list rate could be overrun 6x.
        prices = {"m/x": {"prompt": 1e-06, "completion": 2e-06, "checked": "2026-10-24",
                          "max": {"prompt": 6e-06, "completion": 1.2e-05}}}
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-0.jsonl", [
                {"t": "context_call", "tier": "smart", "prompt_tokens": 1000, "completion_tokens": 100},
                {"t": "game", "smart_a": "openrouter:m/x", "fast_a": "openrouter:m/x"}])
            listed = cr.report_dir(d, prices)["usd"]
            bound = cr.report_dir(d, cr.budget_prices(prices))["usd"]
        self.assertAlmostEqual(listed, 1000 * 1e-06 + 100 * 2e-06)
        self.assertAlmostEqual(bound, 1000 * 6e-06 + 100 * 1.2e-05)
        self.assertEqual(cr.budget_prices({"m/y": {"prompt": 1.0, "completion": 2.0}})["m/y"]["prompt"], 1.0,
                         "an entry with no max is its own bound")

    def test_every_paid_price_states_its_bound_and_the_bound_covers_every_listed_rate(self):
        # A budget is checked at `max`; a model with no `max` must say why (`max_note`: one endpoint), and a `max`
        # that leaves out a rate the entry lists (cache writes) would check that rate at the list price. A rate the
        # entry does not list falls back to the prompt rate, so `max` holds it only where an endpoint charges more.
        for mid, p in cr.load_prices().items():
            if mid.startswith("fake/"):
                continue
            self.assertTrue("max" in p or "max_note" in p, mid)
            bound = p.get("max") or {}
            for k in ("prompt", "completion", "input_cache_read", "input_cache_write"):
                if k in p and bound:
                    self.assertIn(k, bound, (mid, k))
                if k in bound:
                    self.assertGreaterEqual(bound[k], p.get(k, 0), (mid, k))


if __name__ == "__main__":
    unittest.main()
