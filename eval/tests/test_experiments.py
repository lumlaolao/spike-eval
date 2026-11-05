#!/usr/bin/env python3
"""Experiment identity and grouping in aggregate.py.

A directory may hold games recorded under several CONDITIONS (context
profile, memory tools, model, checkpoint, ...). These tests pin the contract:
grouping happens before any seed/swap dedupe, pairs are matched only within an
experiment, pooled win rates are refused for multi-condition dirs, legacy rows
form their own explicit experiment, duplicates are dropped with a warning and
conflicting repeats are called out.

Served identity (ServedIdentityTest): the served_* fields partition
experiments exactly as each game observed them. An unresolved value is never
filled from another game, never paired with a resolved game, never deduped
against one, and two half-resolved games never form a joint condition. A cited
game with unresolved applicable identity is ineligible on its own. Baseline
seats (no model) are not-applicable, never unresolved. MISSING (field absent)
stays distinct from "unknown" (field present, empty).

Run: python3 -m unittest eval.test_experiments   (or discover from eval/)
All fixtures are synthetic; no real run directory is read.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import aggregate  # noqa: E402

AGG = evalpath.EVAL / "aggregate.py"
GUI = (evalpath.REPO / "forge-overlay" / "forge-gui-desktop" / "src"
       / "main" / "java" / "forge" / "spike" / "LlmSpikeGui.java")


def game_line_keys():
    """The game-line field names, in order, read from the writer's single statement. One parser, so
    the two tests that read it (the key-coverage and the env-coverage checks) move together when the
    writer changes."""
    java = GUI.read_text(encoding="utf-8")
    start = java.index('EvalLog.write("{\\"t\\":\\"game\\"')
    return re.findall(r'\\"([a-z_0-9]+)\\":', java[start:java.index(");", start)])


def modern_line(seed, swap, winner, **kw):
    """A game line with every field the current harness stamps."""
    g = {"t": "game", "seed": seed, "swap": swap,
         "deck_a": "Identity-A", "deck_b": "Identity-B",
         "controller_a": "llm", "controller_b": "random",
         "smart_a": "openrouter:student", "smart_b": "haiku",
         "fast_a": "openrouter:student", "fast_b": "haiku",
         "winner": winner, "turns": 9, "win_condition": "life", "duration_ms": 1000,
         "mulligan_heuristic": 0, "skip_heuristics": 0, "plan_a": "", "plan_b": "",
         "smart_budget": 200, "fast_budget": 400, "claude_deadline_ms": 60000,
         "game_cap_ms": 7200000, "engine_sha": "e" * 12, "overlay_sha": "o" * 12,
         "deck_sha": "d" * 12, "discipline": "cited",
         "served_smart_a": "student-v3", "served_smart_b": "unknown",
         "served_fast_a": "student-v3", "served_fast_b": "unknown",
         "openrouter_base_url": "http://127.0.0.1:8080/v1",
         "checkpoint_sha": "c" * 12, "model_cfg_sha": "builtin",
         "temperature_a": "0.7", "temperature_b": "n/a",
         "memory_tools": False, "memory_tools_version": "evidence-tools-v3",
         "seed_bank": "stage3-holdout",
         "context_profile": "v1-compat", "context_sha": "1111", "observation_version": "observation-faithful-v37",
         "claude_effort_smart": "n/a", "claude_effort_fast": "n/a",
         "native_tools": False, "native_tools_seats": "", "native_tools_version": "seat-tools-v4",
         "native_tools_downgraded": "", "answer_tool": False, "answer_tool_seats": "",
         "answer_tool_version": "answer-tool-v1", "rules_tool": False, "rules_tool_version": "rules-tool-v1",
         "rules_effective": "n/a", "lookahead": False, "lookahead_version": "lookahead-preview-v4",
         "one_tier": False, "no_coaching": False, "one_chat": False, "one_chat_version": "one-chat-v1",
         "turn_chat": False, "turn_chat_version": "turn-chat-v1", "harness_epoch": "harness-epoch-2",
         "plan_execute": False, "tier_fast_types": ""}
    g.update(kw)
    return g


def legacy_line(seed, swap, winner):
    """Pre-provenance schema: arm config and outcome only."""
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": "Identity-A", "deck_b": "Identity-B",
            "controller_a": "llm", "controller_b": "random",
            "smart_a": "haiku", "smart_b": None, "fast_a": "haiku", "fast_b": "haiku",
            "winner": winner, "turns": 5}


def write(d, name, lines):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def summary(d, **kw):
    return aggregate.build_summary(Path(d), resamples=200, bootstrap_seed=1, **kw)


class GroupingTest(unittest.TestCase):

    def test_same_seed_swap_under_two_profiles_is_two_experiments(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [modern_line(1, True, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", context_profile="v2-memory",
                                                       context_sha="2222")])
            write(d, "game-1-2001.jsonl", [modern_line(1, True, "B", context_profile="v2-memory",
                                                       context_sha="2222")])
            s = summary(d)
            e = s["experiments"]
            self.assertEqual(e["n"], 2)
            self.assertFalse(e["pooled"])
            # Nothing was deduped away across conditions.
            self.assertEqual(s["games"]["total"], 4)
            self.assertEqual(s["games"]["decisive"], 4)
            # Pooled rates are refused, not silently computed.
            self.assertEqual(s["arms"], {})
            self.assertTrue(s["paired"]["pooled_refused"])
            self.assertEqual(s["paired"]["n_pairs_used"], 0)
            self.assertIsNone(s["games"]["seat1_wins"])
            # Each experiment has its own complete pair and opposite verdicts.
            by_profile = {x["condition"]["context_profile"]: x for x in e["by_id"].values()}
            self.assertEqual(set(by_profile), {"v1-compat", "v2-memory"})
            self.assertEqual(by_profile["v1-compat"]["paired"]["focal_win_rate"], 1.0)
            self.assertEqual(by_profile["v2-memory"]["paired"]["focal_win_rate"], 0.0)
            self.assertEqual(by_profile["v1-compat"]["paired"]["n_pairs_used"], 1)
            self.assertIn("context_profile=v2-memory", by_profile["v2-memory"]["label"])
            self.assertTrue(any("2 distinct experiment conditions" in w for w in s["warnings"]))
            self.assertFalse(any("duplicate game" in w for w in s["warnings"]))

    def test_two_models_and_memory_toggle_partition(self):
        for field, other in (("smart_a", "openrouter:teacher"), ("memory_tools", True),
                             ("checkpoint_sha", "f" * 12), ("context_sha", "9999"),
                             ("observation_version", "observation-faithful-v38"),
                             ("game_cap_ms", 60000), ("claude_effort_smart", "max"),
                             # The tool protocol and the opt-in tools are conditions (CLAUDE.md:
                             # "Native and text runs are different conditions. Never pool them").
                             ("native_tools", True), ("native_tools_version", "seat-tools-v7"),
                             # Tool answers and text answers never pool (owner decision 2026-10-15).
                             ("answer_tool", True), ("answer_tool_seats", "A/smart"),
                             ("answer_tool_version", "answer-tool-v1+strict"),
                             ("native_tools_downgraded", "Identity-A/smart"),
                             ("rules_tool", True), ("lookahead", True),
                             ("native_tools_seats", "A/smart"), ("rules_tool_version", "rules-tool-v2"),
                             ("rules_effective", "unavailable"), ("lookahead_version", "lookahead-preview-v5"),
                             # The two opt-in play switches (issue #117): on-games and off-games
                             # are different measurements and never pool.
                             ("plan_execute", True), ("tier_fast_types", "target,targets")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
                write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", **{field: other})])
                s = summary(d)
                self.assertEqual(s["experiments"]["n"], 2, field)
                self.assertEqual(s["games"]["total"], 2, field)
                labels = [x["label"] for x in s["experiments"]["by_id"].values()]
                self.assertTrue(all(field in lb for lb in labels), labels)

    def test_non_behavioral_labels_do_not_partition(self):
        """discipline and seed_bank describe the run's labelling, not the
        player; they stay in the mixed-provenance report but not in the key."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A", discipline="exploratory",
                                                       seed_bank="dev")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            self.assertTrue(s["provenance_mixed"])
            self.assertIn("discipline", s["provenance"]["mixed_fields"])

    def test_pairs_match_within_experiment_only(self):
        """swap=false under one profile and swap=true under another is NOT a pair."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [modern_line(1, True, "A", context_profile="v3-compact",
                                                       context_sha="3333")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            for x in s["experiments"]["by_id"].values():
                self.assertEqual(x["paired"]["n_pairs_complete"], 0)
                self.assertEqual(x["paired"]["n_pairs_used"], 0)
            self.assertEqual(s["paired"]["n_pairs_complete"], 0)

    def test_single_condition_dir_is_backward_compatible(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in (1, 2):
                write(d, "game-%d-1000.jsonl" % seed, [modern_line(seed, False, "A")])
                write(d, "game-%d-1001.jsonl" % seed, [modern_line(seed, True, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            self.assertTrue(s["experiments"]["pooled"])
            self.assertEqual(s["arms"]["llm[openrouter:student/openrouter:student]"]["wins"], 4)
            self.assertEqual(s["paired"]["n_pairs_used"], 2)
            self.assertEqual(s["games"]["seat1_wins"], 2)
            only = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(only["label"], "(single condition)")
            self.assertFalse(only["legacy"])
            self.assertEqual(only["arms"], s["arms"])
            self.assertEqual(only["paired"]["delta_ci95"], s["paired"]["delta_ci95"])

    def test_legacy_rows_form_their_own_explicit_experiment(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [legacy_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [legacy_line(1, True, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            x = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertTrue(x["legacy"])
            self.assertIn("context_profile", x["legacy_fields"])
            self.assertIn("served_smart_a", x["legacy_fields"])
            self.assertEqual(x["condition"]["context_profile"], aggregate.MISSING)
            # Still reported in full: a legacy dir is usable, just not attributable.
            self.assertEqual(s["paired"]["n_pairs_used"], 1)
            self.assertTrue(s["model_identity"]["legacy"])
            self.assertFalse(s["model_identity"]["attributable"])

        with tempfile.TemporaryDirectory() as d:
            # Legacy and modern lines for the same combo never merge: MISSING is
            # a distinct recorded state, never coerced to the modern value.
            write(d, "game-1-1000.jsonl", [legacy_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            flags = sorted(x["legacy"] for x in s["experiments"]["by_id"].values())
            self.assertEqual(flags, [False, True])

    def test_identical_duplicate_is_dropped_and_labelled_retry_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            x = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(x["n_game_lines"], 2)
            self.assertEqual(x["n_games"], 1)
            self.assertEqual(len(x["duplicates"]), 1)
            dup = x["duplicates"][0]
            self.assertFalse(dup["conflicting"])
            self.assertTrue(dup["same_outcome"])
            self.assertEqual(dup["kept"], "game-1-2000.jsonl")
            self.assertEqual(dup["dropped"], "game-1-1000.jsonl")
            self.assertEqual(x["n_duplicates_conflicting"], 0)
            w = [w for w in s["warnings"] if "duplicate game" in w]
            self.assertEqual(len(w), 1)
            self.assertIn("retry artifact", w[0])
            self.assertEqual(s["games"]["total"], 1)

    def test_conflicting_repeat_keeps_latest_and_reports_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B")])
            s = summary(d)
            x = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(x["n_duplicates_conflicting"], 1)
            dup = x["duplicates"][0]
            self.assertTrue(dup["conflicting"])
            self.assertEqual((dup["dropped_winner"], dup["kept_winner"]), ("A", "B"))
            # The newest rerun is what counts; the conflict is reported, not averaged.
            self.assertEqual(x["arms"]["random"]["wins"], 1)
            self.assertEqual(x["arms"]["llm[openrouter:student/openrouter:student]"]["wins"], 0)
            w = [w for w in s["warnings"] if "CONFLICTING" in w]
            self.assertEqual(len(w), 1)
            self.assertIn("A vs B", w[0])

    def test_duplicate_under_other_condition_is_not_a_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", memory_tools=True)])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            for x in s["experiments"]["by_id"].values():
                self.assertEqual(x["duplicates"], [])
            self.assertFalse(any("duplicate game" in w for w in s["warnings"]))

    def test_experiment_id_is_stable_across_directories(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            write(d1, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d2, "game-7-1000.jsonl", [modern_line(7, True, "B")])
            s1, s2 = summary(d1), summary(d2)
            self.assertEqual(s1["experiments"]["order"], s2["experiments"]["order"])
        cond = aggregate.game_condition(modern_line(1, False, "A"))
        self.assertEqual(len(aggregate.experiment_id(cond)), 12)
        self.assertNotIn("seed", cond)
        self.assertNotIn("swap", cond)
        self.assertNotIn("winner", cond)
        self.assertNotIn("discipline", cond)

    def test_crashed_games_count_invalid_but_join_no_experiment(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-2-1000.jsonl", [{"t": "decision", "seat": "Identity-A", "tier": "smart"}])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            self.assertEqual(s["games"]["total"], 2)
            self.assertEqual(s["games"]["no_game_line"], 1)
            x = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(x["n_games"], 1)

    def test_select_experiment_by_id_or_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [modern_line(1, True, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", memory_tools=True)])
            s = summary(d)
            ids = s["experiments"]["order"]
            wanted = next(i for i in ids if s["experiments"]["by_id"][i]["condition"]["memory_tools"] is False)
            sel = summary(d, experiment=wanted[:6])
            self.assertEqual(sel["experiments"]["selected"], wanted)
            self.assertEqual(sel["experiments"]["n"], 1)
            self.assertEqual(sel["experiments"]["n_in_dir"], 2)
            self.assertTrue(sel["experiments"]["pooled"])
            self.assertEqual(sel["paired"]["n_pairs_used"], 1)
            self.assertEqual(sel["games"]["total"], 2)
            with self.assertRaises(ValueError):
                summary(d, experiment="nope")

    def test_parse_dir_compat_tuple_and_experiment_tag(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", memory_tools=True)])
            games, decisions, warnings = aggregate.parse_dir(Path(d))
            self.assertEqual(len(games), 2)
            self.assertEqual(len({g["_experiment"] for g in games}), 2)
            self.assertEqual(decisions, [])

    def test_every_game_line_key_is_in_the_experiment_key_or_is_an_outcome(self):
        """The game line's single writer, read from the Java source. A stamp left out of
        PROVENANCE_FIELDS pools the runs it was written to separate; that happened to memory_tools,
        claude_effort_* and the nine tool fields, each found only after the fact."""
        keys = game_line_keys()
        self.assertIn("native_tools", keys)          # the extraction found the statement
        self.assertEqual(keys[-1], "claude_effort_fast")  # ... and read it to its end
        outcome = {"t", "seed", "swap", "winner", "turns", "win_condition", "duration_ms", "game_error"}
        known = set(aggregate.CONDITION_FIELDS) | set(aggregate.NON_BEHAVIORAL_PROVENANCE) | outcome
        self.assertEqual([k for k in keys if k not in known], [],
                         "add the new game-line key to aggregate.PROVENANCE_FIELDS (or, if it is an "
                         "outcome, to this test's outcome set)")

    def test_a_precision_filter_splits_experiments_and_its_absence_does_not(self):
        # Owner, 2026-10-26: what an open-weight seat was served at is a condition. A game from before the filter
        # and one that stamps it empty are the same unfiltered condition; a filtered game is another.
        old, empty = {"smart_a": "m"}, {"smart_a": "m", "openrouter_quantizations": ""}
        filtered = {"smart_a": "m", "openrouter_quantizations": '{"m":["fp8"]}'}
        self.assertEqual(aggregate.game_condition(old), aggregate.game_condition(empty))
        self.assertNotEqual(aggregate.game_condition(old), aggregate.game_condition(filtered))
        # A price ceiling too (standards review of #327): at list an open-weight model may find only a 4-bit host.
        capped = {"smart_a": "m", "openrouter_max_price": '{"m":{"prompt":1,"completion":2}}'}
        self.assertEqual(aggregate.game_condition(old), aggregate.game_condition(dict(old, openrouter_max_price="")))
        self.assertNotEqual(aggregate.game_condition(old), aggregate.game_condition(capped))

    def test_provenance_fields_include_effort(self):
        names = [n for n, _ in aggregate.PROVENANCE_FIELDS]
        for f in ("context_profile", "context_sha", "memory_tools", "memory_tools_version",
                  "claude_effort_smart", "claude_effort_fast"):
            self.assertIn(f, names)
            self.assertIn(f, aggregate.CONDITION_FIELDS)


class GameLineStampCoverageTest(unittest.TestCase):
    """Every environment variable LlmSpikeGui reads is either stamped on the game line or is on a
    named allowlist of settings that do not change what the seat decides (issue #117's detector
    gap). The game-line-key test can only see keys already written; a condition switch that is read
    but never stamped -- exactly PLAN_EXECUTE and TIER_FAST_TYPES before this fix -- slips past it
    and pools its on- and off-games. Read the writer's own reads and demand each one is accounted
    for."""

    # Env name -> game-line key, where the key is not simply the lowercased env name.
    STAMPED_ALIASES = {
        "GAME_SEED": "seed", "SWAP_SEATS": "swap",
        "RUN_DISCIPLINE": "discipline",
        "SMART_MODEL": "smart_a", "SMART_MODEL_A": "smart_a", "SMART_MODEL_B": "smart_b",
        "FAST_MODEL": "fast_a", "FAST_MODEL_A": "fast_a", "FAST_MODEL_B": "fast_b",
        "DECKLIST": "deck_a", "DECK_A_NAME": "deck_a", "DECK_B_NAME": "deck_b",
        "CLAUDE_BRIDGE_EFFORT": "claude_effort_smart",
        "CLAUDE_BRIDGE_EFFORT_FAST": "claude_effort_fast",
        "CLAUDE_BRIDGE_EFFORT_SMART": "claude_effort_smart",
        "OPENROUTER_BASE_URL": "openrouter_base_url", "OPENROUTER_MODEL_CFG": "model_cfg_sha",
        # A claude-cli: seat's thinking budget, per identity; the opt-in that lets one play, the dev_lane label.
        "CLAUDE_CLI_THINKING_TOKENS": "claude_cli_thinking_tokens_a", "CLAUDE_CLI_SEAT": "dev_lane",
        # AnswerTool.protocolVersion() folds these into the answer tool's stamp ("+strict", "+per-decision", "+choice=").
        "ANSWER_TOOL_STRICT": "answer_tool_version", "ANSWER_SCHEMA": "answer_tool_version",
        "ANSWER_TOOL_CHOICE": "answer_tool_version",
        # ChatSessionBridge.stamp() adds "+turns=N" to the chat's stamp when the carried pairs are capped.
        "ONE_CHAT_TURNS": "one_chat_version",
    }
    # Read by the harness, but not a condition: each entry says why it changes nothing the seat reads or decides
    # (STANDARDS section 4; test_every_allowlist_entry_says_why). One that ever did would need a stamp.
    NON_CONDITION_ENV = {
        "AUTO_EXIT": "process control: the JVM exits when the game ends",
        "CLAUDE_BIN": "transport: which claude executable a claude-cli: seat runs",
        "EVAL_OUT": "recording: the directory the rows are written to",
        "MATCH_GAMES": "process control: games per match; each game is its own game line",
        "OPENROUTER_ALLOW_EGRESS": "consent: the egress opt-in, not what the seat sees",
        "OPENROUTER_API_KEY": "credentials",
        "OPENROUTER_HTTP_REFERER": "transport: a request header for the provider",
        "OPENROUTER_HTTP_TITLE": "transport: a request header for the provider",
        "RECORD": "recording: a screen capture of the GUI",
        "RECORD_FPS": "recording: the capture's frame rate",
        "RECORD_OUT": "recording: the capture's file",
        "CLAUDE_CLI_ALLOW_API_KEY": "credentials: how a claude-cli: seat's CLI logs in, not what it decides",
        "DEMO_FEED": "recording: a spectator's event feed, display only (DemoFeed)",
        "TRANSCRIPTS": "recording: every HTTP round-trip written to disk (Transcript)",
    }
    # Every spike main file that reads the environment is scanned below or named here with why its reads are not the
    # game line's (standards of #353: the scan read three hand-picked files of twelve). A new reader must be placed.
    SCANNED = ("LlmSpikeGui.java", "ClaudeCliBridge.java", "AnswerTool.java", "ChatSessionBridge.java",
               "NativeTools.java", "HttpDeadline.java", "DemoFeed.java", "Transcript.java")
    NOT_GAME_LINE = {
        "LlmSpikeFork.java": "the fork tool: its rollouts write their own rows, not a game line",
        "LlmSpikeHeadless.java": "the launcher: picks a game, a puzzle or a fork; GAME_CAP_MS is stamped by the game",
        "LlmSpikePuzzle.java": "the puzzle runner: its row stamps its own condition (puzzle_score.BRIDGE_CONDITIONS)",
        "PuzzleArms.java": "the puzzle search arms: a search budget, not the seat",
    }
    # Conditions recorded somewhere other than the game line. OPENROUTER_TIMEOUT_MS changes decisions (a call past
    # it is lost to the fixed legal option): puzzle_solve.py --call-timeout-minutes records it per puzzle (STANDARDS
    # section 4, PR #329), and Build.run strips a shell value. A run_batch.py game under an operator's value is not
    # stamped yet.
    # RULES_FILE picks the Comprehensive Rules text the seat's rules tool returns: that text's date is stamped
    # `rules_effective` (standards of #344: it touches what the seat reads, so it is no allowlist entry).
    # OPENROUTER_IDLE_MS (HttpDeadline's stall cut) changes decisions too: 0 waits out a dead connection to the
    # deadline, and the call is lost to the fixed option. Every launcher pins it to its default
    # (run_batch.CONDITION_ENV_KEYS); a direct JVM launch with a value is not stamped.
    RECORDED_ELSEWHERE = {"OPENROUTER_TIMEOUT_MS", "RULES_FILE", "OPENROUTER_IDLE_MS"}
    # The CTX_* knobs change what the model sees, but are recorded through context_sha and the
    # "+ovr" profile name rather than a field of their own (ContextProfile.fromEnv), so they are not
    # required to have a stamp -- the sha is what partitions those experiments.
    CONTEXT_PREFIX = "CTX_"

    def test_every_allowlist_entry_says_why(self):
        for name, why in self.NON_CONDITION_ENV.items():
            self.assertTrue(why and why.strip(), name)

    def test_rules_file_is_recorded_by_the_rules_date(self):
        # RULES_FILE changes what the rules tool returns: never allowlisted, and partitioned by the text's date
        # (two texts with one effective date are not told apart; the rules file is pinned per engine release).
        self.assertNotIn("RULES_FILE", self.NON_CONDITION_ENV)
        self.assertIn("RULES_FILE", self.RECORDED_ELSEWHERE)
        self.assertIn("rules_effective", set(game_line_keys()))

    def test_the_shadow_label_is_a_stamped_condition(self):
        # A shadow eval that hits the AI timeout weakens the game's isolation (LlmSpikeGui, "[SHADOW] ... WEAKENED").
        self.assertNotIn("SHADOW_AI", self.NON_CONDITION_ENV)
        self.assertIn("shadow_ai", set(game_line_keys()))

    def test_the_terse_refusal_is_a_stamped_condition(self):
        # PROPOSE_TERSE changes what an illegal proposal is told: stamped, keyed, never pooled with the detailed arm.
        self.assertIn("propose_terse", set(game_line_keys()))
        self.assertIn(("propose_terse", False), aggregate.PROVENANCE_FIELDS)

    def test_the_chat_stamp_carries_its_cap(self):
        # ONE_CHAT_TURNS is stamped through one_chat_version only if the game line writes stamp(), not VERSION.
        self.assertIn('"one_chat_version\\":\\"" + ChatSessionBridge.stamp()', GUI.read_text(encoding="utf-8"))

    def test_every_spike_file_that_reads_the_environment_is_scanned_or_placed(self):
        readers = sorted(p.name for p in GUI.parent.glob("*.java") if "System.getenv" in p.read_text(encoding="utf-8"))
        self.assertEqual([n for n in readers if n not in self.SCANNED and n not in self.NOT_GAME_LINE], [])
        self.assertEqual(sorted(set(self.SCANNED) & set(self.NOT_GAME_LINE)), [])
        for name, why in self.NOT_GAME_LINE.items():
            self.assertTrue(why.strip(), name)

    def test_every_env_read_is_stamped_or_allowlisted(self):
        java = "".join((GUI.parent / n).read_text(encoding="utf-8") for n in self.SCANNED)
        keys = set(game_line_keys())
        self.assertIn("native_tools", keys)          # the extraction found the game-line statement
        # Every env-reading form in the SCANNED files: System.getenv("X"), the getOrDefault fallbacks, the
        # envInt / intEnv helpers (which read a name their caller passes), and the effort ternary. A read
        # through a NEW helper must be added here with the helper.
        names = set(re.findall(r'(?:System\.getenv|envInt|intEnv)\((?:fastTier \? )?"([A-Z_0-9]+)"', java))
        names |= set(re.findall(r'System\.getenv\(fastTier \? "[A-Z_0-9]+" : "([A-Z_0-9]+)"\)', java))
        names |= set(re.findall(r'System\.getenv\(\)\.getOrDefault\("([A-Z_0-9]+)"', java))
        # buildPlayers(env) reads its map (env.get / env.getOrDefault); the env("X") helper reads a test's override.
        names |= set(re.findall(r'\benv(?:\.get|\.getOrDefault)?\("([A-Z_0-9]+)"', java))
        # A name read through its constant (System.getenv(THINKING_ENV), env(ClaudeCliBridge.OPT_IN)).
        for k, v in re.findall(r'static final String ([A-Z_]+) = "([A-Z_0-9]+)";', java):
            if re.search(r'(?:System\.getenv|env)\((?:ClaudeCliBridge\.)?%s\)' % k, java):
                names.add(v)
        for probe in ("TIER_FAST_TYPES", "CTX_RECENT_EVENTS", "CLAUDE_BRIDGE_EFFORT_SMART", "DECK_A", "CLAUDE_CLI_SEAT",
                      "CLAUDE_CLI_THINKING_TOKENS", "ONE_CHAT_TURNS", "OPENROUTER_IDLE_MS"):
            self.assertIn(probe, names)              # each form was actually read
        stray = sorted(n for n in names
                       if not n.startswith(self.CONTEXT_PREFIX)
                       and n not in self.NON_CONDITION_ENV and n not in self.RECORDED_ELSEWHERE
                       and self.STAMPED_ALIASES.get(n, n.lower()) not in keys)
        self.assertEqual(stray, [],
                         "stamp these on the game line (aggregate.PROVENANCE_FIELDS) so their "
                         "on/off games never pool, or add them to NON_CONDITION_ENV with the reason "
                         "they do not change play")


def unresolved(seed, swap, winner, **kw):
    """A cited line whose endpoint named nothing for identity A (both tiers)."""
    return modern_line(seed, swap, winner, served_smart_a="unknown",
                       served_fast_a="unknown", **kw)


def experiments_of(s):
    return [s["experiments"]["by_id"][i] for i in s["experiments"]["order"]]


def served_pairs(s):
    """Sorted (served_smart_a, served_fast_a) per experiment, as recorded."""
    return sorted((x["condition"]["served_smart_a"], x["condition"]["served_fast_a"])
                  for x in experiments_of(s))


class ServedIdentityTest(unittest.TestCase):
    """Unresolved served identity is evidence about ONE game and stays with it."""

    def assert_ids_are_own_conditions(self, d):
        """Every grouped game's _experiment is the id of its OWN recorded
        condition: nothing was borrowed from a neighbour."""
        parsed = aggregate.parse_dir_full(Path(d))
        for e in parsed["experiments"]:
            for g in e["all_games"]:
                self.assertEqual(g["_experiment"],
                                 aggregate.experiment_id(aggregate.game_condition(g)))
                self.assertEqual(e["condition"], aggregate.game_condition(g))

    def test_unresolved_game_never_joins_a_resolved_experiment(self):
        """The former wildcard rule admitted the third game into the student-v3
        experiment. It is a game with no model name; it stays apart."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A")])
            write(d, "game-3-1000.jsonl", [unresolved(3, False, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertEqual(served_pairs(s), [("student-v3", "student-v3"),
                                               ("unknown", "unknown")])
            by_status = {x["identity_status"]: x for x in experiments_of(s)}
            self.assertEqual(set(by_status), {"resolved", "unresolved"})
            self.assertEqual(by_status["resolved"]["n_games"], 2)
            self.assertEqual(by_status["unresolved"]["n_games"], 1)
            # The unresolved experiment is labelled with what it recorded.
            self.assertIn("served_smart_a=unknown", by_status["unresolved"]["label"])
            self.assertIn("served_smart_a=student-v3", by_status["resolved"]["label"])
            # Per-field status: A's tiers unresolved; B (random) is not-applicable.
            si = by_status["unresolved"]["served_identity"]
            self.assertEqual(si["served_smart_a"], {"value": "unknown", "status": "unresolved"})
            self.assertEqual(si["served_fast_a"], {"value": "unknown", "status": "unresolved"})
            self.assertEqual(si["served_smart_b"]["status"], "not-applicable")
            # Only the unresolved experiment fails, and the directory inherits it.
            self.assertFalse(by_status["resolved"]["model_identity"]["failed"])
            self.assertTrue(by_status["resolved"]["model_identity"]["attributable"])
            self.assertTrue(by_status["unresolved"]["model_identity"]["failed"])
            self.assertTrue(s["model_identity"]["failed"])
            self.assertEqual(s["model_identity"]["failed_experiments"],
                             [by_status["unresolved"]["id"]])
            self.assertEqual(s["model_identity"]["unresolved_cited_games"], 1)
            self.assertTrue(s["paired"]["pooled_refused"])
            self.assert_ids_are_own_conditions(d)

    def test_complementary_unknowns_do_not_form_a_joint_condition(self):
        """smart=X/fast=unknown and smart=unknown/fast=Y: neither game observed
        X/Y, so no experiment may be labelled X/Y."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A", served_smart_a="model-X",
                                                       served_fast_a="unknown")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A", served_smart_a="unknown",
                                                       served_fast_a="model-Y")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertEqual(served_pairs(s), [("model-X", "unknown"), ("unknown", "model-Y")])
            for x in experiments_of(s):
                self.assertEqual(x["identity_status"], "unresolved")
                self.assertTrue(x["model_identity"]["failed"])
                self.assertFalse(x["model_identity"]["attributable"])
                self.assertEqual(x["n_games"], 1)
                # The resolved half is reported as resolved, the other as unresolved.
                statuses = {f: v["status"] for f, v in x["served_identity"].items()}
                self.assertEqual(sorted(statuses[f] for f in ("served_smart_a", "served_fast_a")),
                                 ["resolved", "unresolved"])
            self.assertTrue(s["model_identity"]["failed"])
            self.assertEqual(len(s["model_identity"]["failed_experiments"]), 2)
            self.assertEqual(s["arms"], {})
            self.assert_ids_are_own_conditions(d)
        # The same two records fed straight to group_experiments (the call the
        # independent review used) give two experiments, neither X/Y.
        exps, crashed = aggregate.group_experiments([
            dict(modern_line(1, False, "A", served_smart_a="model-X", served_fast_a="unknown"),
                 _file="game-1-1000.jsonl"),
            dict(modern_line(2, False, "A", served_smart_a="unknown", served_fast_a="model-Y"),
                 _file="game-2-1000.jsonl")])
        self.assertEqual(crashed, [])
        self.assertEqual(len(exps), 2)
        self.assertNotIn(("model-X", "model-Y"),
                         {(e["condition"]["served_smart_a"], e["condition"]["served_fast_a"])
                          for e in exps})
        self.assertEqual({e["identity_status"] for e in exps}, {"unresolved"})

    def test_adversarial_multi_condition_dir(self):
        """Two resolved checkpoints, an unresolved game, a legacy game and a
        half-resolved game in one dir: five experiments, every value as
        recorded, nothing merged, only the unresolved ones ineligible."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A", served_smart_a="student-v4",
                                                       served_fast_a="student-v4")])
            write(d, "game-3-1000.jsonl", [unresolved(3, False, "A")])
            write(d, "game-4-1000.jsonl", [legacy_line(4, False, "A")])
            write(d, "game-5-1000.jsonl", [modern_line(5, False, "A", served_smart_a="student-v4",
                                                       served_fast_a="unknown")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 5)
            self.assertEqual(served_pairs(s), [
                (aggregate.MISSING, aggregate.MISSING),
                ("student-v3", "student-v3"),
                ("student-v4", "student-v4"),
                ("student-v4", "unknown"),
                ("unknown", "unknown")])
            statuses = sorted(x["identity_status"] for x in experiments_of(s))
            self.assertEqual(statuses, ["legacy", "resolved", "resolved", "unresolved", "unresolved"])
            failed = {x["id"] for x in experiments_of(s) if x["model_identity"]["failed"]}
            self.assertEqual(failed, {x["id"] for x in experiments_of(s)
                                      if x["identity_status"] == "unresolved"})
            self.assertEqual(set(s["model_identity"]["failed_experiments"]), failed)
            self.assertTrue(s["model_identity"]["failed"])
            self.assertEqual(s["games"]["total"], 5)
            self.assertFalse(any("duplicate game" in w for w in s["warnings"]))
            self.assert_ids_are_own_conditions(d)
            # Selecting a resolved experiment yields a clean, attributable summary.
            v3 = next(x for x in experiments_of(s)
                      if x["condition"]["served_smart_a"] == "student-v3")
            sel = summary(d, experiment=v3["id"])
            self.assertFalse(sel["model_identity"]["failed"])
            self.assertTrue(sel["model_identity"]["attributable"])
            self.assertEqual(sel["model_identity"]["served"]["served_smart_a"], ["student-v3"])
            self.assertEqual(sel["games"]["total"], 1)

    def test_same_seed_unresolved_rerun_is_not_a_duplicate(self):
        """A rerun of seed 1 whose endpoint named nothing must neither replace
        nor be replaced by the resolved original."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [unresolved(1, False, "B")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertEqual(s["games"]["total"], 2)
            for x in experiments_of(s):
                self.assertEqual(x["duplicates"], [])
                self.assertEqual(x["n_games"], 1)
            self.assertFalse(any("duplicate" in w for w in s["warnings"]))
            resolved = next(x for x in experiments_of(s) if x["identity_status"] == "resolved")
            # The resolved original still reports its own outcome (A won).
            self.assertEqual(resolved["arms"]["llm[openrouter:student/openrouter:student]"]["wins"], 1)
            self.assertTrue(s["model_identity"]["failed"])
        # Reverse file order: the resolved game is the LATER file; the
        # unresolved one is still not dropped as an older duplicate.
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [unresolved(1, False, "B")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertEqual(s["games"]["total"], 2)
            self.assertEqual(sorted(x["n_games"] for x in experiments_of(s)), [1, 1])

    def test_swapped_pair_with_unresolved_half_is_not_a_pair(self):
        """swap=false resolved + swap=true unresolved for one seed: no pair in
        either experiment and none at the top level."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [unresolved(1, True, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            for x in experiments_of(s):
                self.assertEqual(x["paired"]["n_pairs_complete"], 0)
                self.assertEqual(x["paired"]["n_pairs_used"], 0)
            self.assertEqual(s["paired"]["n_pairs_complete"], 0)
            self.assertEqual(s["paired"]["n_pairs_used"], 0)
            self.assertTrue(s["paired"]["pooled_refused"])
        # Both halves unresolved with the SAME recorded values: they are one
        # experiment and do pair, but that experiment is ineligible.
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [unresolved(1, False, "A")])
            write(d, "game-1-1001.jsonl", [unresolved(1, True, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            only = experiments_of(s)[0]
            self.assertEqual(only["paired"]["n_pairs_used"], 1)
            self.assertEqual(only["identity_status"], "unresolved")
            self.assertTrue(s["model_identity"]["failed"])
            self.assertFalse(s["model_identity"]["attributable"])

    def test_legacy_missing_is_distinct_from_unknown(self):
        """A pre-provenance line (fields absent) and a modern line whose fields
        are present but empty are different evidence: the first is excused,
        the second is not, and they never share an experiment."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [dict(legacy_line(1, False, "A"), discipline="cited")])
            write(d, "game-2-1000.jsonl", [unresolved(2, False, "A")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            by_status = {x["identity_status"]: x for x in experiments_of(s)}
            self.assertEqual(set(by_status), {"legacy", "unresolved"})
            leg, unr = by_status["legacy"], by_status["unresolved"]
            self.assertTrue(leg["legacy"])
            self.assertEqual(leg["served_identity"]["served_smart_a"],
                             {"value": aggregate.MISSING, "status": "missing"})
            self.assertFalse(leg["model_identity"]["failed"])
            self.assertTrue(leg["model_identity"]["legacy"])
            self.assertFalse(unr["legacy"])
            self.assertTrue(unr["model_identity"]["failed"])
            # A legacy neighbour does not excuse a modern cited game that named nothing.
            self.assertTrue(s["model_identity"]["failed"])
            self.assertEqual(s["model_identity"]["failed_experiments"], [unr["id"]])

    def test_deterministic_baseline_seats_are_not_applicable(self):
        """floor vs random: the harness stamps 'unknown' on every seat it never
        routed. That is no model to name, not an unresolved model."""
        with tempfile.TemporaryDirectory() as d:
            for swap, i in ((False, 0), (True, 1)):
                write(d, "game-1-100%d.jsonl" % i, [modern_line(
                    1, swap, "A", controller_a="floor", controller_b="random",
                    smart_a=None, fast_a=None, smart_b=None, fast_b=None,
                    served_smart_a="unknown", served_fast_a="unknown")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            only = experiments_of(s)[0]
            self.assertEqual(only["identity_status"], "not-applicable")
            self.assertEqual({v["status"] for v in only["served_identity"].values()},
                             {"not-applicable"})
            self.assertEqual({v["value"] for v in only["served_identity"].values()}, {"unknown"})
            self.assertTrue(s["model_identity"]["vacuous"])
            self.assertFalse(s["model_identity"]["failed"])
            self.assertEqual(s["model_identity"]["unresolved_fields"], [])
            self.assertEqual(s["paired"]["n_pairs_used"], 1)
            self.assertEqual(s["arms"]["floor"]["wins"], 2)
        # An llm seat A that resolved, opposite a random seat B stamped
        # "unknown": attributable, and B's value is preserved, not judged.
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [modern_line(1, True, "A")])
            s = summary(d)
            only = experiments_of(s)[0]
            self.assertEqual(only["identity_status"], "resolved")
            self.assertEqual(only["served_identity"]["served_smart_b"],
                             {"value": "unknown", "status": "not-applicable"})
            self.assertTrue(s["model_identity"]["attributable"])
            self.assertFalse(s["model_identity"]["failed"])
            self.assertEqual(s["model_identity"]["unresolved_fields"], [])
        # A baseline seat next to an llm seat in ANOTHER experiment does not
        # make the directory's served_smart_a read unresolved.
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-2-1000.jsonl", [modern_line(
                2, False, "A", controller_a="floor", smart_a=None, fast_a=None,
                served_smart_a="unknown", served_fast_a="unknown")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertEqual(s["model_identity"]["unresolved_fields"], [])
            self.assertFalse(s["model_identity"]["failed"])

    def test_cited_gate_is_per_game_not_per_directory(self):
        """A cited unresolved game beside an exploratory one under the same
        condition (discipline is not a partition key) still fails; an
        exploratory-only unresolved experiment is reported, not failed."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [unresolved(1, False, "A")])
            write(d, "game-2-1000.jsonl", [unresolved(2, False, "A", discipline="exploratory")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 1)
            self.assertIsNone(s["model_identity"]["discipline"])
            self.assertEqual(sorted(s["model_identity"]["disciplines"]), ["cited", "exploratory"])
            self.assertEqual(s["model_identity"]["unresolved_cited_games"], 1)
            self.assertTrue(s["model_identity"]["failed"])
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [unresolved(1, False, "A", discipline="exploratory")])
            s = summary(d)
            only = experiments_of(s)[0]
            self.assertEqual(only["identity_status"], "unresolved")
            self.assertFalse(s["model_identity"]["failed"])
            self.assertFalse(s["model_identity"]["attributable"])
            self.assertEqual(s["model_identity"]["unresolved_fields"],
                             ["served_fast_a", "served_smart_a"])

    def test_empty_and_unregistered_are_unresolved_but_kept_as_recorded(self):
        """Every UNATTRIBUTABLE value is unresolved; none is rewritten to
        another, so distinct recorded values stay distinct experiments."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [unresolved(1, False, "A")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A", served_smart_a="",
                                                       served_fast_a="")])
            write(d, "game-3-1000.jsonl", [modern_line(3, False, "A", served_smart_a="unregistered",
                                                       served_fast_a="unregistered")])
            s = summary(d)
            self.assertEqual(s["experiments"]["n"], 3)
            self.assertEqual(served_pairs(s), [("", ""), ("unknown", "unknown"),
                                               ("unregistered", "unregistered")])
            self.assertEqual({x["identity_status"] for x in experiments_of(s)}, {"unresolved"})
            self.assertEqual(len(s["model_identity"]["failed_experiments"]), 3)

    def test_public_constants_do_not_expose_a_substitute_label(self):
        """There is no '(unresolved)' placeholder any more: conditions carry
        recorded values only."""
        self.assertFalse(hasattr(aggregate, "UNRESOLVED"))
        cond = aggregate.game_condition(unresolved(1, False, "A"))
        self.assertEqual(cond["served_smart_a"], "unknown")
        self.assertEqual(aggregate.identity_status(cond), "unresolved")
        self.assertEqual(aggregate.identity_status(aggregate.game_condition(modern_line(1, False, "A"))),
                         "resolved")
        self.assertEqual(aggregate.identity_status(aggregate.game_condition(legacy_line(1, False, "A"))),
                         "legacy")


class CliTest(unittest.TestCase):

    def test_unresolved_rerun_fails_the_cited_gate_on_the_cli(self):
        """Public CLI contract: a cited dir holding resolved games plus one
        same-seed unresolved rerun exits 3, reports two experiments, and
        labels the unresolved one with its recorded value. Selecting the
        resolved experiment exits 0 and is attributable."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-1001.jsonl", [modern_line(1, True, "A")])
            write(d, "game-1-2000.jsonl", [unresolved(1, False, "B")])
            p = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 3, p.stderr)
            self.assertIn("FAILED MODEL-IDENTITY GATE", p.stderr)
            s = json.loads(p.stdout)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertTrue(s["model_identity"]["failed"])
            self.assertEqual(len(s["model_identity"]["failed_experiments"]), 1)
            by_status = {x["identity_status"]: x for x in s["experiments"]["by_id"].values()}
            self.assertEqual(by_status["resolved"]["paired"]["n_pairs_used"], 1)
            self.assertEqual(by_status["unresolved"]["n_games"], 1)
            self.assertEqual(by_status["unresolved"]["condition"]["served_smart_a"], "unknown")
            self.assertIn("served_smart_a=unknown", by_status["unresolved"]["label"])
            self.assertFalse(any("duplicate" in w for w in s["warnings"]))
            human = subprocess.run([sys.executable, str(AGG), d, "--resamples", "50"],
                                   capture_output=True, text=True)
            self.assertEqual(human.returncode, 3, human.stderr)
            self.assertIn("served identity UNRESOLVED", human.stdout)
            self.assertIn("served_smart_a='unknown'", human.stdout)
            self.assertIn("ineligible experiment(s)", human.stdout)
            self.assertNotIn("admitted with unresolved", human.stdout)
            good = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50",
                                   "--experiment", by_status["resolved"]["id"]],
                                  capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            g = json.loads(good.stdout)
            self.assertTrue(g["model_identity"]["attributable"])
            self.assertEqual(g["model_identity"]["failed_experiments"], [])
            self.assertEqual(g["paired"]["n_pairs_used"], 1)

    def test_complementary_unknowns_fail_on_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A", served_smart_a="model-X",
                                                       served_fast_a="unknown")])
            write(d, "game-2-1000.jsonl", [modern_line(2, False, "A", served_smart_a="unknown",
                                                       served_fast_a="model-Y")])
            p = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 3, p.stderr)
            s = json.loads(p.stdout)
            self.assertEqual(s["experiments"]["n"], 2)
            conds = {(x["condition"]["served_smart_a"], x["condition"]["served_fast_a"])
                     for x in s["experiments"]["by_id"].values()}
            self.assertEqual(conds, {("model-X", "unknown"), ("unknown", "model-Y")})
            self.assertNotIn("model-X model-Y", json.dumps(s))
            self.assertEqual(s["arms"], {})

    def test_baseline_dir_passes_on_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            for swap, i in ((False, 0), (True, 1)):
                write(d, "game-1-100%d.jsonl" % i, [modern_line(
                    1, swap, "A", controller_a="floor", controller_b="random",
                    smart_a=None, fast_a=None, smart_b=None, fast_b=None,
                    served_smart_a="unknown", served_fast_a="unknown")])
            p = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            s = json.loads(p.stdout)
            self.assertEqual(s["experiments"]["n"], 1)
            self.assertTrue(s["model_identity"]["vacuous"])
            only = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(only["identity_status"], "not-applicable")

    def test_json_output_and_experiment_selection(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [modern_line(1, False, "A")])
            write(d, "game-1-2000.jsonl", [modern_line(1, False, "B", context_profile="v2-memory")])
            p = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            s = json.loads(p.stdout)
            self.assertEqual(s["experiments"]["n"], 2)
            self.assertTrue(s["paired"]["pooled_refused"])
            human = subprocess.run([sys.executable, str(AGG), d, "--resamples", "50"],
                                   capture_output=True, text=True)
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("2 DISTINCT CONDITIONS", human.stdout)
            self.assertIn("pooled statistics refused", human.stdout)
            self.assertIn("tool accounting", human.stdout)
            one = subprocess.run([sys.executable, str(AGG), d, "--json", "--resamples", "50",
                                  "--experiment", s["experiments"]["order"][0]],
                                 capture_output=True, text=True)
            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertEqual(json.loads(one.stdout)["experiments"]["n"], 1)
            bad = subprocess.run([sys.executable, str(AGG), d, "--experiment", "zzz"],
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("no experiment matches", bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
