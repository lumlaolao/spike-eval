"""Tests for improve_loop.py: the run_batch command it builds for a floor or an llm block."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import improve_loop  # noqa: E402


class BuildCommandTest(unittest.TestCase):

    def cmd(self, *argv):
        args = improve_loop.parse_args(["--forge-home", "/forge", "--out", "/o", *argv])
        return improve_loop.build_run_batch_cmd(args, "/o")

    def test_a_floor_block_names_no_model(self):
        cmd = self.cmd("--games", "1", "--seed", "7")
        self.assertEqual(cmd[cmd.index("--seeds") + 1], "7")
        self.assertIn("--exploratory", cmd)
        self.assertFalse([c for c in cmd if c.startswith(("--smart", "--fast"))])

    def test_an_llm_block_pins_both_tiers_of_both_seats(self):
        cmd = self.cmd("--controller", "llm", "--games", "3", "--seed", "10",
                       "--seat-a", "openrouter:x/a", "--seat-b", "openrouter:x/b")
        self.assertEqual(cmd[cmd.index("--seeds") + 1], "10..12")
        for flag, spec in (("--smart-a", "openrouter:x/a"), ("--fast-a", "openrouter:x/a"),
                           ("--smart-b", "openrouter:x/b"), ("--fast-b", "openrouter:x/b")):
            self.assertEqual(cmd[cmd.index(flag) + 1], spec)

    def test_the_command_runs_the_run_batch_beside_it(self):
        self.assertEqual(self.cmd()[1], str(evalpath.EVAL / "run_batch.py"))

    def test_only_an_openrouter_spec_counts_as_egress(self):
        self.assertTrue(improve_loop.uses_openrouter("openrouter:openai/x"))
        self.assertFalse(improve_loop.uses_openrouter("claude-cli:opus"))
        self.assertFalse(improve_loop.uses_openrouter(None))


if __name__ == "__main__":
    unittest.main()
