#!/usr/bin/env python3
"""Tests for eval/seed_registry.py. Stdlib only: python3 eval/test_seed_registry.py

The load-bearing cases are the ones where a naive implementation passes when it
should fail: a corpus with no readable seed field, a band relabelled after the
harvest, and an eval-only bank nobody has played yet.
"""

import json
import tempfile
import unittest
from pathlib import Path

import seed_registry as sr


def write_registry(tmp, banks):
    p = Path(tmp) / "registry.json"
    p.write_text(json.dumps({"version": 1, "banks": banks}), encoding="utf-8")
    return p


def write_corpus(root, name, rows):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


def write_run(root, name, seeds):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    for s in seeds:
        (d / ("game-%d-1784000000000.jsonl" % s)).write_text(
            json.dumps({"t": "game", "seed": s, "winner": "A"}) + "\n", encoding="utf-8")
    return d


EVAL_BANK = {"name": "eval-holdout", "policy": "eval-only", "ranges": [[9000, 9099]]}
TRAIN_BANK = {"name": "train", "policy": "train", "ranges": [[100, 500]]}
HARNESS_BANK = {"name": "canary", "policy": "harness", "ranges": [[42, 42]]}


class TestRegistryLoading(unittest.TestCase):
    def test_classifies_by_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK, TRAIN_BANK]))
            self.assertEqual(reg.policy_for(9000), "eval-only")
            self.assertEqual(reg.policy_for(9099), "eval-only")
            self.assertEqual(reg.policy_for(250), "train")
            self.assertEqual(reg.policy_for(9100), sr.UNREGISTERED)
            self.assertEqual(reg.bank_name_for(9050), "eval-holdout")

    def test_boundaries_are_inclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK]))
            self.assertEqual(reg.policy_for(8999), sr.UNREGISTERED)
            self.assertEqual(reg.policy_for(9000), "eval-only")
            self.assertEqual(reg.policy_for(9099), "eval-only")
            self.assertEqual(reg.policy_for(9100), sr.UNREGISTERED)

    def test_overlapping_bands_are_a_load_error(self):
        """A seed that is both train and eval-only would silently resolve to
        whichever bank was listed first -- the exact ambiguity the registry
        exists to remove."""
        clash = {"name": "other", "policy": "train", "ranges": [[9050, 9200]]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sr.RegistryError) as cm:
                sr.load_registry(write_registry(tmp, [EVAL_BANK, clash]))
            self.assertIn("overlap", str(cm.exception).lower())

    def test_unknown_policy_rejected(self):
        bad = {"name": "b", "policy": "sometimes", "ranges": [[1, 2]]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(write_registry(tmp, [bad]))

    def test_inverted_and_empty_ranges_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(write_registry(
                    tmp, [{"name": "b", "policy": "train", "ranges": [[10, 1]]}]))
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(write_registry(
                    tmp, [{"name": "b", "policy": "train", "ranges": []}]))

    def test_malformed_file_raises_rather_than_degrading(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "registry.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(p)
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(Path(tmp) / "does-not-exist.json")


BURNED_BANK = {"name": "eval-holdout", "policy": "eval-only", "ranges": [[9000, 9099]],
               "burned": [[9005, 9007]]}


class TestBurnedSeeds(unittest.TestCase):
    """Pre-declaration contamination is frozen and labeled, not hidden — the
    seed-side mirror of eval/holdouts.json's `grandfathered` list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = sr.load_registry(write_registry(self.tmp.name, [BURNED_BANK, TRAIN_BANK]))

    def tearDown(self):
        self.tmp.cleanup()

    def test_burned_seeds_keep_their_bank(self):
        """The band is not silently shrunk: the seed is still eval-holdout, it is
        just unusable. Shrinking would erase the record of what happened."""
        self.assertEqual(self.reg.bank_name_for(9006), "eval-holdout")
        self.assertEqual(self.reg.policy_for(9006), "eval-only")
        self.assertTrue(self.reg.is_burned(9006))
        self.assertFalse(self.reg.is_burned(9004))
        self.assertFalse(self.reg.is_burned(9008))

    def test_burned_seeds_are_refused_for_a_cited_eval(self):
        problems = sr.cited_seed_violations(self.reg, [9005, 9010],
                                            datasets_dir=Path(self.tmp.name) / "nope")
        self.assertEqual(len(problems), 1)
        self.assertIn("BURNED", problems[0])

    def test_burned_seeds_do_not_fail_the_contamination_check(self):
        """A permanently-red gate gets ignored; declared damage must not keep
        failing forever."""
        ds = Path(self.tmp.name) / "datasets"
        write_corpus(ds, "dagger", [{"seed": 9005}, {"seed": 9006}])
        seeds, _ = sr.corpus_seeds(ds)
        self.assertEqual(sr.check_contamination(self.reg, seeds), [])
        burned = sr.burned_contamination(self.reg, seeds)
        self.assertEqual(len(burned), 1)
        self.assertEqual(burned[0][2], [9005, 9006])

    def test_new_contamination_of_a_live_eval_seed_still_fails(self):
        ds = Path(self.tmp.name) / "datasets"
        write_corpus(ds, "oops", [{"seed": 9005}, {"seed": 9050}])
        seeds, _ = sr.corpus_seeds(ds)
        v = sr.check_contamination(self.reg, seeds)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0][2], [9050], "only the un-burned seed should fail")

    def test_burn_outside_its_own_band_is_a_load_error(self):
        bad = {"name": "b", "policy": "eval-only", "ranges": [[9000, 9099]],
               "burned": [[8000, 8001]]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sr.RegistryError) as cm:
                sr.load_registry(write_registry(tmp, [bad]))
            self.assertIn("not inside its own ranges", str(cm.exception))

    def test_burn_on_a_non_eval_band_is_a_load_error(self):
        bad = {"name": "t", "policy": "train", "ranges": [[100, 200]],
               "burned": [[150, 150]]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sr.RegistryError):
                sr.load_registry(write_registry(tmp, [bad]))


class TestSuiteReconciliation(unittest.TestCase):
    """stage3-v1.json's seed_policy defers to this registry; check_suites is what
    makes that deferral enforceable instead of aspirational."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = sr.load_registry(write_registry(self.tmp.name, [BURNED_BANK, TRAIN_BANK]))
        self.suites = Path(self.tmp.name) / "suites"
        self.suites.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_suite(self, name, arms):
        (self.suites / name).write_text(json.dumps({"arms": arms}), encoding="utf-8")

    def test_clean_arm_passes(self):
        self.write_suite("s.json", [{"name": "ok", "seeds": "9010..9020"}])
        self.assertEqual(sr.check_suites(self.reg, self.suites), [])

    def test_arm_straddling_burned_seeds_is_flagged(self):
        self.write_suite("s.json", [{"name": "headline", "seeds": "9005..9015"}])
        out = sr.check_suites(self.reg, self.suites)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], "headline")
        self.assertIn("burned", out[0][2][0])

    def test_arm_drawing_on_training_seeds_is_flagged(self):
        self.write_suite("s.json", [{"name": "bad", "seeds": "100..110"}])
        out = sr.check_suites(self.reg, self.suites)
        self.assertTrue(any("not in an eval-only band" in p for p in out[0][2]))

    def test_missing_suites_dir_is_not_an_error(self):
        self.assertEqual(sr.check_suites(self.reg, Path(self.tmp.name) / "absent"), [])

    def test_unreadable_suite_is_reported(self):
        (self.suites / "broken.json").write_text("{nope", encoding="utf-8")
        out = sr.check_suites(self.reg, self.suites)
        self.assertTrue(any("unreadable" in p for _, _, ps in out for p in ps))


