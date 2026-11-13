"""harness_env_grade.py: the v0 grader scores a perfect record 1, an empty overlay 0, and each break where it lands.

The evidence here is synthetic but has the acceptance OUT layout the grader reads (summary.txt, surfaces.tsv,
testng/testng-results.xml, javatests.log). Each test removes one thing a perfect harness has and asserts the score
drops in the component that thing belongs to, and only there.
"""
import collections
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import fetch_comp_rules
import harness_env_grade as hg
import harness_env_task_grade as htg
import surface_coverage

REPO = str(evalpath.REPO)
LEDGER = os.path.join(REPO, "docs", "harness", "controller-ownership.tsv")
TESTS = os.path.join(REPO, "forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "spike")
PLAYS = ["play:alt:Flashback", "play:opt:Kicker1"]


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def results_xml(classes):
    """classes: {simple name: [status, ...]} -> a TestNG results file, with one config method per class."""
    body = []
    for cls, statuses in classes.items():
        body.append('<class name="forge.spike.%s">' % cls)
        body.append('<test-method is-config="true" name="init" status="PASS"></test-method>')
        for i, s in enumerate(statuses):
            body.append('<test-method name="t%d" status="%s"></test-method>' % (i, s))
        body.append("</class>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<testng-results total="1" passed="1" failed="0" skipped="0">'
            "<suite><test>%s</test></suite></testng-results>\n" % "".join(body))


class Evidence:
    """A perfect acceptance OUT for the hidden classes; each test breaks one part."""

    def __init__(self, root):
        self.root = root
        self.hooks = sorted(surface_coverage.decision_hooks(LEDGER))
        self.asked = set(self.hooks)
        self.played = set(PLAYS)
        self.classes = {c: ["PASS", "PASS"] for c in hg.hidden_classes(TESTS)}
        self.compile = "PASS compile: 172 classes"
        self.ownership = "[OWNERSHIP] silent Forge-AI strategic/payment hooks reachable in Legacy play: 0"

    def write(self):
        write(self.root, "summary.txt", "PASS preflight: x\n%s\n" % self.compile)
        rows = ["%s\tasked" % h for h in sorted(self.asked)] + ["known\t%s" % p for p in PLAYS]
        rows += ["%s\tasked" % p for p in sorted(self.played)]
        write(self.root, "surfaces.tsv", "\n".join(rows) + "\n")
        write(self.root, "testng/testng-results.xml", results_xml(self.classes))
        write(self.root, "javatests.log", "noise\n%s\n" % self.ownership)
        return self.root


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ev = Evidence(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def score(self):
        return hg.score_evidence(self.ev.write(), LEDGER, hg.hidden_classes(TESTS))

    def test_a_perfect_record_scores_one(self):
        s = self.score()
        self.assertEqual(s["score"], 1.0, s["reasons"])
        self.assertEqual(s["reasons"], [])

    def test_an_unasked_hook_costs_only_its_family(self):
        self.ev.asked.discard("declareBlockers")
        s = self.score()
        self.assertEqual(s["families"]["combat"]["unasked"], ["declareBlockers"])
        self.assertLess(s["components"]["hooks"], 1.0)
        self.assertEqual(s["components"]["tests"], 1.0)
        self.assertTrue(all(f["asked"] == f["hooks"] for k, f in s["families"].items() if k != "combat"))

    def test_a_whole_family_unasked_costs_more_than_one_hook_elsewhere(self):
        one = Evidence(self.tmp)
        one.asked.discard("chooseColor")
        a = hg.score_evidence(one.write(), LEDGER, hg.hidden_classes(TESTS))["score"]
        whole = Evidence(self.tmp)
        whole.asked -= {h for h in whole.hooks if hg.family(h) == "combat"}
        b = hg.score_evidence(whole.write(), LEDGER, hg.hidden_classes(TESTS))["score"]
        self.assertLess(b, a)

    def test_an_unplayed_way_to_play_costs_the_plays_component(self):
        self.ev.played.discard("play:opt:Kicker1")
        self.assertEqual(self.score()["components"]["plays"], 0.5)

    def test_a_failed_hidden_test_costs_the_tests_component(self):
        first = sorted(self.ev.classes)[0]
        self.ev.classes[first] = ["PASS", "FAIL"]
        s = self.score()
        self.assertLess(s["components"]["tests"], 1.0)
        self.assertEqual(s["tests"]["classes"][first], {"passed": 1, "total": 2})

    def test_a_hidden_class_that_never_ran_counts_as_failed(self):
        gone = sorted(self.ev.classes)[0]
        del self.ev.classes[gone]
        s = self.score()
        self.assertIn(gone, s["tests"]["missing"])
        self.assertLess(s["components"]["tests"], 1.0)

    def test_a_failure_in_a_class_the_grader_does_not_run_is_ignored(self):
        self.ev.classes["DemoFeedTest"] = ["FAIL"]
        self.assertEqual(self.score()["score"], 1.0)

    def test_a_failed_compile_scores_zero_with_the_reason(self):
        self.ev.compile = "FAIL compile: javac rc=1"
        s = self.score()
        self.assertEqual(s["score"], 0.0)
        self.assertTrue(any("compile" in r for r in s["reasons"]))

    def test_a_seat_that_leaves_a_decision_to_forge_ai_scores_zero(self):
        self.ev.ownership = "[OWNERSHIP] silent Forge-AI strategic/payment hooks reachable in Legacy play: 3"
        s = self.score()
        self.assertEqual(s["score"], 0.0)
        self.assertFalse(s["gates"]["no_forge_ai"])

    def test_no_proof_of_no_forge_ai_is_a_failed_gate(self):
        self.ev.ownership = "nothing"
        self.assertEqual(self.score()["score"], 0.0)

    def test_an_empty_evidence_directory_scores_zero(self):
        s = hg.score_evidence(tempfile.mkdtemp(dir=self.tmp), LEDGER, hg.hidden_classes(TESTS))
        self.assertEqual(s["score"], 0.0)


class CalibrationTest(unittest.TestCase):
    """The benchmark number is gated per family (owner, 2026-10-22); the additive credit is the dense reward only."""

    def test_every_family_at_its_ceiling_is_one_both_ways(self):
        c = hg.calibrated({f: 1.0 for f in hg.FAMILIES})
        self.assertEqual((c["benchmark"], c["dense"]), (1.0, 1.0))

    def test_one_family_at_zero_halves_the_benchmark_and_barely_moves_the_dense_reward(self):
        s = {f: 1.0 for f in hg.FAMILIES}
        s["combat"] = 0.0
        c = hg.calibrated(s)
        self.assertAlmostEqual(c["dense"], 8 / 9, places=6)
        self.assertAlmostEqual(c["benchmark"], hg.FLOOR ** (1 / 9), places=6)
        self.assertLess(c["benchmark"], 0.5)
        self.assertEqual(c["families_at_zero"], ["combat"])

    def test_a_seat_that_scores_only_on_play_is_near_zero(self):
        s = {f: 0.0 for f in hg.FAMILIES}
        s["play"] = 1.0
        self.assertLess(hg.calibrated(s)["benchmark"], 0.01)

    def test_partial_credit_in_a_family_still_moves_the_benchmark(self):
        s = {f: 1.0 for f in hg.FAMILIES}
        s["combat"] = 0.5
        self.assertTrue(hg.calibrated(s)["benchmark"] < 1.0)
        self.assertTrue(hg.calibrated(s)["benchmark"] > hg.calibrated(dict(s, combat=0.0))["benchmark"])

    def test_nothing_at_all_is_zero(self):
        self.assertEqual(hg.calibrated({})["benchmark"], 0.0)


class EvidenceCalibrationTest(unittest.TestCase):
    def test_an_acceptance_record_with_no_combat_ask_scores_far_below_ours(self):
        tmp = tempfile.mkdtemp()
        try:
            ev = Evidence(tmp)
            ev.asked -= {h for h in ev.hooks if hg.family(h) == "combat"}
            s = hg.score_evidence(ev.write(), LEDGER, hg.hidden_classes(TESTS))
            self.assertLess(s["score"], 0.5)
            self.assertGreater(s["dense"], 0.9)
        finally:
            shutil.rmtree(tmp)


REPORT_HEAD = "seat\tinstance\tfamily\thook\tlegal\treached\tmin_requests\tasked\toffered\tcarried\tshown\tnoparse\tpass\tai\tnote\n"


def report_row(seat, inst, fam, ok):
    flag = "1" if ok else "0"
    return "\t".join([seat, inst, fam, "h", "2", "2", "1", flag, flag, flag, flag, "1", flag, "0", ""]) + "\n"


class EnvReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        rows = []
        for fam in ("combat", "play", "costs"):
            for i in range(2):
                rows.append(report_row("ours", "%s.%d" % (fam, i), fam, True))
                rows.append(report_row("default", "%s.%d" % (fam, i), fam, fam == "play"))
        rows.append(report_row("ours", "combat.2", "combat", False))
        self.path = write(self.tmp, "env-grade.tsv", REPORT_HEAD + "".join(rows))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_the_table_counts_passes_per_seat_and_family(self):
        t = hg.env_table(hg.read_env_report(self.path))
        self.assertEqual(t["ours"]["combat"], {"passed": 2, "total": 3})
        self.assertEqual(t["default"]["play"], {"passed": 2, "total": 2})
        self.assertEqual(t["default"]["combat"], {"passed": 0, "total": 2})

    def test_the_default_bridge_scores_where_it_routes_and_nowhere_else(self):
        scores = hg.env_scores(hg.env_table(hg.read_env_report(self.path)))
        self.assertGreater(scores["ours"]["benchmark"], 0.5)
        self.assertLess(scores["default"]["benchmark"], 0.05)
        self.assertEqual(scores["default"]["families_at_zero"], ["combat", "costs"])
        self.assertEqual(scores["empty"]["benchmark"], 0.0)
        self.assertEqual(scores["empty"]["dense"], 0.0)

    def test_any_forge_ai_decision_gates_the_seats_benchmark_to_zero_but_not_its_dense_reward(self):
        rows = hg.read_env_report(self.path)
        rows.append(dict(seat="ours", instance="x", family="combat", hook="declareBlockers", ai="2", **{"pass": "0"}))
        gated = hg.ai_gated(rows)
        self.assertEqual(gated, {"ours"})
        scores = hg.env_scores(hg.env_table(rows), gated)
        self.assertEqual(scores["ours"]["benchmark"], 0.0)
        self.assertGreater(scores["ours"]["dense"], 0.5)
        self.assertTrue(scores["ours"]["forge_ai_gate"])

    def test_an_instance_with_one_legal_answer_can_cost_credit_but_never_earn_it(self):
        forced_pass = dict(seat="s", instance="a", family="combat", hook="declareBlockers", legal="1", **{"pass": "1"})
        forced_fail = dict(forced_pass, seat="t", **{"pass": "0"})
        choice_fail = dict(seat="s", instance="b", family="combat", hook="declareBlockers", legal="4", **{"pass": "0"})
        choice_pass = dict(choice_fail, seat="t", **{"pass": "1"})
        for by in ("hook", "surface"):
            t = hg.env_table([forced_pass, choice_fail, forced_fail, choice_pass], by=by)
            self.assertEqual(t["s"]["combat"], {"passed": 0, "total": 1}, "a forced pass does not keep combat off zero")
            self.assertEqual(t["t"]["combat"], {"passed": 1, "total": 2}, "an illegal answer where one was legal still costs")

    def test_payment_executor_entries_do_not_gate(self):
        rows = [dict(seat="ours", instance="x", family="costs", hook="payManaCost", ai="0", ai_payment="9", **{"pass": "1"})]
        self.assertEqual(hg.ai_gated(rows), set())

    def test_by_hook_an_instance_counts_in_its_hooks_ledger_family_not_the_surface_group(self):
        rows = [dict(seat="ours", family="combat", hook="chooseSpellAbilityToPlay", **{"pass": "1"}),
                dict(seat="ours", family="combat", hook="declareBlockers", **{"pass": "0"})]
        t = hg.env_table(rows, by="hook")
        self.assertEqual(t["ours"], {"play": {"passed": 1, "total": 1}, "combat": {"passed": 0, "total": 1}})

    def test_the_markdown_table_has_a_row_per_family_and_a_column_per_seat(self):
        text = hg.env_markdown(hg.env_table(hg.read_env_report(self.path)))
        self.assertIn("| combat | 3 | 2/3 |", text)
        self.assertIn("| benchmark |", text)


class InstancesCommandTest(unittest.TestCase):
    def test_the_instance_grader_runs_the_suite_with_its_report_and_the_harness_properties(self):
        argv = hg.instances_command("/j/java", "/c/classes", "/c/cp.txt", "/r", "/o/env-grade.tsv", only="combat.",
                                    seats="mine=a.b.Mine")
        self.assertEqual(argv[0], "/j/java")
        self.assertIn("forge.harnessenv.grader.GraderSuite", argv)
        self.assertIn("-Dharness.envgrade.report=/o/env-grade.tsv", argv)
        self.assertIn("-Dharness.envgrade.only=combat.", argv)
        self.assertIn("-Dharness.envgrade.seats=mine=a.b.Mine", argv)
        with_set = hg.instances_command("/j/java", "/c", "/c/cp.txt", "/r", "/o/r.tsv", set_file="/s/set.json")
        self.assertIn("-Dharness.envgrade.set=/s/set.json", with_set)
        self.assertFalse(any("envgrade.set" in a for a in argv))
        self.assertTrue(any(a.startswith("-Dharness.ownership.tsv=/r/") for a in argv))
        self.assertTrue(argv[argv.index("-cp") + 1].startswith("/c/classes:"))

    def test_the_build_compiles_the_overlay_the_grader_and_the_ai_counter_patch_together(self):
        tmp = tempfile.mkdtemp()
        try:
            sub = os.path.join(tmp, "sub")
            write(sub, "forge-gui-desktop/src/main/java/a/Seat.java", "class Seat {}\n")
            write(sub, "forge-game/src/main/java/forge/game/Game.java", "// ENGINE PATCH: x\nclass Game {}\n")
            argv = hg.compile_command("/j/javac", "/c/cp.txt", sub, REPO, "/p/forge/ai/PlayerControllerAi.java", "/o/classes")
            files = [a for a in argv if a.endswith(".java")]
            self.assertIn(os.path.join(sub, "forge-gui-desktop/src/main/java/a/Seat.java"), files)
            self.assertIn(os.path.join(sub, "forge-game/src/main/java/forge/game/Game.java"), files)
            self.assertIn("/p/forge/ai/PlayerControllerAi.java", files)
            self.assertTrue(any(f.endswith("forge/harnessenv/grader/Grader.java") for f in files))
            self.assertTrue(any(f.endswith("forge/harnessenv/SeatFactory.java") for f in files))
            self.assertFalse(any("/src/test/" in f and sub in f for f in files), "a submission's own tests are not compiled")
            self.assertEqual(argv[argv.index("-d") + 1], "/o/classes")
        finally:
            shutil.rmtree(tmp)

    def test_the_grader_suite_is_outside_the_acceptance_test_glob(self):
        grader = os.path.join(REPO, "forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "harnessenv", "grader")
        self.assertTrue(os.path.isfile(os.path.join(grader, "GraderSuite.java")))
        self.assertFalse(any(f.endswith("Test.java") for f in os.listdir(grader)))


class FamilyTest(unittest.TestCase):
    def test_every_decision_hook_in_the_ledger_has_a_family_and_every_family_a_hook(self):
        hooks = surface_coverage.decision_hooks(LEDGER)
        fams = {hg.family(h) for h in hooks}
        self.assertEqual(sorted(fams), sorted(hg.FAMILIES))
        self.assertEqual(hg.family("declareAttackers"), "combat")
        self.assertEqual(hg.family("chooseSpellAbilityToPlay"), "play")
        self.assertEqual(hg.family("chooseRollToSwap"), "variants")


class ClosureTest(unittest.TestCase):
    def test_the_hidden_tests_bring_their_helpers_and_not_the_rest(self):
        names = hg.test_closure(TESTS, hg.hidden_roots(TESTS))
        for need in ("KeywordSurfaceSweepTest", "LineFindingProbeTest", "AskContract", "GoldenChecksTest",
                     "SurfaceCostChoicesTest", "ControllerOwnershipTest"):
            self.assertIn(need, names)
        self.assertNotIn("DemoFeedTest", names)


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.sub = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.sub)

    def test_an_empty_overlay_is_empty_and_scores_zero_without_a_jvm(self):
        info = hg.intake(self.sub)
        self.assertEqual((info["java_files"], info["abi"]), (0, None))
        verdict = hg.grade(self.sub, REPO, os.path.join(self.sub, "out"), forge=None, run_jvm=False)
        self.assertEqual(verdict["score"], 0.0)
        self.assertIn("no Java", verdict["reasons"][0])

    def test_the_abi_is_read_from_the_properties_file_or_our_layout(self):
        write(self.sub, "harness-env.properties", "seat.factory=a.b.MySeat\n")
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/MySeat.java", "class MySeat {}\n")
        self.assertEqual(hg.intake(self.sub)["abi"], "harness-env-abi/1")
        self.assertEqual(hg.intake(self.sub)["factory"], "a.b.MySeat")

    def test_a_v1_submission_is_not_scored_by_the_v0_grader(self):
        write(self.sub, "harness-env.properties", "seat.factory=a.b.MySeat\n")
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/MySeat.java", "class MySeat {}\n")
        verdict = hg.grade(self.sub, REPO, os.path.join(self.sub, "out"), forge=None, run_jvm=False)
        self.assertIsNone(verdict["score"])

    def test_forge_ai_references_and_unmarked_engine_patches_are_flagged(self):
        write(self.sub, "forge-gui-desktop/src/main/java/a/Seat.java",
              "import forge.ai.PlayerControllerAi;\nclass Seat extends PlayerControllerAi {}\n")
        write(self.sub, "forge-game/src/main/java/forge/game/zone/MagicStack.java", "class MagicStack {}\n")
        write(self.sub, "forge-game/src/main/java/forge/game/player/Player.java",
              "// ENGINE PATCH: why\nclass Player {}\n")
        info = hg.intake(self.sub)
        self.assertEqual(info["forge_ai_references"], ["forge-gui-desktop/src/main/java/a/Seat.java"])
        self.assertEqual(info["unmarked_engine_patches"], ["forge-game/src/main/java/forge/game/zone/MagicStack.java"])

    def test_an_engine_file_is_one_the_engine_commits_not_one_installed_into_its_working_tree(self):
        forge = tempfile.mkdtemp()
        try:
            write(forge, "forge-game/src/main/java/forge/game/Game.java", "class Game {}\n")
            subprocess.run(["git", "init", "-q"], cwd=forge, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=forge, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "u"], cwd=forge,
                           check=True)
            seat = "forge-gui-desktop/src/main/java/forge/spike/Seat.java"
            write(forge, seat, "class Seat {}\n")  # what install.sh leaves behind
            write(self.sub, seat, "import forge.ai.PlayerControllerAi;\nclass Seat {}\n")
            write(self.sub, "forge-game/src/main/java/forge/game/Game.java", "// ENGINE PATCH: why\nclass Game {}\n")
            info = hg.intake(self.sub, forge)
            self.assertEqual(info["engine_patches"], ["forge-game/src/main/java/forge/game/Game.java"])
            self.assertEqual(info["forge_ai_references"], [seat])
        finally:
            shutil.rmtree(forge)

    def test_our_overlay_reads_as_the_v0_layout(self):
        info = hg.intake(os.path.join(REPO, "forge-overlay"))
        self.assertEqual(info["abi"], "v0-internal")
        self.assertEqual(info["unmarked_engine_patches"], [])


@unittest.skipUnless(fetch_comp_rules.present(), fetch_comp_rules.MISSING)
class AssembleTest(unittest.TestCase):
    def test_the_workspace_compile_stage_can_run_its_engine_patch_step(self):
        # The compile stage applies the repository's engine patches (eval/engine_patches.py) before javac. In the
        # grading workspace there are none (a submission brings whole engine files), but the step must run: a
        # missing tool fails the compile stage of every submission.
        sub, ws = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "ws")
        try:
            write(sub, "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java", "class LlmSpikeGui {}\n")
            hg.assemble(sub, REPO, ws)
            r = subprocess.run(["python3", os.path.join(ws, "eval", "engine_patches.py"), "apply", "--forge",
                                "/nonexistent", "--out", os.path.join(ws, "patched")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            shutil.rmtree(sub)
            shutil.rmtree(os.path.dirname(ws))

    def test_the_workspace_preflight_finds_its_rules_text(self):
        # The acceptance preflight runs eval/fetch_comp_rules.py; in the workspace it must find the copied rules text
        # (no download), or every grading run fails its preflight.
        sub, ws = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "ws")
        try:
            write(sub, "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java", "class LlmSpikeGui {}\n")
            hg.assemble(sub, REPO, ws)
            r = subprocess.run(["python3", os.path.join(ws, "eval", "fetch_comp_rules.py")], capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            shutil.rmtree(sub)
            shutil.rmtree(os.path.dirname(ws))

    def test_the_workspace_holds_the_submission_main_and_the_hidden_tests_not_its_own(self):
        sub = tempfile.mkdtemp()
        ws = os.path.join(tempfile.mkdtemp(), "ws")
        try:
            write(sub, "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java", "class LlmSpikeGui {}\n")
            write(sub, "forge-gui-desktop/src/test/java/forge/spike/MyOwnTest.java", "class MyOwnTest {}\n")
            hg.assemble(sub, REPO, ws)
            spike = os.path.join(ws, "forge-overlay", "forge-gui-desktop", "src")
            self.assertTrue(os.path.isfile(os.path.join(spike, "main", "java", "forge", "spike", "LlmSpikeGui.java")))
            tests = os.listdir(os.path.join(spike, "test", "java", "forge", "spike"))
            self.assertIn("KeywordSurfaceSweepTest.java", tests)
            self.assertNotIn("MyOwnTest.java", tests)
            self.assertNotIn("DemoFeedTest.java", tests)
            for f in hg.GRADER_FILES:
                self.assertTrue(os.path.exists(os.path.join(ws, f)), f)
            with open(os.path.join(ws, "eval", "surface_gaps.txt"), encoding="utf-8") as f:
                self.assertTrue(all(line.startswith("#") or not line.strip() for line in f))
        finally:
            shutil.rmtree(sub)
            shutil.rmtree(os.path.dirname(ws))


class RunTimeoutTest(unittest.TestCase):
    @unittest.skipUnless(fetch_comp_rules.present(), fetch_comp_rules.MISSING)
    def test_a_hung_grading_run_scores_zero_with_the_reason_instead_of_crashing(self):
        tmp = tempfile.mkdtemp()
        real = hg.subprocess.run

        def hang(cmd, **kw):
            if cmd and cmd[0] == "bash":
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return real(cmd, **kw)

        hg.subprocess.run = hang
        try:
            write(tmp, "sub/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java", "class LlmSpikeGui {}\n")
            v = hg.grade(os.path.join(tmp, "sub"), REPO, os.path.join(tmp, "out"), forge="/nowhere", run_jvm=True)
            self.assertEqual(v["score"], 0.0)
            self.assertTrue(any("time" in r for r in v["reasons"]), v["reasons"])
        finally:
            hg.subprocess.run = real
            shutil.rmtree(tmp)


class RunGuardTest(unittest.TestCase):
    def test_a_busy_marker_refuses_the_jvm_run(self):
        tmp = tempfile.mkdtemp()
        try:
            marker = write(tmp, "BUSY", "x")
            write(tmp, "sub/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java", "class LlmSpikeGui {}\n")
            with self.assertRaises(hg.Busy):
                hg.grade(os.path.join(tmp, "sub"), REPO, os.path.join(tmp, "out"), forge="/nowhere",
                         run_jvm=True, busy_markers=[marker])
        finally:
            shutil.rmtree(tmp)


class GraderPackageTest(unittest.TestCase):
    """A submission cannot ship a class in the grader's package: same-name classes would collide in the one javac, and
    a new one could reach the grader's package-private state (framework 4.7, the open in-JVM hole)."""

    def setUp(self):
        self.sub = tempfile.mkdtemp()
        write(self.sub, "harness-env.properties", "seat.factory=a.b.MySeat\n")
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/MySeat.java", "package a.b;\nclass MySeat {}\n")

    def tearDown(self):
        shutil.rmtree(self.sub)

    def test_a_clean_submission_is_not_refused(self):
        info = hg.intake(self.sub)
        self.assertEqual(info["grader_package"], [])
        self.assertIsNone(hg.refusal(info))

    def test_a_class_declared_in_the_grader_package_is_refused_wherever_it_lives(self):
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/Sneak.java",
              "// a comment\npackage forge.harnessenv.grader;\npublic class Sneak {}\n")
        info = hg.intake(self.sub)
        self.assertEqual(info["grader_package"], ["forge-gui-desktop/src/main/java/a/b/Sneak.java"])
        self.assertIn("forge.harnessenv", hg.refusal(info))

    def test_a_file_under_a_forge_harnessenv_path_is_refused_even_in_its_tests(self):
        write(self.sub, "forge-gui-desktop/src/test/java/forge/harnessenv/Helper.java", "class Helper {}\n")
        self.assertEqual(hg.intake(self.sub)["grader_package"], ["forge-gui-desktop/src/test/java/forge/harnessenv/Helper.java"])
        self.assertIsNotNone(hg.refusal(hg.intake(self.sub)))

    def test_comments_spaces_and_unicode_escapes_do_not_hide_the_package(self):
        for i, decl in enumerate(("package forge . harnessenv . grader;", "/* x */ package forge.harnessenv;",
                                  "package/**/forge.harnessenv.toys;", "\\u0070ackage forge.\\u0068arnessenv;",
                                  "package forge // why\n  .harnessenv;")):
            rel = "forge-gui-desktop/src/main/java/a/b/S%d.java" % i
            write(self.sub, rel, "// header\n%s\npublic class S%d {}\n" % (decl, i))
            self.assertIn(rel, hg.intake(self.sub)["grader_package"], decl)

    def test_a_package_named_only_in_a_comment_or_a_lookalike_is_not_refused(self):
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/C.java",
              "// package forge.harnessenv;\n/* package forge.harnessenv; */\npackage forge.harnessenvx;\nclass C {}\n")
        self.assertEqual(hg.intake(self.sub)["grader_package"], [])

    def test_importing_the_abi_is_not_declaring_a_class_in_it(self):
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/Factory.java",
              "package a.b;\nimport forge.harnessenv.SeatFactory;\nclass Factory implements SeatFactory {}\n")
        self.assertEqual(hg.intake(self.sub)["grader_package"], [])

    def test_an_unmarked_engine_patch_is_refused(self):
        write(self.sub, "forge-game/src/main/java/forge/game/zone/MagicStack.java", "class MagicStack {}\n")
        self.assertIn("unmarked engine patch", hg.refusal(hg.intake(self.sub)))

    def test_our_seats_main_sources_declare_nothing_in_the_grader_package(self):
        found = hg.intake(os.path.join(REPO, "forge-overlay"))["grader_package"]
        self.assertTrue(found, "the overlay holds the grader itself, under src/test")
        self.assertEqual([f for f in found if "/src/main/" in f], [])


