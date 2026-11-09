#!/usr/bin/env python3
"""Tests for eval/rl/eval_only.py: the one guard that keeps eval data out of training data, and its call sites
(MtgEnv.reset and rollout.main). The environment is mtg_env's stub JVM (test_mtg_env.py).

    python3 eval/tests/test_eval_only.py
"""
import io
import json
import os
import contextlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "tests"))
import evalpath  # noqa: E402,F401
import eval_only  # noqa: E402
import mtg_env  # noqa: E402
import puzzle_format  # noqa: E402
import puzzle_twin  # noqa: E402
import rollout  # noqa: E402
from test_mtg_env import stub_launcher  # noqa: E402

OPEN = ROOT / "eval/puzzles/rules-pilot/RP_017.pzl"
HELDOUT = ROOT / "eval/puzzles/hard/gen/drain_0026d851c85f.pzl"
BURN = ROOT / "eval/decks/burn.txt"
GAME = {"seed": 1000, "deck": str(BURN)}


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

    def _twin(self, tag, orig_id, orig_pzl, twin_id=None):
        """A real twin of the board `orig_pzl` (a path): its renamed .pzl and manifest under tmp/<tag>, and the record
        that names them. The record's `pzl` is absolute, so no repository is needed to find the files."""
        orig_pzl = Path(orig_pzl)
        text = orig_pzl.read_text(encoding="utf-8")
        names = puzzle_twin.board_names(text) - puzzle_twin.KEEP_NAMES
        mapping = puzzle_twin.assign_twins(names, seed=1, real_names=set())
        twin_id = twin_id or orig_id + "-twin"
        d = self.tmp / tag
        (d / "pzl").mkdir(parents=True)
        board = d / "pzl" / (twin_id + ".pzl")
        board.write_text(puzzle_twin.twin_pzl(text, mapping), encoding="utf-8")
        scripts = {mapping[n]: puzzle_format.sha256_bytes(("script " + n).encode()) for n in sorted(names)}
        (d / puzzle_twin.MANIFEST).write_text(json.dumps({
            "cards": [{"original": n, "twin": mapping[n], "twin_sha256": scripts[mapping[n]]} for n in sorted(names)],
            "puzzles": [{"id": orig_id, "twin_id": twin_id, "pzl": "pzl/%s.pzl" % twin_id,
                         "original_pzl": str(orig_pzl), "cards": sorted(names),
                         "original_pzl_sha256": puzzle_format.sha256_file(orig_pzl)}]}))
        rec = {"id": twin_id, "split": puzzle_format.split_for(orig_id), "twin_of": orig_id, "pzl": str(board),
               "pzl_sha256": puzzle_format.sha256_file(board),
               "original_pzl_sha256": puzzle_format.sha256_file(orig_pzl),
               "twin_cards": scripts, "twin_set_sha256": puzzle_format.twin_set_sha(scripts)}
        return rec, board

    def _suite(self, *records):
        d = self.tmp / "suites"
        d.mkdir(exist_ok=True)
        suite_with(d, *records)
        return d

    @staticmethod
    def _heldout(reasons):
        return any("heldout puzzle" in w for w in reasons)

    def _ids(self):
        held = next(i for i in ("o%d" % k for k in range(200)) if puzzle_format.split_for(i) == "heldout")
        open_ = next(i for i in ("p%d" % k for k in range(200)) if puzzle_format.split_for(i) == "open")
        return held, open_

    def test_a_verified_twin_is_judged_by_the_board_it_renames(self):
        held, open_ = self._ids()
        h_rec = {"id": held, "split": "heldout", "pzl_sha256": puzzle_format.sha256_file(HELDOUT)}
        o_rec = {"id": open_, "split": "open", "pzl_sha256": puzzle_format.sha256_file(OPEN)}
        rec, twin = self._twin("h", held, HELDOUT)
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(h_rec, rec))))
        rec, twin = self._twin("o", open_, OPEN)
        self.assertEqual(eval_only.reasons(puzzle=twin, suites_dir=self._suite(o_rec, rec)), [])

    def test_a_twin_of_a_heldout_board_that_claims_an_open_original_reads_heldout(self):
        """The reviewer's cases: the twin of heldout H with split open, twin_of an open P and P's board hash, whether
        P is in the suite (A) or only the twin is (B)."""
        held, open_ = self._ids()
        h_rec = {"id": held, "split": "heldout", "pzl_sha256": puzzle_format.sha256_file(HELDOUT)}
        o_rec = {"id": open_, "split": "open", "pzl_sha256": puzzle_format.sha256_file(OPEN)}
        rec, twin = self._twin("h", held, HELDOUT)
        forged = dict(rec, id=open_ + "-twin", split="open", twin_of=open_,
                      original_pzl_sha256=puzzle_format.sha256_file(OPEN))
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(o_rec, forged))), "A")
        self.assertEqual(eval_only.puzzle_splits(self.tmp / "suites")[rec["pzl_sha256"]], {"open", "heldout"})
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(forged))), "B")
        self.assertEqual(eval_only.puzzle_splits(self.tmp / "suites")[rec["pzl_sha256"]], {"open", "heldout"})
        # one card of an honest open twin edited
        rec, twin = self._twin("o", open_, OPEN)
        twin.write_text(twin.read_text().replace("p1life=", "p1life=1", 1))
        edited = dict(rec, pzl_sha256=puzzle_format.sha256_file(twin))
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(o_rec, edited))))

    def test_a_twin_without_its_manifest_or_original_fails_closed(self):
        _, open_ = self._ids()
        o_rec = {"id": open_, "split": "open", "pzl_sha256": puzzle_format.sha256_file(OPEN)}
        rec, twin = self._twin("o", open_, OPEN)
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(rec))), "no original")
        (twin.parent.parent / puzzle_twin.MANIFEST).unlink()
        suites = self._suite(o_rec, rec)
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=suites)), "no manifest")
        for bare in ({"twin_of": open_, "original_pzl_sha256": "c" * 64}, {"twin_of": "nonexistent"}):
            twin = self.tmp / "bare.pzl"
            twin.write_text("[metadata]\nName:renamed\n")
            bare = dict(bare, id=open_ + "-twin", split="open", pzl_sha256=puzzle_format.sha256_file(twin))
            self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=self._suite(bare))), bare)

    def test_a_changed_twin_manifest_is_seen_though_no_suite_file_moved(self):
        # Kills: leave the manifest out of the cache key in puzzle_splits -> the split judged before the manifest
        # changed is served again, and a twin that no longer verifies still reads open.
        _, open_ = self._ids()
        o_rec = {"id": open_, "split": "open", "pzl": str(OPEN), "pzl_sha256": puzzle_format.sha256_file(OPEN)}
        rec, twin = self._twin("o", open_, OPEN)
        suites = self._suite(o_rec, rec)
        self.assertEqual(eval_only.reasons(puzzle=twin, suites_dir=suites), [], "verified: reads open")
        manifest = twin.parent.parent / puzzle_twin.MANIFEST
        m = json.loads(manifest.read_text())
        name = m["cards"][0]["twin"]
        # same size, other mtime: one script hash edited in place
        m["cards"][0]["twin_sha256"] = "f" * 64
        before = manifest.stat()
        manifest.write_text(json.dumps(m))
        os.utime(manifest, ns=(before.st_mtime_ns + 5 * 10**9,) * 2)
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=suites)), name)
        # the manifest restored: open again; then removed: heldout (an appearing or vanishing file is seen)
        m["cards"][0]["twin_sha256"] = rec["twin_cards"][name]
        manifest.write_text(json.dumps(m))
        os.utime(manifest, ns=(before.st_mtime_ns + 9 * 10**9,) * 2)
        self.assertEqual(eval_only.reasons(puzzle=twin, suites_dir=suites), [])
        manifest.unlink()
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=twin, suites_dir=suites)))

    def test_a_twin_of_a_twin_reads_heldout(self):
        """A chain: T2 is a verified twin of T1's board, T1 a verified twin of the open P. T2's original is a twin, not
        a non-twin suite record, so it does not read open."""
        _, open_ = self._ids()
        o_rec = {"id": open_, "split": "open", "pzl_sha256": puzzle_format.sha256_file(OPEN)}
        t1, t1_file = self._twin("t1", open_, OPEN)
        t2, t2_file = self._twin("t2", t1["id"], t1_file)
        self.assertEqual(t2["original_pzl_sha256"], t1["pzl_sha256"])
        self.assertEqual(eval_only.reasons(puzzle=t1_file, suites_dir=self._suite(o_rec, t1, t2)), [])
        self.assertTrue(self._heldout(eval_only.reasons(puzzle=t2_file, suites_dir=self._suite(o_rec, t1, t2))))

    def test_an_unlisted_puzzle_is_refused(self):
        p = self.tmp / "mine.pzl"
        p.write_text("[metadata]\nName:not in any suite\n")
        why = eval_only.reasons(puzzle=p)
        self.assertTrue(why and "not listed" in why[0], why)

    def test_every_suite_file_with_puzzles_counts_and_only_open_passes(self):
        p = self.tmp / "p.pzl"
        p.write_text("[metadata]\nName:y\n")
        h = puzzle_format.sha256_file(p)
        d = self.tmp / "suites"
        d.mkdir()
        (d / "greedy-check-v0.json").write_text(json.dumps({"puzzles": [{"id": "a", "split": "heldout", "pzl_sha256": h}]}))
        (d / "panels-v1.json").write_text(json.dumps({"panels": []}))   # no puzzles list: ignored
        self.assertTrue(any("heldout puzzle" in w for w in eval_only.reasons(puzzle=p, suites_dir=d)))
        (d / "greedy-check-v0.json").write_text(json.dumps({"puzzles": [{"id": "a", "pzl_sha256": h}]}))
        self.assertTrue(eval_only.reasons(puzzle=p, suites_dir=d))   # a record with no split is not open
        (d / "greedy-check-v0.json").write_text(json.dumps({"puzzles": [{"id": "a", "split": "calib", "pzl_sha256": h}]}))
        self.assertTrue(eval_only.reasons(puzzle=p, suites_dir=d))
        (d / "greedy-check-v0.json").write_text(json.dumps({"puzzles": [{"id": "a", "split": "open", "pzl_sha256": h}]}))
        self.assertEqual(eval_only.reasons(puzzle=p, suites_dir=d), [])

    def test_an_open_listed_puzzle_passes(self):
        self.assertEqual(eval_only.reasons(puzzle=OPEN), [])