class TestCorpusScan(unittest.TestCase):
    def test_collects_seeds_per_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_corpus(tmp, "c1", [{"seed": 1}, {"seed": 2}, {"seed": 2}])
            write_corpus(tmp, "c2", [{"seed": 9000}])
            seeds, unreadable = sr.corpus_seeds(tmp)
            self.assertEqual(seeds["c1"], {1, 2})
            self.assertEqual(seeds["c2"], {9000})
            self.assertEqual(unreadable, [])

    def test_corpus_with_rows_but_no_seed_field_is_unreadable(self):
        """The silent-pass shape: a corpus we cannot read scans as clean. It has
        to fail the gate, not contribute a reassuring empty set."""
        with tempfile.TemporaryDirectory() as tmp:
            write_corpus(tmp, "opaque", [{"prompt": "x"}, {"prompt": "y"}])
            seeds, unreadable = sr.corpus_seeds(tmp)
            self.assertEqual(seeds["opaque"], set())
            self.assertEqual(unreadable, ["opaque"])

    def test_empty_corpus_is_not_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_corpus(tmp, "empty", [])
            _, unreadable = sr.corpus_seeds(tmp)
            self.assertEqual(unreadable, [])

    def test_malformed_lines_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = write_corpus(tmp, "c", [{"seed": 5}])
            with open(d / "meta.jsonl", "a", encoding="utf-8") as f:
                f.write("{broken\n\n")
            seeds, unreadable = sr.corpus_seeds(tmp)
            self.assertEqual(seeds["c"], {5})
            self.assertEqual(unreadable, [])


