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
from unittest import mock
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


class TestRequireForgeClasspath(unittest.TestCase):
    """A missing /tmp/forge_cp.txt must stop the batch, not every game.

    WHAT WOULD MAKE THIS FAIL: deleting the call site in main(), or letting a
    zero-byte file through. Either restores the failure this guards: 12
    matchups "generated" in under a second each and 0 games produced, with the
    real cause (`cat: /tmp/forge_cp.txt: No such file or directory`) buried in
    per-game logs and the batch summary saying only "nonzero-exit".
    """

    def test_missing_file_refuses_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as cm:
                run_batch.require_forge_classpath(
                    cp_file=str(Path(tmp) / "nope.txt"))
            self.assertEqual(cm.exception.code, 2)

    def test_empty_file_refuses(self):
        """An empty file is the realistic half-failure: a truncated or
        interrupted `dependency:build-classpath` leaves the path existing."""
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "cp.txt"
            cp.write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                run_batch.require_forge_classpath(cp_file=str(cp))
            self.assertEqual(cm.exception.code, 2)

    def test_populated_file_is_accepted(self):
        """Negative control: without this the assertions above would pass on a
        guard that refuses unconditionally."""
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "cp.txt"
            cp.write_text("/a/b.jar:/c/d.jar\n", encoding="utf-8")
            run_batch.require_forge_classpath(cp_file=str(cp))  # must not raise

    def test_refusal_prints_the_regeneration_command(self):
        """The message has to carry the fix. The operator is looking at a batch
        that produced nothing and has no other clue what to run."""
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            run_batch.require_forge_classpath(cp_file="/nonexistent/cp.txt")
        msg = err.getvalue()
        self.assertIn("dependency:build-classpath", msg)
        self.assertIn("-Dmdep.outputFile=", msg)
        self.assertNotIn("--exploratory", msg)


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


class TestCitedJobsGuard(unittest.TestCase):
    """The single-JVM guard must fire exactly where its reason applies.

    Its stated reason is CPU contention starving the `claude -p` CLI, which
    inflates timeouts and fallbacks and corrupts an LLM win rate. That hazard
    needs a model in a seat. Applied blanket-wide it instead blocked the thing
    the benchmark most needed: STR-AI had no floor next to it -- which by the
    repo's own rule means it was not a score -- because measuring one meant
    ~3,840 serial games at ~13 hours.

    WHAT WOULD MAKE THIS FAIL:
      * re-widening the guard to refuse every --jobs != 1 (test 2/3 below);
      * opening it up for LLM seats, which would let CPU contention back into
        a cited model measurement (test 1);
      * dropping the ceiling, letting jobs climb until the wall-clock cap
        decides which games finish -- that changes the measurement, not just
        its speed (test 4).
    """

    #: A real mirror deck: guard 1 runs before guard 3 and refuses on unset
    #: or mismatched decks, so a fixture without one would refuse for the
    #: WRONG reason and the allow-cases below would look like passes.
    DECK = str(Path(run_batch.__file__).parent / "decks" / "ur-delver.txt")

    class _Args(object):
        def __init__(self, jobs, ca, cb, deck):
            self.jobs, self.controller_a, self.controller_b = jobs, ca, cb
            self.env, self.plan_a, self.plan_b = [], None, None
            self.deck_a = self.deck_b = deck
            self.name_a, self.name_b = "A", "B"
            self.seeds = [7100]

    def _guard(self, jobs, ca, cb):
        """-> None if allowed, or the SystemExit code if refused.

        Drives the real cited_pre_guards, so patching only the jobs block
        cannot make this pass. The deck/seed guards run first, so they are
        stubbed out -- this test is about guard 3 alone.
        """
        args = self._Args(jobs, ca, cb, self.DECK)
        saved = (run_batch.cited_seed_guard,)
        run_batch.cited_seed_guard = lambda a: None
        real_mirror = getattr(run_batch, "cited_mirror_guard", None)
        try:
            with mock.patch.object(run_batch, "_refuse",
                                   side_effect=SystemExit(2)):
                try:
                    run_batch.cited_pre_guards(args)
                except SystemExit as e:
                    return e.code
                except Exception:
                    # an earlier guard we are not testing; re-raise so a
                    # silently-skipped assertion cannot look like a pass
                    raise
            return None
        finally:
            run_batch.cited_seed_guard = saved[0]
            if real_mirror is not None:
                run_batch.cited_mirror_guard = real_mirror

    def test_llm_seat_still_refused(self):
        for ca, cb in (("llm", "ai"), ("ai", "llm"), ("llm", "llm")):
            self.assertEqual(self._guard(6, ca, cb), 2,
                             "jobs=6 with %s/%s must still refuse" % (ca, cb))

    def test_model_free_controllers_are_allowed_to_parallelise(self):
        for ca, cb in (("random", "ai"), ("floor", "ai"), ("ai", "ai")):
            self.assertIsNone(self._guard(6, ca, cb),
                              "jobs=6 with %s/%s should be allowed" % (ca, cb))

    def test_serial_is_always_fine(self):
        self.assertIsNone(self._guard(1, "llm", "llm"))

    def test_there_is_still_a_ceiling(self):
        self.assertEqual(
            self._guard(run_batch.CITED_MAX_JOBS_NO_LLM + 1, "random", "ai"), 2)



if __name__ == "__main__":
    unittest.main(verbosity=2)
