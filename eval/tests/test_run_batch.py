#!/usr/bin/env python3
"""Tests for run_batch.py's provenance, condition and resume logic.

Run: python3 eval/tests/test_run_batch.py

Covers the checkpoint-identity hash, the cited seed guard, the context
profile / memory-tools condition flags, the batch manifest, and
condition-aware resume. Nothing here launches a JVM or contacts a model.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import run_batch  # noqa: E402

RUN_BATCH = evalpath.EVAL / "run_batch.py"


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
         "smart_a": "haiku", "smart_b": None, "fast_a": "haiku", "fast_b": None,
         "context_profile": run_batch.DEFAULT_CONTEXT_PROFILE}
    g.update(kw)
    if g["context_profile"] is None:  # a pre-profile line
        del g["context_profile"]
    return g


def write_game(d, name, *lines):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")


class TestForgeHome(unittest.TestCase):
    def test_the_forge_checkout_comes_from_forge_home_as_the_launchers_take_it(self):
        # CLAUDE.md's paid-game command passes no --forge-home: the default must follow $FORGE_HOME (a checkout at
        # /root/forge was refused as "run-llm-headless.sh not found" under ~/code/forge).
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"FORGE_HOME": d}):
            (Path(d) / "forge-gui/res/puzzle").mkdir(parents=True)
            self.assertEqual(run_batch.build_parser().parse_args([]).forge_home, d)
        # pr-review of #328: a FORGE_HOME that is set but no checkout (a typo) is taken as given, so the run refuses
        # it ("run-llm-headless.sh not found") rather than playing on another checkout's installed overlay.
        with mock.patch.dict(os.environ, {"FORGE_HOME": "/nonexistent/typo-forge"}):
            self.assertEqual(str(run_batch.build_parser().parse_args([]).forge_home), "/nonexistent/typo-forge")


class TestOverlayIdentity(unittest.TestCase):
    """OVERLAY_SHA and the drift refusal covered only LlmSpikeGui/LlmSpikeHeadless (2026-10-16 review H3):
    a stale AnswerTool or engine patch was invisible. Every overlay source counts now, and an engine patch
    (engine-patches/) counts as the file it makes, so the hash is the one the whole-file copies had."""

    def test_an_installed_copy_hashes_like_the_checkout_and_drift_names_any_stale_file(self):
        import shutil
        import engine_patches
        forge = engine_patches.forge_home()
        if forge is None:
            self.skipTest("no Forge checkout: the engine patches cannot be applied")
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"FORGE_HOME": str(forge)}):
            shutil.copytree(run_batch.REPO_ROOT / "forge-overlay", home, dirs_exist_ok=True)
            os.symlink(forge / ".git", Path(home, ".git"))  # as the acceptance stand-in home does
            engine_patches.materialize(home, forge)
            self.assertEqual(run_batch._overlay_sha(home),
                             run_batch.benchmark_version.overlay_sha_of_checkout(run_batch.REPO_ROOT))
            self.assertNotEqual(run_batch._overlay_sha(home), "unknown")
            self.assertEqual(run_batch._spike_drift(home), [])
            patch = "forge-game/src/main/java/forge/game/player/Player.java"
            self.assertIn(patch, run_batch.benchmark_version.overlay_files())
            Path(home, patch).write_text("// stale\n")
            self.assertEqual(run_batch._spike_drift(home), [patch])
            self.assertNotEqual(run_batch._overlay_sha(home),
                                run_batch.benchmark_version.overlay_sha_of_checkout(run_batch.REPO_ROOT))
            os.remove(Path(home, patch))  # an engine patch never installed is drift too
            self.assertEqual(run_batch._spike_drift(home), [patch])


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
    the faithful seat (unset -> v4-faithful, tools off) is the default."""

    def test_flags_set_env_and_ambient_is_scrubbed(self):
        ambient = {"CONTEXT_PROFILE": "v3-compact", "MEMORY_TOOLS": "1",
                   "CTX_RECENT_EVENTS": "99", "OPENROUTER_ALLOW_EGRESS": "1"}
        with mock.patch.dict(os.environ, ambient, clear=False):
            with tempfile.TemporaryDirectory() as d:
                env = run_batch.build_env(make_args(), 7100, False, Path(d))
                # The runner PINS every condition key explicitly and never inherits the ambient
                # environment. Unset is the faithful seat (2026-10-16 review B1: v1-compat carries
                # coaching, a reasoning cap and a 25-decision turn cap, and is only a named arm).
                self.assertEqual(env["CONTEXT_PROFILE"], "v4-faithful")
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

    def test_cited_runs_in_parallel_only_off_the_claude_cli(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d) / "mirror.txt"
            deck.write_text("4 Lightning Bolt\n", encoding="utf-8")
            luna = "openrouter:openai/gpt-6-luna"
            for smart, fast, refused in (("haiku", "haiku", True), (luna, "haiku", True), (luna, luna, False)):
                args = make_args(deck_a=str(deck), deck_b=str(deck), exploratory=False, jobs=4,
                                 smart_a=smart, fast_a=fast, seeds=[30000])
                with self.subTest(smart=smart, fast=fast), mock.patch("sys.stderr"), \
                        mock.patch.object(run_batch, "cited_seed_guard"):
                    if refused:
                        with self.assertRaises(SystemExit):
                            run_batch.cited_pre_guards(args)
                    else:
                        run_batch.cited_pre_guards(args)

    def test_condition_env_is_refused_in_every_mode_before_anything_runs(self):
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100",
                            "--exploratory", "--env", "MEMORY_TOOLS=1",
                            "--forge-home", "/nonexistent"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--memory-tools", p.stderr)
        self.assertNotIn("[out]", p.stdout)

    def test_a_routing_filter_is_refused_in_env_and_scrubbed_from_the_shell(self):
        # pr-review of #327: the game line stamps the variable as set, so a ceiling naming no model in the game (or
        # mistyped) would read as capped while nothing was sent. A batch has no ceiling; puzzle_solve.py has the flag.
        for key, flag in (("OPENROUTER_MAX_PRICE", "--max-price-at-list"), ("OPENROUTER_QUANTIZATIONS", "--quantizations")):
            p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                                "--env", key + "={}", "--forge-home", "/nonexistent"], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, key)
            self.assertIn(flag, p.stderr)
            self.assertIn(key, run_batch.RUN_DEFINING_KEYS, "an ambient value is scrubbed")