class TestContaminationCheck(unittest.TestCase):
    def test_clean_corpora_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK, TRAIN_BANK]))
            ds = Path(tmp) / "datasets"
            write_corpus(ds, "train-corpus", [{"seed": 10}, {"seed": 20}])
            seeds, _ = sr.corpus_seeds(ds)
            self.assertEqual(sr.check_contamination(reg, seeds), [])

    def test_eval_seed_in_corpus_is_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK, TRAIN_BANK]))
            ds = Path(tmp) / "datasets"
            write_corpus(ds, "dirty", [{"seed": 10}, {"seed": 9005}, {"seed": 9006}])
            seeds, _ = sr.corpus_seeds(ds)
            v = sr.check_contamination(reg, seeds)
            self.assertEqual(len(v), 1)
            bank, corpus, hits = v[0]
            self.assertEqual((bank, corpus), ("eval-holdout", "dirty"))
            self.assertEqual(hits, [9005, 9006])


class TestCitedSeedGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = sr.load_registry(
            write_registry(self.tmp.name, [EVAL_BANK, TRAIN_BANK, HARNESS_BANK]))
        self.ds = Path(self.tmp.name) / "datasets"
        self.ds.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_eval_only_seeds_with_clean_corpora_are_usable(self):
        write_corpus(self.ds, "c", [{"seed": 10}])
        self.assertEqual(
            sr.cited_seed_violations(self.reg, [9000, 9001], datasets_dir=self.ds), [])

    def test_training_band_seeds_are_refused(self):
        """The stage1/stage2 student-eval case: seeds drawn from the harvest
        pool, refused on the declaration alone."""
        write_corpus(self.ds, "c", [{"seed": 999}])   # 100 is NOT harvested yet
        problems = sr.cited_seed_violations(self.reg, [100, 101], datasets_dir=self.ds)
        self.assertEqual(len(problems), 1)
        self.assertIn("not eval-only", problems[0])

    def test_harvested_eval_band_seed_is_refused_even_if_declared_eval_only(self):
        """Declaration alone is not the guarantee: if a band was relabelled after
        a harvest, the live corpus scan is what catches it."""
        write_corpus(self.ds, "c", [{"seed": 9000}])
        problems = sr.cited_seed_violations(self.reg, [9000, 9001], datasets_dir=self.ds)
        self.assertEqual(len(problems), 1)
        self.assertIn("training corpus c", problems[0])

    def test_unregistered_seeds_are_refused(self):
        problems = sr.cited_seed_violations(self.reg, [50000], datasets_dir=self.ds)
        self.assertTrue(any("unregistered" in p for p in problems))

    def test_harness_seeds_are_refused_for_cited_evals(self):
        problems = sr.cited_seed_violations(self.reg, [42], datasets_dir=self.ds)
        self.assertTrue(any("harness" in p for p in problems))

    def test_both_failure_modes_report_independently(self):
        write_corpus(self.ds, "c", [{"seed": 9000}])
        problems = sr.cited_seed_violations(self.reg, [9000, 100], datasets_dir=self.ds)
        self.assertEqual(len(problems), 2)


class TestSeedBankLabel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = sr.load_registry(
            write_registry(self.tmp.name, [EVAL_BANK, TRAIN_BANK]))

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_bank(self):
        self.assertEqual(sr.seed_bank_label(self.reg, [9000, 9001]), "eval-holdout")

    def test_mixed_banks_are_named_not_smoothed_over(self):
        label = sr.seed_bank_label(self.reg, [9000, 100])
        self.assertEqual(label, "mixed[eval-holdout+train]")

    def test_unregistered(self):
        self.assertEqual(sr.seed_bank_label(self.reg, [77777]), sr.UNREGISTERED)