class SuiteCacheTest(unittest.TestCase):
    """The split cache is keyed on each suite file's path, mtime and size: an edit shows through either way."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="test-rl-cache-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.f = self.d / "puzzles-x.json"

    def put(self, split, mtime_ns=None):
        self.f.write_text(json.dumps({"puzzles": [{"id": "a", "split": split, "pzl_sha256": "h"}]}))
        if mtime_ns is not None:
            os.utime(self.f, ns=(mtime_ns, mtime_ns))

    def test_a_changed_mtime_with_the_same_size_is_seen(self):
        self.put("open", 10**18)
        self.assertEqual(eval_only.puzzle_splits(self.d), {"h": {"open"}})
        self.put("oper", 2 * 10**18)   # same length, other mtime
        self.assertEqual(eval_only.puzzle_splits(self.d), {"h": {"oper"}})

    def test_a_changed_size_with_the_same_mtime_is_seen(self):
        self.put("open", 10**18)
        self.assertEqual(eval_only.puzzle_splits(self.d), {"h": {"open"}})
        self.put("heldout", 10**18)   # longer, same mtime
        self.assertEqual(eval_only.puzzle_splits(self.d), {"h": {"heldout"}})


class DeckTest(unittest.TestCase):
    def test_a_held_out_deck_is_refused_and_an_in_corpus_deck_is_not(self):
        for slug in ("painter", "sneak-and-show"):
            self.assertTrue(any("held-out deck" in w for w in eval_only.reasons(deck=ROOT / ("eval/decks/%s.txt" % slug))), slug)
        self.assertEqual(eval_only.reasons(deck=ROOT / "eval/decks/burn.txt"), [])

    def test_a_copied_or_reformatted_held_out_deck_is_still_held_out(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-rl-deck-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        text = (ROOT / "eval/decks/painter.txt").read_text()
        lines = text.splitlines()
        variants = {"copy.txt": text,
                    "blank.txt": text + "\n\n",
                    "spaces.txt": "\n".join("  " + l + "  " for l in lines) + "\n",
                    "shuffled.txt": "# my deck\n" + "\n\n".join(reversed(lines)) + "\n"}
        for name, body in variants.items():
            (tmp / name).write_text(body)
            self.assertTrue(any("held-out deck" in w for w in eval_only.reasons(deck=tmp / name)), name)
        (tmp / "other.txt").write_text(text.replace("4 Ancient Tomb", "3 Ancient Tomb"))
        self.assertEqual(eval_only.reasons(deck=tmp / "other.txt"), [])

    def test_a_deck_that_cannot_be_read_is_refused(self):
        self.assertTrue(eval_only.reasons(deck="/nonexistent/mine.txt"))

    def test_the_registry_is_read_once_per_judgement(self):
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=Path.read_text) as rt:
            eval_only.reasons(deck=BURN)
        self.assertEqual(sum(1 for c in rt.call_args_list if c.args[0].name == "holdouts.json"), 1)


class FailClosedRegistryTest(unittest.TestCase):
    """Each way the deck registry or a puzzle file can fail to be judged yields exactly one reason."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="test-rl-registry-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        patch = mock.patch.object(evalpath, "EVAL", self.d)
        patch.start()
        self.addCleanup(patch.stop)

    def one(self, fragment):
        why = eval_only.reasons(deck=BURN)
        self.assertEqual(len(why), 1, why)
        self.assertIn(fragment, why[0])

    def test_no_holdouts_file_refuses(self):
        self.one("cannot be checked")

    def test_a_registry_with_no_held_out_decks_refuses_as_vacuous(self):
        (self.d / "holdouts.json").write_text(json.dumps({"decks": {"burn": {"status": "open", "file": "eval/decks/burn.txt"}}}))
        self.one("vacuous")

    def test_invalid_json_refuses(self):
        (self.d / "holdouts.json").write_text("{not json")
        self.one("cannot be checked")

    def test_an_unreadable_puzzle_file_refuses(self):
        why = eval_only.reasons(puzzle=self.d / "missing.pzl")
        self.assertEqual(len(why), 1, why)
        self.assertIn("unreadable", why[0])


