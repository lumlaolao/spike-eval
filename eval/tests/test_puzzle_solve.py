"""puzzle_solve.py refuses a condition the seat would not honour."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_solve  # noqa: E402



def classes_with_transcript():
    """A classes directory holding the compiled Transcript, as a build of this overlay has."""
    d = Path(tempfile.mkdtemp())
    (d / "forge/spike").mkdir(parents=True)
    (d / "forge/spike/Transcript.class").write_bytes(b"\xca\xfe\xba\xbe")
    return d


# A stand-in for puzzle_baselines.Build whose provenance() main() can write into puzzle-run.json.
STUB_BUILD = {"return_value.provenance.return_value": {}, "return_value.classes": classes_with_transcript()}


class RulesToolNeedsMemoryToolsTest(unittest.TestCase):
    """The seat wires the rules lookup only with MEMORY_TOOLS=1, so --rules-tool alone ran puzzles that
    recorded RULES_TOOL=1 and never offered the tool (runs/puzzles/bench-pilot/on, 2026-10-20)."""

    def test_rules_tool_without_memory_tools_is_refused_before_anything_runs(self):
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "/nonexistent/suite.json", "--split", "open", "--model", "greedy:",
                               "--budget-usd", "0", "--out", "/nonexistent/out", "--rules-tool"])
        self.assertIn("--memory-tools", str(cm.exception.code))


class UnpricedModelTest(unittest.TestCase):
    """The budget is the only thing that stops a paid run, and cost_report prices a model it has no price for at
    $0, so a budget over an unpriced model never stopped anything (provider audit, 2026-10-24: 18 of the 22
    frontier models were unpriced). run_batch.py already refuses; puzzle_solve.py must too."""

    def test_an_unpriced_model_is_refused_before_anything_runs(self):
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "/nonexistent/suite.json", "--split", "open",
                               "--model", "openrouter:nobody/unpriced-model", "--budget-usd", "1",
                               "--out", "/nonexistent/out"])
        self.assertIn("prices.json", str(cm.exception.code))
        self.assertIn("nobody/unpriced-model", str(cm.exception.code))


class BudgetAtTheBoundTest(unittest.TestCase):
    """The budget is checked at each model's dearest endpoint (cost_report.budget_prices): latency routing may land on
    a dearer endpoint than the list price, so a run checked at the list price could overrun its budget."""

    def test_a_run_under_budget_at_list_price_but_over_at_the_bound_is_stopped(self):
        spec = "openrouter:deepseek/deepseek-v4.1-flash"
        root = Path(tempfile.mkdtemp())
        (root / "p1").mkdir()
        (root / "p1" / "game-1-0.jsonl").write_text(
            json.dumps({"t": "context_call", "tier": "smart", "prompt_tokens": 100000, "completion_tokens": 1000}) + "\n"
            + json.dumps({"t": "game", "smart_a": spec, "fast_a": spec}) + "\n")
        listed, bound = puzzle_solve.spent(root), puzzle_solve.spent(root, bound=True)
        self.assertLess(listed, bound)
        budget = (listed + bound) / 2
        self.assertTrue(puzzle_solve.over_budget(root, budget), "checked at the bound")
        self.assertFalse(puzzle_solve.over_budget(root, bound * 2))

    def test_main_does_not_start_a_puzzle_once_the_bound_reaches_the_budget(self):
        # The helper alone passes even if main() goes back to checking the list price; drive main() itself.
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        spec = "openrouter:deepseek/deepseek-v4.1-flash"
        root = Path(tempfile.mkdtemp())
        (root / "earlier").mkdir()
        (root / "earlier" / "game-1-0.jsonl").write_text(
            json.dumps({"t": "context_call", "tier": "smart", "prompt_tokens": 100000, "completion_tokens": 1000}) + "\n"
            + json.dumps({"t": "game", "smart_a": spec, "fast_a": spec}) + "\n")
        listed, bound = puzzle_solve.spent(root), puzzle_solve.spent(root, bound=True)
        self.assertLess(listed, bound)
        out = io.StringIO()
        with mock.patch.object(puzzle_solve, "solve_puzzle", return_value=(pid, 0, [])) as solve, \
                mock.patch.object(puzzle_solve.pb, "Build", **STUB_BUILD), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(out):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", str((listed + bound) / 2), "--ids", pid, "--out", str(root)])
        solve.assert_not_called()
        self.assertIn("[budget] not started: %s" % pid, out.getvalue())


class ResumeTest(unittest.TestCase):
    """A run stopped by a container restart is started again with --resume (owner, 2026-10-22): a puzzle whose
    k attempts on this suite are already recorded is not paid for twice; one cut off part way is played again."""

    def write(self, out, rows):
        out.mkdir(parents=True, exist_ok=True)
        (out / "puzzle-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_a_puzzle_with_its_k_attempts_on_this_suite_is_done(self):
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S"}, {"seed": 2, "suite_sha256": "S"}])
        self.assertTrue(puzzle_solve.already_done(out, 2, "S"))
        self.assertFalse(puzzle_solve.already_done(out, 3, "S"), "a third attempt is still owed")
        self.assertFalse(puzzle_solve.already_done(out, 2, "OTHER"), "rows of another suite version")
        self.assertFalse(puzzle_solve.already_done(Path(tempfile.mkdtemp()) / "none", 1, "S"))

    def test_a_puzzle_with_a_row_that_did_not_reach_the_disk_is_not_done(self):
        # #430: k-1 rows and a "[PUZZLE] write failed" line: a resume plays it again, puzzle_score refuses it.
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S"}])
        (out / "jvm.log").write_text("[PUZZLE] write failed: a row of puzzle-results.jsonl did not reach the disk and is missing from it\n")
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"), "even with every wanted seed present")
        self.assertEqual(puzzle_solve.prepare(out, 1, "S", resume=True), "run")
        self.assertTrue((out / "jvm.partial0.log").is_file(), "the log goes aside with the rows")
        self.write(out, [{"seed": 1, "suite_sha256": "S"}])
        self.assertTrue(puzzle_solve.already_done(out, 1, "S"))

    def test_an_attempt_refused_under_the_price_ceiling_is_played_again(self):
        # pr-review of #327: such a row is not a result (puzzle_score refuses it), so a resume replays the puzzle.
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S", "routing_refusals": 2}])
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"))
        self.write(out, [{"seed": 1, "suite_sha256": "S", "routing_refusals": 0}])
        self.assertTrue(puzzle_solve.already_done(out, 1, "S"))

    def test_an_attempt_a_dead_bridge_played_is_played_again(self):
        # Issue #378: puzzle_score refuses such a row, so a resume replays the puzzle (its rows set aside, still priced).
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S", "bridge_down_decisions": 11}])
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"))
        self.assertEqual(puzzle_solve.prepare(out, 1, "S", resume=True), "run")
        self.assertTrue((out / "puzzle-results.partial0.jsonl").is_file(), "the dead attempt's row is kept aside")
        self.write(out, [{"seed": 1, "suite_sha256": "S", "bridge_down_decisions": 0}])
        self.assertTrue(puzzle_solve.already_done(out, 1, "S"))

    def test_an_attempt_whose_log_failed_to_write_is_played_again(self):
        # Issue #427: puzzle_score refuses the row and says "replay it with --resume", so already_done must not keep it.
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S", "eval_log_write_failed": True}])
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"))
        self.write(out, [{"seed": 1, "suite_sha256": "S", "eval_log_write_failed": False}])
        self.assertTrue(puzzle_solve.already_done(out, 1, "S"))

    def test_a_row_from_before_the_field_whose_log_shows_a_dead_bridge_is_played_again(self):
        # pr-review of #389: the row has no count, so its puzzle's jvm.log is read (aggregate.log_bridge_down).
        from test_puzzle_score import LOG_402
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S", "model_calls": 11}])
        (out / "jvm.log").write_text(LOG_402)
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"))
        (out / "jvm.log").write_text("[Solver] played\n")
        self.assertTrue(puzzle_solve.already_done(out, 1, "S"))

    def test_a_puzzle_cut_off_part_way_keeps_its_paid_rows_aside_and_is_played_again(self):
        out = Path(tempfile.mkdtemp()) / "p"
        for _ in range(2):   # cut off twice: neither set of rows overwrites the other
            self.write(out, [{"seed": 1, "suite_sha256": "S"}])
            (out / "jvm.log").write_text("the cut-off attempt")
            self.assertEqual(puzzle_solve.prepare(out, 2, "S", resume=True), "run")
        self.assertFalse((out / "puzzle-results.jsonl").exists(), "a scorer would read the partial rows")
        self.assertEqual(len(list(out.glob("puzzle-results.partial*.jsonl"))), 2)
        self.assertEqual(len(list(out.glob("jvm.partial*.log"))), 2)

    def test_a_done_puzzle_is_skipped_only_on_resume(self):
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S"}])
        self.assertEqual(puzzle_solve.prepare(out, 1, "S", resume=True), "done")
        self.assertEqual(puzzle_solve.prepare(out, 1, "S", resume=False), "run")

    def test_main_refuses_a_resume_under_another_condition_before_playing_anything(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        root = Path(tempfile.mkdtemp())
        puzzle_solve.write_manifest(root / pid, "fake:another-model", suite["suite_sha256"], pid, {})
        with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve.pb, "Build", **STUB_BUILD):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", "0", "--ids", pid, "--out", str(root), "--resume"])
        self.assertIn("another condition", str(cm.exception.code))

    def test_two_cut_offs_before_any_row_keep_both_logs(self):
        out = Path(tempfile.mkdtemp()) / "p"
        out.mkdir()
        for _ in range(2):
            (out / "jvm.log").write_text("cut off before its first result row")
            puzzle_solve.set_aside_partial(out)
        self.assertEqual(len(list(out.glob("jvm.partial*.log"))), 2)

    def test_a_resume_under_another_condition_is_refused(self):
        # pr-review of #300: a run resumed with --memory-tools on pooled two seats in one score (STANDARDS section 4).
        root = Path(tempfile.mkdtemp())
        out = root / "p"
        cond = {"MEMORY_TOOLS": "0", "CONTEXT_PROFILE": "v4-faithful"}
        puzzle_solve.write_manifest(out, "m", "S", "p", cond, self.BUILD)
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p", "q"], "m", "S", cond, self.BUILD), [])
        for model, sha, c in (("other", "S", cond), ("m", "T", cond), ("m", "S", dict(cond, MEMORY_TOOLS="1"))):
            self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["p"], model, sha, c, self.BUILD)), 1,
                             (model, sha, c))

    def test_a_resume_into_the_other_play_interface_is_refused(self):
        # PROPOSE_ACTIONS is pinned only when asked for: a menu directory resumes unchanged, and a resume that adds or
        # drops the arm is refused, as run_batch.py refuses it (TestProposeActions).
        import argparse
        base = dict(context_profile="v4-faithful", memory_tools=False, rules_tool=False)
        menu = puzzle_solve.condition(argparse.Namespace(**base))
        propose = puzzle_solve.condition(argparse.Namespace(**base, propose_actions=True))
        self.assertNotIn("PROPOSE_ACTIONS", menu)
        self.assertEqual(propose["PROPOSE_ACTIONS"], "1")
        root = Path(tempfile.mkdtemp())
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", menu, self.BUILD)
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", menu, self.BUILD), [])
        self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", propose, self.BUILD)), 1)

    def test_the_terse_refusal_is_pinned_only_when_asked_and_never_resumed_across(self):
        # PROPOSE_TERSE: the refusal names no rule. A detailed directory resumes unchanged; adding the arm is refused.
        import argparse
        base = dict(context_profile="v4-faithful", memory_tools=False, rules_tool=False, propose_actions=True)
        detailed = puzzle_solve.condition(argparse.Namespace(**base))
        terse = puzzle_solve.condition(argparse.Namespace(**base, propose_terse=True))
        self.assertNotIn("PROPOSE_TERSE", detailed)
        self.assertEqual(terse["PROPOSE_TERSE"], "1")
        root = Path(tempfile.mkdtemp())
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", detailed, self.BUILD)
        self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", terse, self.BUILD)), 1)
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "s.json", "--split", "open", "--model", "m", "--budget-usd", "0",
                               "--out", str(root / "o"), "--propose-terse"])
        self.assertIn("--propose-actions", str(cm.exception.code))

    def test_the_refusal_notebook_is_pinned_only_when_asked_and_never_resumed_across(self):
        # PROPOSE_NOTEBOOK: REFUSED EARLIER in a proposal decision. A directory without it resumes unchanged; adding the
        # arm is refused, and it needs --propose-actions.
        import argparse
        base = dict(context_profile="v4-faithful", memory_tools=False, rules_tool=False, propose_actions=True)
        without = puzzle_solve.condition(argparse.Namespace(**base))
        notebook = puzzle_solve.condition(argparse.Namespace(**base, propose_notebook=True))
        self.assertNotIn("PROPOSE_NOTEBOOK", without)
        self.assertEqual(notebook["PROPOSE_NOTEBOOK"], "1")
        root = Path(tempfile.mkdtemp())
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", without, self.BUILD)
        self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", notebook, self.BUILD)), 1)
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "s.json", "--split", "open", "--model", "m", "--budget-usd", "0",
                               "--out", str(root / "o"), "--propose-notebook"])
        self.assertIn("--propose-actions", str(cm.exception.code))

    BUILD = {"build_dir": "/b", "build_overlay_sha": "aaaaaaaaaaaa", "checkout_overlay_sha": "aaaaaaaaaaaa",
             "stale_build_allowed": False}

    def test_a_resume_with_another_price_ceiling_is_refused(self):
        # standards review of #327: a ceiling can move a request to another provider, so capped and uncapped attempts
        # are not resumed into one directory. A directory from before the ceiling existed was uncapped.
        root = Path(tempfile.mkdtemp())
        cond = {"MEMORY_TOOLS": "0"}
        cap = '{"m": {"prompt": 2, "completion": 10}}'
        puzzle_solve.write_manifest(root / "capped", "m", "S", "capped", cond, self.BUILD,
                                    {"openrouter_max_price": json.loads(cap)})
        puzzle_solve.write_manifest(root / "old", "m", "S", "old", cond, self.BUILD)
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["capped"], "m", "S", cond, self.BUILD, cap), [])
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["old"], "m", "S", cond, self.BUILD), [])
        for pid, ceiling in (("capped", None), ("old", cap),
                             ("capped", '{"m": {"prompt": 3, "completion": 10}}')):
            bad = puzzle_solve.resume_conflicts(root, [pid], "m", "S", cond, self.BUILD, ceiling)
            self.assertEqual(len(bad), 1, (pid, ceiling))
            self.assertIn("openrouter_max_price", bad[0])

    def test_a_resume_on_another_build_is_refused(self):
        # pr-review of #319: the attempts of two harnesses pooled in one score when a resume ran on another build.
        root = Path(tempfile.mkdtemp())
        cond = {"MEMORY_TOOLS": "0"}
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", cond, self.BUILD)
        puzzle_solve.write_manifest(root / "old", "m", "S", "old", cond)   # a run from before builds were recorded
        # Another directory holding the same compiled overlay is the same harness.
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, dict(self.BUILD, build_dir="/c")),
                         [])
        for other in (dict(self.BUILD, build_overlay_sha="bbbbbbbbbbbb"), dict(self.BUILD, stale_build_allowed=True)):
            bad = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, other)
            self.assertEqual(len(bad), 1, other)
            self.assertIn("build", bad[0])
        self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["old"], "m", "S", cond, self.BUILD)), 1)

    def test_main_checks_the_build_before_the_resume(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        root = Path(tempfile.mkdtemp())
        argv = ["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1", "--budget-usd", "1",
                "--ids", pid, "--out", str(root), "--resume"]
        import argparse
        cond = puzzle_solve.condition(argparse.Namespace(context_profile="v4-faithful", memory_tools=False,
                                                          rules_tool=False))
        puzzle_solve.write_manifest(root / pid, "fake:m", suite["suite_sha256"], pid, cond,
                                    dict(self.BUILD, build_overlay_sha="bbbbbbbbbbbb"))
        stub = {"return_value.provenance.return_value": self.BUILD, "return_value.classes": classes_with_transcript()}
        with mock.patch.object(puzzle_solve.pb, "Build", **stub), \
                mock.patch.object(puzzle_solve, "solve_puzzle", side_effect=AssertionError("played")), \
                mock.patch.object(puzzle_solve, "load_secrets"):
            with self.assertRaises(SystemExit) as cm:
                puzzle_solve.main(argv)
        self.assertIn("build_overlay_sha", str(cm.exception.code))


class BuildProvenanceTest(unittest.TestCase):
    """The build a puzzle ran on is named in its directory's manifest, with --allow-stale-build when that let an old
    build run (puzzle_baselines.Build refuses one whose overlay is not this checkout's otherwise)."""

    def test_the_flag_reaches_the_build_and_the_manifest_records_it(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        prov = {"build_dir": "/b", "build_overlay_sha": "bbbbbbbbbbbb", "checkout_overlay_sha": "aaaaaaaaaaaa",
                "stale_build_allowed": True}
        seen = []

        class FakeBuild:
            classes = classes_with_transcript()

            def __init__(self, build_dir, forge, allow_stale=False):
                seen.append((build_dir, allow_stale))

            def provenance(self):
                return prov

            def run(self, env, out, **kw):
                return 0

        root = Path(tempfile.mkdtemp())
        with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(io.StringIO()):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", "1", "--ids", pid, "--out", str(root), "--build", "/b",
                               "--allow-stale-build"])
        self.assertEqual(seen, [("/b", True)])
        self.assertEqual(json.loads((root / pid / "batch-manifest.json").read_text())["build"], prov)
        self.assertEqual(json.loads((root / puzzle_solve.RUN_FILE).read_text())["build"], prov, "the run file too")


