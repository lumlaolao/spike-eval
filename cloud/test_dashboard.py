#!/usr/bin/env python3
"""Unit tests for cloud/dashboard.py (stdlib only).

Run: python3 cloud/test_dashboard.py

All fixtures are synthetic files in a tempdir — the live runs/ directory is
never touched.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402


def d_row(dtype="play", shadow=1, **kw):
    row = {"t": "decision", "type": dtype, "tier": "smart",
           "shadow_choice": shadow}
    row.update(kw)
    return json.dumps(row)


def g_row(winner="A", **kw):
    row = {"t": "game", "winner": winner, "turns": 10}
    row.update(kw)
    return json.dumps(row)


class CorpusScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = os.path.join(self.tmp.name, "runs")

    def tearDown(self):
        self.tmp.cleanup()

    def write_cell(self, cell, fname, lines):
        cdir = os.path.join(self.runs, cell)
        os.makedirs(cdir, exist_ok=True)
        path = os.path.join(cdir, fname)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_counts_games_decisions_labels_and_wld(self):
        self.write_cell("a-vs-b", "g1.jsonl", [
            d_row("play", shadow=2),
            d_row("play", shadow=None),          # decision but NOT a label
            d_row("mulligan", shadow=0),
            g_row("A"),
        ])
        self.write_cell("a-vs-b", "g2.jsonl", [
            d_row("attackers", shadow=1),
            d_row("blockers", shadow=0),
            g_row("B"),
        ])
        self.write_cell("c-vs-d", "g3.jsonl", [
            d_row("play", shadow=0),
            g_row("invalid"),                     # neither A nor B -> draw/other
        ])
        agg = dashboard.scan_corpus(self.runs, cache={})
        self.assertTrue(agg["ok"])
        self.assertEqual(agg["files"], 3)
        self.assertEqual(agg["games"], 3)
        self.assertEqual(agg["decisions"], 6)
        self.assertEqual(agg["labels"], 5)
        self.assertEqual(agg["wins"], 1)
        self.assertEqual(agg["losses"], 1)
        self.assertEqual(agg["draws"], 1)
        self.assertEqual(agg["cells"], 2)
        self.assertEqual(agg["by_type"],
                         {"play": 2, "mulligan": 1, "attackers": 1, "blockers": 1})
        self.assertAlmostEqual(agg["label_yield_pct"], 83.3, places=1)

    def test_shadow_choice_zero_is_a_label(self):
        # regression guard: `0` is falsy but IS a valid label
        self.write_cell("a-vs-b", "g.jsonl", [d_row("play", shadow=0), g_row()])
        agg = dashboard.scan_corpus(self.runs, cache={})
        self.assertEqual(agg["labels"], 1)

    def test_garbage_lines_and_empty_files_do_not_crash(self):
        self.write_cell("a-vs-b", "bad.jsonl",
                        ["not json at all", "{\"t\": \"decision\"", "", g_row("A")])
        self.write_cell("a-vs-b", "empty.jsonl", [""])
        agg = dashboard.scan_corpus(self.runs, cache={})
        self.assertTrue(agg["ok"])
        self.assertEqual(agg["games"], 1)
        self.assertEqual(agg["decisions"], 0)

    def test_missing_dir_reports_not_ok(self):
        agg = dashboard.scan_corpus(os.path.join(self.tmp.name, "nope"), cache={})
        self.assertFalse(agg["ok"])
        self.assertIn("not found", agg["note"])

    def test_cache_skips_unchanged_files_and_drops_deleted(self):
        p = self.write_cell("a-vs-b", "g.jsonl", [d_row(), g_row("A")])
        cache = {}
        a1 = dashboard.scan_corpus(self.runs, cache)
        key1 = cache[p][0]
        a2 = dashboard.scan_corpus(self.runs, cache)     # unchanged -> same entry
        self.assertEqual(cache[p][0], key1)
        self.assertEqual(a1, a2)
        # append a row with a bumped mtime -> rescan picks it up
        with open(p, "a") as f:
            f.write(g_row("B") + "\n")
        future = time.time() + 5
        os.utime(p, (future, future))
        a3 = dashboard.scan_corpus(self.runs, cache)
        self.assertEqual(a3["games"], 2)
        # delete -> cache entry evicted
        os.remove(p)
        dashboard.scan_corpus(self.runs, cache)
        self.assertNotIn(p, cache)


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "ledger.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows):
        with open(self.path, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def test_total_and_month_math(self):
        now = datetime(2026, 7, 24, 12, 0, 0)
        self.write([
            {"ts": datetime(2026, 7, 2).timestamp(), "what": "g5.xlarge spot",
             "hours": 2.5, "rate": 0.44},                      # 1.10 this month
            {"ts": "2026-07-20T08:00:00", "what": "g5.2xlarge spot",
             "hours": 4, "rate": 0.60},                        # 2.40 this month
            {"ts": "2026-06-30", "what": "g5.xlarge spot",
             "hours": 10, "rate": 0.44},                       # 4.40 June only
        ])
        out = dashboard.summarize_ledger(self.path, now=now)
        self.assertEqual(out["rows"], 3)
        self.assertAlmostEqual(out["total"], 7.90, places=2)
        self.assertAlmostEqual(out["month"], 3.50, places=2)
        self.assertEqual(out["bad_rows"], 0)
        self.assertEqual(len(out["recent"]), 3)
        self.assertEqual(out["recent"][0]["what"], "g5.xlarge spot")  # newest first

    def test_bad_rows_counted_not_fatal(self):
        self.write([
            {"ts": 1784900000, "what": "ok", "hours": 1, "rate": 1.0},
            "not json",
            {"ts": 1784900000, "what": "no rate", "hours": 1},
            {"ts": 1784900000, "what": "bad types", "hours": "x", "rate": 1},
        ])
        out = dashboard.summarize_ledger(self.path)
        self.assertEqual(out["rows"], 1)
        self.assertEqual(out["bad_rows"], 3)
        self.assertAlmostEqual(out["total"], 1.0, places=2)

    def test_missing_ledger_is_empty_not_error(self):
        out = dashboard.summarize_ledger(os.path.join(self.tmp.name, "nope.jsonl"))
        self.assertEqual(out["rows"], 0)
        self.assertEqual(out["total"], 0.0)
        self.assertIn("no ledger", out["note"])

    def test_ts_parsing_variants(self):
        self.assertIsNotNone(dashboard.parse_ledger_ts(1784900000))
        self.assertIsNotNone(dashboard.parse_ledger_ts(1784900000.5))
        self.assertIsNotNone(dashboard.parse_ledger_ts("2026-07-24"))
        self.assertIsNotNone(dashboard.parse_ledger_ts("2026-07-24T09:30:00"))
        self.assertIsNotNone(dashboard.parse_ledger_ts("2026-07-24 09:30:00"))
        self.assertIsNone(dashboard.parse_ledger_ts("yesterday"))
        self.assertIsNone(dashboard.parse_ledger_ts(None))


class LogParsingTest(unittest.TestCase):
    def test_driver_log(self):
        lines = [
            "[20:00:01] === DAgger round 1 v2 (kernel-panic mitigations) ===",
            "[20:00:10] server up (pid 100, attempt 1)",
            "[20:00:11] cell ant-storm-vs-elves (offset 5, attempt 1)",
            "[20:10:00] cell ant-storm-vs-elves validate: OK 6",
            "[20:10:01] cell burn-vs-food-chain (offset 5, attempt 1)",
            "[20:20:00] cell burn-vs-food-chain validate: BAD 4 quarantined=2",
            "[20:20:01] cell burn-vs-food-chain (offset 5, attempt 2)",
            "[20:25:00] server up (pid 200, attempt 1)",
            "[20:30:00] cell burn-vs-food-chain validate: OK 5",
        ]
        out = dashboard.parse_driver_log(lines)
        self.assertEqual(out["current_cell"], "burn-vs-food-chain")
        self.assertEqual(out["current_attempt"], 2)
        self.assertEqual(out["server_starts"], 2)
        self.assertEqual(out["cells_validated_ok"], 1)   # only the >=6 cell
        self.assertEqual(out["games_validated"], 11)     # 6 + 5
        self.assertFalse(out["finished"])

    def test_heartbeat_incidents_last5_newest_first(self):
        lines = ["[10:00:00] ok rss=100KB"]
        for i in range(1, 8):
            lines.append("[10:%02d:00] UNHEALTHY strike=1 rss=%dKB" % (i, i))
        lines.append("[10:09:00] RESTART: killing JVMs + server")
        lines.append("[10:10:00] ok rss=200KB")
        out = dashboard.parse_heartbeat(lines)
        self.assertEqual(out["last_status"], "ok")
        self.assertEqual(out["last_ts"], "10:10:00")
        self.assertEqual(out["unhealthy_count"], 7)
        self.assertEqual(out["restart_count"], 1)
        self.assertEqual(len(out["incidents"]), 5)
        self.assertTrue(out["incidents"][0]["what"].startswith("RESTART"))

    def test_empty_logs(self):
        self.assertEqual(dashboard.parse_driver_log([])["games_validated"], 0)
        self.assertIsNone(dashboard.parse_heartbeat([])["last_status"])


class DaggerPanelHealthTest(unittest.TestCase):
    """Hermetic tests for the health classifier — synthetic scratch dir,
    pgrep mocked out, mtimes pinned with os.utime."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cfg = {"dagger_dir": self.dir, "target_games": 192}
        patcher = mock.patch.object(dashboard, "_pgrep_count", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write_log(self, name, lines, age_s=0):
        p = os.path.join(self.dir, name)
        with open(p, "w") as f:
            f.write("\n".join(lines) + "\n")
        t = time.time() - age_s
        os.utime(p, (t, t))
        return p

    def test_ok_when_last_beat_ok_and_fresh(self):
        self.write_log("driver.log", ["[10:00:00] cell a-vs-b (offset 5, attempt 1)"])
        self.write_log("heartbeat.log", ["[10:00:45] ok rss=100KB"], age_s=30)
        self.write_log("batch.log", ["[start] seed=9100 swap=0 -> g.log"])
        p = dashboard.dagger_panel(self.cfg)
        self.assertEqual(p["health"], "ok")
        self.assertEqual(p["current_cell"], "a-vs-b")

    def test_stale_when_heartbeat_stopped_writing(self):
        self.write_log("heartbeat.log", ["[10:00:45] ok rss=100KB"], age_s=300)
        p = dashboard.dagger_panel(self.cfg)
        self.assertEqual(p["health"], "stale")

    def test_boundary_119s_ok_120s_stale(self):
        self.write_log("heartbeat.log", ["[10:00:45] ok rss=1KB"], age_s=118)
        self.assertEqual(dashboard.dagger_panel(self.cfg)["health"], "ok")
        self.write_log("heartbeat.log", ["[10:00:45] ok rss=1KB"], age_s=121)
        self.assertEqual(dashboard.dagger_panel(self.cfg)["health"], "stale")

    def test_unhealthy_when_last_beat_is_a_strike(self):
        self.write_log("heartbeat.log",
                       ["[10:00:45] ok rss=100KB",
                        "[10:01:30] UNHEALTHY strike=1 rss=101KB"], age_s=10)
        p = dashboard.dagger_panel(self.cfg)
        self.assertEqual(p["health"], "unhealthy")
        self.assertEqual(p["heartbeat"]["unhealthy_count"], 1)

    def test_unknown_when_heartbeat_log_missing(self):
        self.write_log("driver.log", ["[10:00:00] server up (pid 1, attempt 1)"])
        p = dashboard.dagger_panel(self.cfg)
        self.assertEqual(p["health"], "unknown")

    def test_missing_dir_degrades(self):
        p = dashboard.dagger_panel({"dagger_dir": os.path.join(self.dir, "nope"),
                                    "target_games": 192})
        self.assertFalse(p["available"])
        self.assertIn("note", p)


class FeedTest(unittest.TestCase):
    """Event feed: match/heartbeat merge, late game rows, ok-condensation."""

    REF = datetime(2026, 7, 24, 12, 0, 0)  # fixed "now" for heartbeat times

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = os.path.join(self.tmp.name, "runs")
        self.dagger = os.path.join(self.tmp.name, "dagger")
        os.makedirs(self.dagger)
        self.cfg = {"runs_dir": self.runs, "dagger_dir": self.dagger}

    def tearDown(self):
        self.tmp.cleanup()

    def epoch(self, h, m, s=0):
        return self.REF.replace(hour=h, minute=m, second=s).timestamp()

    def write_game(self, cell, fname, lines, ts):
        cdir = os.path.join(self.runs, cell)
        os.makedirs(cdir, exist_ok=True)
        p = os.path.join(cdir, fname)
        with open(p, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.utime(p, (ts, ts))
        return p

    def write_heartbeat(self, lines, mtime):
        p = os.path.join(self.dagger, "heartbeat.log")
        with open(p, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.utime(p, (mtime, mtime))
        return p

    def test_merge_ordering_newest_first(self):
        self.write_game("a-vs-b", "game-9100-1.jsonl",
                        [d_row("play"), g_row("A", seed=9100, swap=False, turns=13)],
                        ts=self.epoch(11, 0))
        self.write_game("c-vs-d", "game-9101-2.jsonl",
                        [d_row("play"), d_row("mulligan"),
                         g_row("B", seed=9101, swap=True, turns=8)],
                        ts=self.epoch(11, 45))
        self.write_heartbeat(["[11:15:00] ok rss=1KB",
                              "[11:30:00] UNHEALTHY strike=1 rss=2KB"],
                             mtime=self.epoch(11, 30, 30))
        panel = dashboard.build_feed(self.cfg, {})
        self.assertTrue(panel["ok"])
        kinds = [e["kind"] for e in panel["events"]]
        # 11:45 loss, 11:30 UNHEALTHY, 11:00 win — the lone ok beat is condensed
        self.assertEqual(kinds, ["loss", "unhealthy", "win"])
        self.assertIn("c-vs-d seed 9101 swap 1 — LOSS (B) in 8 turns", panel["events"][0]["text"])
        self.assertIn("2 labels", panel["events"][0]["text"])
        self.assertIn("a-vs-b seed 9100 swap 0 — WIN (A) in 13 turns", panel["events"][2]["text"])
        self.assertEqual(panel["hb_status"]["state"], "UNHEALTHY strike=1")

    def test_game_row_gained_later_appears_on_rescan(self):
        cache = {}
        p = self.write_game("a-vs-b", "game-9100-1.jsonl",
                            [d_row("play"), d_row("play")], ts=self.epoch(10, 0))
        panel = dashboard.build_feed(self.cfg, cache)
        self.assertEqual([e for e in panel["events"] if e["kind"] in
                          ("win", "loss", "draw")], [])  # in-flight: no event
        with open(p, "a") as f:
            f.write(g_row("A", seed=9100, swap=False, turns=20) + "\n")
        t2 = self.epoch(10, 30)
        os.utime(p, (t2, t2))
        panel = dashboard.build_feed(self.cfg, cache)   # same cache object
        wins = [e for e in panel["events"] if e["kind"] == "win"]
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0]["ts"], t2)

    def test_heartbeat_condensation_and_recovery(self):
        lines = (["[09:%02d:00] ok rss=1KB" % i for i in range(5)]
                 + ["[09:05:00] UNHEALTHY strike=1 rss=2KB",
                    "[09:06:00] UNHEALTHY strike=2 rss=2KB",
                    "[09:07:00] RESTART: killing JVMs + server",
                    "[09:08:00] ok rss=3KB",
                    "[09:09:00] ok rss=3KB"])
        status, events = dashboard.heartbeat_feed(lines, self.epoch(9, 9, 30))
        kinds = [e["kind"] for e in events]
        # plain ok beats never become events; recovery after the bad run does
        self.assertEqual(kinds, ["unhealthy", "unhealthy", "restart", "recovered"])
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["consecutive_ok"], 2)
        self.assertIn("(2 consecutive)", status["text"])
        self.assertIn("09:09:00", status["text"])
        # UNHEALTHY always surfaces even when the log ends healthy
        self.assertTrue(all("heartbeat" in e["text"] for e in events))

    def test_hb_time_crossing_midnight(self):
        ref = datetime(2026, 7, 24, 0, 30, 0).timestamp()
        late = dashboard.parse_hb_time("23:50:00", ref)
        self.assertLess(late, ref)                       # mapped to yesterday
        self.assertAlmostEqual(ref - late, 40 * 60, delta=61)

    def test_both_sources_missing_degrades(self):
        cfg = {"runs_dir": os.path.join(self.tmp.name, "nope"),
               "dagger_dir": os.path.join(self.tmp.name, "nada")}
        panel = dashboard.build_feed(cfg, {})
        self.assertFalse(panel["ok"])
        self.assertIn("no data", panel["note"])
        self.assertEqual(panel["events"], [])

    def test_capped_at_40_events(self):
        for i in range(50):
            self.write_game("a-vs-b", "game-%d-1.jsonl" % (9000 + i),
                            [g_row("A", seed=9000 + i, swap=False)],
                            ts=self.epoch(8, 0) + i)
        panel = dashboard.build_feed(self.cfg, {})
        self.assertEqual(len(panel["events"]), 40)
        self.assertEqual(panel["events"][0]["text"].split()[2], "9049")  # newest first


