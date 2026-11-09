#!/usr/bin/env python3
"""eval/research_stats.py on board and game rows built here."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import research_stats as rs  # noqa: E402

OPTS = ["A", "B", "C", "PASS"]


def scratch(test):
    """A temporary folder removed when `test` finishes."""
    path = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, path, True)
    return path


def decision(choice, reply="", options=OPTS, fallback=None):
    return {"t": "decision", "options": options, "choice": str(choice), "raw_reply": reply, "fallback": fallback}


def board(path, **extra):
    row = {"source_game": path, "regret_k": 1,
           "floor": {"options": {"A": False, "B": False, "C": True, "PASS": True}, "model_label": "B"},
           "positions": [{"k": 0, "winnable": True}, {"k": 1, "winnable": True}, {"k": 2, "winnable": False}]}
    row.update(extra)
    return row


class TestResearchStats(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)
        self.game = os.path.join(self.dir, "game.jsonl")
        rows = [decision(3, "WHY: set up the win\nCHOICE: 4"), decision(1, "WHY: B\nCHOICE: 2"),
                decision(0, "WHY: this is lethal, so cast A"), decision(0, "WHY: x", fallback="forced")]
        with open(self.game, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_menu_counts_a_step_by_hand(self):
        out = rs.menu([board(self.game)])
        # B (position 1) is the earliest of the losing options A, B; kept are C and PASS (positions 2, 3)
        self.assertEqual((out["steps"], out["earliest_losing"], out["before_every_kept"], out["top_pick"]),
                         (1, 0, 1, 0))
        self.assertEqual((out["earliest_losing_chance"], out["before_every_kept_chance"], out["top_pick_chance"]),
                         (0.5, 1.0, 0.25))
        self.assertEqual((out["pick_pos"], out["kept_pos"]), (0.33, 0.83))

    def test_menu_counts_unmatched_and_missing_games(self):
        no_match = board(self.game, regret_k=1)
        no_match["floor"] = dict(no_match["floor"], model_label="C")
        out = rs.menu([no_match, board(os.path.join(self.dir, "gone.jsonl")), {"floor": None}])
        self.assertEqual((out["steps"], out["unmatched"], out["game_missing"]), (0, 1, 1))

    def test_loss_awareness_joins_the_kth_non_auto_row(self):
        out = rs.loss_awareness([board(self.game)])
        # k=0 "set up the win" (no claim), k=1 "B", k=2 "this is lethal" in a lost position
        self.assertEqual(out["winnable"], [2, 0, 0])
        self.assertEqual(out["lost"], [1, 1, 0])
        self.assertEqual((out["attempts"], out["failed_with_regret_step"], out["never_concede_after_step"]), (1, 1, 1))

    def test_a_concession_after_the_step_is_counted(self):
        rows = [decision(0, "WHY: a"), decision(1, "WHY: b"), decision(0, "WHY: it is impossible now")]
        with open(self.game, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        out = rs.loss_awareness([board(self.game)])
        self.assertEqual((out["lost"], out["never_concede_after_step"]), ([1, 0, 1], 0))

    def test_the_note_quotes_the_scripts_patterns(self):
        note = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research",
                            "2026-11-06-loss-awareness-regex-fails.md")
        with open(note, encoding="utf-8") as f:
            text = f.read()
        for pattern in (rs.CLAIMS_WIN, rs.CONCEDES):
            self.assertIn("`" + pattern.pattern + "`", text)

    def test_usage_error(self):
        self.assertEqual(rs.main([]), 2)


def tool_row(request, result, seat="Solver"):
    return {"t": "context_tool", "seat": seat, "request": request, "result": result}


def ask_row(via, invalid, kind="play", **extra):
    row = {"t": "decision", "seat": "Solver", "type": kind, "choice": 1, "answer_via": via, "answer_invalid": invalid}
    row.update(extra)
    return row


ANSWER = 'TOOL CALL answer {"option": 1}'
NOT_ON_LIST = "ERROR: option 0 is not on the list; the options are 1-2. Nothing was done. Call `answer` again."
WRONG_FIELD = "ERROR: this decision is answered with `option`; you gave lines, why. Nothing was done."
NUDGE = "NUDGE: no `answer` call; asked for one in the same conversation."
LOST = "LOST: no `answer` call after 2 reminders."


def write_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")


class TestFailureClasses(unittest.TestCase):
    """Each message the AnswerTool.validate source can write lands in one class; the texts are the source's."""

    CASES = {
        "index": ["ERROR: option 0 is not on the list; the options are 1-2. Nothing was done.",
                  "ERROR: blocker 3 is not on the list; your blockers are 1-2. Nothing was done.",
                  "ERROR: 9 is out of range; choose from 1 through 5. Nothing was done.",
                  "ERROR: step 4 is not a CAST/ACTIVATE option of this list. Nothing was done."],
        "type": ["ERROR: the arguments are not a JSON object. Nothing was done.",
                 "ERROR: the `option` value must be one integer from the list. Nothing was done.",
                 "ERROR: the `keep` value must be true or false. Nothing was done.",
                 "ERROR: the `blocks` value must be a list (empty for no blocks). Nothing was done.",
                 "ERROR: the `yield` value is none, end-of-turn or my-next-turn. Nothing was done."],
        "count": ["ERROR: pick exactly 1 options; you picked 0. Nothing was done.",
                  "ERROR: give exactly 3 amounts, one per target in order; you gave 2. Nothing was done.",
                  "ERROR: the amounts must sum to 4; yours sum to 5. Nothing was done.",
                  "ERROR: option 6 cancels the play and must be the only number. Nothing was done.",
                  "ERROR: each chosen target must get at least 1 (CR 601.2d). Nothing was done.",
                  "ERROR: each option may be picked once. Nothing was done.",
                  "ERROR: blocker 1 blocks attacker 3 twice. Nothing was done."],
        "schema": [WRONG_FIELD, "ERROR: unknown field `notes`. Nothing was done.",
                   "ERROR: FORMAT: this play targets: give its target in `targets`, a handle. Nothing was done.",
                   "ERROR: FORMAT: this decision is answered with `action`, the play you propose (you gave option)."],
        "engine": ["ERROR: ILLEGAL (timing): Lava Spike @2 can be cast only when you could cast a sorcery.",
                   "ERROR: these blocks are not legal together: Kithkin Soldier Token (127) cannot be blocked.",
                   "ERROR: blocker 1 (Baithook Angler 2/1 @16) cannot block attacker 3 (Priest 1/3 @22)."],
        "no_call": [NUDGE, LOST],
        "other": ["ERROR: FORMAT: no object @16 is in view now; use a handle from the state above.",
                  "ERROR: something nobody wrote a message for"],
    }

    def test_every_message_lands_in_its_class(self):
        for want, texts in self.CASES.items():
            for text in texts:
                self.assertEqual(rs.classify_failure(text), want, text)

    def test_the_classes_the_decision_rule_calls_shape_are_four(self):
        self.assertEqual(set(rs.SHAPE), {"index", "type", "count", "schema"})


