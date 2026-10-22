"""harness_env_sandbox.py: the agent's starting sandbox holds what the spec releases and nothing that leaks.

Each leak test plants one leak the way it would really happen (a withheld file copied under another name, our class
name in a released document, a repository with history, a symlink back to the repository, a stray file) and
asserts the check names it; the clean build must pass, or the checks would be decoration.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import harness_env_sandbox as sb
import harness_env_spec as hs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOLUTION = "final class PlayerControllerLLM { /* HARNESS PATCH */ }\n"
LEDGER = "signature\timpl\tkind\nchooseBinary[x]\tLLM\tstrategic\n"


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def fixture_spec():
    with open(os.path.join(REPO, "eval", "harness_env", "episode-v0.json"), encoding="utf-8") as f:
        s = json.load(f)
    s["copy"] = [
        {"from": "eval/harness_env/TASK.md", "to": "TASK.md"},
        {"from": "data/rules/Rules.txt", "to": "spec/Rules.txt"},
        {"from": "eval/decks/*.txt", "exclude": ["eval/decks/hook-*"], "to": "dev/decks/"},
    ]
    return s


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        write(self.repo, "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/Seat.java", SOLUTION)
        write(self.repo, "docs/harness/controller-ownership.tsv", LEDGER)
        write(self.repo, "eval/harness_env/TASK.md", "Build a seat.\n")
        write(self.repo, "eval/decks/burn.txt", "20 Mountain\n40 Lightning Bolt\n")
        write(self.repo, "eval/decks/hook-flip.txt", "60 Krark's Thumb\n")
        write(self.repo, "data/rules/Rules.txt", "100.1. These are the rules.\n")
        self.spec = fixture_spec()
        self.box = os.path.join(self.tmp, "box")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self):
        return sb.build(self.spec, self.repo, self.box)

    def test_a_clean_build_passes_and_holds_exactly_what_was_released(self):
        manifest = self.build()
        self.assertEqual(sb.leak_problems(self.spec, self.repo, self.box, manifest), [])
        self.assertEqual(sorted(manifest["files"]), ["TASK.md", "dev/decks/burn.txt", "spec/Rules.txt"])
        self.assertTrue(os.path.isdir(os.path.join(self.box, "overlay")))

    def test_the_hook_probe_decks_stay_out(self):
        self.build()
        self.assertFalse(os.path.exists(os.path.join(self.box, "dev", "decks", "hook-flip.txt")))

    def test_a_withheld_file_under_another_name_is_caught_by_its_content(self):
        manifest = self.build()
        shutil.copy(os.path.join(self.repo, "docs/harness/controller-ownership.tsv"),
                    os.path.join(self.box, "spec", "hooks.tsv"))
        manifest["files"]["spec/hooks.tsv"] = sb.sha256(os.path.join(self.box, "spec", "hooks.tsv"))
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("same bytes as withheld docs/harness/controller-ownership.tsv" in p for p in found), found)

    def test_a_solution_name_in_a_released_document_is_caught(self):
        write(self.repo, "eval/harness_env/TASK.md", "Build a seat like our PlayerControllerLLM.\n")
        manifest = self.build()
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("TASK.md" in p and "PlayerControllerLLM" in p for p in found), found)

    def test_a_file_the_manifest_does_not_list_is_caught(self):
        manifest = self.build()
        write(self.box, "spec/extra.txt", "hello\n")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertIn("spec/extra.txt is not in the manifest", found)

    def test_a_file_whose_bytes_changed_after_the_build_is_caught(self):
        manifest = self.build()
        write(self.box, "spec/Rules.txt", "100.1. Edited.\n")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertIn("spec/Rules.txt differs from the manifest", found)

    def test_the_agent_directories_start_empty(self):
        manifest = self.build()
        write(self.box, "overlay/forge-gui-desktop/src/main/java/A.java", "class A {}\n")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("overlay/forge-gui-desktop/src/main/java/A.java" in p for p in found), found)

    def test_a_forbidden_path_fragment_is_caught_whatever_the_bytes(self):
        manifest = self.build()
        write(self.box, "spec/surface_gaps.txt", "# nothing\n")
        manifest["files"]["spec/surface_gaps.txt"] = sb.sha256(os.path.join(self.box, "spec/surface_gaps.txt"))
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("surface_gaps" in p and "path" in p for p in found), found)

    def test_a_symlink_is_caught(self):
        manifest = self.build()
        os.symlink(self.repo, os.path.join(self.box, "notes", "repo"))
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("symlink" in p for p in found), found)

    def test_a_repository_with_history_is_caught_and_a_fresh_one_passes(self):
        manifest = self.build()
        git(self.box, "init", "-q")
        git(self.box, "add", "-A")
        git(self.box, "commit", "-q", "-m", "initial task environment")
        self.assertEqual(sb.leak_problems(self.spec, self.repo, self.box, manifest), [])
        write(self.box, "notes/n.txt", "x\n")
        git(self.box, "add", "-A")
        git(self.box, "commit", "-q", "-m", "second")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("2 commits" in p for p in found), found)

    def test_a_history_squashed_to_one_commit_but_not_pruned_is_caught(self):
        manifest = self.build()
        git(self.box, "init", "-q")
        write(self.box, "notes/answer.txt", "the solution\n")
        git(self.box, "add", "-A")
        git(self.box, "commit", "-q", "-m", "with the answer")
        os.remove(os.path.join(self.box, "notes", "answer.txt"))
        git(self.box, "add", "-A")
        git(self.box, "commit", "-q", "--amend", "-m", "initial task environment")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("unreachable" in p for p in found), found)

    def test_a_repository_with_a_remote_is_caught(self):
        manifest = self.build()
        git(self.box, "init", "-q")
        git(self.box, "add", "-A")
        git(self.box, "commit", "-q", "-m", "initial")
        git(self.box, "remote", "add", "origin", "https://example.invalid/x.git")
        found = sb.leak_problems(self.spec, self.repo, self.box, manifest)
        self.assertTrue(any("remote" in p for p in found), found)

    def test_building_into_a_non_empty_directory_is_refused(self):
        write(self.box, "old.txt", "x\n")
        with self.assertRaises(sb.BuildError):
            self.build()


class EngineCheckTest(unittest.TestCase):
    def setUp(self):
        self.forge = tempfile.mkdtemp()
        write(self.forge, "forge-game/src/main/java/forge/game/zone/MagicStack.java", "class MagicStack {}\n")
        git(self.forge, "init", "-q")
        git(self.forge, "add", "-A")
        git(self.forge, "commit", "-q", "-m", "upstream")
        self.pin = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.forge, capture_output=True,
                                  text=True, check=True).stdout.strip()

    def tearDown(self):
        shutil.rmtree(self.forge)

    def test_a_clean_checkout_at_the_pin_passes(self):
        self.assertEqual(sb.engine_problems(self.forge, self.pin), [])

    def test_another_commit_is_refused(self):
        self.assertTrue(any("not the pin" in p for p in sb.engine_problems(self.forge, "0" * 40)))

    def test_an_engine_patch_is_caught(self):
        write(self.forge, "forge-game/src/main/java/forge/game/zone/MagicStack.java", "class MagicStack { } // patched\n")
        self.assertTrue(any("MagicStack.java" in p for p in sb.engine_problems(self.forge, self.pin)))

    def test_our_installed_overlay_is_caught_even_when_git_ignores_it(self):
        write(self.forge, ".gitignore", "target/\n")
        git(self.forge, "add", "-A")
        git(self.forge, "commit", "-q", "-m", "ignore target")
        pin = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.forge, capture_output=True, text=True).stdout.strip()
        write(self.forge, "forge-gui-desktop/target/classes/forge/spike/Seat.class", "x")
        found = sb.engine_problems(self.forge, pin)
        self.assertTrue(any("forge/spike" in p for p in found), found)


class RealRepoTest(unittest.TestCase):
    def test_the_shipped_spec_builds_a_sandbox_from_this_repo_with_no_leak(self):
        spec = hs.load(os.path.join(REPO, "eval", "harness_env", "episode-v0.json"), REPO)
        box = os.path.join(tempfile.mkdtemp(), "box")
        try:
            manifest = sb.build(spec, REPO, box)
            self.assertEqual(sb.leak_problems(spec, REPO, box, manifest), [])
            self.assertIn("spec/SPEC.md", manifest["files"])
            self.assertFalse(any(f.startswith("dev/decks/hook-") for f in manifest["files"]))
        finally:
            shutil.rmtree(os.path.dirname(box))


if __name__ == "__main__":
    unittest.main()
