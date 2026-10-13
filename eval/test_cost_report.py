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

    def test_the_price_file_has_the_authorised_model(self):
        p = cr.load_prices()["deepseek/deepseek-v4.1-flash"]
        self.assertTrue(0 < p["prompt"] < p["completion"] < 0.001)
        self.assertRegex(p["checked"], r"^\d{4}-\d\d-\d\d$")


if __name__ == "__main__":
    unittest.main()