class TestPatternsAreTheSource(unittest.TestCase):
    def test_every_fragment_is_a_string_in_the_spike_package_sources(self):
        """The messages come from AnswerTool.validate and the engine-side checks in LlmSpikeGui.java."""
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "forge-overlay",
                              "forge-gui-desktop", "src", "main", "java", "forge", "spike")
        source = ""
        for name in ("AnswerTool.java", "LlmSpikeGui.java"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                source += f.read()
        for name, fragments in rs.FAILURE_PATTERNS:
            for fragment in fragments:
                self.assertIn(fragment, source, "%s: %r is no longer in the sources" % (name, fragment))


class TestFailedDecisions(unittest.TestCase):
    def failed(self, *rows):
        return rs.failed_decisions(list(rows))

    def test_a_clean_answer_is_not_a_failure(self):
        self.assertEqual(self.failed(tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 0)), [])

    def test_a_decision_without_the_field_is_not_asked_by_tool(self):
        self.assertEqual(self.failed({"t": "decision", "type": "play"}), [])

    def test_lost_with_no_call_is_caused_by_no_call(self):
        out = self.failed(tool_row("prose", NUDGE), tool_row("prose", NUDGE), tool_row("prose", LOST),
                          ask_row("lost", 0, attempts=3))
        self.assertEqual([(f["outcome"], f["cause"]) for f in out], [("lost", "no_call")])

    def test_lost_after_two_invalid_calls_takes_the_last_error(self):
        out = self.failed(tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, WRONG_FIELD), ask_row("lost", 2))
        self.assertEqual([(f["outcome"], f["cause"], f["events"]) for f in out],
                         [("lost", "schema", ["index", "schema"])])

    def test_a_corrected_call_is_an_invalid_one_that_then_answered(self):
        out = self.failed(tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 1))
        self.assertEqual([(f["outcome"], f["cause"]) for f in out], [("corrected", "index")])

    def test_a_nudged_answer_is_caused_by_no_call(self):
        out = self.failed(tool_row("prose", NUDGE), tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("nudged", 0))
        self.assertEqual([(f["outcome"], f["cause"]) for f in out], [("nudged", "no_call")])

    def test_a_lookup_error_is_not_an_answer_error(self):
        out = self.failed(tool_row("TOOL CALL memory_read {}", "ERROR: tool budget exhausted"),
                          tool_row(ANSWER, NOT_ON_LIST), ask_row("tool", 1))
        self.assertEqual(out[0]["events"], ["index"])

    def test_a_failure_with_no_recorded_text_is_unrecorded_not_guessed(self):
        out = self.failed(ask_row("lost", 2), ask_row("tool", 1))
        self.assertEqual([f["cause"] for f in out], ["unrecorded", "unrecorded"])

    def test_messages_belong_to_the_next_decision_only(self):
        out = self.failed(tool_row(ANSWER, NOT_ON_LIST), ask_row("tool", 1), ask_row("tool", 1))
        self.assertEqual([f["cause"] for f in out], ["index", "unrecorded"])


