"""play-vs-llm.sh: the launcher that seats a person against the model in the GUI. Run with --dry-run only, so no
window, JVM or model is started. Each run uses a copy of the script in a scratch repo (it finds its repo from $0), so
the clone's own secrets.env never decides a test and nothing is skipped."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "play-vs-llm.sh"


class PlayVsLlmTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        shutil.copy(SCRIPT, cls.repo)
        shutil.copytree(REPO / "eval", cls.repo / "eval",
                        ignore=lambda d, names: [n for n in names if n in ("__pycache__", "tooluse")
                                                 or (Path(d, n).is_dir() and n not in ("decks", "suites"))])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def tearDown(self):
        (self.repo / "secrets.env").unlink(missing_ok=True)

    def run_script(self, *args, **env):
        base = {k: v for k, v in os.environ.items()
                if k not in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY", "CLAUDE_CLI_SEAT")}
        base.update(env)
        return subprocess.run(["bash", str(self.repo / "play-vs-llm.sh"), "--dry-run", *args], capture_output=True,
                              text=True, env=base, cwd=tempfile.gettempdir())

    def consented(self, *args, **env):
        return self.run_script(*args, OPENROUTER_ALLOW_EGRESS="1", **env)

    @staticmethod
    def dry_env(proc):
        return dict(ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln and ln[0].isupper())

    def test_the_person_sits_at_a_and_the_model_at_b(self):
        p = self.consented()
        self.assertEqual(p.returncode, 0, p.stderr)
        e = self.dry_env(p)
        self.assertEqual(e["CONTROLLER_A"], "human")
        self.assertEqual(e["CONTROLLER_B"], "llm")
        self.assertEqual(e["SMART_MODEL_B"], "openrouter:openai/gpt-6-luna")
        self.assertEqual(e["FAST_MODEL_B"], e["SMART_MODEL_B"])
        self.assertEqual(e["DECK_A"], str(self.repo / "eval/decks/burn.txt"))
        self.assertTrue(e["EVAL_OUT"].startswith(str(self.repo / "runs/human/")), e["EVAL_OUT"])
        self.assertEqual(e["TRANSCRIPTS"], "1", "the model's chat is kept")

    def test_the_model_plays_under_the_panels_pilot_condition_whatever_the_shell_holds(self):
        fields = json.loads((REPO / "eval/suites/panels-v1.json").read_text())["pilot_condition"]["fields"]
        e = self.dry_env(self.consented(LOOKAHEAD="0", RULES_TOOL="1", CONTEXT_PROFILE="v1-compat", GAME_FORMAT="x"))
        for k, v in fields.items():
            self.assertEqual(e[k.upper()], ("1" if v else "0") if isinstance(v, bool) else v, k)
        self.assertNotIn("GAME_FORMAT", e, "a condition the pilot does not set is unset, not inherited")
        self.assertEqual(e["LOOKAHEAD"], "1")
        self.assertEqual(e["RULES_TOOL"], "0")

    def test_an_openrouter_model_without_consent_is_refused_and_consent_is_never_set(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 3)
        self.assertIn("OPENROUTER_ALLOW_EGRESS=1", p.stderr)
        self.assertNotIn("OPENROUTER_ALLOW_EGRESS", self.dry_env(p))
        for line in SCRIPT.read_text().splitlines():
            self.assertFalse("OPENROUTER_ALLOW_EGRESS=" in line and "export" in line and "$k" not in line, line)

    def test_consent_is_read_from_secrets_env_and_the_environment_wins(self):
        secrets = self.repo / "secrets.env"
        secrets.write_text("OPENROUTER_API_KEY=k\n# OPENROUTER_ALLOW_EGRESS=1\n")
        self.assertEqual(self.run_script().returncode, 3, "a commented-out consent is no consent")
        for text in ("OPENROUTER_ALLOW_EGRESS=1\n", "OPENROUTER_ALLOW_EGRESS=1\r\n", "  OPENROUTER_ALLOW_EGRESS = 1 \n",
                     "OPENROUTER_API_KEY=k\nOPENROUTER_ALLOW_EGRESS=1"):
            secrets.write_text(text)
            self.assertEqual(self.run_script().returncode, 0, repr(text))
        secrets.write_text("OPENROUTER_ALLOW_EGRESS=0\n")
        self.assertEqual(self.run_script().returncode, 3)
        self.assertEqual(self.consented().returncode, 0, "the environment wins")

    def test_the_claude_code_lane_needs_its_opt_in(self):
        self.assertEqual(self.run_script("--model", "claude-cli:opus").returncode, 3)
        self.assertEqual(self.run_script("--model", "claude-cli:opus", CLAUDE_CLI_SEAT="1").returncode, 0)

    def test_decks_by_name_or_path_and_an_unknown_deck_is_refused(self):
        e = self.dry_env(self.consented("--deck", "elves", "--vs", str(REPO / "eval/decks/ur-delver.txt")))
        self.assertEqual(e["DECK_A"], str(self.repo / "eval/decks/elves.txt"))
        self.assertEqual(e["DECK_B"], str(REPO / "eval/decks/ur-delver.txt"))
        bad = self.consented("--deck", "no-such-deck")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("no deck 'no-such-deck'", bad.stderr)

    def test_games_must_be_a_positive_number_and_a_flag_needs_its_value(self):
        self.assertEqual(self.consented("--games", "0").returncode, 2)
        self.assertEqual(self.dry_env(self.consented("--games", "3"))["MATCH_GAMES"], "3")
        p = self.consented("--deck")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--deck needs a value", p.stderr)

    def test_commentary_follows_the_games_directory_and_why_implies_it(self):
        p = self.consented("--why")
        feed = "python3 %s %s --follow --why" % (self.repo / "eval/play_commentary.py", self.dry_env(p)["EVAL_OUT"])
        self.assertIn("would also run: " + feed, p.stdout)
        self.assertNotIn("would also run", self.consented().stdout)

    def test_run_batch_offers_no_human_seat(self):
        p = subprocess.run([sys.executable, str(REPO / "eval/run_batch.py"), "--controller-a", "human", "--seeds", "1"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("invalid choice: 'human'", p.stderr)


if __name__ == "__main__":
    unittest.main()