class TestRulesToolNeedsMemoryTools(unittest.TestCase):
    """The seat wires the rules lookup only with MEMORY_TOOLS=1 (LlmSpikeGui, `MEMORY_TOOLS && RULES_TOOL`),
    so --rules-tool alone recorded rules_tool=1 for games that never offered it (benchmark pilot,
    runs/puzzles/bench-pilot/on, 2026-10-20: 0 rules_lookup calls, none possible)."""

    def test_rules_tool_without_memory_tools_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            run_batch.main(["--rules-tool", "--seeds", "1"])
        self.assertIn("--memory-tools", str(cm.exception.code))


class TestUnpinnedSeat(unittest.TestCase):
    """Issue #234: a seat with no controller is an llm seat, and an llm tier with no model spec fell through
    to the in-JVM default (Opus for the smart tier): a Luna mirror launched with only seat A pinned played
    Luna against Opus 5.5, every stamp healthy. Every llm tier must be named, in every mode."""

    def test_an_llm_seat_without_both_model_specs_is_refused(self):
        luna = "openrouter:openai/gpt-6-luna"
        for kw in ({"controller_b": None}, {"controller_b": "llm", "smart_b": luna},
                   {"controller_b": "llm", "fast_b": luna}, {"smart_a": None}):
            with self.subTest(**kw):
                self.assertTrue(run_batch.unpinned_llm_tiers(make_args(**kw)))
        for kw in ({}, {"controller_b": "llm", "smart_b": luna, "fast_b": luna},
                   {"controller_a": "floor", "smart_a": None, "fast_a": None}):
            with self.subTest(**kw):
                self.assertEqual(run_batch.unpinned_llm_tiers(make_args(**kw)), [])

    def test_the_launcher_refuses_before_anything_runs(self):
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                            "--deck", "/tmp/deck.txt", "--controller-a", "llm",
                            "--smart-a", "openrouter:openai/gpt-6-luna", "--fast-a", "openrouter:openai/gpt-6-luna",
                            "--forge-home", "/nonexistent"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("--smart-b", p.stderr)
        self.assertNotIn("[out]", p.stdout)


class TestOneTier(unittest.TestCase):
    """ONE_TIER (every decision on the smart bridge) changes how hard the model may think per
    decision, so it is a condition: set only by --one-tier, recorded, checked against `one_tier`."""

    def test_only_the_flag_sets_it_and_lines_keep_it_apart(self):
        with mock.patch.dict(os.environ, {"ONE_TIER": "1"}, clear=False), tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_batch.build_env(make_args(), 7100, False, Path(d))["ONE_TIER"], "0")
            self.assertEqual(run_batch.build_env(make_args(one_tier=True), 7100, False, Path(d))["ONE_TIER"], "1")
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                            "--env", "ONE_TIER=1", "--forge-home", "/nonexistent"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--one-tier", p.stderr)
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(one_tier=True))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))        # predates the stamp
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, one_tier=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, one_tier=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        one = run_batch.batch_condition(make_args(one_tier=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, one), [("one_tier", False, True)])


class TestNoCoaching(unittest.TestCase):
    """NO_COACHING drops PRINCIPLES and the static reference from the system prompt, an assistance
    condition: set only by --no-coaching, recorded, checked against `no_coaching`."""

    def test_only_the_flag_sets_it_and_lines_keep_it_apart(self):
        with mock.patch.dict(os.environ, {"NO_COACHING": "1"}, clear=False), tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_batch.build_env(make_args(), 7100, False, Path(d))["NO_COACHING"], "0")
            self.assertEqual(run_batch.build_env(make_args(no_coaching=True), 7100, False, Path(d))["NO_COACHING"], "1")
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                            "--env", "NO_COACHING=1", "--forge-home", "/nonexistent"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--no-coaching", p.stderr)
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(no_coaching=True))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))        # predates the stamp
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, no_coaching=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, no_coaching=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        bare = run_batch.batch_condition(make_args(no_coaching=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, bare), [("no_coaching", False, True)])