class TestRunContext(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)

    def test_a_game_line_names_the_seat_model_and_epoch(self):
        path = os.path.join(self.dir, "g", "game-1.jsonl")
        write_rows(path, [{"t": "game", "smart_a": "openrouter:vendor/m", "harness_epoch": "harness-epoch-9"}])
        self.assertEqual(rs.run_context(path), ("vendor/m", "harness-epoch-9"))

    def test_a_puzzle_folder_names_them_through_its_results_file(self):
        path = os.path.join(self.dir, "p", "game-1.jsonl")
        write_rows(path, [ask_row("tool", 0)])
        write_rows(os.path.join(self.dir, "p", "puzzle-results.jsonl"),
                   [{"solver_model": "vendor/m", "harness_epoch": "harness-epoch-7"}])
        self.assertEqual(rs.run_context(path), ("vendor/m", "harness-epoch-7"))

    def test_a_manifest_names_the_model_when_there_is_no_results_file(self):
        path = os.path.join(self.dir, "p", "run", "game-1.jsonl")
        write_rows(path, [ask_row("tool", 0)])
        with open(os.path.join(self.dir, "p", "run", "batch-manifest.json"), "w") as f:
            json.dump({"condition": {"smart_a": "openrouter:vendor/m"}}, f)
        self.assertEqual(rs.run_context(path), ("vendor/m", "unknown"))

    def test_a_manifest_in_the_parent_folder_names_the_model(self):
        path = os.path.join(self.dir, "p", "run", "game-1.jsonl")
        write_rows(path, [ask_row("tool", 0)])
        with open(os.path.join(self.dir, "p", "batch-manifest.json"), "w") as f:
            json.dump({"condition": {"smart_a": "openrouter:vendor/m"}}, f)
        self.assertEqual(rs.run_context(path), ("vendor/m", "unknown"))
        self.assertEqual(rs.run_context(os.path.join(self.dir, "p", "elsewhere", "run", "game-1.jsonl")),
                         ("unknown", "unknown"))      # a grandparent's manifest is not read

    def test_two_models_in_one_folder_are_not_guessed(self):
        path = os.path.join(self.dir, "p", "game-1.jsonl")
        write_rows(path, [ask_row("tool", 0)])
        write_rows(os.path.join(self.dir, "p", "puzzle-results.jsonl"),
                   [{"solver_model": "a", "harness_epoch": "e1"}, {"solver_model": "b", "harness_epoch": "e1"}])
        self.assertEqual(rs.run_context(path), ("mixed", "e1"))

    def test_a_folder_that_says_nothing_is_unknown(self):
        path = os.path.join(self.dir, "x", "game-1.jsonl")
        write_rows(path, [ask_row("tool", 0)])
        self.assertEqual(rs.run_context(path), ("unknown", "unknown"))

    def test_the_propose_arm_is_read_from_the_manifest_or_the_game_line(self):
        a = os.path.join(self.dir, "a", "run", "game-1.jsonl")
        write_rows(a, [ask_row("tool", 0)])
        with open(os.path.join(self.dir, "a", "run", "batch-manifest.json"), "w") as f:
            json.dump({"condition": {"smart_a": "x", "env": {"PROPOSE_ACTIONS": "1"}}}, f)
        b = os.path.join(self.dir, "b", "game-1.jsonl")
        write_rows(b, [{"t": "game", "smart_a": "x", "propose_actions": "True"}])
        c = os.path.join(self.dir, "c", "game-1.jsonl")
        write_rows(c, [{"t": "game", "smart_a": "x", "propose_actions": "False"}])
        self.assertEqual([rs.run_arm(p) for p in (a, b, c)], ["propose", "propose", "menu"])

    def test_a_population_never_pools_arms_lanes_or_fixtures(self):
        self.assertEqual(rs.population("vendor/m", "menu"), "menu")
        self.assertEqual(rs.population("vendor/m", "propose"), "propose")
        self.assertEqual(rs.population("claude-cli:opus", "menu"), "dev-lane")
        self.assertEqual(rs.population("openrouter:fake/harness-model", "propose"), "fixtures")
        for retired in ("opus", "haiku", "sonnet"):      # the retired bare-name Claude CLI text lane
            self.assertEqual(rs.population(retired, "menu"), "dev-lane")

    def test_fixtures_are_not_models(self):
        for spec in ("openrouter:fake/harness-model", "script:/tmp/x", "greedy:", "random", "unknown", "mixed", ""):
            self.assertFalse(rs.is_model(spec), spec)
        for spec in ("openrouter:vendor/m", "vendor/m", "claude-cli:opus"):
            self.assertTrue(rs.is_model(spec), spec)


