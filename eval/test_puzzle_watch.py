"""puzzle_watch.py reads a puzzle_solve.py run as it is written: each puzzle's state, spend and defects, and when the
run is over or dead. Fixture lines are copied from runs/puzzles/jev-test-1 (a jev: seat) and from the log lines
LlmSpikeGui.java prints (TALLY, TEXT-PARSE, BLOCK-INVALID). A context_call row's `cost_usd` is the provider's own cost
of the call (jev-test-1: 15 rows, $0.002322)."""
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


def call(cost_usd=None, tokens=1000):
    r = {"t": "context_call", "seat": "Solver", "tier": "smart", "prompt_tokens": tokens, "completion_tokens": 10}
    if cost_usd is not None:
        r["cost_usd"] = cost_usd
    return r


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
                    [call(0.001), decision(0), call(0.001), call(0.0005), decision(1)],
                    [result(1, "solved"), result(2, "failed")])
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

    def test_the_providers_cost_is_summed_over_every_call_row(self):
        # The jvm.log figure is one bridge's running total and resets with each attempt: the rows do not.
        self.assertAlmostEqual(self.p["done"]["cost"], 0.0025, places=9)

    def test_the_cost_survives_a_resume_that_set_the_log_aside(self):
        puzzle_solve.set_aside_partial(self.run.root / "done")
        self.assertAlmostEqual(self.run.by_id()["done"]["cost"], 0.0025, places=9)

    def test_a_provider_that_reports_no_cost_is_priced_as_the_budget_is(self):
        d = self.run.root / "running"
        (d / "game-1-1.jsonl").write_text(json.dumps(call(tokens=10 ** 6)) + "\n" + json.dumps(decision(0)) + "\n")
        want = cost_report.report_dir(str(d), cost_report.budget_prices(cost_report.load_prices()))["usd"]
        self.assertGreater(want, 0)
        self.assertAlmostEqual(self.run.by_id()["running"]["cost"], want, places=12)

    def test_a_done_puzzle_with_fewer_than_k_results_is_flagged_short(self):
        # puzzle_score refuses a puzzle with fewer than k attempts: a crashed or capped JVM loses its score.
        p = Run(finished=True).by_id()
        self.assertEqual(p["running"]["state"], "done")
        self.assertIn("SHORT", p["running"]["flags"])
        self.assertNotIn("SHORT", p["done"]["flags"])

    def test_a_game_thread_that_outlived_the_cap_is_a_defect_not_done(self):
        with open(self.run.root / "running" / "jvm.log", "a") as f:
            f.write("[PZL] the game thread outlived the cap at seed 1; later rollouts would share the JVM with it, "
                    "so the run stops here\n")
        p = self.run.by_id()["running"]
        self.assertEqual(p["state"], "running")
        self.assertIn("CAP-OUTLIVED", p["flags"])

    def test_a_set_aside_puzzle_not_yet_restarted_on_resume_is_waiting(self):
        d = self.run.root / "stalled"
        puzzle_solve.set_aside_partial(d)
        puzzle_solve.write_run(self.run.root, self.run.ids, 2, 0.5, finished=False)
        p = self.run.by_id()["stalled"]
        self.assertEqual(p["state"], "waiting")
        self.assertNotIn("STALLED", p["flags"])

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

    def test_a_row_refused_under_the_price_ceiling_is_flagged_as_that(self):
        # pr-review of #327: not "a goal the engine did not enforce", but an endpoint the ceiling left unreachable.
        with open(self.run.root / "running" / "puzzle-results.jsonl", "a") as f:
            f.write(json.dumps(dict(result(1, "failed"), price_ceiling_refusals=4)) + "\n")
            f.write(json.dumps(dict(result(2, "failed"), price_ceiling_refusals=2)) + "\n")
        flags = self.run.by_id()["running"]["flags"]
        self.assertEqual(flags["PRICE-CEILING"][0], 2)
        self.assertIn("6 decisions", flags["PRICE-CEILING"][1], "the lost decisions of every refused row")
        self.assertNotIn("UNSCOREABLE", flags)

    def test_an_openrouter_seat_is_priced_at_the_dearest_endpoint_as_the_budget_is(self):
        # OpenRouterBridge logs "replied in N ms" and no cost; puzzle_solve checks the budget at budget_prices.
        root = Path(tempfile.mkdtemp())
        d = root / "p"
        puzzle_solve.write_manifest(d, "openrouter:openai/gpt-6-luna", "S", "p", {})
        (d / "jvm.log").write_text("[OPENROUTER Solver/smart] replied in 900 ms\n")
        (d / "game-1-1.jsonl").write_text(json.dumps(call(tokens=10 ** 6)) + "\n")
        want = puzzle_solve.spent(root, bound=True)
        self.assertGreater(want, puzzle_solve.spent(root))
        self.assertAlmostEqual(puzzle_watch.snapshot(str(root), 600)["puzzles"][0]["cost"], want, places=12)

    def test_each_defect_line_the_gauntlet_and_the_analyzer_know_is_flagged(self):
        d = self.run.root / "running"
        cases = {
            "FALLBACK->AI": "[Solver] [FALLBACK->AI] chooseSingleEntity",
            "TEXT-FORMAT-LEAK": "[Solver] [TEXT-FORMAT-LEAK] CHOICE: in a tool reply",
            "(unparsed": "[Solver] [ROUTER] play: (unparsed reply)",
            "FAILED to pay": "[Solver] [ROUTER] Shock FAILED to pay",
            "failed to target": "[Solver] [ROUTER] Shock failed to target",
            "Couldn't add to stack": "Couldn't add to stack: Shock",
            "AI looped": "[Opponent] AI looped 999 times",
            "EXCEPTION": "java.lang.IllegalStateException: boom",
            "NO-ANSWER": "[OPENROUTER Solver/smart] non-answer -> treating as bridge-down",
        }
        for kind, line in cases.items():
            (d / "jvm.log").write_text(line + "\n")
            self.assertIn(kind, self.run.by_id()["running"]["flags"], line)
        for line in ("[BRIDGE Solver/smart] no reply", "[JEV Solver/smart] [JEV-NO-ANSWER] HTTP 500: x"):
            (d / "jvm.log").write_text(line + "\n")
            self.assertIn("NO-ANSWER", self.run.by_id()["running"]["flags"], line)

    def test_a_claude_cli_seat_that_gave_no_answer_is_flagged(self):
        # ClaudeCliBridge logs [CLAUDE-CLI-NO-ANSWER] for a process that failed, timed out or reported an error.
        line = "[CLAUDE-CLI Solver/smart] [CLAUDE-CLI-NO-ANSWER] exit 1: x -> no answer; the seat takes its fixed legal option"
        self.assertIn("NO-ANSWER", puzzle_watch.flags_of([line], [], puzzle_watch.tool_audit.audit([])))
        self.assertNotIn("NO-ANSWER", puzzle_watch.flags_of(["[CLAUDE-CLI Solver/smart] claude-cli-bridge-v2 -> TOOL CALL "
                                                             "answer {\"option\":2}"], [], puzzle_watch.tool_audit.audit([])))

    def test_a_choice_the_harness_took_and_a_refused_lookup_are_flagged(self):
        d = self.run.root / "running"
        rows = [decision(0, tier="auto", fallback="heuristic(mulligan)"),
                {"t": "context_tool", "seat": "Solver", "request": "TOOL CALL inspect {}",
                 "result": "ERROR: unknown tool inspect"},
                {"t": "context_tool", "seat": "Solver", "request": "TOOL CALL simulate {}",
                 "result": "ERROR: preview threw"}]
        (d / "game-1-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        flags = self.run.by_id()["running"]["flags"]
        for kind in ("TAKEN", "TOOL-REFUSED", "TOOL-FAILED"):
            self.assertIn(kind, flags)


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
        self.assertNotIn("$", lines[0], "a backlog line must not show the cost of a later moment")
        self.assertIn("running FLAG TEXT-PARSE x1", lines[1])
        self.assertIn("running DONE solved: solved 2/2", lines[2])
        self.assertIn("$", lines[2])

    def test_the_running_cost_is_a_line_of_its_own_when_it_moves(self):
        run = Run()
        before = puzzle_watch.snapshot(str(run.root), 600)
        with open(run.root / "running" / "game-1-1.jsonl", "a") as f:
            f.write(json.dumps(call(0.003)) + "\n")
        lines = puzzle_watch.events(before, puzzle_watch.snapshot(str(run.root), 600))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("running cost $0.0030", lines[0])

    def test_follow_on_an_empty_directory_gives_up_after_the_stall(self):
        root = tempfile.mkdtemp()
        out = []
        looks = []

        def sleep(s):
            looks.append(s)
            if len(looks) > 50:
                raise AssertionError("still looking at an empty directory")
            time.sleep(0.02)
        self.assertEqual(puzzle_watch.follow(root, 0, 0.01, emit=out.append, sleep=sleep), 3)
        self.assertTrue(out[-1].startswith("[watch] DEAD?"), out[-1])

    def test_follow_reports_a_puzzle_solve_that_stopped_at_once_with_its_own_code(self):
        run = Run()
        puzzle_solve.write_run(run.root, run.ids, 2, 0.5, finished=False, stopped="KeyboardInterrupt()")
        out = []
        self.assertEqual(puzzle_watch.follow(str(run.root), 0, 600, emit=out.append, sleep=no_second_look), 4)
        self.assertTrue(out[-1].startswith("[watch] STOPPED"), out[-1])
        self.assertIn("KeyboardInterrupt", out[-1])

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


class FullRecordIsAdditiveTest(unittest.TestCase):
    """A run that keeps its full record (Transcript.java: <puzzle>/transcripts/http-*.jsonl and engine-*.jsonl, plus
    the .pzl copy and the run's suite.json, puzzle_solve.py) reads exactly as one that does not: the watcher's
    states, counts and cost, cost_report's bill and puzzle_score's rows. The http rows hold a whole request (a
    `prompt_tokens` inside a body must not be billed) and are not decision rows."""

    def record(self, root):
        (root / "suite.json").write_text("{}")
        for d in root.iterdir():
            if not d.is_dir():
                continue
            (d / "puzzle.pzl").write_text("[metadata]\nName:x\n[state]\n")
            t = d / "transcripts"
            t.mkdir()
            body = json.dumps({"messages": [{"role": "user", "content": "x" * 5000}], "usage": {"prompt_tokens": 10 ** 9}})
            (t / "http-1-2.jsonl").write_text(json.dumps({"t": "http", "version": "transcript-v1", "seq": 1,
                                                          "request_body": body, "response_body": body,
                                                          "status": 200}) + "\n")
            (t / "engine-x-seed1-r0-1.jsonl").write_text(json.dumps({"t": "engine", "seq": 1, "type": "TURN",
                                                                     "message": "Turn 1 (Solver)"}) + "\n")

    def view(self, run):
        keep = ("state", "attempts", "solved", "tiers", "text_parses", "cost", "flags")
        snap = puzzle_watch.snapshot(str(run.root), 10 * 60, now=time.time())
        return [{k: p[k] for k in keep} for p in snap["puzzles"]]

    def test_the_watcher_the_bill_and_the_score_ignore_the_record(self):
        run = Run(finished=True)
        prices = cost_report.load_prices()
        dirs = sorted(d for d in run.root.iterdir() if d.is_dir())
        before = (self.view(run), [cost_report.report_dir(str(d), prices)["usd"] for d in dirs],
                  puzzle_watch.puzzle_score.load_rows([run.root]), puzzle_solve.spent(run.root))
        self.record(run.root)
        after = (self.view(run), [cost_report.report_dir(str(d), prices)["usd"] for d in dirs],
                 puzzle_watch.puzzle_score.load_rows([run.root]), puzzle_solve.spent(run.root))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