class WriteFailureTest(unittest.TestCase):
    """Issue #430: a result row the JVM could not write (a full disk) is named in the run's output."""

    def test_a_row_that_did_not_reach_the_disk_is_named(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")

        class FakeBuild:
            classes = classes_with_transcript()

            def __init__(self, build_dir, forge, allow_stale=False):
                pass

            def provenance(self):
                return {"build_dir": "/b", "build_overlay_sha": "a", "checkout_overlay_sha": "a", "stale_build_allowed": False}

            def run(self, env, out, **kw):
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / "jvm.log").write_text("[PUZZLE] write failed: a row of puzzle-results.jsonl did not reach the disk\n")
                return 0

        out = io.StringIO()
        with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(out):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", "1", "--ids", pid, "--out", str(Path(tempfile.mkdtemp())), "--build", "/b"])
        self.assertIn("[puzzles] %s: [PUZZLE] write failed" % pid, out.getvalue())


class RunFileTest(unittest.TestCase):
    """eval/puzzles/puzzle_watch.py reads puzzle-run.json to show the puzzles not started, when each is done (k rows) and
    when the run is over; without it a watcher cannot tell a finished run from a dead one."""

    def test_the_run_file_names_the_puzzles_k_the_budget_and_whether_the_run_is_over(self):
        root = Path(tempfile.mkdtemp())
        puzzle_solve.write_run(root, ["a", "b"], 4, 1.5, finished=False)
        self.assertEqual(json.loads((root / puzzle_solve.RUN_FILE).read_text()),
                         {"ids": ["a", "b"], "k": 4, "budget_usd": 1.5, "finished": False})
        puzzle_solve.write_run(root, ["a", "b"], 4, 1.5, finished=True)
        self.assertTrue(json.loads((root / puzzle_solve.RUN_FILE).read_text())["finished"])
        self.assertEqual(sorted(p.name for p in root.iterdir()), [puzzle_solve.RUN_FILE], "no .tmp left behind")

    def test_a_run_stopped_by_an_exception_says_so_in_the_run_file_and_still_raises(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        pid = next(r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        root = Path(tempfile.mkdtemp())
        with mock.patch.object(puzzle_solve.pb, "Build", **STUB_BUILD), \
                mock.patch.object(puzzle_solve, "solve_puzzle", side_effect=KeyboardInterrupt()), \
                mock.patch.object(puzzle_solve, "spent", return_value=0.0), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                                   "--budget-usd", "1", "--ids", pid, "--out", str(root)])
        run = json.loads((root / puzzle_solve.RUN_FILE).read_text())
        self.assertFalse(run["finished"])
        self.assertIn("KeyboardInterrupt", run["stopped"])


