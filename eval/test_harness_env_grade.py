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
