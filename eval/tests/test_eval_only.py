#!/usr/bin/env python3
"""Tests for eval/rl/eval_only.py: the one guard that keeps eval data out of training data, and its call sites
(MtgEnv.reset and rollout.main). The environment is mtg_env's stub JVM (test_mtg_env.py).

    python3 eval/tests/test_eval_only.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "tests"))
import evalpath  # noqa: E402,F401
import eval_only  # noqa: E402
import mtg_env  # noqa: E402
import puzzle_format  # noqa: E402
import rollout  # noqa: E402
from test_mtg_env import stub_launcher  # noqa: E402

OPEN = ROOT / "eval/puzzles/rules-pilot/RP_017.pzl"
HELDOUT = ROOT / "eval/puzzles/hard/gen/drain_0026d851c85f.pzl"


def suite_with(dirpath, *records):
    (Path(dirpath) / "puzzles-x.json").write_text(json.dumps({"puzzles": list(records)}))


class SeedTest(unittest.TestCase):
    def test_a_panel_seed_the_registry_marks_eval_only_is_refused(self):
        # 7800..7999 are STR-AI panel seeds: eval-only in seed_registry, not in seed_policy's reserved blocks
        why = eval_only.reasons(seeds=[7800])
        self.assertTrue(why and "eval-only" in why[0], why)

    def test_the_attempt_seed_of_a_puzzle_is_checked(self):
        self.assertEqual(eval_only.reasons(puzzle=OPEN, seeds=[1, 2, 3]), [])
        self.assertTrue(any("frozen-legacy" in w for w in eval_only.reasons(puzzle=OPEN, seeds=[500, 501])))

    def test_a_clean_seed_passes(self):
        self.assertEqual(eval_only.reasons(seeds=[1000]), [])


class PuzzleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-rl-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_copied_heldout_puzzle_is_still_heldout(self):
        copy = self.tmp / "renamed.pzl"
        shutil.copyfile(HELDOUT, copy)
        self.assertTrue(any("heldout puzzle" in w for w in eval_only.reasons(puzzle=copy)))

    def test_a_record_whose_split_disagrees_with_the_stem_hash_follows_the_record(self):
        # 180 of the suites' records have a split that puzzle_format.split_for(stem) would not give
        recs = [r for f in (ROOT / "eval/suites").glob("puzzles-*.json") for r in json.loads(f.read_text())["puzzles"]
                if r["split"] == "heldout" and puzzle_format.split_for(Path(r["pzl"]).stem) == "open"
                and not r["pzl"].startswith("forge:")]
        self.assertTrue(recs)
        self.assertTrue(any("heldout puzzle" in w for w in eval_only.reasons(puzzle=ROOT / recs[0]["pzl"])))

    def test_heldout_if_any_record_says_heldout(self):
        p = self.tmp / "p.pzl"
        p.write_text("[metadata]\nName:x\n")
        h = puzzle_format.sha256_file(p)
        d = self.tmp / "suites"
        d.mkdir()
        suite_with(d, {"id": "a", "split": "open", "pzl_sha256": h, "pzl": "p.pzl"},
                   {"id": "b", "split": "heldout", "pzl_sha256": h, "pzl": "q.pzl"})
        self.assertTrue(any("heldout puzzle" in w for w in eval_only.reasons(puzzle=p, suites_dir=d)))
        suite_with(d, {"id": "a", "split": "open", "pzl_sha256": h, "pzl": "p.pzl"})
        self.assertEqual(eval_only.reasons(puzzle=p, suites_dir=d), [])

    def test_an_unlisted_puzzle_is_refused(self):
        p = self.tmp / "mine.pzl"
        p.write_text("[metadata]\nName:not in any suite\n")
        why = eval_only.reasons(puzzle=p)
        self.assertTrue(why and "not listed" in why[0], why)

    def test_an_open_listed_puzzle_passes(self):
        self.assertEqual(eval_only.reasons(puzzle=OPEN), [])


class DeckTest(unittest.TestCase):
    def test_a_held_out_deck_is_refused_and_an_in_corpus_deck_is_not(self):
        for slug in ("painter", "sneak-and-show"):
            self.assertTrue(any("held-out deck" in w for w in eval_only.reasons(deck=ROOT / ("eval/decks/%s.txt" % slug))), slug)
        self.assertEqual(eval_only.reasons(deck=ROOT / "eval/decks/burn.txt"), [])


class DuplicateTest(unittest.TestCase):
    def test_duplicate_groups_are_named(self):
        self.assertEqual(eval_only.duplicate_groups(["a.pzl", "b.pzl"], [1, 20], 4), [])
        self.assertTrue(eval_only.duplicate_groups(["a.pzl", "./a.pzl"], [], 2))
        self.assertTrue(eval_only.duplicate_groups([], [5, 5], 1))
        self.assertTrue(eval_only.duplicate_groups([], [5, 7], 4))   # 5..8 and 7..10 share seeds 7, 8


class CallSiteTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-rl-guard-env-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def env(self, **kw):
        e = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=1), out_root=self.root, step_timeout_s=20,
                           first_timeout_s=20, **kw)
        self.addCleanup(e.close)
        return e

    def test_reset_refuses_eval_only_data_unless_allowed(self):
        env = self.env()
        with self.assertRaises(mtg_env.EnvError):
            env.reset(puzzle=str(HELDOUT), seed=1)
        with self.assertRaises(mtg_env.EnvError):
            env.reset(seed=7800)
        with self.assertRaises(mtg_env.EnvError):
            env.reset(puzzle=str(OPEN), seed=501)   # a puzzle's attempt seed is a seed too
        with self.assertRaises(mtg_env.EnvError):
            env.reset(seed=3, deck=str(ROOT / "eval/decks/painter.txt"))
        self.assertIsNone(env.proc)   # nothing was started

    def test_reset_with_allow_marks_the_episode_eval_only(self):
        env = self.env(allow_eval_only=True)
        env.reset(puzzle=str(HELDOUT), seed=1)
        _, _, _, info = env.step(2)
        self.assertTrue(any("heldout puzzle" in w for w in info["eval_only"]))

    def test_run_episode_marks_the_record_and_the_readers_skip_it(self):
        env = self.env(allow_eval_only=True)
        path = Path(self.root) / "r.jsonl"
        with open(path, "w") as out:
            rec = rollout.run_episode(env, lambda o: {"option": 2}, "g", "r:g#0", out,
                                      {"puzzle": str(HELDOUT), "seed": 1}, run_id="r")
        self.assertTrue(rec["eval_only"])
        self.assertEqual(rollout.sft_examples(rollout.load_episodes(path)), [])
        self.assertEqual(rollout.grpo_groups(rollout.load_episodes(path)), {})

    def test_the_trainers_own_loop_hits_the_guard(self):
        env = self.env()   # the README's snippet: no allow flag
        with open(Path(self.root) / "r.jsonl", "w") as out, self.assertRaises(mtg_env.EnvError):
            rollout.run_episode(env, rollout.first_policy, "g", "g#0", out, {"puzzle": str(HELDOUT), "seed": 1})


if __name__ == "__main__":
    unittest.main()