class FullRecordTest(unittest.TestCase):
    """Owner, 2026-10-25: a puzzle run keeps every model call and the engine log whole, and the position it started
    from. Each JVM is asked for the transcript (TRANSCRIPTS=1, forge/spike/Transcript.java), the manifest names its
    version, and the .pzl and the suite are copied into the run, so an archive of the directory is self-contained."""

    def test_a_puzzle_runs_with_the_transcript_on_and_keeps_its_position_and_suite(self):
        suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
        suite = json.loads(suite_path.read_text())
        rec = next(r for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active")
        envs = []

        class FakeBuild:
            classes = classes_with_transcript()

            def __init__(self, build_dir, forge, allow_stale=False):
                pass

            def provenance(self):
                return {}

            def run(self, env, out, **kw):
                envs.append(env)
                return 0

        root = Path(tempfile.mkdtemp())
        with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(io.StringIO()):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", "1", "--ids", rec["id"], "--out", str(root), "--build", "/b"])
        self.assertEqual(envs[0].get("TRANSCRIPTS"), "1", "the JVM must be asked for the full record")
        java = (evalpath.REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
                / "Transcript.java").read_text()
        version = java.split('static final String VERSION = "', 1)[1].split('"', 1)[0]
        manifest = json.loads((root / rec["id"] / "batch-manifest.json").read_text())
        self.assertEqual(manifest["record"]["transcript_version"], version)
        kept = root / rec["id"] / manifest["record"]["pzl_file"]
        self.assertEqual(puzzle_solve.pf.sha256_file(kept), rec["pzl_sha256"], "the exact position the JVM read")
        run = json.loads((root / puzzle_solve.RUN_FILE).read_text())
        self.assertEqual(puzzle_solve.pf.sha256_file(root / run["suite_file"]),
                         puzzle_solve.pf.sha256_file(suite_path), "the suite, byte for byte")


class TwinInstalledTest(unittest.TestCase):
    """A run on a twin record stops before its first game when the checkout's twin scripts are not the record's."""

    def test_a_twin_puzzle_is_not_played_on_a_checkout_without_its_scripts(self):
        from test_puzzle_suite import rec, suite as make_suite
        import puzzle_format as pf
        tmp = Path(tempfile.mkdtemp())
        base = rec("tw1", tmp)
        cards = {"Zorbin Kel": "a" * 64}
        twin = dict(base, id="tw1-twin", split=pf.split_for("tw1"), twin_of="tw1", original_pzl_sha256="1" * 64,
                    twin_cards=cards, twin_set_sha256=pf.twin_set_sha(cards), pzl=str(tmp / base["pzl"]))
        suite_path = tmp / "suite.json"
        suite_path.write_text(json.dumps(make_suite([twin])))
        forge, ran = tmp / "forge", []

        class FakeBuild:
            def __init__(self, build_dir, forge_dir, allow_stale=False):
                self.forge = forge

            def provenance(self):
                return {}

            def run(self, env, out, **kw):
                ran.append(env)
                return 0

        argv = ["--suite", str(suite_path), "--split", twin["split"], "--model", "greedy:", "--k", "1",
                "--budget-usd", "1", "--out", str(tmp / "out"), "--build", "/b"]
        with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                puzzle_solve.main(argv)
        self.assertIn("not installed", str(cm.exception))
        self.assertEqual(ran, [], "no JVM was started")


TIMEOUTS = []   # the timeout_s each fake build.run was given, newest last


def run_solve(classes, *extra, model="greedy:", suite=None):
    """main() on the gen2 suite's first open active puzzle with a FakeBuild of `classes` that records each JVM's env:
    (the run root, the puzzle record, the envs)."""
    suite_path = evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json"
    rec = next(r for r in json.loads(suite_path.read_text())["puzzles"]
               if r["split"] == "open" and r["status"] == "active")
    ran = []

    class FakeBuild:
        def __init__(self, build_dir, forge, allow_stale=False):
            self.classes = classes

        def provenance(self):
            return {}

        def run(self, env, out, **kw):
            ran.append(env)
            TIMEOUTS.append(kw.get("timeout_s"))
            return 0

    root = Path(tempfile.mkdtemp())
    if suite == "own-copy":
        (root / puzzle_solve.SUITE_COPY).write_bytes(suite_path.read_bytes())
        suite_path = root / puzzle_solve.SUITE_COPY
    with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
            contextlib.redirect_stdout(io.StringIO()):
        puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", model, "--k", "1",
                           "--budget-usd", "1", "--ids", rec["id"], "--out", str(root), "--build", "/b"]
                          + list(extra))
    return root, rec, ran