class TestAnswerTool(unittest.TestCase):
    """The answer tool (every decision answered by a function call) is the default decision protocol on
    a backend with function calling, --text-answers is the opt-out, and the two never share a --out."""

    OR = "openrouter:deepseek/deepseek-v4.1-flash"

    def test_it_is_the_default_on_openrouter_and_the_flag_opts_out(self):
        self.assertTrue(run_batch.answer_tool_requested(make_args(smart_a=self.OR)))     # no --memory-tools needed
        self.assertFalse(run_batch.answer_tool_requested(make_args(smart_a=self.OR, text_answers=True)))
        self.assertFalse(run_batch.answer_tool_requested(make_args(smart_a=self.OR, text_tools=True)))
        self.assertFalse(run_batch.answer_tool_requested(make_args(smart_a="haiku")))   # the Claude CLI lane
        self.assertFalse(run_batch.answer_tool_requested(make_args(smart_a=self.OR, controller_a="floor")))
        self.assertTrue(run_batch.answer_tool_requested(make_args(smart_a="haiku", fast_a=self.OR)))

    def test_a_decision_model_seat_answers_through_the_tool(self):
        # A jev: seat's typed answer becomes the `answer` call (DecisionsBridge): the game line stamps it so.
        self.assertTrue(run_batch.answer_tool_requested(make_args(smart_a="jev:typesafe/jev-1.14")))
        self.assertFalse(run_batch.native_tools_requested(make_args(smart_a="jev:typesafe/jev-1.14", memory_tools=True)))

    def test_a_decision_model_seat_is_not_a_claude_cli_spec(self):
        # pr-review of #305: the cited --jobs guard, the parallel warning and the key preflight read jev: as a
        # bare claude-CLI spec.
        jev = "jev:typesafe/jev-1.14"
        self.assertEqual(run_batch.cli_specs(make_args(smart_a=jev, fast_a=jev, controller_b="ai")), [])
        self.assertEqual(run_batch.cli_specs(make_args(smart_a=jev, fast_a="haiku", controller_b="ai")), ["haiku"])
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            with self.assertRaises(SystemExit) as cm:
                run_batch.preflight_openrouter(make_args(smart_a=jev, fast_a=jev, controller_b="ai"))
        self.assertIn("OPENROUTER_API_KEY", str(cm.exception.code))

    def test_a_decision_model_seat_refuses_lookups_lookahead_and_text_answers(self):
        for flag, reason in (("--memory-tools", "cannot call a lookup"), ("--lookahead", "cannot call a lookup"),
                             ("--text-answers", "only through the `answer` call"),
                             ("--text-tools", "only through the `answer` call")):
            with self.assertRaises(SystemExit) as cm:
                run_batch.main(["--seeds", "1", "--smart-a", "jev:typesafe/jev-1.14", "--fast-a", "jev:typesafe/jev-1.14",
                                "--controller-b", "ai", flag])
            self.assertIn(flag, str(cm.exception.code))
            self.assertIn("jev:", str(cm.exception.code))
            self.assertIn(reason, str(cm.exception.code))

    CLI = "claude-cli:claude-opus-5-5"

    def test_a_claude_cli_seat_answers_through_the_tool_on_the_local_cli(self):
        # Its structured output becomes the `answer` call (ClaudeCliBridge); it runs on the local CLI, so the cited
        # --jobs 1 rule and the parallel warning cover it, and it needs no OpenRouter key.
        self.assertTrue(run_batch.answer_tool_requested(make_args(smart_a=self.CLI, fast_a=self.CLI)))
        self.assertFalse(run_batch.native_tools_requested(make_args(smart_a=self.CLI, fast_a=self.CLI, memory_tools=True)))
        self.assertEqual(run_batch.cli_specs(make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai")), [self.CLI])
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENROUTER_ALLOW_EGRESS": ""}, clear=False):
            run_batch.preflight_openrouter(make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai"))

    def test_a_claude_cli_seat_refuses_lookups_text_answers_and_a_kept_conversation(self):
        for flag, reason in (("--memory-tools", "cannot call a lookup"), ("--lookahead", "cannot call a lookup"),
                             ("--text-answers", "only through the `answer` call"),
                             ("--text-tools", "only through the `answer` call"),
                             ("--one-chat", "fresh process"), ("--turn-chat", "fresh process")):
            with self.assertRaises(SystemExit) as cm, mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
                run_batch.main(["--seeds", "1", "--smart-a", self.CLI, "--fast-a", self.CLI, "--controller-b", "ai", flag])
            self.assertIn(flag, str(cm.exception.code))
            self.assertIn("claude-cli:", str(cm.exception.code))
            self.assertIn(reason, str(cm.exception.code))

    def test_a_claude_cli_seat_is_refused_without_its_explicit_opt_in(self):
        # Owner, 2026-10-24: the lane is local development on the owner's subscription, chosen explicitly.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CLI_SEAT", None)
            with self.assertRaises(SystemExit) as cm:
                run_batch.main(["--seeds", "1", "--exploratory", "--smart-a", self.CLI, "--fast-a", self.CLI,
                                "--controller-b", "ai"])
        msg = str(cm.exception.code)
        self.assertIn("CLAUDE_CLI_SEAT=1", msg)
        self.assertIn("local development", msg)

    def test_the_lane_refusals_cover_every_lane_present(self):
        # A jev: seat and a claude-cli: seat in one batch: each lane's refusal is named, not only the first.
        with self.assertRaises(SystemExit) as cm, mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
            run_batch.main(["--seeds", "1", "--smart-a", "jev:typesafe/jev-1.14", "--fast-a", "jev:typesafe/jev-1.14",
                            "--smart-b", self.CLI, "--fast-b", self.CLI, "--memory-tools", "--one-chat"])
        msg = str(cm.exception.code)
        self.assertIn("jev:", msg)
        self.assertIn("claude-cli:", msg)
        self.assertIn("--one-chat", msg)

    def test_the_bare_cli_lane_is_retired(self):
        # A bare Claude name (opus, haiku) as a model seat: refused, opted in or not, saying what to use instead.
        for env in ({}, {"CLAUDE_CLI_SEAT": "1"}):
            with mock.patch.dict(os.environ, env, clear=False):
                if not env:
                    os.environ.pop("CLAUDE_CLI_SEAT", None)
                with self.assertRaises(SystemExit) as cm:
                    run_batch.main(["--seeds", "1", "--exploratory", "--smart-a", "opus", "--fast-a", "haiku",
                                    "--controller-b", "ai"])
            self.assertIn("retired", str(cm.exception.code))
            self.assertIn("claude-cli:<model>", str(cm.exception.code))
        with self.assertRaises(SystemExit) as cm, mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
            run_batch.main(["--seeds", "1", "--exploratory", "--smart-a", "Claude-CLI:opus", "--fast-a", "Claude-CLI:opus",
                            "--controller-b", "ai"])
        self.assertIn("claude-cli:", str(cm.exception.code))
        # No default model stands in for an unset tier: the specs in play are the ones named.
        self.assertEqual(run_batch.effective_llm_models(make_args(smart_a=None, fast_a=None, controller_b="ai")), [])

    def test_only_a_model_seats_spec_needs_the_opt_in(self):
        # As in the JVM: a floor or random seat's model name starts nothing, so it needs no opt-in and is no dev lane.
        a = make_args(smart_a=self.CLI, fast_a=self.CLI, controller_a="floor", controller_b="random")
        self.assertEqual(run_batch.cli_specs(a), [])
        self.assertFalse(run_batch.has_cli_seat(a))
        self.assertEqual(run_batch.cli_specs(make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai")), [self.CLI])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CLI_SEAT", None)
            self.assertIsNone(run_batch.opt_in_refusal(a))
            self.assertIn("CLAUDE_CLI_SEAT=1", run_batch.opt_in_refusal(make_args(smart_a=self.CLI, fast_a=self.CLI,
                                                                                   controller_b="ai")))

    def test_the_cli_settings_are_the_flags_alone(self):
        # Standards section 4: an --env override would run a value the manifest records as "cli-default".
        for k in ("CLAUDE_BRIDGE_EFFORT", "CLAUDE_BRIDGE_EFFORT_SMART", "CLAUDE_BRIDGE_EFFORT_FAST", "CLAUDE_CLI_THINKING_TOKENS"):
            self.assertIn(k, run_batch.CONDITION_ENV_KEYS)
            with self.assertRaises(SystemExit) as cm, mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
                run_batch.main(["--seeds", "1", "--exploratory", "--smart-a", self.CLI, "--fast-a", self.CLI,
                                "--controller-b", "ai", "--env", "%s=7" % k])
            self.assertIn(k, str(cm.exception.code))
        # And the shell's value never reaches a batch, whatever its seats.
        with mock.patch.dict(os.environ, {"CLAUDE_BRIDGE_EFFORT_SMART": "low"}), tempfile.TemporaryDirectory() as d:
            self.assertNotIn("CLAUDE_BRIDGE_EFFORT_SMART", run_batch.build_env(make_args(smart_a=self.OR, fast_a=self.OR), 1, False,
                                                                              Path(d)))

    def test_a_claude_cli_seat_is_never_in_a_cited_run_or_a_panel(self):
        args = make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai", exploratory=False)
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            run_batch.cited_pre_guards(args)
        self.assertIn("development lane", err.getvalue())
        with self.assertRaises(SystemExit) as cm, mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
            run_batch.main(["--panel", "mirror", "--budget-usd", "1", "--smart-a", self.CLI, "--fast-a", self.CLI])
        self.assertIn("benchmark", str(cm.exception.code))

    def test_a_claude_cli_batch_pins_its_effort_and_thinking_budget_never_the_shells(self):
        shell = {"CLAUDE_BRIDGE_EFFORT": "max", "CLAUDE_BRIDGE_EFFORT_SMART": "low", "CLAUDE_CLI_THINKING_TOKENS": "999"}
        with mock.patch.dict(os.environ, shell), tempfile.TemporaryDirectory() as d:
            a = make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai", claude_cli_effort="high",
                          claude_cli_thinking_tokens=16000)
            env = run_batch.build_env(a, 1, False, Path(d))
            self.assertEqual((env["CLAUDE_BRIDGE_EFFORT"], env["CLAUDE_BRIDGE_EFFORT_SMART"], env["CLAUDE_BRIDGE_EFFORT_FAST"],
                              env["CLAUDE_CLI_THINKING_TOKENS"]), ("high", "", "", "16000"))
            unset = make_args(smart_a=self.CLI, fast_a=self.CLI, controller_b="ai")
            env = run_batch.build_env(unset, 1, False, Path(d))
            self.assertEqual((env["CLAUDE_BRIDGE_EFFORT"], env["CLAUDE_CLI_THINKING_TOKENS"]), ("", ""))
            cond = run_batch.batch_condition(a, cited=False)
            self.assertEqual((cond["claude_cli_effort"], cond["claude_cli_thinking_tokens"]), ("high", "16000"))
            self.assertEqual(run_batch.batch_condition(unset, cited=False)["claude_cli_thinking_tokens"], "cli-default")
            self.assertIn("claude_cli_effort", [k for k, _, _ in run_batch.condition_diff(cond,
                                                                                         run_batch.batch_condition(unset, False))])
            # Another model's batch has no claude-cli key in its condition.
            other = make_args(smart_a=self.OR, fast_a=self.OR, controller_b="ai")
            self.assertIsNone(run_batch.batch_condition(other, cited=False).get("claude_cli_effort"))

    def test_only_the_flags_set_it_and_lines_keep_it_apart(self):
        with mock.patch.dict(os.environ, {"ANSWER_TOOL": "1", "ANSWER_TOOL_STRICT": "1"}, clear=False), \
                tempfile.TemporaryDirectory() as d:
            off = run_batch.build_env(make_args(), 7100, False, Path(d))
            self.assertEqual((off["ANSWER_TOOL"], off["ANSWER_TOOL_STRICT"], off["NATIVE_TOOLS"]), ("0", "0", "0"))
            on = run_batch.build_env(make_args(answer_tool=True), 7100, False, Path(d))
            # Function calling stays on for the answer even without --memory-tools.
            self.assertEqual((on["ANSWER_TOOL"], on["ANSWER_TOOL_STRICT"], on["NATIVE_TOOLS"]), ("1", "0", "1"))
            strict = run_batch.build_env(make_args(answer_tool=True, answer_tool_strict=True), 7100, False, Path(d))
            self.assertEqual(strict["ANSWER_TOOL_STRICT"], "1")
        for key in ("ANSWER_TOOL", "ANSWER_TOOL_STRICT"):
            p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                                "--env", key + "=1", "--forge-home", "/nonexistent"], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("--text-answers", p.stderr)
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(answer_tool=True))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))        # predates the stamp
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, answer_tool=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, answer_tool=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        tool = run_batch.batch_condition(make_args(answer_tool=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, tool), [("answer_tool", False, True)])

    def test_a_legacy_directory_of_text_answers_refuses_tool_answers(self):
        with tempfile.TemporaryDirectory() as d:
            write_game(d, "game-7100-1.jsonl", game_line(7100, False))
            legacy = run_batch.legacy_dir_condition(d)
            self.assertEqual(legacy["fields"]["answer_tool"], [False])


