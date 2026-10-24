"""puzzle_watch.py reads a puzzle_solve.py run as it is written: each puzzle's state, spend and defects, and when the
run is over or dead. Fixture lines are copied from runs/puzzles/jev-test-1 (a jev: seat) and from the log lines
LlmSpikeGui.java prints (TALLY, TEXT-PARSE, BLOCK-INVALID)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cost_report  # noqa: E402
import puzzle_solve  # noqa: E402
import puzzle_watch  # noqa: E402

JEV = ("[JEV Solver/smart] decisions-bridge-v1 ONE -> answer {\"option\":%d} (state_bytes=11622, input_tokens=3995, "
       "cost so far $%.6f)")
TALLY = "[Solver] [TALLY] smart=%d fast=0 auto=0 ai=%d textParses=%d"


def no_second_look(seconds):
    raise AssertionError("follow looked again where it should have exited")


def decision(i, tier="smart", via="tool", fallback=None):
    return {"t": "decision", "seat": "Solver", "turn": 1, "phase": "MAIN2", "type": "play", "n_options": 2,
            "choice": i, "tier": tier, "fallback": fallback, "answer_via": via,
            "options": ["CAST Shock @2 {R}", "PASS (do nothing)"], "prompt_tokens": 4000, "completion_tokens": 50}


def result(seed, status):
    return {"schema": "pzl-result-v1", "id": "x", "seed": seed, "status": status, "solved": status == "solved"}


class Run:
    """A puzzle_solve.py --out directory, k=2: one puzzle per state the watcher must tell apart."""

    def __init__(self, k=2, finished=False):
        self.root = Path(tempfile.mkdtemp())
        self.ids = ["done", "running", "stalled", "bad", "waiting"]
        puzzle_solve.write_run(self.root, self.ids, k, 0.5, finished=finished)
        self.puzzle("done", ["[EVAL] jsonl game-1-1.jsonl", JEV % (1, 0.001), JEV % (2, 0.002), "PZL_RESULT {}",
                             "[EVAL] jsonl game-2-2.jsonl", JEV % (1, 0.0005), "PZL_RESULT {}"],
                    [decision(0), decision(1)], [result(1, "solved"), result(2, "failed")])
        self.puzzle("running", ["[EVAL] jsonl game-1-1.jsonl", JEV % (1, 0.001)], [decision(0)], [])
        self.puzzle("stalled", ["[EVAL] jsonl game-1-1.jsonl", JEV % (1, 0.001)], [decision(0)], [])
        self.age("stalled", 20 * 60)
        self.puzzle("bad", ["[Solver] [TEXT-PARSE] refused: a decision tried to read CHOICE from the text of a tool ask",
                            TALLY % (1, 1, 1),
                            "[Solver] [BLOCK-INVALID] Grizzly Bears must block: no legal set was found"],
                    [decision(0), decision(1, tier="ai"), decision(1, tier="auto", via="lost")], [])

    def puzzle(self, pid, log, rows, res):
        d = self.root / pid
        puzzle_solve.write_manifest(d, "jev:typesafe/jev-1.14", "S", pid, {})
        (d / "jvm.log").write_text("\n".join(log) + "\n")
        (d / "game-1-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (d / "puzzle-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in res))

    def age(self, pid, seconds):
        t = time.time() - seconds
        for f in (self.root / pid).iterdir():
            os.utime(f, (t, t))

    def age_all(self, seconds):
        t = time.time() - seconds
        for f in self.root.rglob("*"):
            os.utime(f, (t, t))

    def by_id(self):
        return {p["id"]: p for p in puzzle_watch.snapshot(str(self.root), 10 * 60)["puzzles"]}


class EachPuzzleTest(unittest.TestCase):
    def setUp(self):
        self.run = Run()
        self.p = self.run.by_id()

    def test_a_puzzle_with_k_result_rows_is_done_and_one_short_is_running(self):
        self.assertEqual(self.p["done"]["state"], "done")
        self.assertEqual(self.p["running"]["state"], "running")
        self.assertEqual(self.p["waiting"]["state"], "waiting", "a puzzle in puzzle-run.json with no directory yet")

    def test_solved_attempts_are_counted_by_the_scorer(self):
        self.assertEqual((self.p["done"]["solved"], self.p["done"]["attempts"]), (1, 2))

    def test_decisions_are_counted_by_tier(self):
        self.assertEqual(self.p["bad"]["tiers"], {"smart": 1, "fast": 0, "auto": 1, "ai": 1})

    def test_the_jev_bridge_cost_is_summed_over_attempts(self):
        # Each attempt builds a new bridge, whose running total starts again: 0.002 + 0.0005, not 0.0005.
        self.assertAlmostEqual(self.p["done"]["cost"], 0.0025, places=9)

    def test_a_clean_puzzle_has_no_flags(self):
        self.assertEqual(dict(self.p["done"]["flags"]), {})
        self.assertEqual(dict(self.p["running"]["flags"]), {})

    def test_every_defect_acceptance_fails_on_is_flagged(self):
        flags = self.p["bad"]["flags"]
        for kind in ("ai", "TEXT-PARSE", "LOST", "BLOCK-INVALID"):
            self.assertIn(kind, flags)
        self.assertEqual(flags["ai"][0], 1)

    def test_a_running_puzzle_whose_files_stopped_growing_is_flagged_stalled(self):
        self.assertIn("STALLED", self.p["stalled"]["flags"])
        self.assertEqual(self.p["stalled"]["state"], "running")

    def test_an_exception_in_the_log_is_flagged_but_not_its_stack_frames(self):
        d = self.run.root / "running"
        with open(d / "jvm.log", "a") as f:
            f.write("java.lang.IllegalStateException: boom\n"
                    "\tat forge.spike.LlmSpikeGui$EvalLog.writeError(LlmSpikeGui.java:1)\n")
        self.assertEqual(self.run.by_id()["running"]["flags"]["EXCEPTION"][0], 1)

    def test_an_unscoreable_row_is_flagged_not_a_crash(self):
        with open(self.run.root / "running" / "puzzle-results.jsonl", "a") as f:
            f.write(json.dumps(result(1, "unscoreable")) + "\n")
        self.assertIn("UNSCOREABLE", self.run.by_id()["running"]["flags"])

    def test_an_openrouter_seat_is_priced_by_cost_report(self):
        # OpenRouterBridge logs "replied in N ms" and no cost: the rows are priced as the budget prices them.
        d = self.run.root / "running"
        puzzle_solve.write_manifest(d, "openrouter:openai/gpt-6-luna", "S", "running", {})
        (d / "jvm.log").write_text("[OPENROUTER Solver/smart] replied in 900 ms\n")
        want = cost_report.report_dir(str(d), cost_report.load_prices())["usd"]
        self.assertGreater(want, 0)
        self.assertAlmostEqual(self.run.by_id()["running"]["cost"], want, places=12)


class AnswerTest(unittest.TestCase):
    def test_an_answer_is_shown_as_the_option_labels_the_seat_read(self):
        row = dict(decision(0), type="cleanup-discard", picks=[0, 1])
        self.assertEqual(puzzle_watch.answer_of(row), "CAST Shock @2 {R}, PASS (do nothing)")
        # A play row records a pass as choice -1 when the menu lists no PASS label.
        self.assertEqual(puzzle_watch.answer_of(dict(decision(-1), options=["CAST Shock @2 {R}"])), "PASS / none")
        self.assertEqual(puzzle_watch.answer_of(dict(decision(3), n_options=-1)), "3")


class TableTest(unittest.TestCase):
    def test_the_table_has_a_row_per_puzzle_and_a_totals_line_against_the_budget(self):
        run = Run()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(puzzle_watch.main([str(run.root)]), 0)
        lines = out.getvalue().splitlines()
        for pid in run.ids:
            self.assertTrue(any(l.startswith(pid + " ") for l in lines), pid)
        self.assertIn("1/5 puzzles done, 1 solved (attempts 1/2)", lines[-1])
        self.assertIn("of the $0.50 budget", lines[-1])
        bad = next(l for l in lines if l.startswith("bad "))
        self.assertIn("ai x1", bad)
        self.assertIn("TEXT-PARSE x1", bad)
        self.assertIn("play -> PASS (do nothing)", next(l for l in lines if l.startswith("done ")))

    def test_the_watcher_never_writes_into_the_run(self):
        run = Run(finished=True)

        def tree():
            return sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in run.root.rglob("*"))
        before = tree()
        with redirect_stdout(io.StringIO()):
            puzzle_watch.main([str(run.root)])
            puzzle_watch.follow(str(run.root), 0, 600, emit=lambda l: None, sleep=no_second_look)
        self.assertEqual(tree(), before)

    def test_the_watcher_loads_no_network_module(self):
        code = ("import sys; sys.path.insert(0, %r); import puzzle_watch; "
                "print([m for m in ('socket', 'ssl', 'http.client', 'urllib.request') if m in sys.modules])"
                % os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "[]")


class FollowTest(unittest.TestCase):
    def test_a_new_decision_and_a_finished_puzzle_are_each_one_line(self):
        run = Run()
        before = puzzle_watch.snapshot(str(run.root), 600)
        self.assertEqual(puzzle_watch.events(before, before), [])
        d = run.root / "running"
        with open(d / "game-1-1.jsonl", "a") as f:
            f.write(json.dumps(decision(0)) + "\n")
        with open(d / "jvm.log", "a") as f:
            f.write("[Solver] [TEXT-PARSE] refused\n")
        with open(d / "puzzle-results.jsonl", "w") as f:
            f.write(json.dumps(result(1, "solved")) + "\n" + json.dumps(result(2, "solved")) + "\n")
        lines = puzzle_watch.events(before, puzzle_watch.snapshot(str(run.root), 600))
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("running decision 2 smart play -> CAST Shock @2 {R}", lines[0])
        self.assertIn("running FLAG TEXT-PARSE x1", lines[1])
        self.assertIn("running DONE solved: solved 2/2", lines[2])

    def test_follow_exits_0_once_puzzle_solve_wrote_finished(self):
        run = Run(finished=True)
        out = []
        self.assertEqual(puzzle_watch.follow(str(run.root), 0, 600, emit=out.append, sleep=no_second_look), 0)
        self.assertTrue(out[-1].startswith("[watch] run over:"), out[-1])
        self.assertTrue(any("waiting" not in l and "DONE" in l for l in out))

    def test_follow_keeps_watching_a_live_run(self):
        run = Run()
        looks = []

        def sleep(s):
            looks.append(s)
            if len(looks) > 2:
                raise AssertionError("follow did not exit on finished")
            if len(looks) == 2:
                puzzle_solve.write_run(run.root, run.ids, 2, 0.5, finished=True)
        self.assertEqual(puzzle_watch.follow(str(run.root), 7, 600, emit=lambda l: None, sleep=sleep), 0)
        self.assertEqual(looks, [7, 7])

    def test_follow_exits_nonzero_when_nothing_in_the_run_has_grown_for_the_stall(self):
        run = Run()
        run.age_all(20 * 60)
        out = []
        self.assertEqual(puzzle_watch.follow(str(run.root), 0, 600, emit=out.append, sleep=no_second_look), 3)
        self.assertTrue(out[-1].startswith("[watch] DEAD?"), out[-1])

    def test_a_run_without_puzzle_run_json_is_over_when_every_log_ended_on_a_result_and_went_quiet(self):
        run = Run()
        (run.root / puzzle_solve.RUN_FILE).unlink()
        for pid in ("running", "stalled", "bad", "waiting"):
            for f in (run.root / pid).iterdir() if (run.root / pid).is_dir() else ():
                f.unlink()
            if (run.root / pid).is_dir():
                (run.root / pid).rmdir()
        snap = puzzle_watch.snapshot(str(run.root), 600)
        self.assertFalse(puzzle_watch.over(snap), "a log that just wrote its result may start the next seed")
        run.age_all(5 * 60)
        self.assertTrue(puzzle_watch.over(puzzle_watch.snapshot(str(run.root), 600)))


if __name__ == "__main__":
    unittest.main()