def run_openrouter(model, *extra):
    """run_solve for an openrouter: model against a loopback endpoint: (the JVM envs, the puzzle's manifest)."""
    with mock.patch.dict(os.environ, {"OPENROUTER_BASE_URL": "http://127.0.0.1:9/api/v1"}):
        root, rec, envs = run_solve(classes_with_transcript(), *extra, model=model)
    return envs, json.loads((root / rec["id"] / "batch-manifest.json").read_text())


class RecordBuildTest(unittest.TestCase):
    """A build compiled before Transcript would run a puzzle with no record while its manifest said it kept one: the
    run refuses it unless --allow-no-record, which the manifest then states. A resume passing the run's own suite copy
    as --suite does not copy the file onto itself."""

    def test_a_build_without_transcript_is_refused_before_anything_runs(self):
        with self.assertRaises(SystemExit) as cm:
            run_solve(Path(tempfile.mkdtemp()))
        self.assertIn("Transcript", str(cm.exception))
        self.assertIn("--allow-no-record", str(cm.exception))

    def test_allowed_without_the_record_the_manifest_says_so(self):
        root, rec, ran = run_solve(Path(tempfile.mkdtemp()), "--allow-no-record")
        self.assertEqual(len(ran), 1)
        record = json.loads((root / rec["id"] / "batch-manifest.json").read_text())["record"]
        self.assertIsNone(record["transcript_version"], "a run without the record must not claim one")

    def test_the_runs_own_suite_copy_is_not_copied_onto_itself(self):
        root, rec, ran = run_solve(classes_with_transcript(), suite="own-copy")
        self.assertEqual(len(ran), 1)

    def test_the_version_is_the_stamp_versions_py_reads(self):
        import versions
        want = dict((name, value) for name, value, _, _ in versions.read_stamps())["Transcript.VERSION"]
        self.assertEqual(puzzle_solve.transcript_version(), want)