class TestOneChat(unittest.TestCase):
    """ONE_CHAT (one conversation per seat) changes what each ask carries, so it is a condition:
    set only by --one-chat, recorded, checked against `one_chat`."""

    def test_only_the_flag_sets_it_and_lines_keep_it_apart(self):
        with mock.patch.dict(os.environ, {"ONE_CHAT": "1"}, clear=False), tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_batch.build_env(make_args(), 7100, False, Path(d))["ONE_CHAT"], "0")
            self.assertEqual(run_batch.build_env(make_args(one_chat=True), 7100, False, Path(d))["ONE_CHAT"], "1")
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                            "--env", "ONE_CHAT=1", "--forge-home", "/nonexistent"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--one-chat", p.stderr)
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(one_chat=True))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))        # predates the stamp
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, one_chat=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, one_chat=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        bare = run_batch.batch_condition(make_args(one_chat=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, bare), [("one_chat", False, True)])


class TestTurnChat(unittest.TestCase):
    """TURN_CHAT (the conversation bounded by the game turn) is its own condition: set only by --turn-chat,
    recorded, and never pooled with stateless or whole-game-chat games."""

    def test_only_the_flag_sets_it_and_lines_keep_it_apart(self):
        with mock.patch.dict(os.environ, {"TURN_CHAT": "1"}, clear=False), tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_batch.build_env(make_args(), 7100, False, Path(d))["TURN_CHAT"], "0")
            self.assertEqual(run_batch.build_env(make_args(turn_chat=True), 7100, False, Path(d))["TURN_CHAT"], "1")
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                            "--env", "TURN_CHAT=1", "--forge-home", "/nonexistent"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--turn-chat", p.stderr)
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(turn_chat=True))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, turn_chat=True), off))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False, turn_chat=True), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        base = run_batch.batch_condition(make_args(), cited=False)
        turn = run_batch.batch_condition(make_args(turn_chat=True), cited=False)
        self.assertEqual(run_batch.condition_diff(base, turn), [("turn_chat", False, True)])


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
        self.assertEqual(run_batch.condition_diff(old, la), [])
        self.assertEqual(run_batch.condition_diff(old, base), [("lookahead", True, False)])

    def test_resume_refuses_every_directory_that_mixes_or_contradicts_lookahead(self):
        # The rule: a line's lookahead is its stamp, or, unstamped (before 2026-10-13), what a
        # pre-flag manifest recorded (--env LOOKAHEAD=1), else off. A
        # directory resumes only when every line and the manifest agree with the request; a
        # pre-flag manifest that recorded nothing is overruled only by lines all stamped alike.
        base = run_batch.batch_condition(make_args(), cited=False)
        la = run_batch.batch_condition(make_args(lookahead=True), cited=False)
        pre = {k: v for k, v in base.items() if k != "lookahead"}
        manifests = {"none": None, "key=off": base, "key=on": la, "pre-flag": pre,
                     "pre-flag+env": dict(pre, env_overrides={"LOOKAHEAD": "1"})}
        U, T, F = {}, {"lookahead": True}, {"lookahead": False}
        lines = {"zero": [], "U": [U], "T": [T], "F": [F], "U+T": [U, T], "U+F": [U, F], "T+F": [T, F]}
        # (manifest, lines) -> (resumes off, resumes on)
        expect = {
            "none":         {"zero": (1, 1), "U": (1, 0), "T": (0, 1), "F": (1, 0), "U+T": (0, 0), "U+F": (1, 0), "T+F": (0, 0)},
            "key=off":      {"zero": (1, 0), "U": (1, 0), "T": (0, 0), "F": (1, 0), "U+T": (0, 0), "U+F": (1, 0), "T+F": (0, 0)},
            "key=on":       {"zero": (0, 1), "U": (0, 0), "T": (0, 1), "F": (0, 0), "U+T": (0, 0), "U+F": (0, 0), "T+F": (0, 0)},
            "pre-flag":     {"zero": (1, 0), "U": (1, 0), "T": (0, 1), "F": (1, 0), "U+T": (0, 0), "U+F": (1, 0), "T+F": (0, 0)},
            "pre-flag+env": {"zero": (0, 1), "U": (0, 1), "T": (0, 1), "F": (0, 0), "U+T": (0, 1), "U+F": (0, 0), "T+F": (0, 0)},
        }
        for mname, manifest in manifests.items():
            for lname, recs in lines.items():
                with tempfile.TemporaryDirectory() as d:
                    if manifest is not None:
                        run_batch.write_manifest(d, manifest, seeds=[7100], pairs=False, jobs=1)
                    for i, extra in enumerate(recs):
                        write_game(d, "game-%d-1000.jsonl" % (7100 + i), game_line(7100 + i, False, **extra))
                    got = tuple(int("lookahead" not in (run_batch.check_resume_condition(d, c, cited=False) or ""))
                                for c in (base, la))
                    with self.subTest(manifest=mname, lines=lname):
                        self.assertEqual(got, expect[mname][lname])

    def test_unstamped_lines_of_a_pre_flag_lookahead_batch_count_as_done(self):
        # A batch run with --env LOOKAHEAD=1 before the flag: its unstamped lines ran with the
        # preview on, so resuming under --lookahead must not replay them.
        args = make_args(lookahead=True)
        args.unstamped_lookahead = run_batch.unstamped_lookahead(
            {"condition": {"env_overrides": {"LOOKAHEAD": "1"}}})
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), run_batch.resume_expectation(args)))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), run_batch.resume_expectation(make_args(lookahead=True))))


