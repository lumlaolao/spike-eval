"""demo_compare: the older harness gets the audience feed by a port that is idempotent and refuses a tree it cannot
place it in; both arms play the same conditions; a run never starts without a budget. No game is played here."""
import os
import shutil
import tempfile
import unittest

import demo_compare as dc

OBS = "class SeatObservation {\n" + dc.OBS_ANCHOR + "}\n"
GUI = "class LlmSpikeGui {\n" + dc.CTRL_ANCHOR + "}\n"


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

    def test_the_feed_and_its_two_hooks_are_ported_once(self):
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java", "SeatObservation.java", "LlmSpikeGui.java"])
        self.assertIn("Map<Integer, Integer> handleMap()", self.read("SeatObservation.java"))
        self.assertIn("DemoFeed.attach(game, seat, observer);", self.read("LlmSpikeGui.java"))
        self.assertTrue(os.path.isfile(os.path.join(self.spike, "DemoFeed.java")))
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java"])  # a second port adds nothing
        self.assertEqual(self.read("LlmSpikeGui.java").count("DemoFeed.attach"), 1)

    def test_a_tree_without_the_anchor_is_refused_not_guessed(self):
        with open(os.path.join(self.spike, "LlmSpikeGui.java"), "w") as f:
            f.write("class LlmSpikeGui {}\n")
        with self.assertRaises(SystemExit):
            dc.port_feed(self.tree)


class ArmTest(unittest.TestCase):
    def test_an_arm_plays_the_llm_seat_against_forge_ai_with_the_feed_and_a_budget(self):
        cmd = dc.arm_command("/r", "/home", 4201, "/a.txt", "/b.txt", "openrouter:x/y", 0.4, "/out")
        flat = " ".join(cmd)
        for part in ("--seeds 4201", "--budget-usd 0.4", "--forge-home /home", "--controller-a llm",
                     "--controller-b ai", "--smart-a openrouter:x/y", "--fast-a openrouter:x/y", "--env DEMO_FEED=1",
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
