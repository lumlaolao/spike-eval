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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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

    def test_an_attempt_refused_under_the_price_ceiling_is_played_again(self):
        # pr-review of #327: such a row is not a result (puzzle_score refuses it), so a resume replays the puzzle.
        out = Path(tempfile.mkdtemp()) / "p"
        self.write(out, [{"seed": 1, "suite_sha256": "S", "routing_refusals": 2}])
        self.assertFalse(puzzle_solve.already_done(out, 1, "S"))
        self.write(out, [{"seed": 1, "suite_sha256": "S", "routing_refusals": 0}])
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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


class RunFileTest(unittest.TestCase):
    """eval/puzzle_watch.py reads puzzle-run.json to show the puzzles not started, when each is done (k rows) and
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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
        suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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
        java = (Path(__file__).resolve().parent.parent / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
                / "Transcript.java").read_text()
        version = java.split('static final String VERSION = "', 1)[1].split('"', 1)[0]
        manifest = json.loads((root / rec["id"] / "batch-manifest.json").read_text())
        self.assertEqual(manifest["record"]["transcript_version"], version)
        kept = root / rec["id"] / manifest["record"]["pzl_file"]
        self.assertEqual(puzzle_solve.pf.sha256_file(kept), rec["pzl_sha256"], "the exact position the JVM read")
        run = json.loads((root / puzzle_solve.RUN_FILE).read_text())
        self.assertEqual(puzzle_solve.pf.sha256_file(root / run["suite_file"]),
                         puzzle_solve.pf.sha256_file(suite_path), "the suite, byte for byte")


def run_solve(classes, *extra, model="greedy:", suite=None):
    """main() on the gen2 suite's first open active puzzle with a FakeBuild of `classes` that records each JVM's env:
    (the run root, the puzzle record, the envs)."""
    suite_path = Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json"
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

    SUITE = str(Path(__file__).resolve().parent / "suites" / "puzzles-hard-gen2-v1.json")

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
                       "suite_sha256": suite["suite_sha256"], "routing_refusals": 3}
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
        self.assertEqual(stdout.getvalue().count("[ROUTING-FILTER]"), 2, stdout.getvalue())
        self.assertTrue(json.loads((root / puzzle_solve.RUN_FILE).read_text())["finished"])

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
        java = (Path(__file__).resolve().parent.parent / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
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


if __name__ == "__main__":
    unittest.main()