class TestPlanExecuteAndTierFastTypes(unittest.TestCase):
    """PLAN_EXECUTE and TIER_FAST_TYPES change how the seat plays (issue #117): the harness plays a
    step of the plan, or a decision type moves off the reasoning tier. Both are conditions like
    --lookahead: set only by their flags, scrubbed from the ambient environment, recorded in the
    manifest and checked against each game line's own stamp."""

    def test_only_the_flags_set_them(self):
        with mock.patch.dict(os.environ, {"PLAN_EXECUTE": "1", "TIER_FAST_TYPES": "targets"},
                             clear=False), tempfile.TemporaryDirectory() as d:
            env = run_batch.build_env(make_args(), 7100, False, Path(d))
            self.assertEqual(env["PLAN_EXECUTE"], "0")   # ambient value scrubbed
            self.assertEqual(env["TIER_FAST_TYPES"], "")
            on = run_batch.build_env(make_args(plan_execute=True, tier_fast_types="targets,target"),
                                     7100, False, Path(d))
            self.assertEqual(on["PLAN_EXECUTE"], "1")
            self.assertEqual(on["TIER_FAST_TYPES"], "target,targets")  # normalized
        for kv in ("PLAN_EXECUTE=1", "TIER_FAST_TYPES=targets"):
            p = subprocess.run([sys.executable, str(RUN_BATCH), "--seeds", "7100", "--exploratory",
                                "--env", kv, "--forge-home", "/nonexistent"],
                               capture_output=True, text=True)
            with self.subTest(env=kv):
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("--plan-execute", p.stderr)

    def test_game_lines_and_manifests_keep_them_apart(self):
        off = run_batch.resume_expectation(make_args())
        on = run_batch.resume_expectation(make_args(plan_execute=True, tier_fast_types="target,targets"))
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), off))            # pre-stamp line
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, plan_execute=True), off))
        self.assertTrue(run_batch.game_line_matches(
            game_line(1, False, plan_execute=True, tier_fast_types="target,targets"), on))
        self.assertFalse(run_batch.game_line_matches(game_line(1, False), on))
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False, tier_fast_types="target"), on))  # wrong type set
        base = run_batch.batch_condition(make_args(), cited=False)
        sw = run_batch.batch_condition(make_args(plan_execute=True, tier_fast_types="targets,target"),
                                       cited=False)
        self.assertEqual(run_batch.condition_diff(base, sw),
                         [("plan_execute", False, True),
                          ("tier_fast_types", "", "target,targets")])
        # A manifest from before the flags recorded them as `--env`; the same values under the flags
        # are the same condition, and without the flags they are not.
        old = dict(base, env_overrides={"PLAN_EXECUTE": "1", "TIER_FAST_TYPES": "Targets, target"})
        del old["plan_execute"]
        del old["tier_fast_types"]
        self.assertEqual(run_batch.condition_diff(old, sw), [])
        self.assertEqual(run_batch.condition_diff(old, base),
                         [("plan_execute", True, False),
                          ("tier_fast_types", "target,targets", "")])

    def test_unstamped_lines_of_a_pre_flag_batch_count_as_done(self):
        # A batch run with `--env PLAN_EXECUTE=1 TIER_FAST_TYPES=...` before the flags: its unstamped
        # lines ran with them on, so resuming under the flags must not replay its finished games.
        manifest = {"condition": {"env_overrides": {"PLAN_EXECUTE": "1", "TIER_FAST_TYPES": "targets"}}}
        args = make_args(plan_execute=True, tier_fast_types="targets")
        args.unstamped_plan_execute = run_batch.unstamped_play_switch(manifest, "plan_execute",
                                                                      "PLAN_EXECUTE")
        args.unstamped_tier_fast_types = run_batch.unstamped_play_switch(manifest, "tier_fast_types",
                                                                         "TIER_FAST_TYPES")
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), run_batch.resume_expectation(args)))
        # Without a pre-flag manifest that proves the value, an unstamped line is not this batch's.
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False), run_batch.resume_expectation(
                make_args(plan_execute=True, tier_fast_types="targets"))))

    def test_tier_fast_types_is_normalized(self):
        self.assertEqual(run_batch.normalize_tier_fast_types("Targets, target ,targets"), "target,targets")
        self.assertEqual(run_batch.normalize_tier_fast_types(None), "")
        self.assertEqual(run_batch.normalize_tier_fast_types("  "), "")


