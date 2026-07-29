#!/usr/bin/env python3
"""Tests for run_batch.py's provenance additions.

Run: python3 eval/test_run_batch.py

Scope is deliberately narrow: the checkpoint-identity hash and the cited seed
guard. The rest of run_batch drives JVMs and is covered by the validation ladder
in CLAUDE.md, not by unit tests.
"""

import argparse
import tempfile
import unittest
from pathlib import Path

import run_batch


class TestResolveDeckPaths(unittest.TestCase):
    """Deck paths must be absolutised in BOTH modes, before any JVM launches.

    WHAT WOULD MAKE THESE FAIL: moving the resolve back inside
    cited_pre_guards (so --exploratory skips it), or dropping the
    existence check. Either regression reproduces the original defect: a
    repo-relative deck path is handed verbatim to a JVM launched with
    cwd=$FORGE_HOME, loadDeck throws NoSuchFileException, and every game in
    the batch dies -- one macOS crash-report dialog per parallel job, none of
    which names the cwd mismatch that caused it.
    """

    def _args(self, deck_a, deck_b=None):
        return argparse.Namespace(deck_a=deck_a, deck_b=deck_b,
                                  forge_home="/nonexistent/forge-home")

    def test_relative_path_becomes_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "d.txt"
            deck.write_text("4 Mountain\n", encoding="utf-8")
            # Pass a path with a redundant segment: resolve() must normalise it.
            a = self._args(str(Path(tmp) / "." / "d.txt"))
            run_batch.resolve_deck_paths(a)
            self.assertEqual(a.deck_a, str(deck.resolve()))
            self.assertTrue(Path(a.deck_a).is_absolute())

    def test_missing_deck_refuses_with_exit_2(self):
        a = self._args("definitely/not/here.txt")
        with self.assertRaises(SystemExit) as cm:
            run_batch.resolve_deck_paths(a)
        self.assertEqual(cm.exception.code, 2)

    def test_empty_deck_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "empty.txt"
            deck.write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                run_batch.resolve_deck_paths(self._args(str(deck)))
            self.assertEqual(cm.exception.code, 2)

    def test_unset_seat_is_left_alone(self):
        """--deck-b may legitimately be None (single-seat exploratory pokes);
        that must not be mistaken for a missing file."""
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "d.txt"
            deck.write_text("4 Island\n", encoding="utf-8")
            a = self._args(str(deck), None)
            run_batch.resolve_deck_paths(a)
            self.assertIsNone(a.deck_b)

    def test_refusal_names_the_cwd_mismatch(self):
        """The message must explain WHY a path that exists from the shell fails
        inside the JVM. Without this the operator sees only 'file not found'
        for a file they can `cat`."""
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            run_batch.resolve_deck_paths(self._args("eval/decks/nope.txt"))
        msg = err.getvalue()
        self.assertIn("cwd=", msg)
        # ...and must NOT advertise a bypass that does not exist for this guard.
        self.assertNotIn("--exploratory", msg)


class TestResolveIsActuallyWired(unittest.TestCase):
    """The unit tests above call resolve_deck_paths() directly, so they pass
    even if nothing in main() calls it. This drives the real CLI instead, so
    deleting the call site fails here -- which is the regression that actually
    happened."""

    def test_cli_refuses_missing_deck_before_launching_any_jvm(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(Path(run_batch.__file__).resolve()),
                 "--seeds", "1", "--exploratory",
                 "--deck-a", "definitely/not/here.txt",
                 "--out", str(Path(tmp) / "out")],
                capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("deck file does not exist", proc.stderr)
        # No JVM should have been reached: the launcher banner never prints.
        self.assertNotIn("[start]", proc.stdout)


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
