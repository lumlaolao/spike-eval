"""demo_compare: the older harness gets the audience feed by a port that is idempotent and refuses a tree it cannot
place it in; both arms play the same conditions; a run never starts without a budget. No game is played here."""
import os
import shutil
import subprocess
import tempfile
import unittest

import demo_compare as dc

OBS = "class SeatObservation {\n" + dc.OBS_ANCHOR + "}\n"
GUI = "class LlmSpikeGui {\n" + dc.CTRL_ANCHOR + dc.WRITE_ANCHOR + "}\n"


class PortTest(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp()
        self.spike = os.path.join(self.tree, dc.SPIKE)
        os.makedirs(self.spike)
        for name, text in (("SeatObservation.java", OBS), ("LlmSpikeGui.java", GUI)):
            with open(os.path.join(self.spike, name), "w") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.tree)

    def read(self, name):
        with open(os.path.join(self.spike, name)) as f:
            return f.read()

    def test_the_feed_and_its_hooks_are_ported_once(self):
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java", "SeatObservation.java", "LlmSpikeGui.java", "LlmSpikeGui.java"])
        self.assertIn("Map<Integer, Integer> handleMap()", self.read("SeatObservation.java"))
        self.assertIn("DemoFeed.attach(game, seat, observer);", self.read("LlmSpikeGui.java"))
        self.assertTrue(os.path.isfile(os.path.join(self.spike, "DemoFeed.java")))
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java"])  # a second port adds nothing
        self.assertEqual(self.read("LlmSpikeGui.java").count("DemoFeed.attach"), 1)
        self.assertEqual(self.read("LlmSpikeGui.java").count("DemoFeed.beforeDecisionRow"), 1)

    def test_the_ported_decision_hook_is_the_current_one(self):
        with open(os.path.join(dc.REPO, dc.SPIKE, "LlmSpikeGui.java")) as f:
            self.assertIn(dc.WRITE_HOOK, f.read())

    def test_the_ported_handle_map_is_the_current_one(self):
        with open(os.path.join(dc.REPO, dc.SPIKE, "SeatObservation.java")) as f:
            current = f.read()
        sig = "Map<Integer, Integer> handleMap() {"
        self.assertIn(sig + dc.OBS_HOOK.split(sig, 1)[1], current)

    def test_a_tree_without_the_anchor_is_refused_not_guessed(self):
        with open(os.path.join(self.spike, "LlmSpikeGui.java"), "w") as f:
            f.write("class LlmSpikeGui {}\n")
        with self.assertRaises(SystemExit):
            dc.port_feed(self.tree)


