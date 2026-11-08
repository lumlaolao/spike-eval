"""eval/leak_scan.py: a clean fixture flags nothing, a leak planted in a prompt copy and a phrase planted in a
reply copy are both flagged, the Description (shown on purpose) is not, and the run directory maps to its .pzl."""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401
import leak_scan  # noqa: E402

PZL = """[metadata]
Name:drain_aaaaaaaaaaaa
Goal:Survive
Turns:1
Difficulty:Hard
Description:It is your second main phase. You control Blood Artist. Reduce the opponent to 0 life this turn.
Rule:generated-drain | the exhaustive maximum is 27 drained in 14 steps: every death with every drainer still out
[state]
turn=1
[solution]
# steps: 14
cast Gravecrawler
activate Carrion Feeder
Blood Artist; Zulaport Cutthroat; Cruel Celebrant
[acceptance]
life p1 <= 0
"""
PROMPT = ("== Turn 1 MAIN2 ==\nIt is your second main phase. You control Blood Artist. Reduce the opponent to 0 life "
          "this turn.\nDECISION: 1) CAST Gravecrawler @1\n2) PASS")
REPLY = "I will cast Gravecrawler, then pass."


def rows():
    return [{"t": "decision", "prompt": PROMPT, "options": ["CAST Gravecrawler @1", "PASS"], "raw_reply": REPLY},
            {"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY, "reasoning": "Two options, one wins.",
             "messages": [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": REPLY}]},
            {"t": "context_tool", "request": "inspect @1", "result": "Gravecrawler 2/1"}]


def write_run(root, rows_, name="a", goal_oracle=""):
    d = os.path.join(root, "puzzles", name)
    os.makedirs(d, exist_ok=True)
    sha = hashlib.sha256(PZL.encode()).hexdigest()
    with open(os.path.join(d, "puzzle-results.jsonl"), "w") as fh:
        fh.write(json.dumps({"schema": "pzl-result-v1", "id": "drain_aaaaaaaaaaaa", "pzl_sha256": sha, "solver_model": "openrouter:x/y",
                             "solver_arm": "llm", "harness_epoch": "e1", "goal_oracle": goal_oracle}) + "\n")
    with open(os.path.join(d, "game-1-1.jsonl"), "w") as fh:
        for r in rows_:
            fh.write(json.dumps(r) + "\n")
    return d


class LeakScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pzl_dir = os.path.join(self.tmp.name, "pzl")
        os.makedirs(self.pzl_dir)
        with open(os.path.join(self.pzl_dir, "drain_aaaaaaaaaaaa.pzl"), "w") as fh:
            fh.write(PZL)

    def run_scan(self, rows_):
        runs = os.path.join(self.tmp.name, "runs")
        write_run(runs, rows_)
        table, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        return table, hits

    def test_clean_fixture_flags_nothing(self):
        table, hits = self.run_scan(rows())
        self.assertEqual(hits, [])
        (key, c), = table.items()
        self.assertEqual(key[:3], ("puzzles/a", "openrouter:x/y", "model"))
        self.assertEqual(c["rows"], 3)
        self.assertEqual(c["puzzle_mapped_sha"], 2)     # the game file and puzzle-results.jsonl

    def test_planted_prompt_leaks_are_flagged(self):
        for cat, text in (("rule_text", "note: drained in 14 steps: every death with every drainer still out"),
                          ("solution_seq", "cast Gravecrawler\nactivate Carrion Feeder\nBlood Artist; Zulaport Cutthroat; Cruel Celebrant"),
                          ("solution_comment", "# steps: 14"),
                          ("acceptance_line", "life p1 <= 0"),
                          ("rule_slug", "generated-drain"),
                          ("id", "puzzle drain_aaaaaaaaaaaa"),
                          ("pzl_path", "eval/puzzles/hard/x.pzl"),
                          ("section_header", "[solution]")):
            r = copy.deepcopy(rows())
            r[0]["prompt"] += "\n" + text
            _t, hits = self.run_scan(r)
            self.assertIn(cat, {h["category"] for h in hits if h["side"] == "prompt"}, cat)

    def test_planted_reply_phrase_is_flagged_and_prompt_echo_is_not_a_leak(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " This looks like a test of the benchmark harness."
        _t, hits = self.run_scan(r)
        cats = {h["category"] for h in hits if h["side"] == "reply" and not h["echo"]}
        self.assertTrue({"awareness", "harness"} <= cats, cats)
        r = copy.deepcopy(rows())
        r[0]["prompt"] += " (Forge game log)"
        r[0]["raw_reply"] += " Forge says so."
        _t, hits = self.run_scan(r)
        forge = [h for h in hits if h["category"] == "forge"]
        self.assertTrue(forge and all(h["echo"] for h in forge))

    def test_unmapped_puzzle_still_flags_its_id(self):
        runs = os.path.join(self.tmp.name, "runs2")
        write_run(runs, rows())
        r = rows()
        r[0]["prompt"] += " drain_aaaaaaaaaaaa"
        write_run(runs, r)
        table, hits = leak_scan.scan(runs, [os.path.join(self.tmp.name, "nowhere")], 1)
        self.assertIn("id", {h["category"] for h in hits})
        self.assertEqual(sum(c["puzzle_mapped_none"] for c in table.values()), 2)

    def test_model_kind(self):
        self.assertEqual(leak_scan.model_kind("script:/x.pzl"), "script")
        self.assertEqual(leak_scan.model_kind("openrouter:fake/harness-model"), "fake")
        self.assertEqual(leak_scan.model_kind("", "random"), "zero-model")
        self.assertEqual(leak_scan.model_kind("openrouter:a/b", "llm"), "model")

    def test_a_brief_that_names_the_puzzle_is_flagged_and_the_description_is_not(self):
        runs = os.path.join(self.tmp.name, "runs4")
        write_run(runs, rows(), goal_oracle="Turns Limit: 1\n\nIt is your second main phase. You control Blood Artist.")
        _t, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        self.assertEqual(hits, [])
        runs = os.path.join(self.tmp.name, "runs5")
        write_run(runs, rows(), goal_oracle="drain_aaaaaaaaaaaa\nDifficulty: Hard\n\nGoal: Survive")
        _t, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        self.assertTrue({"id", "meta_line"} <= {h["category"] for h in hits if h["field"] == "goal_oracle"})

    def test_one_solution_step_alone_is_a_menu_label_not_a_leak(self):
        r = rows()
        r[0]["prompt"] += "\n3) cast Gravecrawler\n4) activate Carrion Feeder\nBlood Artist; Zulaport Cutthroat; Cruel Celebrant"
        _t, hits = self.run_scan(r)
        self.assertEqual([h for h in hits if h["category"].startswith("solution")], [])

    def test_selfcheck_detects_everything_it_plants(self):
        runs = os.path.join(self.tmp.name, "runs3")
        write_run(runs, rows())
        out = leak_scan.selfcheck(runs, [self.pzl_dir], 1, 1)
        for k, v in out.items():
            if k != "rows_planted":
                self.assertEqual(v["hits"], v["n"], k)


if __name__ == "__main__":
    unittest.main()