ENGINE_PCAI = ("package forge.ai;\n"
               "public class PlayerControllerAi extends PlayerController {\n"
               "    @Override\n    public void declareBlockers(Player defender, Combat combat) {\n"
               "        brains.declareBlockersFor(defender, combat);\n    }\n\n"
               "    @Override\n    public List<SpellAbility> chooseSpellAbilityToPlay() {\n"
               "        return brains.chooseSpellAbilityToPlay();\n    }\n}\n")
# The same file as a submission might ship it: re-indented with tabs, one signature split over two lines.
OWN_PCAI = ("// HARNESS PATCH: the submission's own change\npackage forge.ai;\n"
            "public class PlayerControllerAi extends PlayerController {\n"
            "\t@Override\n\tpublic void declareBlockers(Player defender, Combat combat) {\n"
            "\t\tmine();\n\t}\n\n"
            "\t@Override\n\tpublic List<SpellAbility>\n\t\t\tchooseSpellAbilityToPlay() {\n"
            "\t\treturn null;\n\t}\n}\n")


class SubmissionAiControllerTest(unittest.TestCase):
    """A marked engine patch of PlayerControllerAi.java must not meet the grader's counter copy in one javac: the
    counter is generated from the submission's copy instead, and the submission's raw copy is not compiled. The
    check is per name: for every hook name, the copy must get at least as many counters as the engine's own file
    gives that name (an overload left uncounted is missing even when the name is counted elsewhere), or the grade
    is refused before javac."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.forge = os.path.join(self.tmp, "forge")
        write(self.forge, "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java", ENGINE_PCAI)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "pin"]):
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=self.forge, check=True)
        self.sub = os.path.join(self.tmp, "sub")
        write(self.sub, "harness-env.properties", "seat.factory=a.b.MySeat\n")
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/MySeat.java", "class MySeat {}\n")
        self.own = write(self.sub, "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java", OWN_PCAI)
        self.real = hg.subprocess.run
        self.calls = []

        def fake(cmd, **kw):
            if cmd[0] == "git":
                return self.real(cmd, **kw)
            self.calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0)

        hg.subprocess.run = fake

    def tearDown(self):
        hg.subprocess.run = self.real
        shutil.rmtree(self.tmp)

    def build(self):
        return hg.build_classes(self.sub, "/nope/cp.txt", self.forge, os.path.join(self.tmp, "out"), "javac")

    def test_the_counter_is_built_from_the_submissions_copy_and_compiled_once(self):
        rc, classes, counted, missing = self.build()
        self.assertEqual(rc, 0)
        self.assertEqual(missing, [])
        self.assertEqual(sorted(counted), ["chooseSpellAbilityToPlay", "declareBlockers"],
                         "tabs and a split signature count")
        argv = self.calls[0][0]
        pcai = [a for a in argv if a.endswith("PlayerControllerAi.java")]
        self.assertEqual(len(pcai), 1, pcai)
        self.assertNotEqual(pcai[0], self.own)
        with open(pcai[0], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("mine();", text, "the submission's change survives")
        self.assertIn('AiCounter.enter("declareBlockers", this);', text)
        self.assertIn('AiCounter.enter("chooseSpellAbilityToPlay", this);', text)

    def test_a_copy_where_a_hook_the_engine_counts_goes_uncounted_is_refused_before_javac(self):
        write(self.sub, "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java",
              OWN_PCAI.replace("chooseSpellAbilityToPlay()", "pickSomething()"))
        rc, _, counted, missing = self.build()
        self.assertIsNone(rc)
        self.assertEqual(missing, ["chooseSpellAbilityToPlay"])
        self.assertEqual(self.calls, [], "nothing is compiled")

    def test_an_overload_the_copy_leaves_uncounted_is_missing_though_its_name_is_counted(self):
        # An honest reformat: the copy writes one overload's signature on its @Override line, which the counter
        # does not match, while the name is still counted on the other overload.
        second = ("    @Override\n    public void declareBlockers(Player defender) {\n"
                  "        brains.declareBlockersFor(defender, null);\n    }\n}\n")
        write(self.forge, "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java", ENGINE_PCAI[:-2] + second)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "two"],
                       cwd=self.forge, check=True)
        write(self.sub, "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java", OWN_PCAI[:-2]
              + "\t@Override public void declareBlockers(Player defender) {\n\t\tmine();\n\t}\n}\n")
        rc, _, counted, missing = self.build()
        self.assertIsNone(rc)
        self.assertEqual(missing, ["declareBlockers"])
        self.assertEqual(self.calls, [], "nothing is compiled")

    def test_the_submissions_copy_is_read_once_and_that_text_is_what_gets_patched(self):
        own = os.path.join(self.sub, hg.harness_env_aicount.ENGINE_PATH)
        with mock.patch.object(hg.harness_env_aicount, "write", wraps=hg.harness_env_aicount.write) as w, \
                mock.patch("builtins.open", wraps=open) as opened:
            self.build()
        self.assertEqual([c for c in opened.call_args_list if c.args and c.args[0] == own],
                         [mock.call(own, encoding="utf-8")], "one read of the submission's copy")
        self.assertEqual(w.call_count, 1)
        self.assertEqual(w.call_args.kwargs.get("source"), OWN_PCAI)

    def test_the_build_command_says_how_many_hooks_and_how_many_methods(self):
        counted = collections.Counter(declareBlockers=2, chooseSpellAbilityToPlay=1)
        printed = io.StringIO()
        with mock.patch.object(hg, "build_classes", return_value=(0, "/c", counted, [])), \
                mock.patch.object(htg, "java_home", return_value="/jdk"), contextlib.redirect_stdout(printed):
            rc = hg.main(["grade", "build", self.sub, "/nope/cp.txt", self.forge, os.path.join(self.tmp, "out")])
        self.assertEqual(rc, 0)
        self.assertIn("Forge-AI counter in 2 hooks, 3 methods", printed.getvalue())

    def test_the_build_command_takes_the_jdk_the_task_grader_finds_never_usr_bin(self):
        home = os.path.join(self.tmp, "brew", "libexec/openjdk.jdk/Contents/Home")
        os.chmod(write(home, "bin/javac", "#!/bin/sh\nexit 0\n"), 0o755)
        argv = ["grade", "build", self.sub, "/nope/cp.txt", self.forge, os.path.join(self.tmp, "out")]
        env = {k: v for k, v in os.environ.items() if k != "JAVA_HOME"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(htg, "JDK_DIRS", (os.path.join(self.tmp, "brew"),)), \
                mock.patch.object(hg, "build_classes", return_value=(0, "/c", collections.Counter(), [])) as built, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hg.main(argv), 0)
        self.assertEqual(built.call_args.args[4], os.path.join(home, "bin", "javac"))
        printed = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(htg, "JDK_DIRS", ()), \
                mock.patch.object(htg.shutil, "which", return_value=None), \
                mock.patch.object(hg, "build_classes") as built, contextlib.redirect_stdout(printed):
            self.assertEqual(hg.main(argv), 2, "no JDK is a setup fault, not a failed build")
        built.assert_not_called()
        self.assertIn("no javac", printed.getvalue())

    def test_the_grader_environment_carries_no_secret_or_provider_variable(self):
        with mock.patch.dict(os.environ, HARNESS_ENV_SEED="s3cret", OPENROUTER_API_KEY="k",
                             OPENROUTER_ALLOW_EGRESS="1", PATH="/bin"):
            env = hg.grader_env("/jdk")
        self.assertEqual(env["JAVA_HOME"], "/jdk")
        self.assertEqual(env["PATH"], "/bin")
        self.assertFalse([k for k in env if k.startswith("OPENROUTER") or k == "HARNESS_ENV_SEED"], env)


class CounterOffTest(unittest.TestCase):
    def test_a_missing_counter_gates_the_seat_and_is_named(self):
        rows = [dict(seat="ours", instance="x", family="combat", hook="declareBlockers", ai="-1", ai_payment="-1",
                     **{"pass": "1"})]
        self.assertEqual(hg.counter_off(rows), {"ours"})
        self.assertEqual(hg.ai_gated(rows), {"ours"}, "ai=-1 is not proof of no Forge-AI decision")
        self.assertEqual(hg.counter_off([dict(rows[0], ai="0")]), set())


if __name__ == "__main__":
    unittest.main()
