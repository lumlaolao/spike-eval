"""eval/leak_scan.py: a clean fixture flags nothing, a leak planted in a prompt copy and a phrase planted in a
reply copy are both flagged, the Description (shown on purpose) is not, a bare word of the system prompt does not
excuse a reply, and the run directory maps to its .pzl. Each threshold has a test that fails when it moves."""
import contextlib
import copy
import hashlib
import io
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


def write_run(root, rows_, name="a", goal_oracle="", pzl=PZL, pid="drain_aaaaaaaaaaaa", model="openrouter:x/y",
              arm="llm"):
    d = os.path.join(root, "puzzles", name)
    os.makedirs(d, exist_ok=True)
    sha = hashlib.sha256(pzl.encode()).hexdigest()
    with open(os.path.join(d, "puzzle-results.jsonl"), "w") as fh:
        fh.write(json.dumps({"schema": "pzl-result-v1", "id": pid, "pzl_sha256": sha, "solver_model": model,
                             "solver_arm": arm, "harness_epoch": "e1", "goal_oracle": goal_oracle}) + "\n")
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

    def run_scan(self, rows_, pzl=PZL, pid="drain_aaaaaaaaaaaa", system=None, tools=None):
        self.runs_n += 1
        runs = os.path.join(self.tmp.name, "runs%d" % self.runs_n)
        d = write_run(runs, rows_, pzl=pzl, pid=pid)
        if system or tools:                         # what the seat was sent: one http request, system prompt and tools
            os.makedirs(os.path.join(d, "transcripts"))
            body = {"messages": [{"role": "system", "content": system or "play"}]}
            if tools:
                body["tools"] = [{"function": {"description": tools}}]
            with open(os.path.join(d, "transcripts", "http-1.jsonl"), "w") as fh:
                fh.write(json.dumps({"phase": "sent", "request_body": json.dumps(body)}) + "\n")
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

    def test_every_match_of_a_field_is_checked_until_a_non_echo_one(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += " (the Forge game log)"
        r[0]["raw_reply"] += " Per the Forge game log, it resolved. Then Forge's AI blocks it."
        _t, hits = self.run_scan(r)
        self.assertIn("forge", self.reply_cats(hits), "the first 'Forge' is an echo, the second is not")

    def test_an_echo_looks_two_neighbours_to_each_side(self):
        for reply, said in ((" Please note Forge runs the game now.", "forge runs the engine"),
                            (" The game engine Forge ends it.", "tell the game engine forge")):
            r = copy.deepcopy(rows())
            r[0]["raw_reply"] += reply
            _t, hits = self.run_scan(r, system=said)
            self.assertNotIn("forge", self.reply_cats(hits), reply)
            self.assertIn("forge", self.reply_cats(hits, echo=True), reply)

    def test_the_tool_list_counts_as_what_the_seat_was_told(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " The Forge rules engine says so."
        _t, hits = self.run_scan(r, tools="Look up the Forge rules engine text of a card")
        self.assertNotIn("forge", self.reply_cats(hits))
        self.assertIn("forge", self.reply_cats(hits, echo=True))

    def test_game_text_in_a_reply_is_not_a_solution_or_acceptance_leak(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " life p1 <= 0 is the goal. cast Gravecrawler, activate Carrion Feeder, Blood Artist; Zulaport Cutthroat; Cruel Celebrant"
        _t, hits = self.run_scan(r)
        self.assertEqual([h for h in hits if h["side"] == "reply"], [])

    def test_a_forge_puzzle_recognised_from_memory_is_a_recognition_hit(self):
        rx = leak_scan.REPLY_PATTERNS["recognition"]
        for text in ("This looks like it is from the Forge puzzle collection, I recall this exact puzzle.",
                     "It is part of a known set of Forge puzzles.", "I recalled this puzzle from my memory as a Forge puzzle deck.",
                     "Maybe a known puzzle from a Forge puzzle series.",
                     "I recall " + "so " * 11 + "this Forge puzzle.",         # 12 words between: the window
                     "I recall the Forge " + "so " * 10 + "puzzle.",           # 10 words between Forge and puzzle: the core
                     "This Forge puzzle" + " so" * 12 + " recall",          # 12 words between puzzle and the recall word
                     "The puzzle " + "so " * 10 + "Forge, which I recall."):

            self.assertTrue(rx.search(text), text)
        for text in ("The Forge puzzle goal is to win this turn.", "I recall Forge asks which cards to splice.",
                     "We forge ahead: the puzzle is known.",
                     "I recall " + "so " * 12 + "this Forge puzzle.",         # 13 words between: one outside the window
                     "I recall the Forge " + "so " * 11 + "puzzle.",           # 11 words between Forge and puzzle: outside
                     "This Forge puzzle" + " so" * 13 + " recall",          # 13 words: one outside the window
                     "The puzzle " + "so " * 11 + "Forge, which I recall."):
            self.assertFalse(rx.search(text), text)

    def test_forge_class_names_are_their_own_category_and_not_hidden_by_the_word(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " Forge checks CardFactory and then ComputerUtilCost."
        _t, hits = self.run_scan(r)
        self.assertTrue({"forge", "forge_class"} <= self.reply_cats(hits), self.reply_cats(hits))

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
        self.assertIn("name_title", {h["category"] for h in hits if h["side"] == "reply"})   # the reply-side list

    def test_a_short_title_is_plain_english_and_not_flagged(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += "\nI will exploit itself next turn."
        r[0]["raw_reply"] += " Exploit itself, then pass."
        _t, hits = self.run_scan(r, pzl=PZL_SHORT_TITLE, pid="rules_cccccccccccc")
        self.assertNotIn("name_title", {h["category"] for h in hits})

    def test_a_three_word_title_is_not_a_needle_and_a_four_word_one_is(self):
        def cats(text):
            return {c for c, _n in leak_scan.build_needles(text, "rules_bbbbbbbbbbbb")}
        self.assertIn("name_title", cats(PZL_TITLED))
        three = PZL_TITLED.replace("The Scales still count", "Scales still count")
        self.assertNotIn("name_title", cats(three))

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
        for arm in ("ai-log", "search", "script"):      # solver arms that name no model: nothing a model wrote
            self.assertEqual(leak_scan.model_kind("", arm), "zero-model", arm)
        self.assertEqual(leak_scan.model_kind("", "llm"), "unlabelled")      # an llm seat that names no model is unknown
        self.assertEqual(leak_scan.model_kind("", ""), "unlabelled")
        self.assertEqual(leak_scan.model_kind("openrouter:a/b", "random"), "model")      # the spec wins over the arm
        self.assertEqual(leak_scan.model_kind("random"), "zero-model")
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

    def test_selfcheck_does_not_count_a_planted_sentence_the_seat_was_told(self):
        runs = os.path.join(self.tmp.name, "runs6")
        d = write_run(runs, rows())
        os.makedirs(os.path.join(d, "transcripts"))
        said = " ".join(leak_scan.PLANT_REPLIES[k] for k in ("forge", "harness"))
        with open(os.path.join(d, "transcripts", "http-1.jsonl"), "w") as fh:
            fh.write(json.dumps({"phase": "sent", "request_body": json.dumps(
                {"messages": [{"role": "system", "content": said}]})}) + "\n")
        out = leak_scan.selfcheck(runs, [self.pzl_dir], 1, 1)
        for k in ("reply:forge", "reply:harness"):
            self.assertEqual((out[k]["hits"], out[k]["n"]), (0, 1), k)
        self.assertEqual(out["reply:seed"]["hits"], 1)

    def test_evidence_files_are_written(self):
        r = copy.deepcopy(rows())
        r[0]["raw_reply"] += " This looks like a test."
        self.run_scan(r)
        runs = os.path.join(self.tmp.name, "runs%d" % self.runs_n)
        table, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        out = os.path.join(self.tmp.name, "ev")
        leak_scan.write_evidence(runs, table, hits, out, "cmd")
        self.assertEqual(sorted(os.listdir(out)), [
            "awareness-hits.jsonl", "forge-class-hits.jsonl", "header-rows-model-seats.jsonl",
            "name-title-reply-hits.jsonl", "recognition-hits.jsonl", "summary.compact.json"])
        with open(os.path.join(out, "awareness-hits.jsonl")) as fh:
            self.assertEqual(len(fh.readlines()), 1)
        with open(os.path.join(out, "summary.compact.json")) as fh:
            self.assertEqual(fh.read().count("\n"), 0)

    def test_an_echoed_phrase_is_not_written_to_the_awareness_evidence(self):
        r = copy.deepcopy(rows())
        r[0]["prompt"] += " (this looks like a test of your plan)"
        r[0]["raw_reply"] += " This looks like a test of your plan."
        self.run_scan(r)
        runs = os.path.join(self.tmp.name, "runs%d" % self.runs_n)
        table, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        self.assertTrue([h for h in hits if h["category"] == "awareness" and h["echo"]])
        out = os.path.join(self.tmp.name, "ev2")
        leak_scan.write_evidence(runs, table, hits, out, "cmd")
        with open(os.path.join(out, "awareness-hits.jsonl")) as fh:
            self.assertEqual(fh.read(), "")

    def test_header_rows_list_model_seats_and_not_zero_model_arms(self):
        runs = os.path.join(self.tmp.name, "hdr")
        oracle = "drain_aaaaaaaaaaaa\nDifficulty: Hard\n\nGoal: Survive"
        write_run(runs, rows(), name="seat", goal_oracle=oracle)
        write_run(runs, rows(), name="floor", goal_oracle=oracle, model="", arm="random")
        table, hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        out = os.path.join(self.tmp.name, "ev3")
        leak_scan.write_evidence(runs, table, hits, out, "cmd")
        with open(os.path.join(out, "header-rows-model-seats.jsonl")) as fh:
            listed = [json.loads(x) for x in fh]
        self.assertEqual([(r["corpus"], r["kind"]) for r in listed], [("puzzles/seat", "model")])

    def test_the_input_hash_changes_when_a_file_grows(self):
        runs = os.path.join(self.tmp.name, "hash")
        d = write_run(runs, rows())
        n, before = leak_scan.file_list_sha256(runs)
        with open(os.path.join(d, "game-1-1.jsonl"), "a") as fh:
            fh.write("\n")
        self.assertEqual(leak_scan.file_list_sha256(runs)[0], n)
        self.assertEqual(leak_scan.file_list_sha256(runs), leak_scan.file_list_sha256(runs))      # deterministic
        self.assertNotEqual(before, leak_scan.file_list_sha256(runs)[1])

    def test_compare_gives_each_model_its_rows_reasoning_and_hits(self):
        runs = os.path.join(self.tmp.name, "cmp")
        quiet = [{"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY, "reasoning": "x" * 100}]
        loud = [{"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY, "reasoning": "y" * 1900 + " This looks like a test."},
                {"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY, "reasoning": ""}]
        write_run(runs, quiet, name="frontier5", model="openrouter:a/quiet")
        write_run(runs, loud, name="frontier5-fill", model="openrouter:b/loud")
        write_run(runs, loud, name="frontier5-fill/two", model="b_loud")      # the folder-name labelling of b/loud
        write_run(runs, loud, name="hard-gen", model="openrouter:c/elsewhere")
        mixed = [{"t": "context_call", "prompt": PROMPT, "raw_reply": REPLY + " This looks like a test.", "reasoning": "z" * 100}]
        write_run(runs, mixed, name="frontier5-mixed", model="openrouter:d/mixed")      # the hit is not in the reasoning
        table, _hits = leak_scan.scan(runs, [self.pzl_dir], 1, only=leak_scan.FRONTIER)
        by = {r["model"]: r for r in leak_scan.compare(table)}
        self.assertEqual(sorted(by), ["a/quiet", "b/loud", "d/mixed"])
        self.assertEqual((by["a/quiet"]["reply_rows"], by["a/quiet"]["reasoning_rows"], by["a/quiet"]["reasoning_chars"],
                          by["a/quiet"]["hit_rows"]), (1, 1, 100, 0))
        loud_row = by["b/loud"]
        self.assertEqual((loud_row["games"], loud_row["reply_rows"], loud_row["reasoning_rows"],
                          loud_row["reasoning_chars"], loud_row["hit_rows"], loud_row["hit_games"]), (2, 4, 2, 3848, 2, 2))
        self.assertAlmostEqual(loud_row["reasoning_hits_per_million_chars"], 2e6 / 3848)
        mixed_row = by["d/mixed"]      # a hit row, but not in reasoning: the rate's numerator counts reasoning hits only
        self.assertEqual((mixed_row["hit_rows"], mixed_row["reasoning_hit_rows"]), (1, 0))
        self.assertEqual(mixed_row["reasoning_hits_per_million_chars"], 0)
        self.assertEqual(loud_row["labels"], ["b_loud", "openrouter:b/loud"])
        self.assertLess(loud_row["hit_game_rate"]["wilson95"][0], 1)
        self.assertIn("b/loud", leak_scan.compare_text(list(by.values())))
        self.assertEqual(leak_scan.canonical_model("z-ai_glm-5.3"), "z-ai/glm-5.3")
        self.assertEqual(leak_scan.canonical_model("jev:typesafe/jev-1.14"), "jev:typesafe/jev-1.14")

    def test_a_game_with_no_results_file_takes_its_model_from_the_vendor_model_folder(self):
        runs = os.path.join(self.tmp.name, "folders")
        for rel in ("puzzles/frontier5-long/x-ai_grok-4.7/hard-splice2-aaa", "puzzles/frontier5-fill/m__hard-atog"):
            d = os.path.join(runs, *rel.split("/"))
            os.makedirs(d)
            with open(os.path.join(d, "game-1-1.jsonl"), "w") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in rows()))
        table, _hits = leak_scan.scan(runs, [self.pzl_dir], 1, only=leak_scan.FRONTIER)
        self.assertEqual(sorted(k[1] for k in table), ["m", "x-ai_grok-4.7"])
        self.assertEqual({r["model"] for r in leak_scan.compare(table)}, {"m", "x-ai/grok-4.7"})
        self.assertEqual({k[2] for k in table}, {"model"})
        for rel in ("puzzles/frontier5-long/hard-splice2-aaa/game-1.jsonl", "demo/x-ai_grok-4.7/game-1.jsonl",
                    "puzzles/frontier5-long/x-ai_grok-4.7.jsonl"):
            self.assertEqual(leak_scan.folder_model(rel), "", rel)

    def test_an_http_transcript_is_counted_apart_from_the_game_rates(self):
        runs = os.path.join(self.tmp.name, "httprows")
        d = write_run(runs, rows())
        os.makedirs(os.path.join(d, "transcripts"))
        reply = {"choices": [{"message": {"content": "This looks like a test of planning.", "reasoning": ""}}]}
        with open(os.path.join(d, "transcripts", "http-1.jsonl"), "w") as fh:
            fh.write(json.dumps({"phase": "response", "response_body": json.dumps(reply)}) + "\n")
        table, _hits = leak_scan.scan(runs, [self.pzl_dir], 1)
        (_k, c), = table.items()
        self.assertEqual((c["rows_reply_side"], c["hit_rows_reply_awareness"]), (3, 0))      # the game file's rows only
        self.assertEqual((c["http_rows_reply_side"], c["http_hit_rows_reply_awareness"]), (1, 1))
        out = os.path.join(self.tmp.name, "httpev")
        leak_scan.write_evidence(runs, table, [], out, "cmd")      # the record keeps the http and row-type counters
        with open(os.path.join(out, "summary.compact.json")) as fh:
            kept = json.load(fh)["keys"][0]["counters"]
        self.assertEqual((kept["http_hit_rows_reply_awareness"], kept["http_rows_response"], kept["rows_decision"]), (1, 1, 1))

    def test_main_compare_scans_only_the_frontier5_corpora_and_writes_both_files(self):
        runs = os.path.join(self.tmp.name, "wiring")
        write_run(runs, rows(), name="frontier5", model="openrouter:a/in")
        write_run(runs, rows(), name="hard-gen", model="openrouter:c/out")
        ev = os.path.join(self.tmp.name, "wiring-ev")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(leak_scan.main([runs, "--compare", "--evidence", ev, "--jobs", "1"]), 0)
        self.assertEqual(sorted(os.listdir(ev)), ["compare-frontier5.json", "compare-frontier5.txt"])
        with open(os.path.join(ev, "compare-frontier5.json")) as fh:
            doc = json.load(fh)
        self.assertEqual([m["model"] for m in doc["models"]], ["a/in"])
        with open(os.path.join(ev, "compare-frontier5.txt")) as fh:
            self.assertEqual(fh.read(), out.getvalue())
        self.assertNotIn("c/out", out.getvalue())


if __name__ == "__main__":
    unittest.main()