class TestInvalidAnswers(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)
        # model m1, epoch e1: 3 tool answers, one corrected (index), one lost to schema errors, one lost to no call
        write_rows(os.path.join(self.dir, "a", "game-1.jsonl"), [
            {"t": "game", "smart_a": "openrouter:v/m1", "harness_epoch": "e1"},
            tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 0),
            tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 1, kind="block"),
            tool_row(ANSWER, WRONG_FIELD), tool_row(ANSWER, WRONG_FIELD), ask_row("lost", 2, kind="block"),
            tool_row("p", NUDGE), tool_row("p", NUDGE), tool_row("p", LOST), ask_row("lost", 0)])
        # the propose arm and the Claude CLI lane are separate populations
        write_rows(os.path.join(self.dir, "c", "game-1.jsonl"), [
            {"t": "game", "smart_a": "openrouter:v/m1", "harness_epoch": "e1", "propose_actions": "True"},
            tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 1)])
        write_rows(os.path.join(self.dir, "d", "game-1.jsonl"), [
            {"t": "game", "smart_a": "claude-cli:opus", "harness_epoch": "e1"},
            tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, "ANSWER ACCEPTED"), ask_row("tool", 1)])
        # a fixture: one lost decision to no call, which the model tables must not count
        write_rows(os.path.join(self.dir, "b", "game-1.jsonl"), [
            {"t": "game", "smart_a": "openrouter:fake/harness-model", "harness_epoch": "e1"},
            tool_row("p", NUDGE), tool_row("p", NUDGE), tool_row("p", LOST), ask_row("lost", 0),
            ask_row("tool", 0)])
        self.report = rs.invalid_answers([self.dir])

    def test_the_menu_population_excludes_the_fixture_the_propose_arm_and_the_dev_lane(self):
        pop = self.report["populations"]["menu"]
        self.assertEqual((pop["asked"], pop["lost"], pop["corrected"], pop["nudged"]), (4, 2, 1, 0))
        pops = self.report["populations"]
        self.assertEqual((pops["fixtures"]["lost"], pops["propose"]["corrected"], pops["dev-lane"]["corrected"]),
                         (1, 1, 1))

    def test_lost_causes_and_shape_share_with_interval(self):
        pop = self.report["populations"]["menu"]
        self.assertEqual(pop["lost_causes"], {"schema": 1, "no_call": 1})
        self.assertEqual(pop["lost_shape"], 1)
        lo, hi = pop["lost_shape_ci"]
        self.assertAlmostEqual(lo, 0.0945, places=3)   # Wilson, 1 of 2
        self.assertAlmostEqual(hi, 0.9055, places=3)

    def test_breakdowns_carry_asked_and_lost(self):
        by = self.report["by_model"]["v/m1"]
        self.assertEqual((by["asked"], by["lost"], by["failed"]), (4, 2, 3))
        self.assertAlmostEqual(by["failed_rate_ci"][0], 0.3006, places=3)   # Wilson, 3 of 4
        self.assertAlmostEqual(by["failed_rate_ci"][1], 0.9544, places=3)
        self.assertEqual(self.report["by_epoch"]["e1"]["lost"], 2)
        self.assertEqual(self.report["by_type"]["block"]["failed"], 2)
        self.assertEqual((self.report["by_type"]["block"]["asked"], self.report["by_type"]["play"]["asked"]), (2, 2))
        self.assertEqual(sum(t["asked"] for t in self.report["by_type"].values()), by["asked"])

    def lost_game(self, root, name, epoch, shape, no_call):
        """A one-model game with `shape` decisions lost to schema errors and `no_call` lost to no call."""
        game = {"t": "game", "smart_a": "openrouter:v/m1"}
        if epoch:
            game["harness_epoch"] = epoch
        rows = [game, ask_row("tool", 0)]
        for _ in range(shape):
            rows += [tool_row(ANSWER, WRONG_FIELD), tool_row(ANSWER, WRONG_FIELD), ask_row("lost", 2)]
        for _ in range(no_call):
            rows += [tool_row("p", LOST), ask_row("lost", 0)]
        write_rows(os.path.join(root, name, "game-1.jsonl"), rows)

    def verdict(self, shape, no_call, epoch="e1"):
        d = scratch(self)
        self.lost_game(d, "x", epoch, shape, no_call)
        return rs.invalid_answers([d])["verdict"]["per_epoch"][epoch or "unknown"]

    def test_the_rule_follows_the_point_share_and_more_than_half_is_strict(self):
        at_ten = self.verdict(6, 4)
        self.assertEqual((at_ten["lost"], at_ten["shape_share"], at_ten["build_schema"]), (10, 0.6, True))
        self.assertFalse(self.verdict(5, 5)["build_schema"])      # exactly half
        self.assertTrue(self.verdict(10, 0)["build_schema"])

    def test_too_few_lost_decisions_give_no_answer_not_a_false(self):
        for shape, no_call, note in ((9, 0, "too few lost decisions (9 < 10)"), (0, 0, "no lost decisions"),
                                     (2, 0, "too few lost decisions (2 < 10)")):
            v = self.verdict(shape, no_call)
            self.assertIsNone(v["build_schema"], (shape, no_call))
            self.assertIn(note, v["note"])
            self.assertIn("rule not applicable", v["note"])
        self.assertEqual(self.report["verdict"]["per_epoch"]["e1"]["lost"], 2)
        self.assertIsNone(self.report["verdict"]["per_epoch"]["e1"]["build_schema"])   # setUp: 2 lost

    def test_an_epoch_that_is_unknown_is_pooled_and_refuses_a_verdict(self):
        d = scratch(self)
        self.lost_game(d, "puzzles/bench-v1/r1", None, 10, 0)
        self.lost_game(d, "demo/luna-x", None, 0, 0)
        out = rs.invalid_answers([d])
        row = out["by_epoch"]["unknown"]
        self.assertEqual((row["pooled"], row["epochs"], row["lost"]), (True, ["unknown"], 10))
        self.assertEqual(row["sources"], {"demo/luna-x": 1, "puzzles/bench-v1": 1})
        verdict = out["verdict"]["per_epoch"]["unknown"]
        self.assertIsNone(verdict["build_schema"])      # 10 of 10 shape, but the epoch is not one condition
        self.assertIn("epoch unknown", verdict["note"])

    def test_one_epoch_pooled_row_is_the_epoch_row(self):
        d = scratch(self)
        self.lost_game(d, "x", "e1", 10, 0)
        out = rs.invalid_answers([d])
        pooled = out["verdict"]["pooled"]
        self.assertEqual((out["populations"]["menu"]["pooled"], pooled["build_schema"]), (False, True))
        self.assertEqual(pooled, out["verdict"]["per_epoch"]["e1"])
        self.assertEqual(out["populations"]["menu"]["epochs"], ["e1"])

    def test_two_epochs_print_per_epoch_rows_and_the_pooled_row_refuses_a_verdict(self):
        d = scratch(self)
        self.lost_game(d, "x", "e1", 10, 0)
        self.lost_game(d, "y", "e2", 0, 0)
        out = rs.invalid_answers([d])
        menu = out["populations"]["menu"]
        self.assertEqual((menu["pooled"], menu["epochs"], menu["lost"]), (True, ["e1", "e2"], 10))
        self.assertEqual(out["by_epoch"]["e1"]["pooled"], False)
        self.assertEqual((out["by_epoch"]["e1"]["lost"], out["by_epoch"]["e2"]["lost"]), (10, 0))
        self.assertTrue(out["verdict"]["per_epoch"]["e1"]["build_schema"])
        self.assertIsNone(out["verdict"]["per_epoch"]["e2"]["build_schema"])
        self.assertIsNone(out["verdict"]["pooled"]["build_schema"])
        self.assertIn("pooled over epochs", out["verdict"]["pooled"]["note"])

    def test_a_log_naming_no_one_model_is_unattributed_not_a_fixture(self):
        d = scratch(self)
        write_rows(os.path.join(d, "u", "game-1.jsonl"), [ask_row("lost", 0)])
        out = rs.invalid_answers([d])
        self.assertEqual((out["populations"]["unattributed"]["lost"], out["populations"]["fixtures"]["lost"]), (1, 0))
        self.assertEqual(rs.population("mixed", "menu"), "unattributed")

    def test_a_nudged_decision_then_invalid_call_counts_as_corrected(self):
        rows = [tool_row("p", NUDGE), tool_row(ANSWER, NOT_ON_LIST), tool_row(ANSWER, "ANSWER ACCEPTED"),
                ask_row("nudged", 1)]
        out = rs.failed_decisions(rows)
        self.assertEqual((out[0]["outcome"], out[0]["cause"]), ("corrected", "index"))   # no_call is a floor

    def test_a_missing_folder_is_counted_not_fatal(self):
        out = rs.invalid_answers([os.path.join(self.dir, "none")])
        self.assertEqual(out["files"], 0)