class EnginePinTest(unittest.TestCase):
    """Which engine each arm runs: its own overlay copy of every patched engine file, else the pinned stock source."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def tree(self, name, files):
        root = os.path.join(self.d, name)
        for rel, text in files.items():
            self.write(os.path.join(root, "forge-overlay", rel), text)
        return root

    def test_engine_patches_are_main_sources_outside_the_harness_package(self):
        t = self.tree("t", {"forge-game/src/main/java/x/A.java": "", dc.SPIKE.split("forge-overlay/")[1] + "/S.java": "",
                            "forge-gui-desktop/src/test/java/x/T.java": "", "forge-gui/res/a.png": ""})
        self.assertEqual(dc.engine_patches(t), ["forge-game/src/main/java/x/A.java"])

    def test_each_file_is_the_trees_overlay_copy_or_the_stock_source_and_a_missing_stock_is_refused(self):
        a, b = "forge-game/src/main/java/x/A.java", "forge-ai/src/main/java/x/B.java"
        tree = self.tree("then", {a: "class A { /* then */ }"})
        forge = os.path.join(self.d, "forge")
        self.write(os.path.join(forge, b), "class B { /* stock */ }")
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s"]):
            subprocess.run(["git", "-C", forge] + cmd, check=True)
        build = os.path.join(self.d, "build")
        self.write(os.path.join(build, "classpath.txt"), "/cp")
        compiled = []
        real = dc.subprocess.run

        def fake_run(cmd, **kw):  # javac stubbed: record what it would compile
            if cmd[0] == "javac":
                compiled.extend(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real(cmd, **kw)
        dc.subprocess.run = fake_run
        try:
            dc.pin_engine(tree, build, forge, [a, b])
        finally:
            dc.subprocess.run = real
        with open(os.path.join(build, "engine-patches.txt")) as f:
            self.assertEqual(f.read(), "stock\t%s\noverlay\t%s\n" % (b, a))
        with open(os.path.join(build, "engine-src", b)) as f:
            self.assertIn("stock", f.read())
        with open(os.path.join(build, "engine-src", a)) as f:
            self.assertIn("then", f.read())
        self.assertIn("--release", compiled)
        self.assertIn(os.path.join(build, "classes"), compiled)
        with self.assertRaises(SystemExit):  # neither an overlay copy nor a stock source: refused, never guessed
            dc.pin_engine(tree, build, forge, ["forge-game/src/main/java/x/Gone.java"])


class ArmTest(unittest.TestCase):
    def test_an_arm_plays_the_llm_seat_against_forge_ai_with_the_feed_and_a_budget(self):
        cmd = dc.arm_command("/r", "/home", 4201, "/a.txt", "/b.txt", "openrouter:x/y", 0.4, "/out")
        flat = " ".join(cmd)
        for part in ("--seeds 4201", "--budget-usd 0.4", "--forge-home /home", "--controller-a llm",
                     "--controller-b ai", "--context-profile v4-faithful", "--smart-a openrouter:x/y", "--fast-a openrouter:x/y", "--env DEMO_FEED=1",
                     "--exploratory", "--out /out"):
            self.assertIn(part, flat)

    def test_the_loopback_launch_pins_the_same_conditions(self):
        env = dc.direct_env(4201, "/d/vintage-esper-control.txt", "/d/pioneer-red-deck-wins.txt", "m", "/out", "u")
        self.assertEqual((env["CONTROLLER_B"], env["DEMO_FEED"], env["MEMORY_TOOLS"], env["LOOKAHEAD"]),
                         ("ai", "1", "1", "1"))
        self.assertEqual(env["DECK_A_NAME"], "vintage-esper-control")
        self.assertNotIn("OPENROUTER_ALLOW_EGRESS", env)

    def test_a_run_without_a_budget_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            dc.main(["run", "--seed", "1", "--deck-a", "a", "--deck-b", "b", "--out", "/tmp/x"])
        self.assertIn("--budget-usd", str(cm.exception))

    def test_a_deck_outside_the_fixed_sets_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            dc.main(["run", "--seed", "1", "--deck-a", "burn", "--deck-b", "burn", "--budget-usd", "0.1",
                     "--out", "/tmp/x"])
        self.assertIn("vintage-esper-control", str(cm.exception))  # burn is a Forge AI deck, not the seat's

    def test_every_deck_in_the_fixed_sets_exists_and_the_sets_do_not_overlap(self):
        agent, ai = dc.deck_sets()
        self.assertFalse(set(agent) & set(ai))
        for name in list(agent) + list(ai):
            self.assertTrue(os.path.isfile(dc.pick_deck(name, [name], "x")), name)

    def test_the_stand_in_home_runs_the_isolated_classes_from_the_real_checkout(self):
        d = tempfile.mkdtemp()
        try:
            forge = os.path.join(d, "forge")
            os.makedirs(os.path.join(forge, ".git"))
            script = dc.standin_home(os.path.join(d, "home"), "/b/classes", "/b/classpath.txt", forge)
            with open(script) as f:
                text = f.read()
            self.assertIn("cd %s/forge-gui-desktop" % forge, text)
            self.assertIn('-cp "/b/classes:$(cat /b/classpath.txt)" forge.spike.LlmSpikeHeadless', text)
            self.assertTrue(os.access(script, os.X_OK))
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