class DecisionModelSeatTest(unittest.TestCase):
    """A jev: seat (DecisionsBridge) sends each position to openrouter.ai like an openrouter: one, and it cannot call
    a lookup, so a run with them would record a condition it never had."""

    SUITE = str(evalpath.EVAL / "suites" / "puzzles-hard-gen2-v1.json")

    def run_main(self, env, *extra):
        argv = ["--suite", self.SUITE, "--split", "open", "--model", "jev:typesafe/jev-1.14", "--k", "1",
                "--budget-usd", "0", "--out", tempfile.mkdtemp(), "--build", "/nonexistent/build"] + list(extra)
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(puzzle_solve, "load_secrets"):
            for k in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_BASE_URL"):
                if k not in env:
                    os.environ.pop(k, None)
            with self.assertRaises(SystemExit) as cm:
                puzzle_solve.main(argv)
        return str(cm.exception.code)

    def test_a_jev_spec_needs_the_egress_opt_in(self):
        self.assertIn("OPENROUTER_ALLOW_EGRESS", self.run_main({}))

    def test_a_jev_spec_on_loopback_needs_no_opt_in(self):
        # Past the egress check, it stops at the missing build: nothing ran.
        self.assertIn("no compiled overlay", self.run_main({"OPENROUTER_BASE_URL": "http://127.0.0.1:9/api/v1"}))

    def test_a_jev_spec_refuses_the_lookup_tools(self):
        msg = self.run_main({"OPENROUTER_BASE_URL": "http://127.0.0.1:9/api/v1"}, "--memory-tools")
        self.assertIn("--memory-tools", msg)
        self.assertIn("jev:", msg)


class ClaudeCliSeatTest(unittest.TestCase):
    """A claude-cli: seat (ClaudeCliBridge) runs on the local Claude CLI: it needs no OpenRouter opt-in, takes no
    lookups, runs one JVM at a time, and its effort is pinned and recorded (never inherited from the shell)."""

    SUITE = DecisionModelSeatTest.SUITE
    CLI = "claude-cli:claude-opus-5-5"

    def run_main(self, model, *extra, opted_in=True):
        argv = ["--suite", self.SUITE, "--split", "open", "--model", model, "--k", "1", "--budget-usd", "0",
                "--out", tempfile.mkdtemp(), "--build", "/nonexistent/build"] + list(extra)
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(puzzle_solve, "load_secrets"):
            for k in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_BASE_URL", "CLAUDE_CLI_SEAT"):
                os.environ.pop(k, None)
            if opted_in:
                os.environ["CLAUDE_CLI_SEAT"] = "1"
            with self.assertRaises(SystemExit) as cm:
                puzzle_solve.main(argv)
        return str(cm.exception.code)

    def test_it_needs_no_openrouter_opt_in(self):
        # Past every refusal, it stops at the missing build: nothing ran.
        self.assertIn("no compiled overlay", self.run_main(self.CLI, "-j", "1"))

    def test_it_refuses_the_lookup_tools_and_parallel_jvms(self):
        msg = self.run_main(self.CLI, "-j", "1", "--memory-tools")
        self.assertIn("--memory-tools", msg)
        self.assertIn("claude-cli:", msg)
        self.assertIn("-j 1", self.run_main(self.CLI))   # the default is 2

    def test_it_is_refused_without_its_explicit_opt_in(self):
        self.assertIn("CLAUDE_CLI_SEAT=1", self.run_main("opus", "-j", "1", opted_in=False))   # the bare lane too
        msg = self.run_main(self.CLI, "-j", "1", opted_in=False)
        self.assertIn("CLAUDE_CLI_SEAT=1", msg)
        self.assertIn("local development", msg)

    def test_its_effort_and_thinking_budget_are_pinned_and_no_other_model_takes_them(self):
        for flag, v in (("--claude-cli-effort", "high"), ("--claude-cli-thinking-tokens", "16000")):
            self.assertIn(flag, self.run_main("openrouter:openai/gpt-6-luna", flag, v))
        a = puzzle_solve.argparse.Namespace(model=self.CLI, context_profile="v4-faithful", memory_tools=False,
                                            rules_tool=False, claude_cli_effort="high", claude_cli_thinking_tokens=16000)
        cond = puzzle_solve.condition(a)
        self.assertEqual((cond["CLAUDE_BRIDGE_EFFORT"], cond["CLAUDE_BRIDGE_EFFORT_SMART"], cond["CLAUDE_BRIDGE_EFFORT_FAST"],
                          cond["CLAUDE_CLI_THINKING_TOKENS"]), ("high", "", "", "16000"))
        a.claude_cli_effort = None
        a.claude_cli_thinking_tokens = None
        self.assertEqual((puzzle_solve.condition(a)["CLAUDE_BRIDGE_EFFORT"], puzzle_solve.condition(a)["CLAUDE_CLI_THINKING_TOKENS"]),
                         ("", ""))
        a.model = "openrouter:openai/gpt-6-luna"
        self.assertFalse(any(k.startswith("CLAUDE") for k in puzzle_solve.condition(a)), "another model's condition moved")


