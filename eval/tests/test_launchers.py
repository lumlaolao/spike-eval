"""Every file that launches a game JVM, by the rule it follows for conditions (docs/STANDARDS.md section 4).

A repository rule, not a script's test: a file that names a game JVM's main (`forge.spike.LlmSpikeHeadless`,
`forge.spike.LlmSpikeGui`) or its launcher (`run-llm-headless.sh`, `run-llm-spike.sh`) must be listed below with
how its games get their conditions, so the rule's list of launchers cannot go stale (the standards reviews of #346:
the first wording named every launcher, the second missed harness_acceptance.sh; #347's missed run-llm-spike.sh).
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
    "eval/tournament.py": "strips",   # _match_env (test_tournament; pr-review of #347)
    # The RL env: scrubbed_environ drops every OPENROUTER*/PZL_/FORK_/CLAUDE_ name and run_batch.PINNED_ENV_KEYS and
    # RUN_DEFINING_KEYS, then DECISION_ONLY sets its conditions (test_mtg_env ScrubTest); never scored.
    "eval/rl/mtg_env.py": "strips",
    # A person against the seat, never scored: scrubs the run-defining keys (STANDARDS section 4; test_play_vs_llm).
    "play-vs-llm.sh": "strips",
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
    "setup.sh": "mention",
}


def naming_files():
    # No \b: git grep -E uses the system's POSIX regex, and macOS's reads \b as a plain b, which matched no file.
    out = subprocess.run(["git", "grep", "-lE",
                          r"forge\.spike\.LlmSpike(Headless|Gui)([^A-Za-z0-9_]|$)|run-llm-(headless|spike)\.sh", "--",
                          "*.py", "*.sh", ":!eval/tests/*"], cwd=str(evalpath.REPO), stdout=subprocess.PIPE,
                         universal_newlines=True)
    return sorted(out.stdout.split())


class LaunchersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = naming_files()

    def test_every_file_that_names_a_game_launch_is_classified(self):
        self.assertIn("eval/run_batch.py", self.files)   # the search found the reference launcher
        self.assertEqual([f for f in self.files if f not in LAUNCHERS], [],
                         "classify it in LAUNCHERS and, if it launches games, say how they get their conditions "
                         "(docs/STANDARDS.md section 4)")

    def test_no_classified_launcher_is_gone(self):
        self.assertEqual(sorted(set(LAUNCHERS) - set(self.files)), [], "drop a file that no longer launches")

    def test_every_class_is_known_and_a_stripper_names_the_keys_it_strips(self):
        kinds = {"pins", "through run_batch", "strips", "diagnostic", "launcher", "mention"}
        self.assertEqual({f: k for f, k in LAUNCHERS.items() if k not in kinds}, {})
        for f, kind in LAUNCHERS.items():
            if kind == "strips":
                text = (evalpath.REPO / f).read_text(encoding="utf-8")
                self.assertRegex(text, r"PINNED_ENV_KEYS|RUN_DEFINING_KEYS", f)

    def test_the_standards_name_every_diagnostic(self):
        text = (evalpath.REPO / "docs" / "STANDARDS.md").read_text(encoding="utf-8")
        start = text.index("**An opt-in flag is a condition.**")
        text = text[start:text.index("\n- **", start)]   # the rule itself, not a mention elsewhere in the file
        for f, kind in LAUNCHERS.items():
            if kind == "diagnostic":
                self.assertIn(os.path.basename(f), text, f)


if __name__ == "__main__":
    unittest.main()
