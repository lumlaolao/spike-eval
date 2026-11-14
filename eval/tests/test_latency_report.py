"""latency_report.py: the per-decision numbers keep every game's decisions, and a game the model did not fully play is
named unscorable instead of showing its winner (#430)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import module_tmp  # noqa: E402
import latency_report as lr


def setUpModule():
    module_tmp.isolate(__name__)


def run_dir(game_extra):
    d = tempfile.mkdtemp()
    rows = [{"t": "decision", "tier": "smart", "type": "play", "completion_tokens": 30},
            dict({"t": "game", "seed": 1, "swap": False, "winner": "A", "turns": 7}, **game_extra)]
    with open(os.path.join(d, "game-1-1000.jsonl"), "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return d


def report(d):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        lr.report("arm", d, compact=True)
    return out.getvalue()


class LatencyReportTest(unittest.TestCase):
    def test_a_scorable_game_shows_its_winner(self):
        out = report(run_dir({}))
        self.assertIn("winner=A", out)
        self.assertNotIn("unscorable", out)

    def test_a_cut_log_or_dead_bridge_game_is_named_and_its_winner_hidden_but_its_decisions_stay(self):
        for extra in ({"eval_log_write_failed": True}, {"bridge_down_decisions_a": 2}):
            out = report(run_dir(extra))
            self.assertIn("unscorable=1", out, extra)
            self.assertIn("winner=invalid", out, extra)
            self.assertIn("decisions=1", out, extra)

    def test_a_game_line_from_before_the_fields_is_judged_by_its_paired_log(self):
        # No bridge_down_decisions_* on the line: game-<seed>-<swap>.log decides, and the "[EVAL] jsonl" line it names
        # pairs it with its own game file (load tags each game with _file).
        from test_aggregate import LOG_402
        d = run_dir({})
        with open(os.path.join(d, "game-1-0.log"), "w") as f:
            f.write("[EVAL] jsonl game-1-1000.jsonl\n" + LOG_402)
        out = report(d)
        self.assertIn("unscorable=1", out)
        self.assertIn("winner=invalid", out)
        with open(os.path.join(d, "game-1-0.log"), "w") as f:      # the same log, but of another game's file
            f.write("[EVAL] jsonl game-1-2000.jsonl\n" + LOG_402)
        self.assertNotIn("unscorable", report(d))
        with open(os.path.join(d, "game-1-0.log"), "w") as f:
            f.write("[EVAL] jsonl game-1-1000.jsonl\n[Seat] [ROUTER] play: PASS\n")
        self.assertIn("winner=A", report(d))


if __name__ == "__main__":
    unittest.main()