class TestResumeIdentity(unittest.TestCase):
    """A game line counts as 'already done' only under this batch's condition."""

    def test_expectation_matches_baseline_and_legacy_lines(self):
        exp = run_batch.resume_expectation(make_args())
        self.assertTrue(run_batch.game_line_matches(game_line(1, False), exp))
        # Unset is v4-faithful: a v1-compat line or a pre-profile line is a different condition.
        self.assertFalse(run_batch.game_line_matches(game_line(1, False, context_profile=None), exp))
        self.assertFalse(run_batch.game_line_matches(
            game_line(1, False, context_profile="v1-compat", memory_tools=False), exp))
        v1 = run_batch.resume_expectation(make_args(context_profile="v1-compat"))
        self.assertTrue(run_batch.game_line_matches(
            game_line(1, False, context_profile="v1-compat", memory_tools=False), v1))
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
                       game_line(7100, True, context_profile="v4-faithful", memory_tools=False))
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


def panel_args(**kw):
    base = dict(panel="mirror", panel_file=str(run_batch.PANEL_FILE), panel_cells=None, no_control=False,
                sanity_arms=False, budget_usd=5.0, seeds=None, deck=None, deck_a=None, deck_b=None,
                controller_a="llm", controller_b=None, smart_a="openrouter:deepseek/deepseek-v4.1-flash",
                fast_a="openrouter:deepseek/deepseek-v4.1-flash", out="/tmp/panel-root", exploratory=False,
                memory_tools=True, lookahead=True)
    base.update(kw)
    return make_args(**base)


