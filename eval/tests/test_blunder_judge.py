"""blunder_judge: what it judges, what the judge is shown, how its answer is read, and the budget."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import blunder_judge as bj  # noqa: E402


def dec(type_, tier="smart", choice=0, n=2, turn=3, phase="MAIN1", reply="CHOICE: 1", options=("CAST Dark Ritual", "PASS")):
    return {"t": "decision", "seat": "Seat", "type": type_, "tier": tier, "choice": choice, "n_options": n,
            "turn": turn, "phase": phase, "prompt": "== Turn %d %s ==\nDECISION: You have priority" % (turn, phase),
            "raw_reply": reply, "options": list(options)}


class BlunderJudgeTest(unittest.TestCase):
    def game(self, d, rows):
        p = os.path.join(d, "game-1-0.jsonl")
        with open(p, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows + [{"t": "game", "seed": 1}]))
        return p

    def test_only_the_models_real_choices_are_judged(self):
        rows = [dec("play"), dec("play", tier="auto"), dec("play", n=1), dec("chooseNumber"),
                dict(dec("play"), prompt=None)]
        self.assertEqual(len(list(bj.judged(rows))), 1)

    def test_the_judge_sees_the_state_the_turn_so_far_the_choice_and_the_reason(self):
        ritual = dec("play", choice=0, reply="Ritual for mana.\nCHOICE: 1")
        passed = dec("play", choice=-1, reply="Nothing to do.\nCHOICE: 2")
        p = bj.judge_prompt([ritual, passed], passed)
        self.assertIn("DECISION: You have priority", p)
        self.assertIn("- MAIN1 play: chose 1) CAST Dark Ritual  (its reason: Ritual for mana. CHOICE: 1)", p)
        self.assertIn("=== THE OPTION IT CHOSE ===\nPASS / none", p)
        self.assertIn("Nothing to do.", p)

    def test_a_verdict_is_one_json_object(self):
        v = bj.parse_verdict('```json\n{"verdict": "blunder", "better": 2, "reason": "mana wasted", "question_ok": true}\n```')
        self.assertEqual((v["verdict"], v["better"], v["question_ok"]), ("blunder", 2, True))
        self.assertIsNone(bj.parse_verdict('{"verdict": "great"}'))
        self.assertIsNone(bj.parse_verdict("no json here"))
        self.assertFalse(bj.parse_verdict('{"verdict": "ok", "question_ok": false}')["question_ok"])

    def test_judging_stops_at_the_budget_and_lists_blunders_and_bad_questions(self):
        with tempfile.TemporaryDirectory() as d:
            self.game(d, [dec("play"), dec("play"), dec("play")])
            answers = iter(['{"verdict":"blunder","better":2,"reason":"mana wasted","question_ok":true}',
                            '{"verdict":"ok","question_ok":false,"question_problem":"option 1 is not castable"}'])
            fake = lambda spec, prompt, mt: (next(answers), 100000, 1000)   # noqa: E731
            spec = "openrouter:deepseek/deepseek-v4.1-flash"
            # The budget is checked at the dearest endpoint's price (cost_report.budget_prices).
            price = bj.cost_report.price_of(bj.cost_report.budget_prices(bj.cost_report.load_prices()), spec)
            one = bj.cost(price, 100000, 1000)
            results, spent, sheet = bj.judge_run(d, spec, one * 1.5, bj.DEFAULT_TYPES, 0, 100, call=fake, out=io.StringIO())
            self.assertEqual(len(results), 2, "the third call would pass the budget")
            text = bj.render(results, spent, sheet)
            self.assertIn("BLUNDER", text)
            self.assertIn("QUESTION: option 1 is not castable", text)
            with open(sheet) as f:
                self.assertEqual(len(f.readlines()), 2)

    def test_the_judge_is_given_the_rules_text_of_every_named_card(self):
        index = {"Rishadan Port": "{T}: Add {C}. / {1}, {T}: Tap target land.", "Palace Jailer": "When ...",
                 "Plains": "({T}: Add {W}.)"}
        prompt = ("  hand(2): Rishadan Port @2, Palace Jailer 2{W}{W} 2/2 @7 (not castable now)\n"
                  "  lands: Plains x2 [untapped @11 @23]\n  3) PLAY LAND Rishadan Port @2")
        self.assertEqual([t.split(":")[0] for t in bj.card_texts(prompt, index)], ["Rishadan Port", "Palace Jailer", "Plains"])
        self.assertIn("=== CARD TEXT ===\nRishadan Port: {T}: Add {C}.", bj.judge_prompt([dec("play")], dict(dec("play"), prompt=prompt), index))

    def test_the_oracle_index_skips_the_installed_twin_cards(self):
        with tempfile.TemporaryDirectory() as d:
            for sub, name in (("r", "Real Card"), ("zz_twins", "Twin Card")):
                f = Path(d) / "forge-gui/res/cardsfolder" / sub / (name.lower().replace(" ", "_") + ".txt")
                f.parent.mkdir(parents=True)
                f.write_text("Name:%s\nOracle:Draw a card.\n" % name)
            saved, bj._ORACLE = bj._ORACLE, None
            try:
                self.assertEqual(set(bj.oracle_index(d)), {"Real Card"})
            finally:
                bj._ORACLE = saved

    def test_a_judge_without_a_price_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.game(d, [dec("play")])
            with self.assertRaises(SystemExit):
                bj.judge_run(d, "openrouter:nobody/unpriced", 1, bj.DEFAULT_TYPES, 0, 100, call=None)


if __name__ == "__main__":
    unittest.main()
