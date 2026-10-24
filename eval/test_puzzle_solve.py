"""puzzle_solve.py refuses a condition the seat would not honour."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puzzle_solve  # noqa: E402


class RulesToolNeedsMemoryToolsTest(unittest.TestCase):
    """The seat wires the rules lookup only with MEMORY_TOOLS=1, so --rules-tool alone ran puzzles that
    recorded RULES_TOOL=1 and never offered the tool (runs/puzzles/bench-pilot/on, 2026-10-20)."""

    def test_rules_tool_without_memory_tools_is_refused_before_anything_runs(self):
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", "/nonexistent/suite.json", "--split", "open", "--model", "fake",
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
                mock.patch.object(puzzle_solve.pb, "Build"), mock.patch.object(puzzle_solve, "load_secrets"), \
                contextlib.redirect_stdout(out):
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "fake:m", "--k", "1",
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
        with self.assertRaises(SystemExit) as cm:
            puzzle_solve.main(["--suite", str(suite_path), "--split", "open", "--model", "fake:m", "--k", "1",
                               "--budget-usd", "0", "--ids", pid, "--out", str(root), "--resume",
                               "--build", "/nonexistent/build"])
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
        puzzle_solve.write_manifest(out, "m", "S", "p", cond)
        self.assertEqual(puzzle_solve.resume_conflicts(root, ["p", "q"], "m", "S", cond), [])
        for model, sha, c in (("other", "S", cond), ("m", "T", cond), ("m", "S", dict(cond, MEMORY_TOOLS="1"))):
            self.assertEqual(len(puzzle_solve.resume_conflicts(root, ["p"], model, sha, c)), 1, (model, sha, c))


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


if __name__ == "__main__":
    unittest.main()