class TestPanelLaunch(unittest.TestCase):
    def test_the_mirror_panel_plans_one_pilot_block_per_deck_against_the_forge_ai(self):
        root, plan = run_batch.plan_panel(panel_args())
        self.assertEqual(len(plan), 16)
        self.assertTrue(all(kind == "pilot" for kind, _, _ in plan))
        for _, cid, a in plan:
            self.assertEqual(a.deck_a, a.deck_b)
            self.assertTrue(a.deck_a.endswith("/eval/decks/%s.txt" % cid))
            self.assertEqual((a.controller_b, a.smart_b, a.controller_a), ("ai", None, "llm"))
            self.assertEqual(a.seeds, list(range(7800, 7850)))
            self.assertTrue(a.pairs)
            self.assertEqual(a.out, str(root / cid))
            self.assertEqual(a.budget_root, str(root))
            self.assertIsNone(a.cross_pin)

    def test_the_cross_panel_adds_a_control_on_four_times_the_seeds(self):
        root, plan = run_batch.plan_panel(panel_args(panel="cross", seeds=[7800, 7801]))
        self.assertEqual([k for k, _, _ in plan[:2]], ["pilot", "control"])
        self.assertEqual(len(plan), 32)
        _, cid, pilot = plan[0]
        _, _, control = plan[1]
        self.assertEqual(cid, "ant-storm__vs__elves")
        self.assertEqual(pilot.cross_pin, tuple(d["sha12"] for d in json.loads(
            run_batch.PANEL_FILE.read_text())["deck_pool"] if d["name"] in ("ant-storm", "elves")))
        self.assertEqual((control.controller_a, control.controller_b, control.smart_a), ("ai", "ai", None))
        self.assertEqual(control.seeds, list(range(7800, 7808)))
        self.assertEqual(control.out, str(root / (cid + "__control")))
        self.assertEqual(len(run_batch.plan_panel(panel_args(panel="cross", no_control=True))[1]), 16)

    def test_seeds_outside_the_published_list_are_refused(self):
        with self.assertRaises(SystemExit):
            run_batch.plan_panel(panel_args(seeds=[7799, 7800]))

    def test_a_panel_fixes_the_decks_and_the_opponent(self):
        for kw in ({"deck": "/tmp/x.txt"}, {"controller_b": "random"}, {"smart_b": "haiku"}):
            with self.assertRaises(SystemExit):
                run_batch.plan_panel(panel_args(**kw))

    def test_an_llm_pilot_launches_only_under_the_pinned_pilot_condition(self):
        for kw in ({"memory_tools": False}, {"lookahead": False}, {"rules_tool": True}, {"two_tier": True},
                   {"turn_chat": True}, {"context_profile": "v2-memory"}, {"text_answers": True}):
            with self.subTest(**kw), self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                run_batch.plan_panel(panel_args(**kw))
        # A sanity-arm pilot (no model) has no pilot condition to meet.
        self.assertEqual(len(run_batch.plan_panel(panel_args(controller_a="floor", memory_tools=False))[1]), 16)

    def test_a_subset_of_cells_can_be_launched(self):
        _, plan = run_batch.plan_panel(panel_args(panel_cells="burn,elves"))
        self.assertEqual([cid for _, cid, _ in plan], ["burn", "elves"])
        with self.assertRaises(SystemExit):
            run_batch.plan_panel(panel_args(panel_cells="burn,nope"))

    def test_panels_and_sanity_arms_refuse_to_launch_without_a_budget(self):
        for extra in (["--panel", "mirror"], ["--seeds", "7800", "--sanity-arms"]):
            p = subprocess.run([sys.executable, str(RUN_BATCH)] + extra + ["--forge-home", "/nonexistent"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertIn("--budget-usd", p.stderr)

    def test_seeds_stay_required_outside_a_panel(self):
        p = subprocess.run([sys.executable, str(RUN_BATCH), "--forge-home", "/nonexistent"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--seeds is required", p.stderr)


class TestCrossPin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.da = Path(self.tmp.name) / "a.txt"
        self.db = Path(self.tmp.name) / "b.txt"
        self.da.write_text("4 Lightning Bolt\n")
        self.db.write_text("4 Brainstorm\n")

    def tearDown(self):
        self.tmp.cleanup()

    def cited(self, **kw):
        # A cited run's seat is never on the Claude CLI (a development lane): an openrouter: pilot.
        a = make_args(exploratory=False, deck_a=str(self.da), deck_b=str(self.db), seeds=[7800],
                      smart_a="openrouter:deepseek/deepseek-v4.1-flash", fast_a="openrouter:deepseek/deepseek-v4.1-flash", **kw)
        run_batch.cited_pre_guards(a)
        return a

    def test_cited_mode_accepts_asymmetric_decks_only_with_matching_pins(self):
        pin = (run_batch._deck_sha(self.da), run_batch._deck_sha(self.db))
        self.cited(cross_pin=pin)
        with self.assertRaises(SystemExit):
            self.cited(cross_pin=(pin[1], pin[0]))
        with self.assertRaises(SystemExit):
            self.cited()


class TestSanityArmLaunch(unittest.TestCase):
    def test_a_sanity_arm_changes_only_identity_a(self):
        scored = make_args(seeds=[7800, 7801], controller_b="ai", smart_a="openrouter:deepseek/deepseek-v4.1-flash",
                           fast_a="openrouter:deepseek/deepseek-v4.1-flash", checkpoint="tag", memory_tools=True)
        for arm in ("floor", "random", "empty", "garbage"):
            a = run_batch.sanity_arm_args(scored, arm, "/r/burn")
            self.assertEqual((a.deck_a, a.deck_b, a.seeds, a.controller_b, a.cap_minutes),
                             (scored.deck_a, scored.deck_b, scored.seeds, "ai", scored.cap_minutes))
            self.assertEqual(a.out, "/r/burn__sanity-" + arm)
            self.assertIsNone(a.checkpoint)
        self.assertEqual(run_batch.sanity_arm_args(scored, "floor", "/r/burn").controller_a, "floor")
        fake = run_batch.sanity_arm_args(scored, "empty", "/r/burn")
        self.assertEqual((fake.smart_a, fake.fake_policy), ("openrouter:fake/harness-model", "empty"))
        self.assertEqual(scored.controller_a, "llm")      # the scored namespace is untouched

    def test_free_arms_run_past_the_budget_but_arms_facing_a_paid_model_do_not(self):
        spec = "openrouter:deepseek/deepseek-v4.1-flash"
        vs_ai = make_args(controller_b="ai", budget_usd=1.0)
        self.assertIsNone(run_batch.sanity_arm_args(vs_ai, "empty", "/r/x").budget_usd)
        vs_model = make_args(controller_b="llm", smart_b=spec, fast_b=spec, budget_usd=1.0)
        self.assertEqual(run_batch.sanity_arm_args(vs_model, "floor", "/r/x").budget_usd, 1.0)
        vs_cli = make_args(controller_b="llm", smart_b="haiku", fast_b="haiku", budget_usd=1.0)
        self.assertEqual(run_batch.sanity_arm_args(vs_cli, "floor", "/r/x").budget_usd, 1.0)

    def test_the_loopback_endpoint_reaches_the_jvm_but_not_the_manifest(self):
        a = make_args(fake_policy="garbage", loopback_env={"OPENROUTER_BASE_URL": "http://127.0.0.1:5555/v1"})
        self.assertEqual(run_batch.build_env(a, 7800, False, "/tmp/o")["OPENROUTER_BASE_URL"],
                         "http://127.0.0.1:5555/v1")
        cond = run_batch.batch_condition(a, cited=False)
        self.assertEqual(cond["fake_policy"], "garbage")
        self.assertNotIn("5555", json.dumps(cond))

    def test_the_fake_model_is_priced_at_zero_so_a_budget_admits_it(self):
        prices = run_batch.cost_report.load_prices()
        self.assertEqual(run_batch.cost_report.price_of(prices, "openrouter:fake/harness-model")["prompt"], 0.0)

    def test_a_panel_budget_counts_every_directory_under_its_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = "openrouter:deepseek/deepseek-v4.1-flash"
            for sub in ("burn", "elves__sanity-random", "elves"):
                d = Path(tmp) / sub
                d.mkdir()
                write_game(d, "game-1-1.jsonl",
                           {"t": "decision", "seat": "Identity-A", "tier": "smart", "prompt_tokens": 1_000_000,
                            "completion_tokens": 0},
                           game_line(1, False, smart_a=spec, fast_a=spec))
            one = run_batch.spent_usd(Path(tmp) / "burn")
            self.assertGreater(one, 0)
            self.assertAlmostEqual(run_batch.spent_usd_tree(tmp), 3 * one)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCrashRerun(unittest.TestCase):
    """A game whose JVM crashes, writes no game line or hangs is played again; the failed attempt's files
    are set aside under crashed/, still priced, never aggregated."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "batch-manifest.json").write_text(json.dumps({"condition": {"smart_a": "openrouter:x/y"}}))

    def tearDown(self):
        self.tmp.cleanup()

    def crash(self, seed, swap, name):
        (self.out / ("game-%d-%d.log" % (seed, swap))).write_text("boot\n[EVAL] jsonl %s\nException\n" % name)
        (self.out / name).write_text(json.dumps({"t": "context_call", "tier": "smart", "prompt_tokens": 10}) + "\n")

    def test_only_the_crashed_attempts_files_are_set_aside(self):
        self.crash(7, 1, "game-7-200.jsonl")
        (self.out / "game-7-100.jsonl").write_text("{}\n")  # the other swap's game, running at the same time
        moved = run_batch.set_aside(self.out, 7, True, 1)
        self.assertEqual(sorted(moved), ["attempt1-swap1-game-7-1.log", "attempt1-swap1-game-7-200.jsonl"])
        self.assertTrue((self.out / "game-7-100.jsonl").is_file())
        self.assertTrue((self.out / "crashed" / "batch-manifest.json").is_file())
        self.assertFalse(list(self.out.glob("game-7-200*")))

    def test_a_retryable_failure_is_played_again_and_a_mismatch_is_not(self):
        calls = []

        def play(results):
            def f():
                calls.append(1)
                r = results[len(calls) - 1]
                if r != "ok":
                    self.crash(5, 0, "game-5-%d.jsonl" % len(calls))
                return (5, False, r, 1 if r != "ok" else 0)
            return f

        with mock.patch.object(run_batch, "log"):
            r = run_batch.play_with_retries(play(["no-game-line", "ok"]), 5, False, self.out, 1, lambda: False)
            self.assertEqual((r[2], len(calls)), ("ok", 2))
            self.assertEqual(len(list((self.out / "crashed").glob("attempt1-*"))), 2)
            calls.clear()
            r = run_batch.play_with_retries(play(["condition-mismatch", "ok"]), 5, False, self.out, 1, lambda: False)
            self.assertEqual((r[2], len(calls)), ("condition-mismatch", 1))
            calls.clear()
            r = run_batch.play_with_retries(play(["timeout", "timeout"]), 5, False, self.out, 1, lambda: False)
            self.assertEqual((r[2], len(calls)), ("timeout", 2))  # the last failure is reported, not hidden
            calls.clear()
            budget = iter([False, True])
            r = run_batch.play_with_retries(play(["nonzero-exit", "ok"]), 5, False, self.out, 1, lambda: next(budget))
            self.assertEqual((r[2], len(calls)), ("nonzero-exit", 1))  # the budget stops the retry
            self.assertEqual(run_batch.play_with_retries(play(["ok"]), 5, False, self.out, 1, lambda: True)[2], "budget")

    def test_a_set_aside_attempt_still_counts_against_the_budget_once(self):
        self.crash(3, 0, "game-3-1.jsonl")
        prices = {"x/y": {"prompt": 1.0, "completion": 1.0}}
        with mock.patch.object(run_batch.cost_report, "load_prices", return_value=prices):
            before = run_batch.spent_usd(self.out)
            run_batch.set_aside(self.out, 3, False, 1)
            self.assertAlmostEqual(run_batch.spent_usd(self.out), before)
            self.assertGreater(before, 0)
            self.assertAlmostEqual(run_batch.spent_usd_tree(self.out), before)
            self.assertEqual(run_batch.attempt_jsonl(self.out / "crashed" / "attempt1-swap0-game-3-0.log"), "game-3-1.jsonl")
