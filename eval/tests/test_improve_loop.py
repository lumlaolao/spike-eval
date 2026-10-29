"""Tests for improve_loop.py: the run_batch command it builds, and what it refuses before launching."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import improve_loop  # noqa: E402

DECK = str(evalpath.EVAL / "decks" / "burn.txt")


class ImproveLoopTest(unittest.TestCase):

    def setUp(self):
        # The defaults come from IMPROVE_* variables; a developer's shell must not change what is tested.
        env = {k: v for k, v in os.environ.items() if not k.startswith("IMPROVE_") and k != "OPENROUTER_API_KEY"}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = tmp.name  # a regression that gets as far as mkdir writes here, never at the root

    def cmd(self, *argv):
        args = improve_loop.parse_args(["--forge-home", "/forge", "--out", "/o", *argv])
        return improve_loop.build_run_batch_cmd(args, "/o")

    def test_a_floor_block_names_no_model(self):
        cmd = self.cmd("--controller", "floor", "--games", "1", "--seed", "7")
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

    def test_an_openrouter_block_without_a_key_is_refused_before_launching(self):
        with mock.patch.object(improve_loop.subprocess, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as cm:
            improve_loop.main(["--controller", "llm", "--seat-a", "openrouter:x/a", "--seat-b", "openrouter:x/b",
                               "--deck", DECK, "--forge-home", "/forge", "--out", self.out])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("OPENROUTER_API_KEY is not set", err.getvalue())
        run.assert_not_called()

    def test_no_games_and_a_missing_deck_are_refused(self):
        # Mocked and in a scratch --out, so a broken refusal cannot launch a real block or write outside it.
        for argv in (["--games", "0", "--deck", DECK], ["--deck", "/no/such/deck.txt"]):
            with tempfile.TemporaryDirectory() as out, mock.patch.object(improve_loop.subprocess, "run") as run, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                improve_loop.main(["--controller", "floor", "--forge-home", "/forge", "--out", out, *argv])
            self.assertEqual(cm.exception.code, 2, argv)
            run.assert_not_called()

    def test_a_dry_run_prints_the_command_and_launches_nothing(self):
        with mock.patch.object(improve_loop.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = improve_loop.main(["--controller", "floor", "--deck", DECK, "--forge-home", "/forge",
                                    "--out", self.out, "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("run_batch.py", out.getvalue())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