class MaxPriceAtListTest(unittest.TestCase):
    """Owner, 2026-10-25: a frontier run must not be routed to a priority tier (GPT-6.1 Sol's costs 6x list).
    --max-price-at-list sends each request a ceiling at the model's list price in eval/pricing/prices.json, in USD per
    million tokens, exactly (a ceiling rounded below list would rule out the list-price endpoint itself)."""

    SUITE = DecisionModelSeatTest.SUITE

    def test_the_ceiling_is_the_list_price_per_million_tokens_exactly(self):
        prices = puzzle_solve.cost_report.load_prices()
        for spec, want in (("openrouter:openai/gpt-6.1-sol", {"prompt": 2, "completion": 10}),
                           ("openrouter:moonshotai/kimi-k3", {"prompt": 0.17, "completion": 16}),
                           ("openrouter:deepseek/deepseek-v4.1-flash", {"prompt": 0.02, "completion": 1})):
            got = json.loads(puzzle_solve.max_price_env(prices, spec))
            self.assertEqual(got, {spec.split(":", 1)[1]: want}, spec)
        self.assertNotIn("e-", puzzle_solve.max_price_env(prices, "openrouter:deepseek/deepseek-v4.1-flash"))

    def test_the_flag_reaches_the_jvm_and_the_manifest_records_it(self):
        envs, manifest = run_openrouter("openrouter:openai/gpt-6.1-sol", "--max-price-at-list")
        want = puzzle_solve.max_price_env(puzzle_solve.cost_report.load_prices(), "openrouter:openai/gpt-6.1-sol")
        self.assertEqual(envs[0]["OPENROUTER_MAX_PRICE"], want)
        self.assertEqual(manifest["record"]["openrouter_max_price"], json.loads(want))

    def test_without_the_flag_no_ceiling_is_sent_and_none_is_recorded(self):
        envs, manifest = run_openrouter("openrouter:openai/gpt-6.1-sol")
        self.assertNotIn("OPENROUTER_MAX_PRICE", envs[0])
        self.assertIsNone(manifest["record"]["openrouter_max_price"])

    def test_it_is_refused_for_a_model_that_is_not_an_openrouter_chat_model(self):
        for model in ("jev:typesafe/jev-1.14", "greedy:"):
            with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve, "load_secrets"):
                puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", model, "--k", "1",
                                   "--budget-usd", "1", "--out", tempfile.mkdtemp(), "--build", "/b",
                                   "--max-price-at-list"])
            self.assertIn("--max-price-at-list", str(cm.exception.code), model)

    def test_a_refused_attempt_is_reported_and_the_run_goes_on(self):
        # pr-review of #327: the progress line scored each row, so one refused attempt raised and ended a paid run.
        self.assertEqual(self.two_puzzles_with({"routing_refusals": 3}).count("[ROUTING-FILTER]"), 2)

    def test_an_attempt_whose_log_failed_to_write_is_reported_and_the_run_goes_on(self):
        # Issue #427: a solved row with a cut log is no result, so the summary never scores it (attempt_success refuses).
        out = self.two_puzzles_with({"status": "solved", "eval_log_write_failed": True})
        self.assertEqual(out.count("[LOG-CUT]"), 2, out)
        self.assertEqual(out.count("solved=0"), 2, out)

    def two_puzzles_with(self, extra):
        suite = json.loads(Path(self.SUITE).read_text())
        ids = [r["id"] for r in suite["puzzles"] if r["split"] == "open" and r["status"] == "active"][:2]
        played = []

        class FakeBuild:
            classes = classes_with_transcript()

            def __init__(self, build_dir, forge, allow_stale=False):
                pass

            def provenance(self):
                return {}

            def run(self, env, out, **kw):
                played.append(out.name)
                row = {"schema": "pzl-result-v1", "id": out.name, "seed": 1, "status": "failed",
                       "suite_sha256": suite["suite_sha256"], **extra}
                (out / "puzzle-results.jsonl").write_text(json.dumps(row) + "\n")
                return 0

        stdout, root = io.StringIO(), Path(tempfile.mkdtemp())
        with mock.patch.object(puzzle_solve.pb, "Build", FakeBuild), mock.patch.object(puzzle_solve, "load_secrets"), \
                mock.patch.dict(os.environ, {"OPENROUTER_BASE_URL": "http://127.0.0.1:9/api/v1"}), \
                contextlib.redirect_stdout(stdout):
            rc = puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", "openrouter:openai/gpt-6.1-sol",
                                    "--k", "1", "--budget-usd", "1", "--ids", ",".join(ids), "--out", str(root),
                                    "--build", "/b", "-j", "1", "--max-price-at-list"])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(played), sorted(ids), "the second puzzle still ran")
        self.assertTrue(json.loads((root / puzzle_solve.RUN_FILE).read_text())["finished"])
        return stdout.getvalue()

    def test_it_is_refused_for_a_model_with_no_list_price_above_zero(self):
        # pr-review of #327: the JVM refuses a zero ceiling, so every puzzle would fail one by one; refuse it once here.
        with self.assertRaises(SystemExit) as cm:
            run_openrouter("openrouter:fake/harness-model", "--max-price-at-list")
        self.assertIn("list price", str(cm.exception.code))


class PrecisionFloorTest(unittest.TestCase):
    """Owner, 2026-10-26: an open-weight model plays at its maker's released precision, never on a host that
    compresses it further. --quantizations sends OpenRouter the precisions allowed (OPENROUTER_QUANTIZATIONS)."""

    SUITE = DecisionModelSeatTest.SUITE

    def test_the_list_reaches_the_jvm_and_the_manifest(self):
        envs, manifest = run_openrouter("openrouter:z-ai/glm-5.3", "--quantizations", "fp8,bf16,fp8")
        self.assertEqual(json.loads(envs[0]["OPENROUTER_QUANTIZATIONS"]), {"z-ai/glm-5.3": ["bf16", "fp8"]})
        # One set, one spelling (standards review of #327): the order given must not split a resume or a condition.
        self.assertEqual(manifest["record"]["openrouter_quantizations"], ["bf16", "fp8"])
        envs, manifest = run_openrouter("openrouter:z-ai/glm-5.3")
        self.assertNotIn("OPENROUTER_QUANTIZATIONS", envs[0])
        self.assertIsNone(manifest["record"]["openrouter_quantizations"])

    def test_an_unknown_precision_or_another_lane_is_refused(self):
        for model, value in (("openrouter:z-ai/glm-5.3", "fp3"), ("openrouter:z-ai/glm-5.3", ""),
                             ("jev:typesafe/jev-1.14", "fp8")):
            with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve, "load_secrets"):
                puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", model, "--k", "1",
                                   "--budget-usd", "1", "--out", tempfile.mkdtemp(), "--build", "/b",
                                   "--quantizations", value])
            self.assertIn("--quantizations", str(cm.exception.code), (model, value))

    def test_the_python_precision_names_are_the_javas(self):
        # simplify review of #327: two copies, and a name in one only passes puzzle_solve and is refused by the JVM.
        java = (evalpath.REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
                / "LlmSpikeGui.java").read_text()
        body = java.split("QUANTIZATIONS = java.util.Set.of(", 1)[1].split(");", 1)[0]
        self.assertEqual(set(re.findall(r'"([^"]+)"', body)), set(puzzle_solve.QUANTIZATIONS))

    def test_a_resume_under_another_precision_list_is_refused(self):
        root, cond = Path(tempfile.mkdtemp()), {"MEMORY_TOOLS": "0"}
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", cond, ResumeTest.BUILD,
                                    {"openrouter_quantizations": ["fp8", "bf16"]})
        same = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, quantizations=["fp8", "bf16"])
        self.assertEqual(same, [])
        for other in (None, ["fp8"]):
            bad = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, quantizations=other)
            self.assertEqual(len(bad), 1, other)
            self.assertIn("openrouter_quantizations", bad[0])


