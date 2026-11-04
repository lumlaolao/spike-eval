"""Every file that launches a game JVM, by the rule it follows for conditions (docs/STANDARDS.md section 4).

A repository rule, not a script's test: a file that names `forge.spike.LlmSpikeHeadless` or `run-llm-headless.sh`
must be listed below with how its games get their conditions, so the rule's list of launchers cannot go stale (the
standards reviews of #346: the first wording named every launcher, the second missed harness_acceptance.sh).
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

LAUNCHERS = {
    # Pins every condition from its own flags and stamps it (the reference).
    "eval/run_batch.py": "pins",
    # Launches through run_batch.py, so it inherits the pinning.
    "eval/improve_loop.py": "through run_batch",
    # Strips run_batch.CONDITION_ENV_KEYS and SHADOW_AI, then sets its own (test_puzzle_baselines).
    "eval/puzzles/puzzle_baselines.py": "strips",
    # Diagnostics that launch with the shell's environment: never scored; their games carry the game-line stamps.
    "eval/harness_acceptance.sh": "diagnostic",
    "eval/keyword_gauntlet.sh": "diagnostic",
    "eval/shadow_canary.sh": "diagnostic",
    "eval/demo/demo_compare.py": "diagnostic",
    # The launchers themselves, and the installer that copies them into $FORGE_HOME.
    "forge-overlay/run-llm-headless.sh": "launcher",
    "forge-overlay/run-llm-spike.sh": "launcher",
    "install.sh": "launcher",
    # Names a launcher in its prose only.
    "eval/defect_analyzer.py": "mention",
}


def naming_files():
    out = subprocess.run(["git", "grep", "-lE", r"forge\.spike\.LlmSpikeHeadless|run-llm-headless\.sh", "--",
                          "*.py", "*.sh", ":!eval/tests/*"], cwd=str(evalpath.REPO), stdout=subprocess.PIPE,
                         universal_newlines=True)
    return sorted(out.stdout.split())


class LaunchersTest(unittest.TestCase):
    def test_every_file_that_names_a_game_launch_is_classified(self):
        files = naming_files()
        self.assertIn("eval/run_batch.py", files)   # the search found the reference launcher
        self.assertEqual([f for f in files if f not in LAUNCHERS], [],
                         "classify it in LAUNCHERS and, if it launches games, say how they get their conditions "
                         "(docs/STANDARDS.md section 4)")

    def test_no_classified_launcher_is_gone(self):
        self.assertEqual(sorted(set(LAUNCHERS) - set(naming_files())), [], "drop a file that no longer launches")

    def test_the_standards_name_every_diagnostic(self):
        text = (evalpath.REPO / "docs" / "STANDARDS.md").read_text(encoding="utf-8")
        start = text.index("**An opt-in flag is a condition.**")
        text = text[start:text.index("\n- **", start)]   # the rule itself, not a mention elsewhere in the file
        for f, kind in LAUNCHERS.items():
            if kind == "diagnostic":
                self.assertIn(os.path.basename(f), text, f)


if __name__ == "__main__":
    unittest.main()
