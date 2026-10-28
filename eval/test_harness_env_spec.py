"""harness_env_spec.py: what makes an episode spec invalid, and that the shipped one is valid against this repo."""
import copy
import json
import os
import subprocess
import tempfile
import unittest

import harness_env_spec as hs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(REPO, "eval", "harness_env", "episode-v0.json")


def shipped():
    with open(SHIPPED, encoding="utf-8") as f:
        return json.load(f)


class GlobTest(unittest.TestCase):
    def test_a_double_star_crosses_directories_and_a_single_star_does_not(self):
        self.assertTrue(hs.matches("forge-overlay/a/b/C.java", "forge-overlay/**"))
        self.assertTrue(hs.matches("eval/decks/burn.txt", "eval/decks/*.txt"))
        self.assertFalse(hs.matches("eval/decks/old/burn.txt", "eval/decks/*.txt"))
        self.assertTrue(hs.matches("run-llm-spike.sh", "run-llm-*.sh"))
        self.assertFalse(hs.matches("xforge-overlay/a", "forge-overlay/**"))

    def test_a_directory_glob_matches_the_directory_itself(self):
        self.assertTrue(hs.matches(".git", ".git/**"))


class SpecProblemsTest(unittest.TestCase):
    def test_the_shipped_spec_is_valid_against_this_repo(self):
        self.assertEqual(hs.problems(shipped(), REPO), [])

    def test_a_missing_key_is_named(self):
        s = shipped()
        del s["caps"]
        self.assertIn("missing key: caps", hs.problems(s))

    def test_a_wrong_format_is_refused(self):
        s = shipped()
        s["format"] = "harness-env-episode/9"
        self.assertTrue(any("format" in p for p in hs.problems(s)))

    def test_dropping_an_always_withheld_path_is_refused(self):
        s = shipped()
        s["withheld"].remove("forge-overlay/**")
        self.assertIn("withheld must include forge-overlay/**", hs.problems(s))

    def test_a_copy_that_releases_the_solution_is_refused(self):
        s = shipped()
        s["copy"].append({"from": "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/AnswerTool.java",
                          "to": "spec/x.java"})
        self.assertTrue(any("releases" in p and "AnswerTool" in p for p in hs.problems(s)))

    def test_a_copy_destination_may_not_escape_the_sandbox(self):
        for bad in ("../x", "/etc/x", "spec/../../x"):
            s = shipped()
            s["copy"][0]["to"] = bad
            self.assertTrue(any("escapes" in p for p in hs.problems(s)), bad)

    def test_a_copy_that_matches_nothing_is_reported(self):
        s = shipped()
        s["copy"].append({"from": "eval/no-such-*.txt", "to": "dev/"})
        self.assertTrue(any("matches no file" in p for p in hs.problems(s, REPO)))

    def test_caps_must_be_positive_and_network_closed(self):
        s = shipped()
        s["caps"]["wall_hours"] = 0
        s["agent"]["network"] = "internet"
        ps = hs.problems(s)
        self.assertTrue(any("wall_hours" in p for p in ps))
        self.assertTrue(any("network" in p for p in ps))

    def test_the_task_statement_must_be_delivered(self):
        s = shipped()
        s["copy"] = [c for c in s["copy"] if c["from"] != s["task"]]
        self.assertTrue(any("task statement" in p for p in hs.problems(s)))

    def test_the_dev_slice_is_off_unless_said_and_said_as_a_boolean(self):
        self.assertIs(shipped()["grader"]["dev_slice"], False)
        s = shipped()
        s["grader"]["dev_slice"] = "yes"
        self.assertTrue(any("dev_slice" in p for p in hs.problems(s)))

    def test_a_done_signal_is_required(self):
        s = shipped()
        s["done"] = {"on_cap": "grade-last-checkpoint"}
        self.assertTrue(any("done" in p for p in hs.problems(s)))

    def test_load_raises_with_every_problem(self):
        s = copy.deepcopy(shipped())
        del s["id"]
        s["forbidden_tokens"] = []
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(s, f)
        with self.assertRaises(hs.SpecError) as e:
            hs.load(path)
        self.assertIn("missing key: id", str(e.exception))
        self.assertIn("forbidden_tokens", str(e.exception))


class ExpandCopyTest(unittest.TestCase):
    def test_the_hook_probe_decks_are_excluded_and_each_deck_keeps_its_name(self):
        pairs = dict(hs.expand_copy(shipped(), REPO))
        self.assertEqual(pairs["eval/decks/burn.txt"], "dev/decks/burn.txt")
        self.assertFalse(any("hook-" in src for src in pairs))
        self.assertEqual(pairs["eval/harness_env/TASK.md"], "TASK.md")

    def test_a_copy_naming_one_ignored_file_takes_it_but_a_glob_does_not(self):
        # The Comprehensive Rules text is downloaded and git-ignored (eval/fetch_comp_rules.py), and the spec names
        # it by its path; run output under an ignored directory must never be swept in by a glob.
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", repo], check=True)
        for rel, text in ((".gitignore", "data/rules/rules.txt\nruns/\n"), ("data/rules/rules.txt", "rules\n"),
                          ("runs/g1/game.jsonl", "{}\n"), ("data/rules/README.md", "readme\n")):
            os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
            with open(os.path.join(repo, rel), "w") as f:
                f.write(text)
        spec = {"copy": [{"from": "data/rules/rules.txt", "to": "spec/rules.txt"},
                         {"from": "runs/**", "to": "out/"}, {"from": "data/rules/*", "to": "d/"}]}
        self.assertEqual(sorted(hs.expand_copy(spec, repo)),
                         [("data/rules/README.md", "d/README.md"), ("data/rules/rules.txt", "spec/rules.txt")])


if __name__ == "__main__":
    unittest.main()