class TestAudit(unittest.TestCase):
    def test_labels_each_run_with_its_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK, TRAIN_BANK]))
            ds = Path(tmp) / "datasets"
            runs = Path(tmp) / "runs"
            write_corpus(ds, "corpus", [{"seed": 1}, {"seed": 2}])
            write_run(runs, "dirty-eval", [1, 2, 3, 4])   # 2 of 4 harvested
            write_run(runs, "clean-eval", [9000, 9001])
            rows = {r["run"]: r for r in sr.audit_runs(reg, runs, ds)}

            self.assertEqual(rows["dirty-eval"]["n_contaminated"], 2)
            self.assertAlmostEqual(rows["dirty-eval"]["contaminated_frac"], 0.5)
            self.assertEqual(rows["dirty-eval"]["corpus_overlap"]["corpus"], [1, 2])
            self.assertEqual(rows["clean-eval"]["n_contaminated"], 0)
            self.assertEqual(rows["clean-eval"]["banks"], {"eval-holdout": [9000, 9001]})

    def test_run_dirs_with_no_games_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sr.load_registry(write_registry(tmp, [EVAL_BANK]))
            runs = Path(tmp) / "runs"
            (runs / "notes-only").mkdir(parents=True)
            (runs / "notes-only" / "README.md").write_text("x", encoding="utf-8")
            self.assertEqual(sr.audit_runs(reg, runs, Path(tmp) / "datasets"), [])


class TestSeedSpec(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(sr.parse_seed_spec("1..4"), [1, 2, 3, 4])
        self.assertEqual(sr.parse_seed_spec("3,7,42"), [3, 7, 42])
        self.assertEqual(sr.parse_seed_spec("5"), [5])

    def test_bad_specs(self):
        with self.assertRaises(ValueError):
            sr.parse_seed_spec("10..1")
        with self.assertRaises(ValueError):
            sr.parse_seed_spec("abc")


class TestShippedRegistry(unittest.TestCase):
    """The registry that actually ships must load and must describe reality."""

    def setUp(self):
        self.reg = sr.load_registry()

    def test_loads_and_has_an_eval_only_bank(self):
        self.assertTrue(self.reg.eval_only_banks)

    def test_the_suite_declared_band_is_the_registry_band(self):
        """stage3-v1.json's seed_policy said its 9000..9999 bank MUST move under
        the registry once one existed. If someone re-narrows the band without
        updating the suite, the two artifacts have silently diverged again."""
        bank = self.reg.bank_for(9500)
        self.assertIsNotNone(bank, "9000-9999 must be a declared band")
        self.assertEqual(bank["policy"], "eval-only")
        self.assertEqual(self.reg.policy_for(9000), "eval-only")
        self.assertEqual(self.reg.policy_for(9999), "eval-only")

    def test_the_dagger_seeds_are_burned(self):
        """9100-9102 are in datasets/stage2-local-v1-dagger and 9001 was played
        by runs/d4-dagger-pilot. All four must be unusable for a cited eval."""
        for seed in (9001, 9100, 9101, 9102):
            self.assertTrue(self.reg.is_burned(seed), "seed %d should be burned" % seed)
        self.assertFalse(self.reg.is_burned(9500))

    def test_the_contaminated_student_eval_seeds_are_declared_training(self):
        """501-525 carried runs/stage1-student-eval and runs/stage2-student-eval
        and are also in datasets/stage1-v1. The registry must say so, otherwise
        the guard would let that exact batch run cited again."""
        for seed in (501, 513, 525):
            self.assertEqual(self.reg.policy_for(seed), "train")

    def test_shipped_eval_band_is_not_in_any_shipped_corpus(self):
        seeds, unreadable = sr.corpus_seeds()
        self.assertEqual(sr.check_contamination(self.reg, seeds), [])
        self.assertEqual(unreadable, [], "a corpus with no readable seed field would "
                                         "make the shipped check vacuous")


if __name__ == "__main__":
    unittest.main(verbosity=2)
