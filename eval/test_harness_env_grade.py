"""harness_env_grade.py: the v0 grader scores a perfect record 1, an empty overlay 0, and each break where it lands.

The evidence here is synthetic but has the acceptance OUT layout the grader reads (summary.txt, surfaces.tsv,
testng/testng-results.xml, javatests.log). Each test removes one thing a perfect harness has and asserts the score
drops in the component that thing belongs to, and only there.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

import harness_env_grade as hg
import surface_coverage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


class AssembleTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