def puzzle(pid, random_rate, family="f", n=20, status="active", flags=(), **arms):
    base = {"random": {"solved": round(random_rate * n), "n": n, "rate": random_rate}}
    for arm, solved in arms.items():
        base[arm] = {"solved": solved, "n": 4, "rate": solved / 4}
    return {"id": pid, "status": status, "flags": list(flags), "source": {"family": family}, "baselines": base}


class TestPuzzleBands(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)

    def suite(self, name, puzzles):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            json.dump({"suite": name, "puzzles": puzzles}, f)
        return path

    def test_bands_split_at_zero_and_twenty_percent(self):
        self.assertEqual([rs.band(r) for r in (0.0, 0.05, 0.2, 0.21, 1.0)],
                         ["unreachable", "live", "live", "easy", "easy"])

    def test_counts_by_family_with_wilson_intervals(self):
        path = self.suite("puzzles-hard-gen-v1.json", [
            puzzle("a", 0.0, "drain", first=0, greedy=0, ai=0), puzzle("b", 0.0, "drain", first=4, greedy=0, ai=0),
            puzzle("c", 0.1, "drain", first=0, greedy=0, ai=0), puzzle("d", 0.5, "drain", status="dropped",
                                                                       flags=["random_above_threshold"]),
            puzzle("e", 0.0, "drain", status="dropped", flags=["unscoreable", "no_verified_line"])])
        out = rs.puzzle_bands([path])
        row = out["families"]["gen/drain"]
        self.assertEqual((row["n"], row["unreachable"], row["live"], row["easy"], row["unscoreable"]), (4, 2, 1, 1, 1))
        self.assertEqual(row["trivial_any"], 1)       # b: the first-option arm solved it
        self.assertEqual((row["active"], row["active_trivial_any"]), (3, 1))   # d is dropped, e unscoreable
        self.assertEqual(row["random_rollouts"], [20, 20])
        lo, hi = row["unreachable_ci"]
        self.assertAlmostEqual(lo, 0.150, places=2)    # Wilson, 2 of 4
        self.assertAlmostEqual(hi, 0.850, places=2)

    def test_a_suite_row_pools_its_families_and_hard_pools_the_three_suites(self):
        gen = self.suite("puzzles-hard-gen-v1.json", [puzzle("a", 0.0, "drain", first=0, greedy=0, ai=0)])
        gen2 = self.suite("puzzles-hard-gen2-v1.json", [puzzle("b", 0.05, "copy", first=0, greedy=0, ai=0)])
        rules = self.suite("puzzles-rules-sample-v1.json", [puzzle("c", 0.0, None, first=0, ai=0)])
        out = rs.puzzle_bands([gen, gen2, rules])
        self.assertEqual(out["suites"]["gen"]["n"], 1)
        self.assertEqual(out["suites"]["hard-*"]["n"], 2)
        self.assertEqual(out["suites"]["rules"]["n"], 1)
        self.assertNotIn("rules/None", out["families"])   # a suite with no families has only its suite row

    def test_an_arm_the_suite_lacks_is_not_a_zero(self):
        path = self.suite("puzzles-rules-sample-v1.json", [puzzle("c", 0.0, None, first=4, ai=0)])
        row = rs.puzzle_bands([path])["suites"]["rules"]
        self.assertEqual(row["arms"]["first"], 1)
        self.assertNotIn("greedy", row["arms"])
        self.assertEqual(row["trivial_any"], 1)

    def test_trivial_share_is_over_the_active_puzzles_a_dropped_easy_one_is_not_scored(self):
        path = self.suite("puzzles-forge-builtin-v1.json", [
            puzzle("a", 0.0, None, ai=0), puzzle("b", 0.9, None, status="dropped", ai=4)])
        row = rs.puzzle_bands([path])["suites"]["forge-builtin"]
        self.assertEqual((row["trivial_any"], row["active_trivial_any"], row["active"]), (1, 0, 1))
        self.assertEqual(row["label"], "not saturated by zero-model arms")

    def test_the_label_rule_uses_the_stated_thresholds(self):
        ok, bad = "not saturated by zero-model arms", "saturated by zero-model arms"
        row = {"n": 20, "active": 20, "active_trivial_any": 2, "live": 5}
        self.assertEqual(rs.informative(row), (ok, True))                  # both thresholds exactly at their edge
        self.assertEqual(rs.informative(dict(row, active_trivial_any=3)), (bad, True))     # 15% > 10%
        self.assertEqual(rs.informative(dict(row, live=4)), (ok, False))                   # 20% < 25%
        big = {"n": 100, "active": 100, "active_trivial_any": 10, "live": 25}
        self.assertEqual(rs.informative(big), (ok, True))
        self.assertEqual(rs.informative(dict(big, active_trivial_any=11, live=24)), (bad, False))   # one over, one under
        self.assertEqual(rs.informative(dict(row, active_trivial_any=3, live=4)), (bad, False))

    def test_a_dropped_puzzle_counts_in_trivial_any_but_not_in_arms(self):
        path = self.suite("puzzles-forge-builtin-v1.json", [
            puzzle("a", 0.0, None, first=4, ai=0), puzzle("b", 0.9, None, status="dropped", first=4, ai=4),
            puzzle("c", 0.0, None, first=0, ai=0)])
        row = rs.puzzle_bands([path])["suites"]["forge-builtin"]
        self.assertEqual(row["arms"], {"first": 1, "ai": 0})              # b is dropped: not an active solve
        self.assertEqual((row["trivial_any"], row["active_trivial_any"], row["active"]), (2, 1, 2))


if __name__ == "__main__":
    unittest.main()
