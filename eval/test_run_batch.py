#!/usr/bin/env python3
"""Tests for run_batch.py's provenance, condition and resume logic.

Run: python3 eval/test_run_batch.py

Covers the checkpoint-identity hash, the cited seed guard, the context
profile / memory-tools condition flags, the batch manifest, and
condition-aware resume. Nothing here launches a JVM or contacts a model.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_batch  # noqa: E402

RUN_BATCH = Path(__file__).resolve().parent / "run_batch.py"


def make_args(**kw):
    """An argparse namespace with every attribute the pure helpers read."""
    base = dict(seeds=[7100], pairs=True, exploratory=True, jobs=1, out=None,
                forge_home="/nonexistent", cap_minutes=15.0,
                deck=None, deck_a="/tmp/deck.txt", deck_b="/tmp/deck.txt",
                name_a="Identity-A", name_b="Identity-B",
                smart_a="haiku", smart_b=None, fast_a="haiku", fast_b=None,
                controller_a="llm", controller_b="random",
                plan_a=None, plan_b=None, shadow_ai=False, checkpoint=None,
                env=None, context_profile=None, memory_tools=False,
                forced_env={"ENGINE_SHA": "e" * 12, "OVERLAY_SHA": "o" * 12,
                            "DECK_SHA": "d" * 12, "CHECKPOINT_SHA": "unknown",
                            "RUN_DISCIPLINE": "exploratory", "SEED_BANK": "stage3-holdout"})
    base.update(kw)
    return argparse.Namespace(**base)


def game_line(seed, swap, **kw):
    g = {"t": "game", "seed": seed, "swap": swap, "winner": "A",
         "deck_a": "Identity-A", "deck_b": "Identity-B",
         "controller_a": "llm", "controller_b": "random",
         "smart_a": "haiku", "smart_b": None, "fast_a": "haiku", "fast_b": None}
    g.update(kw)
    return g


def write_game(d, name, *lines):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")


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
        registry = run_batch.seed_registry.load_registry()
        # The DAgger corpus that harvested these seeds is not committed, so on a fresh clone the
        # live scan finds nothing; hand the corpus in so the corpus check is tested everywhere.
        problems = run_batch.seed_registry.cited_seed_violations(
            registry, [9100, 9101, 9102],
            seeds_by_corpus={"dagger-r1": {9100, 9101, 9102}})
        self.assertTrue(any("not eval-only" in p for p in problems))
        self.assertTrue(any("training corpus" in p for p in problems))

    def test_seed_bank_labels(self):
        registry = run_batch.seed_registry.load_registry()
        self.assertEqual(
            run_batch.seed_registry.seed_bank_label(registry, [7100, 7500]),
            "stage3-holdout")
        # Derived straight from seed_policy.RESERVED_BLOCKS, not restated in JSON.
        self.assertEqual(
            run_batch.seed_registry.seed_bank_label(registry, [30000, 30099]),
            "certification-holdout")


class TestConditionEnv(unittest.TestCase):
    """--context-profile / --memory-tools are the only route to
    CONTEXT_PROFILE / MEMORY_TOOLS; the ambient environment is scrubbed so
    the historical baseline (unset -> v1-compat, tools off) is the default."""

    def test_flags_set_env_and_ambient_is_scrubbed(self):
        ambient = {"CONTEXT_PROFILE": "v3-compact", "MEMORY_TOOLS": "1",
                   "CTX_RECENT_EVENTS": "99", "OPENROUTER_ALLOW_EGRESS": "1"}
        with mock.patch.dict(os.environ, ambient, clear=False):
            with tempfile.TemporaryDirectory() as d:
                env = run_batch.build_env(make_args(), 7100, False, Path(d))
                # The runner PINS the baseline explicitly. It must NOT inherit the play scripts'
                # always-on match-model defaults (run-llm-headless.sh defaults CONTEXT_PROFILE=
                # v4-faithful, MEMORY_TOOLS=1, RULES_TOOL=1), or a baseline arm would silently stamp
                # the match model and be either refused as a condition mismatch or pooled wrongly.
                self.assertEqual(env["CONTEXT_PROFILE"], "v1-compat")
                self.assertEqual(env["MEMORY_TOOLS"], "0")
                self.assertEqual(env["RULES_TOOL"], "0")
                self.assertNotIn("CTX_RECENT_EVENTS", env)
                # Egress consent is untouched: it is the user's, not the batch's.
                self.assertEqual(env.get("OPENROUTER_ALLOW_EGRESS"), "1")
                env = run_batch.build_env(make_args(context_profile="v2-memory",
                                                    memory_tools=True), 7100, True, Path(d))
                self.assertEqual(env["CONTEXT_PROFILE"], "v2-memory")
                self.assertEqual(env["MEMORY_TOOLS"], "1")
                self.assertEqual(env["SWAP_SEATS"], "1")
                self.assertEqual(env["GAME_CAP_MS"], str(15 * 60_000))
                self.assertEqual(env["RUN_DISCIPLINE"], "exploratory")

    def test_exploratory_ctx_override_via_env_is_honored_and_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            args = make_args(env=["CTX_RECENT_EVENTS=5", "OPENROUTER_API_KEY=abc"])
            env = run_batch.build_env(args, 7100, False, Path(d))
            self.assertEqual(env["CTX_RECENT_EVENTS"], "5")
            cond = run_batch.batch_condition(args, cited=False)
            self.assertEqual(cond["ctx_overrides"], {"CTX_RECENT_EVENTS": "5"})
            self.assertEqual(cond["env_overrides"], {"OPENROUTER_API_KEY": "<redacted>"})

    def test_out_label_names_the_condition(self):
        self.assertEqual(run_batch.default_out_label(make_args()), "llm-haiku-vs-random")
        self.assertEqual(run_batch.default_out_label(make_args(context_profile="v2-memory",
                                                               memory_tools=True)),
                         "llm-haiku-vs-random-ctx-v2-memory-memtools")

    def test_cited_refuses_ctx_overrides_and_condition_env(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d) / "mirror.txt"
            deck.write_text("4 Lightning Bolt\n", encoding="utf-8")
            for bad in ("CTX_RECENT_EVENTS=5", "CONTEXT_PROFILE=v2-memory", "MEMORY_TOOLS=1"):
                args = make_args(deck_a=str(deck), deck_b=str(deck), env=[bad], exploratory=False)
                with self.subTest(env=bad), self.assertRaises(SystemExit) as cm:
                    with mock.patch("sys.stderr"):
                        run_batch.cited_pre_guards(args)
                self.assertEqual(cm.exception.code, 2)

    def test_condition_env_is_refused_in_every_mode_before_anything_runs(self):
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100",
                            "--exploratory", "--env", "MEMORY_TOOLS=1",
                            "--forge-home", "/nonexistent"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--memory-tools", p.stderr)
        self.assertNotIn("[out]", p.stdout)


class TestLookahead(unittest.TestCase):
    """LOOKAHEAD changes what the seat can do (the `simulate` preview), so it is a condition like
    --rules-tool: set only by --lookahead, scrubbed from the ambient environment, recorded in the
    manifest and checked against each game line's `lookahead` stamp."""

    def test_only_the_flag_sets_it(self):
        with mock.patch.dict(os.environ, {"LOOKAHEAD": "1"}, clear=False), tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_batch.build_env(make_args(), 7100, False, Path(d))["LOOKAHEAD"], "0")
            self.assertEqual(run_batch.build_env(make_args(lookahead=True), 7100, False, Path(d))["LOOKAHEAD"], "1")
        for kv in ("LOOKAHEAD=1", "NATIVE_TOOLS=1"):
            p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                                "--env", kv, "--forge-home", "/nonexistent"],
                               capture_output=True, text=True)
            with self.subTest(env=kv):
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("--lookahead", p.stderr)

    def test_game_lines_and_manifests_keep_it_apart(self):
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(lookahead=True))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))       # predates the stamp
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, lookahead=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, lookahead=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        la = run_batch.batch_condition(make_args(lookahead=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, la), [("lookahead", False, True)])
        # A manifest from before the flag recorded it as `--env LOOKAHEAD=1`; resuming it with the
        # flag is the same condition, and without the flag it is not.
        old = dict(base, env_overrides={"LOOKAHEAD": "1"})
        del old["lookahead"]
        old_no_env = {k: v for k, v in base.items() if k != "lookahead"}
        self.assertEqual(run_batch.condition_diff(old, la), [])
        self.assertEqual(run_batch.condition_diff(old, base), [("lookahead", True, False)])
        with tempfile.TemporaryDirectory() as d:
            write_game(d, "game-7100-1000.jsonl", game_line(7100, False, lookahead=True))
            self.assertIn("lookahead", run_batch.check_resume_condition(d, base, cited=False))
            self.assertIsNone(run_batch.check_resume_condition(d, la, cited=False))
            # A manifest from before the flag, of a shell that exported LOOKAHEAD=1: its lines say so.
            run_batch.write_manifest(d, old_no_env, seeds=[7100], pairs=False, jobs=1)
            self.assertIn("lookahead", run_batch.check_resume_condition(d, base, cited=False) or "")
            self.assertIsNone(run_batch.check_resume_condition(d, la, cited=False))
            # Only some lines stamped: the others' condition is unknowable, so neither resumes.
            write_game(d, "game-7101-1000.jsonl", game_line(7101, False))
            for cond in (base, la):
                self.assertIn("stamped on 1 of 2", run_batch.check_resume_condition(d, cond, cited=False) or "")
        with tempfile.TemporaryDirectory() as d:   # the same mix with no manifest at all
            write_game(d, "game-7100-1000.jsonl", game_line(7100, False, lookahead=True))
            write_game(d, "game-7101-1000.jsonl", game_line(7101, False))
            self.assertIn("stamped on 1 of 2", run_batch.check_resume_condition(d, la, cited=False) or "")


