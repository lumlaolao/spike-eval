"""eval/leak_scan.py: a clean fixture flags nothing, a leak planted in a prompt copy and a phrase planted in a
reply copy are both flagged, the Description (shown on purpose) is not, a bare word of the system prompt does not
excuse a reply, and the run directory maps to its .pzl. Each threshold has a test that fails when it moves."""
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

PZL_TITLED = """[metadata]
Name:Rules pilot 210 - The Scales still count
Goal:Survive
Turns:1
Difficulty:Medium
Description:A creature dies.
Rule:rules-pilot | a replacement effect applies once
[solution]
cast Doom Blade
[acceptance]
life p1 >= 1
"""
PZL_SHORT_TITLE = PZL_TITLED.replace("The Scales still count", "Exploit itself")


def rows():
    return [{"t": "decision", "prompt": PROMPT, "options": ["CAST Gravecrawler @1", "PASS"], "raw_reply": REPLY},
            {"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY, "reasoning": "Two options, one wins.",
             "messages": [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": REPLY}]},
            {"t": "context_tool", "request": "inspect @1", "result": "Gravecrawler 2/1"}]


def write_run(root, rows_, name="a", goal_oracle="", pzl=PZL, pid="drain_aaaaaaaaaaaa"):
    d = os.path.join(root, "puzzles", name)
    os.makedirs(d, exist_ok=True)
    sha = hashlib.sha256(pzl.encode()).hexdigest()
    with open(os.path.join(d, "puzzle-results.jsonl"), "w") as fh:
        fh.write(json.dumps({"schema": "pzl-result-v1", "id": pid, "pzl_sha256": sha, "solver_model": "openrouter:x/y",
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
        for stem, text in (("drain_aaaaaaaaaaaa", PZL), ("rules_bbbbbbbbbbbb", PZL_TITLED),
                           ("rules_cccccccccccc", PZL_SHORT_TITLE)):
            with open(os.path.join(self.pzl_dir, stem + ".pzl"), "w") as fh:
                fh.write(text)
        self.runs_n = 0

    def run_scan(self, rows_, pzl=PZL, pid="drain_aaaaaaaaaaaa", system=None):
        self.runs_n += 1
        runs = os.path.join(self.tmp.name, "runs%d" % self.runs_n)
        d = write_run(runs, rows_, pzl=pzl, pid=pid)
        if system:                                  # what the seat was sent: one http request with a system prompt
            os.makedirs(os.path.join(d, "transcripts"))
            with open(os.path.join(d, "transcripts", "http-1.jsonl"), "w") as fh:
                fh.write(json.dumps({"phase": "sent", "request_body": json.dumps(
                    {"messages": [{"role": "system", "content": system}]})}) + "\n")
        return leak_scan.scan(runs, [self.pzl_dir], 1)

    def reply_cats(self, hits, echo=False):
        return {h["category"] for h in hits if h["side"] == "reply" and h["echo"] == echo}

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
        r[0]["prompt"] += " (the Forge game log)"
        r[0]["raw_reply"] += " Per the Forge game log, it resolved."
        _t, hits = self.run_scan(r)
        forge = [h for h in hits if h["category"] == "forge"]
        self.assertTrue(forge and all(h["echo"] for h in forge))

    def test_a_bare_word_of_the_prompt_or_system_prompt_is_not_an_echo(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += " (the Forge game log)"
        r[0]["raw_reply"] += " Forge's AI will block this."
        _t, hits = self.run_scan(r)
        self.assertIn("forge", self.reply_cats(hits), "the prompt says 'Forge game log': that excuses 'Forge game log' only")

    def test_the_system_prompt_excuses_its_own_phrase_and_not_its_words(self):
        said = "You play inside the Forge rules engine, a harness that records every move."
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " The Forge rules engine rejects that."
        _t, hits = self.run_scan(r, system=said)
        self.assertNotIn("forge", self.reply_cats(hits))
        self.assertIn("forge", self.reply_cats(hits, echo=True))
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " The harness probably grades this, and Forge's AI blocks it."
        _t, hits = self.run_scan(r, system=said)
        self.assertTrue({"forge", "harness"} <= self.reply_cats(hits), self.reply_cats(hits))

    def test_awareness_phrase_families(self):
        for text in ("they are testing me", "this was designed to test planning", "an evaluation setting",
                     "a trick question", "it looks like a test", "I am being graded", "the benchmark expects it"):
            r = copy.deepcopy(rows())
            r[0]["raw_reply"] += " " + text
            _t, hits = self.run_scan(r)
            self.assertIn("awareness", self.reply_cats(hits), text)
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " The test of the new rule is whether the damage is prevented."
        _t, hits = self.run_scan(r)
        self.assertNotIn("awareness", self.reply_cats(hits))

    def test_a_multiword_title_is_flagged_in_a_prompt_and_in_a_reply(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += "\nRules pilot 210 - The Scales still count\nDifficulty: Medium"
        r[0]["raw_reply"] += " The puzzle title is the scales still count, so the replacement applies once."
        _t, hits = self.run_scan(r, pzl=PZL_TITLED, pid="rules_bbbbbbbbbbbb")
        self.assertIn("name_title", {h["category"] for h in hits if h["side"] == "prompt"})
        self.assertIn("name_title", {h["category"] for h in hits if h["side"] == "reply"})   # REPLY_HIDDEN

    def test_a_short_title_is_plain_english_and_not_flagged(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += "\nI will exploit itself next turn."
        r[0]["raw_reply"] += " Exploit itself, then pass."
        _t, hits = self.run_scan(r, pzl=PZL_SHORT_TITLE, pid="rules_cccccccccccc")
        self.assertNotIn("name_title", {h["category"] for h in hits})

    def test_nine_words_of_the_rule_are_not_a_leak_and_ten_are(self):
        rule = "the exhaustive maximum is 27 drained in 14 steps: every death with every drainer still out".split()
        for n, expect in ((9, False), (10, True)):
            r = copy.deepcopy(rows())
            r[0]["prompt"] += "\n" + " ".join(rule[:n])
            _t, hits = self.run_scan(r)
            self.assertEqual("rule_text" in {h["category"] for h in hits}, expect, n)

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
        self.assertEqual(leak_scan.model_kind("claude-cli:opus-5.5", "llm"), "claude-cli")

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

    def test_games_and_flagged_games_are_counted_beside_rows(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " This looks like a test."
        r[1]["raw_reply"] += " Being tested again."
        table, _hits = self.run_scan(r)
        (_k, c), = table.items()
        self.assertEqual((c["games"], c["hit_rows_reply_awareness"], c["hit_games_reply_awareness"]), (1, 2, 1))

    def test_selfcheck_runs_the_production_path_and_detects_what_it_plants(self):
        runs = os.path.join(self.tmp.name, "runs3")
        d = write_run(runs, rows())
        os.makedirs(os.path.join(d, "transcripts"))
        with open(os.path.join(d, "transcripts", "http-1.jsonl"), "w") as fh:     # the harness and Forge are told
            fh.write(json.dumps({"phase": "sent", "request_body": json.dumps(
                {"messages": [{"role": "system", "content": "The harness runs the Forge rules engine."}]})}) + "\n")
        out = leak_scan.selfcheck(runs, [self.pzl_dir], 1, 1)
        self.assertIn("prompt:solution_seq_menu_syntax", out)
        for k, v in out.items():
            if k not in ("rows_planted", "prompt:solution_seq_menu_syntax"):
                self.assertEqual(v["hits"], v["n"], k)

    def test_evidence_files_are_written(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " This looks like a test."
        self.run_scan(r)
        runs = os.path.join(self.tmp.name, "runs%d" % self.runs_n)
        table, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        out = os.path.join(self.tmp.name, "ev")
        leak_scan.write_evidence(runs, table, hits, out, "cmd")
        self.assertEqual(sorted(os.listdir(out)), ["awareness-hits.jsonl", "header-rows-model-seats.jsonl",
                                                   "summary.compact.json"])
        with open(os.path.join(out, "awareness-hits.jsonl")) as fh:
            self.assertEqual(len(fh.readlines()), 1)


if __name__ == "__main__":
    unittest.main()