def _proc(returncode=0, stdout="", stderr=""):
    r = mock.Mock()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return r


class CostExplorerTest(unittest.TestCase):
    CE_OK = json.dumps({"ResultsByTime": [
        {"Total": {"UnblendedCost": {"Amount": "12.3456", "Unit": "USD"}}}]})

    def fetch(self, side_effect):
        ce = dashboard.CostExplorer(enabled=True)
        with mock.patch.object(dashboard.subprocess, "run",
                               side_effect=side_effect):
            ce._fetch()
        return ce.snapshot()

    def test_disabled_by_default_reports_optin_hint(self):
        self.assertFalse(dashboard.DEFAULTS["aws_ce"])  # consent gate stays opt-in
        ce = dashboard.CostExplorer(enabled=False)
        self.assertIn("disabled", ce.snapshot()["error"])
        ce.maybe_refresh_async()  # must be a no-op, no thread, no aws call
        self.assertIsNone(ce.snapshot()["mtd"])

    def test_happy_path_parses_mtd(self):
        s = self.fetch([_proc(0, "{}"), _proc(0, self.CE_OK)])
        self.assertEqual(s["mtd"], 12.35)
        self.assertIsNone(s["error"])

    def test_no_credentials_degrades(self):
        s = self.fetch([_proc(255, "", "Unable to locate credentials")])
        self.assertIsNone(s["mtd"])
        self.assertIn("credentials", s["error"])

    def test_ce_call_failure_degrades(self):
        s = self.fetch([_proc(0, "{}"),
                        _proc(254, "", "AccessDeniedException: not authorized")])
        self.assertIsNone(s["mtd"])
        self.assertIn("AccessDenied", s["error"])

    def test_garbage_json_degrades(self):
        s = self.fetch([_proc(0, "{}"), _proc(0, "not json")])
        self.assertIsNone(s["mtd"])
        self.assertIsNotNone(s["error"])

    def test_missing_cli_degrades(self):
        s = self.fetch(FileNotFoundError("aws"))
        self.assertEqual(s["error"], "aws CLI not installed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
