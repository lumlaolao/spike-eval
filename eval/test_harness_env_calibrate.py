"""harness_env_calibrate.py: the scoring settings compared on the same report, and a recommendation by rule.

What would make these fail: corpus weights that do not follow the recorded decision mix, a decision type that maps
to no family, a setting under which a seat missing a whole family is not far below ours, or a recommendation that
picks a setting failing a criterion.
"""
import json
import os
import shutil
import tempfile
import unittest

import harness_env_calibrate as cal
import harness_env_grade as hg

FAMS = ["choices", "combat", "costs", "game-start", "library", "play", "targets", "triggers-and-replacements", "variants"]


def rows_for(seat, passing, per=4, ai=0):
    """per instances in every family; `passing(family, i)` says which pass."""
    out = []
    for f in FAMS:
        for i in range(per):
            out.append({"seat": seat, "instance": "%s.%d" % (f, i), "family": f, "hook": cal.HOOK_OF[f],
                        "pass": "1" if passing(f, i) else "0", "ai": str(ai if (f == "choices" and i == 0) else 0)})
    return out


def report():
    r = rows_for("ours", lambda f, i: True)
    r += rows_for("default", lambda f, i: f == "play")
    r += rows_for("ours-minus-combat", lambda f, i: f != "combat")
    r += rows_for("ours-minus-costs", lambda f, i: f != "costs" or i == 0)
    r += rows_for("toy-ai", lambda f, i: True, ai=3)
    return r


class CorpusTest(unittest.TestCase):
    def test_weights_follow_the_recorded_decision_mix_with_a_floor(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "g.jsonl"), "w", encoding="utf-8") as f:
                for t, n in (("play", 70), ("attackers", 4), ("blockers", 1), ("target", 20), ("scry", 5)):
                    for _ in range(n):
                        f.write(json.dumps({"t": "decision", "type": t}) + "\n")
                f.write(json.dumps({"t": "game", "winner": "A"}) + "\n")
            counts = cal.decision_counts(tmp)
            self.assertEqual(counts["play"], 70)
            w = cal.corpus_weights(counts, FAMS, floor=0.02)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
            self.assertGreater(w["play"], w["combat"])
            self.assertGreater(w["combat"], w["variants"])
            self.assertGreaterEqual(min(w.values()), 0.02 / (1 + 0.02 * len(FAMS)))
        finally:
            shutil.rmtree(tmp)

    def test_every_recorded_decision_type_maps_to_a_family(self):
        for t in ("play", "target", "attackers", "blockers", "damage-assign", "trigger-order", "mulligan", "scry",
                  "mana-sources", "phyrexian", "counter-type", "delve", "modes"):
            self.assertIn(cal.family_of_type(t), FAMS, t)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.weights = {f: (0.7 if f == "play" else 0.3 / 8) for f in FAMS}
        self.result = cal.evaluate(report(), self.weights)

    def test_every_setting_scores_every_seat(self):
        for s in cal.SETTINGS:
            self.assertEqual(set(self.result["scores"][s]), {"ours", "default", "empty", "ours-minus-combat", "ours-minus-costs", "toy-ai"})

    def test_the_geometric_floor_puts_a_missing_family_far_below_and_corpus_weights_do_not(self):
        geo = self.result["scores"]["geometric-equal-0.001"]
        self.assertLess(geo["ours-minus-combat"], 0.5 * geo["ours"])
        corpus = self.result["scores"]["geometric-corpus-0.001"]
        self.assertGreater(corpus["ours-minus-combat"], 0.5 * corpus["ours"], "with combat at 4% of weight it is cheap")
        self.assertFalse(self.result["criteria"]["geometric-corpus-0.001"]["missing family far below"])

    def test_the_forge_ai_gate_zeroes_a_seat_under_every_benchmark_setting(self):
        for s in cal.SETTINGS:
            if s.startswith("dense"):
                continue
            self.assertEqual(self.result["scores"][s]["toy-ai"], 0.0, s)

    def test_the_recommendation_meets_every_criterion(self):
        rec = self.result["recommended"]
        self.assertTrue(all(self.result["criteria"][rec].values()), self.result["criteria"][rec])

    def test_the_markdown_names_every_setting_and_the_recommendation(self):
        text = cal.markdown(self.result)
        for s in cal.SETTINGS:
            self.assertIn(s, text)
        self.assertIn("recommended", text)


if __name__ == "__main__":
    unittest.main()
