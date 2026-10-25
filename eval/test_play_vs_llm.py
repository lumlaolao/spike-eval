"""play-vs-llm.sh: the launcher that seats a person against the model in the GUI. Run with --dry-run only, so no
window, JVM or model is started."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "play-vs-llm.sh"


def run(*args, **env):
    base = {k: v for k, v in os.environ.items()
            if k not in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY", "CLAUDE_CLI_SEAT")}
    base.update(env)
    return subprocess.run(["bash", str(SCRIPT), "--dry-run", *args], capture_output=True, text=True, env=base,
                          cwd=tempfile.gettempdir())


def dry_env(proc):
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line and line[0].isupper())


class PlayVsLlmTest(unittest.TestCase):

    def test_the_person_sits_at_a_and_the_model_at_b(self):
        p = run(OPENROUTER_ALLOW_EGRESS="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        e = dry_env(p)
        self.assertEqual(e["CONTROLLER_A"], "human")
        self.assertEqual(e["CONTROLLER_B"], "llm")
        self.assertEqual(e["SMART_MODEL_B"], "openrouter:openai/gpt-6-luna")
        self.assertEqual(e["FAST_MODEL_B"], e["SMART_MODEL_B"])
        self.assertEqual(e["DECK_A"], str(REPO / "eval/decks/burn.txt"))
        self.assertTrue(e["EVAL_OUT"].startswith(str(REPO / "runs/human/")), e["EVAL_OUT"])
        self.assertEqual(e["TRANSCRIPTS"], "1", "the model's chat is kept")

    def test_an_openrouter_model_without_consent_is_refused_and_consent_is_never_set(self):
        if (REPO / "secrets.env").exists():
            self.skipTest("this clone's secrets.env may hold the consent")
        p = run()
        self.assertEqual(p.returncode, 3)
        self.assertIn("OPENROUTER_ALLOW_EGRESS=1", p.stderr)
        self.assertNotIn("OPENROUTER_ALLOW_EGRESS", SCRIPT.read_text().split("export CONTROLLER_A")[1])

    def test_the_claude_code_lane_needs_its_opt_in(self):
        self.assertEqual(run("--model", "claude-cli:opus").returncode, 3)
        self.assertEqual(run("--model", "claude-cli:opus", CLAUDE_CLI_SEAT="1").returncode, 0)

    def test_decks_by_name_or_path_and_an_unknown_deck_is_refused(self):
        p = run("--deck", "elves", "--vs", str(REPO / "eval/decks/ur-delver.txt"), OPENROUTER_ALLOW_EGRESS="1")
        e = dry_env(p)
        self.assertEqual(e["DECK_A"], str(REPO / "eval/decks/elves.txt"))
        self.assertEqual(e["DECK_B"], str(REPO / "eval/decks/ur-delver.txt"))
        bad = run("--deck", "no-such-deck", OPENROUTER_ALLOW_EGRESS="1")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("no deck 'no-such-deck'", bad.stderr)

    def test_games_must_be_a_positive_number(self):
        self.assertEqual(run("--games", "0", OPENROUTER_ALLOW_EGRESS="1").returncode, 2)
        self.assertEqual(dry_env(run("--games", "3", OPENROUTER_ALLOW_EGRESS="1"))["MATCH_GAMES"], "3")


if __name__ == "__main__":
    unittest.main()