class TestResumeIdentity(unittest.TestCase):
    """A game line counts as 'already done' only under this batch's condition."""

    def test_expectation_matches_baseline_and_legacy_lines(self):
        exp = run_batch.resume_expectation(make_args())
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), exp))  # legacy
        self.assertTrue(run_batch.game_line_matches(
            game_line(1, False, context_profile="v1-compat", memory_tools=False), exp))
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False, context_profile="v2-memory", memory_tools=False), exp))
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False, context_profile="v1-compat", memory_tools=True), exp))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, smart_a="opus"), exp))

    def test_native_tools_is_the_default_protocol_and_its_own_condition(self):
        OR = "openrouter:deepseek/deepseek-v4.1-flash"
        # Tools + a backend with function calling -> native, unless --text-tools opts out.
        self.assertTrue(run_batch.native_tools_requested(make_args(memory_tools=True, smart_a=OR)))
        self.assertFalse(run_batch.native_tools_requested(make_args(memory_tools=True, smart_a=OR, text_tools=True)))
        self.assertFalse(run_batch.native_tools_requested(make_args(memory_tools=False, smart_a=OR)))
        self.assertFalse(run_batch.native_tools_requested(make_args(memory_tools=True, smart_a="haiku")))
        self.assertFalse(run_batch.native_tools_requested(make_args(memory_tools=True, smart_a=OR, controller_a="floor")))
        self.assertTrue(run_batch.native_tools_requested(
            make_args(memory_tools=True, smart_a="haiku", controller_a="ai", smart_b=OR, controller_b="llm")))
        # Each tier decides alone: a CLI smart tier with an openrouter fast tier still calls natively.
        self.assertTrue(run_batch.native_tools_requested(make_args(memory_tools=True, smart_a="haiku", fast_a=OR)))

        text = run_batch.resume_expectation(make_args(context_profile="v4-faithful", memory_tools=True))
        native = run_batch.resume_expectation(make_args(context_profile="v4-faithful", memory_tools=True,
                                                        native_tools=True))
        text_line = game_line(1, False, context_profile="v4-faithful", memory_tools=True, native_tools=False)
        old_line = game_line(1, False, context_profile="v4-faithful", memory_tools=True)   # predates the field
        native_line = game_line(1, False, context_profile="v4-faithful", memory_tools=True, native_tools=True)
        self.assertTrue(run_batch.game_line_matches(text_line, text))
        self.assertTrue(run_batch.game_line_matches(old_line, text))
        self.assertFalse(run_batch.game_line_matches(native_line, text))
        self.assertTrue(run_batch.game_line_matches(native_line, native))
        self.assertFalse(run_batch.game_line_matches(old_line, native))
        self.assertEqual(run_batch.condition_diff({"memory_tools": True}, {"memory_tools": True, "native_tools": False}), [])
        self.assertEqual(run_batch.condition_diff({"memory_tools": True}, {"memory_tools": True, "native_tools": True}),
                         [("native_tools", False, True)])

    def test_expectation_under_memory_tools_rejects_baseline_lines(self):
        exp = run_batch.resume_expectation(make_args(context_profile="v2-memory", memory_tools=True))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), exp))
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False, context_profile="v2-memory", memory_tools=False), exp))
        self.assertTrue(run_batch.game_line_matches(
            game_line(1, False, context_profile="v2-memory", memory_tools=True), exp))

    def test_plan_combos_replays_foreign_condition_and_skips_own(self):
        with tempfile.TemporaryDirectory() as d:
            write_game(d, "game-7100-1000.jsonl",
                       game_line(7100, False, context_profile="v2-memory", memory_tools=True))
            write_game(d, "game-7100-1001.jsonl",
                       game_line(7100, True, context_profile="v1-compat", memory_tools=False))
            write_game(d, "game-7101-1000.jsonl", {"t": "decision", "seat": "Identity-A"})
            combos = [(7100, False), (7100, True), (7101, False)]
            baseline = run_batch.resume_expectation(make_args())
            todo, skipped, foreign = run_batch.plan_combos(d, combos, baseline)
            self.assertEqual(skipped, [(7100, True)])
            self.assertEqual(foreign, [(7100, False)])
            self.assertEqual(todo, [(7100, False), (7101, False)])
            memtools = run_batch.resume_expectation(
                make_args(context_profile="v2-memory", memory_tools=True))
            todo, skipped, foreign = run_batch.plan_combos(d, combos, memtools)
            self.assertEqual(skipped, [(7100, False)])
            self.assertEqual(foreign, [(7100, True)])
            self.assertEqual(todo, [(7100, True), (7101, False)])
            # Legacy behaviour (no expectation) still skips any game line.
            self.assertTrue(run_batch.has_game_line(d, 7100, False))
            self.assertFalse(run_batch.has_game_line(d, 7101, False))