class HeldDeckCacheTest(unittest.TestCase):
    """The held-out deck hashes are cached on each held-out file's (path, mtime, size)."""

    def test_the_key_is_path_mtime_and_size_and_an_edit_shows_through(self):
        d = Path(tempfile.mkdtemp(prefix="test-rl-held-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "held.txt"
        f.write_text("4 Island\n")
        os.utime(f, ns=(10**18, 10**18))
        held = {"h": {"status": "held-out", "file": str(f)}}
        with mock.patch.object(evalpath, "REPO", Path("/")):
            first = eval_only._held_shas(held)
            self.assertEqual(list(eval_only._HELD_SHAS), [((str(f), 10**18, 9),)])
            f.write_text("4 Swamp\n")   # same size, other mtime
            os.utime(f, ns=(2 * 10**18, 2 * 10**18))
            self.assertNotEqual(eval_only._held_shas(held), first)
            f.write_text("40 Swamp\n")   # other size, same mtime
            os.utime(f, ns=(2 * 10**18, 2 * 10**18))
            self.assertEqual(eval_only._held_shas(held), {eval_only.deck_sha(f)})
            f.unlink()   # a missing file is a None hash, not an exception
            self.assertEqual(eval_only._held_shas(held), {None})


class DuplicateTest(unittest.TestCase):
    def test_duplicate_groups_are_named(self):
        self.assertEqual(eval_only.duplicate_groups(["a.pzl", "b.pzl"], [1, 20], 4), [])
        self.assertTrue(eval_only.duplicate_groups(["a.pzl", "./a.pzl"], [], 2))
        self.assertTrue(eval_only.duplicate_groups([], [5, 5], 1))
        self.assertTrue(eval_only.duplicate_groups([], [5, 7], 4))   # 5..8 and 7..10 share seeds 7, 8

    def test_a_puzzle_copied_under_another_name_is_a_duplicate(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-rl-dup-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copyfile(OPEN, tmp / "renamed.pzl")
        self.assertTrue(eval_only.duplicate_groups([str(OPEN), str(tmp / "renamed.pzl")], [], 1))


class FailClosedReaderTest(unittest.TestCase):
    """The readers re-judge each episode; they do not trust the eval_only key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-rl-readers-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, **ep):
        rec = {"type": "episode", "episode_id": "e", "group": "g", "reward": 1.0, "valid": True, "n_steps": 1,
               "info": {}, "guard": eval_only.GUARD_VERSION}
        rec.update(ep)
        step = {"type": "step", "episode_id": "e", "group": "g", "step": 1, "args": {"option": 2}, "accepted": True,
                "messages": [{"role": "user", "content": "q"}]}
        path = self.tmp / "r.jsonl"
        path.write_text(json.dumps(step) + "\n" + json.dumps({k: v for k, v in rec.items() if v is not None}) + "\n")
        return rollout.load_episodes(path)

    def counts(self, eps):
        return len(rollout.sft_examples(eps)), len(rollout.grpo_groups(eps))

    def test_an_unmarked_panel_seed_episode_trains_nothing(self):
        self.assertEqual(self.counts(self.write(seed=7800)), (0, 0))

    def test_an_unmarked_held_out_deck_episode_trains_nothing(self):
        sha = eval_only.deck_sha(ROOT / "eval/decks/painter.txt")
        self.assertEqual(self.counts(self.write(seed=3, deck="/tmp/mine.txt", deck_sha256=sha)), (0, 0))

    def test_an_unmarked_heldout_puzzle_episode_trains_nothing(self):
        sha = puzzle_format.sha256_file(HELDOUT)
        self.assertEqual(self.counts(self.write(puzzle="x.pzl", seed=1, puzzle_sha256=sha)), (0, 0))
        self.assertEqual(self.counts(self.write(puzzle="x.pzl", seed=1, stamps={"pzl_sha256": sha})), (0, 0))

    def test_a_puzzle_episode_with_no_content_hash_trains_nothing(self):
        self.assertEqual(self.counts(self.write(puzzle="x.pzl", seed=1)), (0, 0))

    def test_a_game_episode_with_no_deck_trains_nothing(self):
        self.assertEqual(self.counts(self.write(seed=1000)), (0, 0))
        self.assertTrue(any("no deck" in w for w in eval_only.reasons(seeds=[1000], game=True)))

    def test_the_recorded_puzzle_hashes_are_both_judged(self):
        good, bad = puzzle_format.sha256_file(OPEN), puzzle_format.sha256_file(HELDOUT)
        for top, stamp in ((good, bad), (bad, good)):
            eps = self.write(puzzle="x.pzl", seed=1, puzzle_sha256=top, stamps={"pzl_sha256": stamp})
            self.assertEqual(self.counts(eps), (0, 0))
        why = eval_only.episode_reasons(eps[0])
        self.assertTrue(any("disagree" in w for w in why), why)
        self.assertTrue(any("heldout puzzle" in w for w in why), why)

    def test_a_readable_puzzle_or_deck_is_hashed_again(self):
        good = puzzle_format.sha256_file(OPEN)
        self.assertEqual(self.counts(self.write(puzzle=str(HELDOUT), seed=1, puzzle_sha256=good)), (0, 0))
        why = eval_only.episode_reasons(self.write(puzzle=str(HELDOUT), seed=1, puzzle_sha256=good)[0])
        self.assertTrue(any("disagrees" in w for w in why), why)
        painter = ROOT / "eval/decks/painter.txt"
        self.assertEqual(self.counts(self.write(seed=3, deck=str(painter), deck_sha256=eval_only.deck_sha(BURN))), (0, 0))

    def test_an_unreadable_puzzle_falls_back_to_the_recorded_stamp_hash(self):
        sha = puzzle_format.sha256_file(OPEN)
        self.assertEqual(self.counts(self.write(puzzle="/nonexistent/x.pzl", seed=1, stamps={"pzl_sha256": sha})), (1, 1))

    def test_an_unreadable_puzzle_with_no_hash_says_it_has_none(self):
        why = eval_only.episode_reasons(self.write(puzzle="/nonexistent/x.pzl", seed=1)[0])
        self.assertTrue(any("no content hash" in w for w in why), why)

    def test_the_default_attempt_seed_of_a_puzzle_and_no_seed_for_a_game(self):
        sha = puzzle_format.sha256_file(OPEN)
        self.assertEqual(self.counts(self.write(puzzle=str(OPEN), puzzle_sha256=sha)), (1, 1))   # attempt seed 1
        self.assertEqual(self.counts(self.write(deck=str(BURN))), (0, 0))
        self.assertIn("game episode records no seed", eval_only.episode_reasons(self.write(deck=str(BURN))[0]))

    def test_a_clean_guarded_episode_does_train(self):
        sha = puzzle_format.sha256_file(OPEN)
        self.assertEqual(self.counts(self.write(puzzle=str(OPEN), seed=1, puzzle_sha256=sha)), (1, 1))
        self.assertEqual(self.counts(self.write(**GAME)), (1, 1))

    def test_an_episode_without_the_guard_field_is_dropped(self):
        self.assertEqual(self.write(guard=None, **GAME), [])

    def test_a_list_of_episodes_handed_to_the_readers_is_judged_too(self):
        e = self.write(**GAME)[0]
        e["seed"] = 7800   # not through load_episodes
        self.assertEqual(self.counts([e]), (0, 0))
        e.pop("guard")
        e["seed"] = 1000
        self.assertEqual(self.counts([e]), (0, 0))


class CallSiteTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-rl-guard-env-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def env(self, **kw):
        e = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=1), out_root=self.root, step_timeout_s=20,
                           first_timeout_s=20, **kw)
        self.addCleanup(e.close)
        return e

    def test_the_constructor_has_no_switch_for_the_guard(self):
        with self.assertRaises(TypeError):
            mtg_env.MtgEnv(guard=lambda **kw: [])
        self.assertFalse(hasattr(self.env(), "guard"))   # nothing on the env to replace: tests patch eval_only.reasons

    def test_reset_calls_eval_only_reasons(self):
        with mock.patch.object(eval_only, "reasons", return_value=["patched reason"]) as r:
            with self.assertRaises(mtg_env.EnvError) as cm:
                self.env().reset(puzzle=str(OPEN), seed=1)
        self.assertIn("patched reason", str(cm.exception))
        self.assertEqual(r.call_count, 1)

    def test_a_game_with_no_deck_is_refused_unless_allowed(self):
        with self.assertRaises(mtg_env.EnvError) as cm:
            self.env().reset(seed=1000)
        self.assertIn("no deck", str(cm.exception))
        env = self.env(allow_eval_only=True)
        env.reset(seed=1000)
        self.assertTrue(any("no deck" in w for w in env.eval_only))

    def test_a_cut_episode_keeps_its_eval_only_mark(self):
        e = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=50), out_root=self.root, step_timeout_s=20,
                           first_timeout_s=20, allow_eval_only=True)
        self.addCleanup(e.close)
        with open(Path(self.root) / "r.jsonl", "w") as out:
            rec = rollout.run_episode(e, rollout.first_policy, "g", "g#0", out,
                                      {"puzzle": str(HELDOUT), "seed": 1}, max_steps=3)
        self.assertEqual(rec["info"]["error"], "max_steps")
        self.assertTrue(any("heldout puzzle" in w for w in rec["eval_only"]), rec.get("eval_only"))

    def test_the_episode_record_carries_the_guard_and_the_start(self):
        e = self.env()
        path = Path(self.root) / "r.jsonl"
        with open(path, "w") as out:
            rec = rollout.run_episode(e, lambda o: {"option": 2}, "g", "g#0", out, {"puzzle": str(OPEN), "seed": 1})
        self.assertEqual(rec["guard"], eval_only.GUARD_VERSION)
        self.assertEqual(rec["puzzle_sha256"], puzzle_format.sha256_file(OPEN))
        self.assertNotIn("eval_only", rec)
        deck = ROOT / "eval/decks/burn.txt"
        with open(path, "w") as out:
            rec = rollout.run_episode(self.env(), lambda o: {"option": 2}, "g", "g#1", out,
                                      {"seed": 1000, "deck": str(deck)})
        self.assertEqual((rec["deck"], rec["deck_sha256"]), (str(deck), eval_only.deck_sha(deck)))

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


class MutationPinsTest(unittest.TestCase):
    """Pins found by mutation testing (eval/research/2026-11-08-scorer-mutation.md); each names the mutant it kills."""

    def test_a_reason_lists_at_most_five_seeds_from_each_source(self):
        # `hit[:5]` -> `[:6]` in both the reserved-block and the seed-bank reason
        why = eval_only._seed_reasons(range(501, 511))
        self.assertEqual(why, ["reserved seed block frozen-legacy: [501, 502, 503, 504, 505]",
                               "seed bank frozen-legacy is eval-only in seed_registry: [501, 502, 503, 504, 505]"])

    def test_only_the_seeds_a_run_would_play_twice_are_named(self):
        # `seeds.count(s) > 1` -> `>= 1` named every seed; `[:5]` -> `[:6]` named six
        self.assertEqual(eval_only.duplicate_groups(["a.pzl"], [100, 103], 4),
                         ["game seeds [103] would be played twice (groups of 4 from [100, 103])"])
        self.assertEqual(eval_only.duplicate_groups(["a.pzl"], [100, 100], 8),
                         ["game seeds [100, 101, 102, 103, 104] would be played twice (groups of 8 from [100, 100])"])
        self.assertEqual(eval_only.duplicate_groups(["a.pzl"], [100, 108], 8), [])

    def test_a_missing_file_has_a_stat_key_of_its_path_alone(self):
        # `return (path, None, None)` -> `None`: the cache key for an unreadable deck must still carry its path
        gone = Path(tempfile.gettempdir()) / "eval-only-no-such-file.txt"
        self.assertEqual(eval_only._stat_key(gone), (str(gone), None, None))


if __name__ == "__main__":
    unittest.main()
