"""demo_eval: the eval page shows the acceptance stages as the summary wrote them, PUZ as puzzle_score computes it from
the recorded results (the numbers REPORT.md quotes), and STR-AI as not yet run with its panel's size and cost."""
import os
import shutil
import tempfile
import unittest

import demo_eval

SUMMARY = """PASS preflight: engine 34a8662, Picked up JAVA_TOOL_OPTIONS: -Dhttps.proxyHost=127.0.0.1, decks + ledger present
PASS canary-floor: seed 42: winner=A turns=108 decisions=1828 as pinned (linux pins)
FAIL canary-random: seed 42: winner=B turns=54 (pin moved)
total=2
"""


class EvalTest(unittest.TestCase):
    def test_the_gate_is_every_stage_line_in_order_without_the_jvms_proxy_options(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "summary.txt"), "w") as f:
                f.write(SUMMARY)
            acc = demo_eval.acceptance(d)
        finally:
            shutil.rmtree(d)
        self.assertEqual([(s["stage"], s["status"]) for s in acc["stages"]],
                         [("preflight", "PASS"), ("canary-floor", "PASS"), ("canary-random", "FAIL")])
        self.assertEqual((acc["passed"], acc["total"]), (2, 3))
        self.assertNotIn("proxyHost", acc["stages"][0]["detail"])
        self.assertIn("decks + ledger", acc["stages"][0]["detail"])

    def test_puz_is_computed_from_the_recorded_runs_with_its_floors(self):
        runs = {r["label"]: r for r in demo_eval.puz()["runs"]}
        self.assertEqual(len(runs), 4)
        on67 = runs["lookups on, epoch 67"]
        self.assertEqual((on67["puzzles"], on67["k"], on67["attempts"]), (29, 5, 145))
        self.assertAlmostEqual(on67["headline"]["value"], 0.788, places=3)       # REPORT.md, epoch 67 table
        self.assertAlmostEqual(runs["lookups off, epoch 66"]["headline"]["value"], 0.809, places=3)
        self.assertEqual(on67["headline"]["floors"]["random"], 0.0)

    def test_str_ai_is_not_yet_run_and_carries_its_cost(self):
        h = {x["key"]: x for x in demo_eval.report()["headlines"]}
        self.assertEqual(h["STR-AI"]["status"], "not yet run")
        plan = h["STR-AI"]["plan"]
        self.assertEqual((plan["cells"], plan["games"]), (16, 1600))
        self.assertEqual(plan["cost_usd"], [32.0, 80.0])
        self.assertIsNone(demo_eval.report()["acceptance"])


if __name__ == "__main__":
    unittest.main()