class GameCapTest(unittest.TestCase):
    """Owner, 2026-10-26: the frontier run's game cap is 90 minutes, not 30 (two board-1 trial games found the win
    after 30). The cap is a condition: a raised-cap resume replays every puzzle played under a lower cap, all its
    attempts, since replaying only the cut-off games would draw again only where the first draw was slow and bias
    the score toward fast games (pr-review of #329)."""

    SUITE = DecisionModelSeatTest.SUITE

    def test_the_cap_reaches_the_jvm_and_the_manifest_and_defaults_to_30_minutes(self):
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna", "--game-cap-minutes", "90")
        self.assertEqual(envs[0]["GAME_CAP_MS"], "5400000")
        self.assertEqual(manifest["record"]["game_cap_ms"], 5400000)
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna")
        self.assertEqual(envs[0]["GAME_CAP_MS"], "1800000")
        self.assertEqual(manifest["record"]["game_cap_ms"], 1800000)

    def test_the_jvm_outlives_every_attempts_cap_and_its_grace(self):
        # A capped game still finishes the decision in flight (maxDecisionMs() + 10 s, about 912 s).
        del TIMEOUTS[:]
        run_openrouter("openrouter:openai/gpt-6-luna", "--game-cap-minutes", "300", "--k", "4")
        self.assertEqual(len(TIMEOUTS), 1)
        self.assertGreaterEqual(TIMEOUTS[0], 4 * (300 * 60 + 1000),
                                "a JVM killed while its k-th game is still inside its cap or the grace after it")

    def test_a_token_cap_or_call_deadline_is_refused_for_a_bridge_that_never_reads_it(self):
        # A condition the seat never ran under would split a pair or a resume over a difference that is not there.
        cli = ClaudeCliSeatTest()
        for model, flag, value in (("claude-cli:claude-opus-5-5", "--max-tokens", "131072"),
                                   ("jev:typesafe/jev-1.14", "--max-tokens", "131072"),
                                   ("greedy:", "--max-tokens", "131072"),
                                   ("greedy:", "--call-timeout-minutes", "3")):
            self.assertIn(flag, cli.run_main(model, "-j", "1", flag, value), model)

    def test_a_jev_seat_takes_the_call_deadline(self):
        # DecisionsBridge reads OPENROUTER_TIMEOUT_MS.
        envs, manifest = run_openrouter("jev:typesafe/jev-1.14", "--call-timeout-minutes", "3")
        self.assertEqual(envs[0]["OPENROUTER_TIMEOUT_MS"], "180000")
        self.assertEqual(manifest["record"]["call_timeout_ms"], 180000)

    def test_a_cli_seat_takes_the_call_deadline_as_its_own(self):
        # ClaudeCliBridge reads CLAUDE_DEADLINE_MS, not OPENROUTER_TIMEOUT_MS. The thinking-vs-sampling study stopped
        # because a CLI seat's deadline could only come from the shell, where no manifest records it
        # (eval/research/2026-11-05-thinking-vs-sampling.md, section 4.1).
        with mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
            root, rec, envs = run_solve(classes_with_transcript(), "-j", "1", "--call-timeout-minutes", "20",
                                        model="claude-cli:claude-haiku-5-5")
        manifest = json.loads((root / rec["id"] / "batch-manifest.json").read_text())
        self.assertEqual(envs[0]["CLAUDE_DEADLINE_MS"], "1200000")
        self.assertNotIn("OPENROUTER_TIMEOUT_MS", envs[0], "a deadline the CLI bridge never reads")
        self.assertEqual(manifest["record"]["call_timeout_ms"], 1200000)
        with mock.patch.dict(os.environ, {"CLAUDE_CLI_SEAT": "1"}):
            root, rec, envs = run_solve(classes_with_transcript(), "-j", "1", model="claude-cli:claude-haiku-5-5")
        self.assertNotIn("CLAUDE_DEADLINE_MS", envs[0], "unset: the model's own deadline (ModelCfg)")

    def test_the_call_deadline_reaches_the_jvm_and_the_manifest_and_is_off_by_default(self):
        # Owner, 2026-10-26: no model is cut off mid-thought; the 300 s per-call deadline fired for four models.
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna", "--call-timeout-minutes", "20")
        self.assertEqual(envs[0]["OPENROUTER_TIMEOUT_MS"], "1200000")
        self.assertEqual(manifest["record"]["call_timeout_ms"], 1200000)
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna")
        self.assertNotIn("OPENROUTER_TIMEOUT_MS", envs[0], "unset: each model's own deadline (ModelCfg)")
        self.assertIsNone(manifest["record"]["call_timeout_ms"])

    def test_the_token_cap_reaches_every_model_family_and_the_manifest(self):
        # Owner, 2026-10-26: lift the 32000-token cap for the games it cut short.
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna", "--max-tokens", "131072")
        cfg = json.loads(envs[0]["OPENROUTER_MODEL_CFG"])
        self.assertEqual(set(cfg), set(puzzle_solve.MODEL_CFG_KEYS))
        self.assertTrue(all(v == {"maxTokens": 131072} for v in cfg.values()))
        self.assertEqual(manifest["record"]["max_tokens"], 131072)
        envs, manifest = run_openrouter("openrouter:openai/gpt-6-luna")
        self.assertNotIn("OPENROUTER_MODEL_CFG", envs[0])
        self.assertIsNone(manifest["record"]["max_tokens"])

    def test_the_token_cap_keys_are_the_java_tables(self):
        src = (evalpath.REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
               / "LlmSpikeGui.java").read_text()
        keys = re.search(r'for \(String key : new String\[\]\{([^}]*)\}\)\s*t\.put\(key, uniform\)', src).group(1)
        self.assertEqual(tuple(k.strip().strip('"') for k in keys.split(",")), puzzle_solve.MODEL_CFG_KEYS)

    def test_the_grace_is_run_batchs(self):
        # Two backstops for one JVM grace: raising one alone could kill a capped game mid-decision.
        src = (evalpath.EVAL / "run_batch.py").read_text()
        self.assertEqual(int(re.search(r"^\s*GRACE_S = (\d+)", src, re.M).group(1)), puzzle_solve.GRACE_S)

    def test_a_resume_under_another_token_cap_is_refused_and_the_cap_fits_an_int(self):
        root, cond = Path(tempfile.mkdtemp()), {"MEMORY_TOOLS": "0"}
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", cond, ResumeTest.BUILD,
                                    {"game_cap_ms": 1800000, "max_tokens": 131072})
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, cap_ms=1800000,
                                                       max_tokens=131072), [])
        for other in (None, 32000):
            bad = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, cap_ms=1800000,
                                                max_tokens=other)
            self.assertEqual(len(bad), 1)
            self.assertIn("max_tokens", bad[0])
        with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve, "load_secrets"):
            puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", "greedy:", "--k", "1",
                               "--budget-usd", "1", "--out", tempfile.mkdtemp(), "--build", "/b",
                               "--max-tokens", str(2 ** 31)])
        self.assertIn("--max-tokens", str(cm.exception.code))

    def test_a_long_call_deadline_with_several_attempts_is_refused(self):
        # The post-cap grace is sized from ModelCfg (about 15 minutes), so a longer deadline
        # can cost a capped puzzle its later seeds, and the resume that replays them would redraw only slow puzzles.
        with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve, "load_secrets"):
            puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", "greedy:", "--k", "2",
                               "--budget-usd", "1", "--out", tempfile.mkdtemp(), "--build", "/b",
                               "--call-timeout-minutes", "20"])
        self.assertIn("--call-timeout-minutes", str(cm.exception.code))
        envs, _ = run_openrouter("openrouter:openai/gpt-6-luna", "--call-timeout-minutes", "20")   # k=1: allowed
        self.assertEqual(envs[0]["OPENROUTER_TIMEOUT_MS"], "1200000")
        envs, _ = run_openrouter("openrouter:openai/gpt-6-luna", "--call-timeout-minutes", "5", "--k", "2")
        self.assertEqual(envs[0]["OPENROUTER_TIMEOUT_MS"], "300000", "5 minutes is inside the grace at any k")

    def test_a_resume_under_another_call_deadline_is_refused(self):
        root, cond = Path(tempfile.mkdtemp()), {"MEMORY_TOOLS": "0"}
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", cond, ResumeTest.BUILD,
                                    {"game_cap_ms": 1800000, "call_timeout_ms": 1200000})
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, cap_ms=1800000,
                                                       call_ms=1200000), [])
        for other in (None, 600000):
            bad = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, cap_ms=1800000,
                                                call_ms=other)
            self.assertEqual(len(bad), 1)
            self.assertIn("call_timeout_ms", bad[0])

    def write(self, out, rows, cap_ms=None):
        out.mkdir(parents=True, exist_ok=True)
        (out / "puzzle-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        puzzle_solve.write_manifest(out, "m", "S", out.name, {}, None,
                                    None if cap_ms is None else {"game_cap_ms": cap_ms})

    def test_a_raised_cap_replays_every_puzzle_played_under_a_lower_cap(self):
        root = Path(tempfile.mkdtemp())
        self.write(root / "capped", [{"seed": 1, "suite_sha256": "S", "status": "invalid"}])   # before the record
        self.write(root / "ended", [{"seed": 1, "suite_sha256": "S", "status": "failed"}])
        self.write(root / "capped90", [{"seed": 1, "suite_sha256": "S", "status": "invalid"}], cap_ms=5400000)
        self.assertFalse(puzzle_solve.already_done(root / "capped", 1, "S", cap_ms=5400000))
        self.assertFalse(puzzle_solve.already_done(root / "ended", 1, "S", cap_ms=5400000), "ended fast: still a draw")
        self.assertTrue(puzzle_solve.already_done(root / "capped", 1, "S", cap_ms=1800000))
        self.assertTrue(puzzle_solve.already_done(root / "capped90", 1, "S", cap_ms=5400000))

    def test_a_replayed_puzzle_sets_aside_all_its_attempts_and_only_when_it_will_run(self):
        root = Path(tempfile.mkdtemp())
        rows = [{"seed": 1, "suite_sha256": "S", "status": "solved"},
                {"seed": 2, "suite_sha256": "S", "status": "invalid"}]
        self.write(root / "p", rows)
        manifest = (root / "p" / "batch-manifest.json").read_text()
        self.assertEqual(puzzle_solve.prepare(root / "p", 2, "S", True, 5400000, can_run=lambda: False), "skip")
        self.assertTrue((root / "p" / "puzzle-results.jsonl").exists(), "an unstarted replay keeps a scoreable puzzle")
        self.assertEqual(puzzle_solve.prepare(root / "p", 2, "S", True, 5400000), "run")
        self.assertFalse((root / "p" / "puzzle-results.jsonl").exists())
        self.assertEqual(len((root / "p" / "puzzle-results.partial0.jsonl").read_text().splitlines()), 2)
        self.assertEqual((root / "p" / "batch-manifest.partial0.json").read_text(), manifest,
                         "the set-aside rows keep the manifest (and cap) they were played under")

    def test_a_resume_under_a_lower_cap_is_refused(self):
        root, cond = Path(tempfile.mkdtemp()), {"MEMORY_TOOLS": "0"}
        puzzle_solve.write_manifest(root / "p", "m", "S", "p", cond, ResumeTest.BUILD, {"game_cap_ms": 5400000})
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD,
                                                       cap_ms=5400000), [])
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD,
                                                       cap_ms=7200000), [], "a raised cap is allowed")
        bad = puzzle_solve.resume_conflicts(root, ["p"], "m", "S", cond, ResumeTest.BUILD, cap_ms=1800000)
        self.assertEqual(len(bad), 1)
        self.assertIn("game_cap_ms", bad[0])

    def test_a_cap_is_whole_minutes_and_at_least_one(self):
        # pr-review of #329: 30.00001 minutes would replay every 30-minute puzzle in full; nan crashed the run.
        # Each must stop at the cap's own check: /b has no build, so any later exit would pass a weaker assertion.
        for minutes, says in (("0", "--game-cap-minutes"), ("-5", "--game-cap-minutes"), ("0.5", 2),
                              ("30.00001", 2), ("nan", 2), ("inf", 2)):   # 2: argparse refuses a non-integer
            with self.assertRaises(SystemExit) as cm, mock.patch.object(puzzle_solve, "load_secrets"), \
                    contextlib.redirect_stderr(io.StringIO()):
                puzzle_solve.main(["--suite", self.SUITE, "--split", "open", "--model", "greedy:", "--k", "1",
                                   "--budget-usd", "1", "--out", tempfile.mkdtemp(), "--build", "/b",
                                   "--game-cap-minutes", minutes])
            if says == 2:
                self.assertEqual(cm.exception.code, 2, minutes)
            else:
                self.assertIn(says, str(cm.exception.code), minutes)


if __name__ == "__main__":
    unittest.main()
