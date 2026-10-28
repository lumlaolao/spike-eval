"""harness_env_agents.py: the two scripted agents the episode runner is dry-run with.

copy-ours must leave a submission the grader can build (our main sources, our engine patches as the patched files,
the ABI adapter, the factory named) and nothing must leave an empty overlay; both must say DONE, or the runner would
wait for the cap. The engine-patch check needs the Forge checkout and is skipped without one.
"""
import os
import shutil
import tempfile
import unittest

import engine_patches
import harness_env_agents as ag
import harness_env_grade as hgrade

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.box, "overlay"))

    def tearDown(self):
        shutil.rmtree(self.box)

    def test_copy_ours_leaves_our_seat_behind_the_abi_and_says_done(self):
        ag.copy_ours(REPO, self.box)
        overlay = os.path.join(self.box, "overlay")
        info = hgrade.intake(overlay)
        self.assertEqual(info["factory"], "forge.spike.HarnessEnvOurs$Ours")
        self.assertTrue(os.path.isfile(os.path.join(overlay, "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java")))
        self.assertTrue(os.path.isfile(os.path.join(overlay, "forge-gui-desktop/src/main/java/forge/spike/HarnessEnvOurs.java")))
        self.assertEqual(info["own_test_files"], 0, "our tests are not part of the submission")
        self.assertTrue(os.path.isfile(os.path.join(self.box, "DONE")))

    def test_copy_ours_brings_our_engine_patches_as_the_marked_files_they_make(self):
        forge = engine_patches.forge_home()
        if forge is None:
            self.skipTest("no Forge checkout: the engine patches cannot be applied")
        ag.copy_ours(REPO, self.box, forge)
        info = hgrade.intake(os.path.join(self.box, "overlay"), str(forge))
        self.assertEqual(sorted(info["engine_patches"]), sorted(engine_patches.patch_files(REPO)))
        self.assertEqual(info["unmarked_engine_patches"], [])

    def test_the_build_does_not_add_the_adapter_twice(self):
        ag.copy_ours(REPO, self.box)
        argv = hgrade.compile_command("javac", "/nope", os.path.join(self.box, "overlay"), REPO, "/p/PlayerControllerAi.java", "/o")
        self.assertEqual(sum(1 for a in argv if a.endswith("HarnessEnvOurs.java")), 1)

    def test_nothing_leaves_an_empty_overlay_and_says_done(self):
        ag.nothing(self.box)
        self.assertEqual(os.listdir(os.path.join(self.box, "overlay")), [])
        self.assertTrue(os.path.isfile(os.path.join(self.box, "DONE")))


if __name__ == "__main__":
    unittest.main()
