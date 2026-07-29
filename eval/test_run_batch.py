#!/usr/bin/env python3
"""Tests for run_batch.py's provenance additions.

Run: python3 eval/test_run_batch.py

Scope is deliberately narrow: the checkpoint-identity hash and the cited seed
guard. The rest of run_batch drives JVMs and is covered by the validation ladder
in CLAUDE.md, not by unit tests.
"""

import tempfile
import unittest
from pathlib import Path

import run_batch


class TestCheckpointSha(unittest.TestCase):
    def test_missing_spec_is_unknown(self):
        self.assertEqual(run_batch._checkpoint_sha(None), "unknown")
        self.assertEqual(run_batch._checkpoint_sha(""), "unknown")

    def test_nonexistent_path_passes_through_as_an_opaque_tag(self):
        """A tag or an externally-computed hash is recorded verbatim rather than
        being silently downgraded to 'unknown'."""
        self.assertEqual(run_batch._checkpoint_sha("stage2-dagger-r1"),
                         "stage2-dagger-r1")

    def test_file_is_hashed_by_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "adapter.safetensors"
            p.write_bytes(b"weights-v1")
            first = run_batch._checkpoint_sha(str(p))
            self.assertEqual(len(first), 12)
            p.write_bytes(b"weights-v2")
            self.assertNotEqual(run_batch._checkpoint_sha(str(p)), first)

    def test_directory_hash_changes_when_any_weight_changes(self):
        """The adapter-dir case: the stage1 -> stage2 swap that the old artifact
        could not see must move this hash."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "adapters"
            d.mkdir()
            (d / "adapter_config.json").write_text("{}", encoding="utf-8")
            (d / "adapters.safetensors").write_bytes(b"stage1")
            stage1 = run_batch._checkpoint_sha(str(d))
            (d / "adapters.safetensors").write_bytes(b"stage2")
            stage2 = run_batch._checkpoint_sha(str(d))
            self.assertEqual(len(stage1), 12)
            self.assertNotEqual(stage1, stage2)

    def test_directory_hash_changes_when_a_file_is_renamed(self):
        """Names are hashed too, so a same-bytes rename is still a new identity."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "adapters"
            d.mkdir()
            (d / "a.safetensors").write_bytes(b"same")
            before = run_batch._checkpoint_sha(str(d))
            (d / "a.safetensors").rename(d / "b.safetensors")
            self.assertNotEqual(run_batch._checkpoint_sha(str(d)), before)

    def test_directory_hash_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "adapters"
            (d / "sub").mkdir(parents=True)
            (d / "sub" / "w.bin").write_bytes(b"x")
            self.assertEqual(run_batch._checkpoint_sha(str(d)),
                             run_batch._checkpoint_sha(str(d)))


class TestSeedGuardWiring(unittest.TestCase):
    """run_batch must consult the same registry seed_registry.py enforces --
    a second, drifting copy of the rule would be worse than none."""

    def test_contaminated_student_eval_seeds_are_refused(self):
        """501-525 carried both student evals. seed_policy.py reserves the block
        and calls it burned for held-out measurement, so the guard must refuse
        it — on the burn AND on the live corpus scan, independently."""
        registry = run_batch.seed_registry.load_registry()
        problems = run_batch.seed_registry.cited_seed_violations(
            registry, list(range(501, 526)))
        self.assertTrue(any("BURNED" in p for p in problems))
        self.assertTrue(any("training corpus" in p for p in problems),
                        "the live corpus scan should also fire on 501-525")

    def test_the_claimed_holdout_band_is_accepted(self):
        """stage3-holdout (7000-7999), claimed from seed_policy's open lane and
        verified clean when claimed."""
        registry = run_batch.seed_registry.load_registry()
        self.assertEqual(
            run_batch.seed_registry.cited_seed_violations(
                registry, list(range(7100, 7125))), [])

    def test_dagger_territory_is_refused(self):
        """Dagger-band seeds must be refused on BOTH counts: policy (not
        eval-only) and corpus presence.

        The corpus half is checked against a synthetic datasets dir rather
        than the repo's own. `datasets/` is gitignored, so scanning it made
        this assertion depend on whether the author happened to have the
        dagger corpora on disk -- it passed in a checkout that had them and
        failed in a fresh worktree that did not, which is a test reporting
        ambient state rather than code behavior.
        """
        registry = run_batch.seed_registry.load_registry()

        # Policy half: seed-band classification, no corpus scan involved.
        # Empty seeds_by_corpus isolates this from any on-disk datasets.
        problems = run_batch.seed_registry.cited_seed_violations(
            registry, [9100, 9101, 9102], seeds_by_corpus={})
        self.assertTrue(any("not eval-only" in p for p in problems), problems)

        # Corpus half: a corpus that demonstrably contains those seeds.
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "stage2-dagger-fixture"
            corpus.mkdir()
            (corpus / "meta.jsonl").write_text(
                "".join('{"seed": %d}\n' % s for s in (9100, 9101, 9102)),
                encoding="utf-8")
            problems = run_batch.seed_registry.cited_seed_violations(
                registry, [9100, 9101, 9102], datasets_dir=tmp)
        self.assertTrue(any("training corpus" in p for p in problems),
                        problems)
        self.assertTrue(any("stage2-dagger-fixture" in p for p in problems),
                        problems)

    def test_a_clean_corpus_does_not_trip_the_corpus_check(self):
        """Negative control for the fixture above: same seeds, a corpus that
        does not contain them -> the 'training corpus' problem must NOT be
        raised. Without this, the assertion above could pass on a check that
        fires unconditionally."""
        registry = run_batch.seed_registry.load_registry()
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "unrelated-corpus"
            corpus.mkdir()
            (corpus / "meta.jsonl").write_text(
                '{"seed": 12345}\n', encoding="utf-8")
            problems = run_batch.seed_registry.cited_seed_violations(
                registry, [9100, 9101, 9102], datasets_dir=tmp)
        self.assertFalse(any("training corpus" in p for p in problems),
                         problems)

    def test_seed_bank_labels(self):
        registry = run_batch.seed_registry.load_registry()
        self.assertEqual(
            run_batch.seed_registry.seed_bank_label(registry, [7100, 7500]),
            "stage3-holdout")
        # Derived straight from seed_policy.RESERVED_BLOCKS, not restated in JSON.
        self.assertEqual(
            run_batch.seed_registry.seed_bank_label(registry, [30000, 30099]),
            "certification-holdout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