class TestManifest(unittest.TestCase):

    def test_manifest_written_once_and_invocations_appended(self):
        with tempfile.TemporaryDirectory() as d:
            cond = run_batch.batch_condition(make_args(), cited=False)
            self.assertIsNone(run_batch.load_manifest(d))
            run_batch.write_manifest(d, cond, seeds=[7100, 7101], pairs=True, jobs=1, argv=["x"])
            run_batch.write_manifest(d, cond, seeds=[7102], pairs=True, jobs=1, argv=["y"])
            m = run_batch.load_manifest(d)
            self.assertEqual(m["schema"], run_batch.MANIFEST_SCHEMA)
            self.assertEqual(m["condition"], cond)
            self.assertEqual([i["seeds"] for i in m["invocations"]], [[7100, 7101], [7102]])
            self.assertEqual(set(run_batch.CONDITION_KEYS) - set(cond), set())

    def test_resume_same_condition_ok_different_condition_refused(self):
        with tempfile.TemporaryDirectory() as d:
            cond = run_batch.batch_condition(make_args(), cited=False)
            run_batch.write_manifest(d, cond, seeds=[7100], pairs=True, jobs=1)
            self.assertIsNone(run_batch.check_resume_condition(d, dict(cond), cited=False))
            for change in (dict(memory_tools=True), dict(context_profile="v2-memory"),
                           dict(smart_a="opus"), dict(controller_b="ai"),
                           dict(forced_env=dict(make_args().forced_env, CHECKPOINT_SHA="abc")),
                           dict(env=["CTX_RECENT_EVENTS=3"]), dict(cap_minutes=30.0)):
                other = run_batch.batch_condition(make_args(**change), cited=False)
                msg = run_batch.check_resume_condition(d, other, cited=False)
                with self.subTest(change=change):
                    self.assertIsNotNone(msg)
                    self.assertIn("different condition", msg)
                    key = next(k for k, _, _ in run_batch.condition_diff(cond, other))
                    self.assertIn(key, msg)
            # Extending the seed set is not a condition change.
            self.assertIsNone(run_batch.check_resume_condition(d, dict(cond), cited=True))
            # A cited invocation into an exploratory dir IS a condition change.
            cited_cond = run_batch.batch_condition(make_args(), cited=True)
            self.assertIn("discipline", run_batch.check_resume_condition(d, cited_cond, True))

    def test_corrupt_manifest_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / run_batch.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
            with self.assertRaises(run_batch.ManifestError):
                run_batch.check_resume_condition(d, {}, cited=False)
            (Path(d) / run_batch.MANIFEST_NAME).write_text('{"schema": "other"}', encoding="utf-8")
            with self.assertRaises(run_batch.ManifestError):
                run_batch.check_resume_condition(d, {}, cited=False)

    def test_legacy_dir_without_manifest_is_compared_on_its_game_lines(self):
        with tempfile.TemporaryDirectory() as d:
            # Empty dir: nothing to contradict.
            self.assertIsNone(run_batch.check_resume_condition(
                d, run_batch.batch_condition(make_args(), False), cited=True))
            write_game(d, "game-7100-1000.jsonl",
                       game_line(7100, False, context_profile="v2-memory", memory_tools=True,
                                 checkpoint_sha="unknown", discipline="exploratory"))
            baseline = run_batch.batch_condition(make_args(), cited=False)
            msg = run_batch.check_resume_condition(d, baseline, cited=False)
            self.assertIsNotNone(msg)
            self.assertIn("context_profile", msg)
            self.assertIn("memory_tools", msg)
            same = run_batch.batch_condition(
                make_args(context_profile="v2-memory", memory_tools=True), cited=False)
            self.assertIsNone(run_batch.check_resume_condition(d, same, cited=False))

    def test_legacy_text_grammar_dir_refuses_native_games(self):
        OR = "openrouter:deepseek/deepseek-v4.1-flash"
        with tempfile.TemporaryDirectory() as d:
            # Written before native_tools existed: a memory-tools game that ran the text grammar.
            write_game(d, "game-7100-1000.jsonl",
                       game_line(7100, False, context_profile="v4-faithful", memory_tools=True, rules_tool=False,
                                 smart_a=OR, fast_a=OR, checkpoint_sha="unknown", discipline="exploratory"))
            kw = dict(context_profile="v4-faithful", memory_tools=True, smart_a=OR, fast_a=OR)
            native = make_args(**kw)
            native.native_tools = run_batch.native_tools_requested(native)
            self.assertTrue(native.native_tools)
            msg = run_batch.check_resume_condition(d, run_batch.batch_condition(native, cited=False), cited=False)
            self.assertIsNotNone(msg)
            self.assertIn("native_tools", msg)
            text = make_args(text_tools=True, **kw)
            text.native_tools = run_batch.native_tools_requested(text)
            self.assertIsNone(run_batch.check_resume_condition(d, run_batch.batch_condition(text, cited=False), cited=False))

    def test_unverifiable_legacy_dir_refused_for_cited_warned_for_exploratory(self):
        with tempfile.TemporaryDirectory() as d:
            write_game(d, "game-7100-1000.jsonl", game_line(7100, False))
            cond = run_batch.batch_condition(make_args(), cited=False)
            self.assertIsNone(run_batch.check_resume_condition(d, cond, cited=False))
            cited = run_batch.batch_condition(make_args(), cited=True)
            # discipline is not on legacy lines, so only verifiability blocks it.
            msg = run_batch.check_resume_condition(d, cited, cited=True)
            self.assertIsNotNone(msg)
            self.assertIn("cannot be verified", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
