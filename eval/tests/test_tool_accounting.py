#!/usr/bin/env python3
"""Tool accounting in aggregate.py over synthetic context_call / context_tool
rows (schema: PLAYER-HARNESS.md "Implemented in this continuation").

Pins: per-seat model rounds by tier, tool request/ok/error/denied counts,
elapsed-time stats, reported token sums, unknown (-1 / null / absent) token
handling, no double counting between decision rows and their context rows,
and honest incompleteness when backend retries left attempts without a token
record. No real logs are read.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import aggregate  # noqa: E402

V = "evidence-tools-v1"
DENIED = "ERROR: tool budget exhausted; no tool executed."


def game(seed=1, swap=False, winner="A", memory_tools=True, **kw):
    g = {"t": "game", "seed": seed, "swap": swap, "deck_a": "Identity-A",
         "deck_b": "Identity-B", "controller_a": "llm", "controller_b": "random",
         "smart_a": "haiku", "smart_b": None, "fast_a": "haiku", "fast_b": None,
         "winner": winner, "turns": 4, "memory_tools": memory_tools,
         "memory_tools_version": V, "context_profile": "v2-memory", "context_sha": "2222"}
    g.update(kw)
    return g


def call(n, tier="smart", pt=100, ct=10, reply="CHOICE 1", elapsed=1000, seat="Identity-A"):
    return {"t": "context_call", "version": V, "seat": seat, "turn": 2, "call": n,
            "tier": tier, "elapsed_ms": elapsed, "prompt_tokens": pt,
            "completion_tokens": ct, "prompt": "P", "raw_reply": reply}


def tool(n, request, result, seat="Identity-A"):
    return {"t": "context_tool", "version": V, "seat": seat, "turn": 2, "call": n,
            "request": request, "result": result}


def decision(seat="Identity-A", tier="smart", attempts=1, pt=100, ct=10, **kw):
    d = {"t": "decision", "seat": seat, "turn": 2, "phase": "MAIN1", "type": "play",
         "n_options": 3, "choice": 1, "tier": tier, "latency_ms": 1500, "fallback": None,
         "retry_ms": 0, "attempts": attempts, "finish_reason": "stop",
         "first_failed_reply": None, "prompt_tokens": pt, "completion_tokens": ct,
         "shadow_choice": None}
    d.update(kw)
    return d


def write(d, name, rows):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def account(d):
    s = aggregate.build_summary(Path(d), resamples=50, bootstrap_seed=1)
    return s, s["tool_accounting"]


class ClassifierTest(unittest.TestCase):
    def test_tool_result_classes(self):
        self.assertEqual(aggregate.classify_tool_result("Matches=3 next=END\nM1 ..."), "ok")
        self.assertEqual(aggregate.classify_tool_result(DENIED), "denied")
        self.assertEqual(aggregate.classify_tool_result("ERROR: unknown evidence id."), "error")
        self.assertEqual(aggregate.classify_tool_result("ERROR: malformed MEMORY command."), "error")
        self.assertEqual(aggregate.classify_tool_result(None), "unknown")

    def test_tool_commands(self):
        self.assertEqual(aggregate.tool_command("MEMORY SEARCH 0 Force of Will"), "SEARCH")
        self.assertEqual(aggregate.tool_command("  MEMORY READ 12 0"), "READ")
        self.assertEqual(aggregate.tool_command("MEMORY NOTE 12 maybe a counter"), "NOTE")
        self.assertEqual(aggregate.tool_command("MEMORY FROB 1 x"), "other")
        self.assertEqual(aggregate.tool_command("CHOICE 2"), "other")
        self.assertEqual(aggregate.tool_command(None), "other")

    def test_token_field_normalization(self):
        self.assertEqual(aggregate._tok(5), 5)
        self.assertEqual(aggregate._tok(0), 0)
        self.assertIsNone(aggregate._tok(-1))       # context_call unknown
        self.assertIsNone(aggregate._tok(None))     # decision-row unknown
        self.assertIsNone(aggregate._tok(True))
        self.assertIsNone(aggregate._tok("12"))


class SeatAccountingTest(unittest.TestCase):

    def fixture_rows(self):
        """One decision with 3 model rounds (SEARCH ok, READ error, third round
        lost its reply and reports -1 tokens), a denied NOTE on the next ask,
        a plain decision with no context rows, an auto fallback with no model,
        and the random opponent's rows."""
        return [
            call(1, pt=100, ct=10, reply="MEMORY SEARCH 0 bolt", elapsed=1000),
            tool(1, "MEMORY SEARCH 0 bolt", "Matches=1 next=END\nM3 (observation): bolt"),
            call(2, pt=150, ct=12, reply="MEMORY READ 99 0", elapsed=2000),
            tool(2, "MEMORY READ 99 0", "ERROR: unknown evidence id."),
            call(3, tier="fast", pt=-1, ct=-1, reply=None, elapsed=3000),
            # decision row: 3 recorded rounds + 1 bridge retry = 4 attempts; its
            # own token fields repeat the final round and must not be re-added.
            decision(attempts=4, pt=None, ct=None, tier="auto", fallback="bridge-down"),
            # second ask: a NOTE denied by the per-ask budget, then an answer
            call(4, pt=200, ct=20, reply="MEMORY NOTE 3 opp holds a counter", elapsed=500),
            tool(4, "MEMORY NOTE 3 opp holds a counter", DENIED),
            call(5, pt=210, ct=21, reply="CHOICE 2", elapsed=600),
            decision(attempts=2, pt=210, ct=21),
            # a decision that never went through memory tools (decision-row tokens count)
            decision(attempts=1, pt=50, ct=5, tier="fast"),
            # deterministic fallback with no model call at all
            decision(attempts=0, pt=None, ct=None, tier="auto", fallback="cli-timeout"),
            # opponent seat: random controller, no model
            decision(seat="Identity-B", attempts=0, pt=None, ct=None, tier="auto"),
            game(),
        ]

    def test_per_seat_counts_and_no_double_counting(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", self.fixture_rows())
            s, ta = account(d)
            self.assertEqual(ta["n_files"], 1)
            self.assertEqual(ta["memory_tools_games"], 1)
            self.assertEqual(ta["n_context_calls"], 5)
            self.assertEqual(ta["n_context_tools"], 3)
            self.assertEqual(ta["versions"], [V])
            a = ta["seats"]["Identity-A"]
            self.assertEqual(a["identity"], "a")
            self.assertEqual(a["controller"], "llm")
            self.assertEqual(a["games"], 1)
            self.assertEqual(a["model_rounds"]["total"], 5)
            self.assertEqual(a["model_rounds"]["by_tier"], {"smart": 4, "fast": 1})
            self.assertEqual(a["model_rounds"]["without_reply"], 1)
            self.assertEqual(a["model_rounds"]["unattached"], 0)
            t = a["tools"]
            self.assertEqual((t["requests"], t["ok"], t["errors"], t["denied"]), (3, 1, 1, 1))
            self.assertEqual(t["by_command"], {"SEARCH": 1, "READ": 1, "NOTE": 1})
            self.assertEqual(t["max_per_game"], 3)
            el = a["elapsed_ms"]
            self.assertEqual(el["n"], 5)
            self.assertEqual(el["sum"], 7100)
            self.assertAlmostEqual(el["mean"], 1420.0)
            self.assertEqual(el["p90"], 3000)
            tk = a["tokens"]
            # context rows with known usage: 100+150+200+210 / 10+12+20+21
            self.assertEqual(tk["context_call"], {"prompt": 660, "completion": 63,
                                                  "rounds_known": 4, "rounds_unknown": 1})
            # decision rows: the two memory-mode decisions overlap their final
            # context row and are skipped; the plain decision (50/5) counts.
            self.assertEqual(tk["decision_rows"]["rows_overlapping_skipped"], 2)
            self.assertEqual(tk["decision_rows"]["prompt"], 50)
            self.assertEqual(tk["decision_rows"]["completion"], 5)
            self.assertEqual(tk["decision_rows"]["rows_known"], 1)
            self.assertEqual(tk["decision_rows"]["rows_unknown"], 0)
            self.assertEqual(tk["reported_prompt"], 710)
            self.assertEqual(tk["reported_completion"], 68)
            self.assertEqual(tk["rounds_costed"], 5)
            # uncosted: 1 unknown context round + 1 retried attempt
            self.assertEqual(tk["rounds_uncosted"], 2)
            dr = a["decision_rows"]
            self.assertEqual(dr["total"], 4)
            self.assertEqual(dr["with_model_call"], 3)   # the attempts=0 auto row has no model
            self.assertEqual(dr["attempts_sum"], 7)
            self.assertEqual(dr["with_retries"], 1)
            self.assertEqual(dr["attempts_without_token_record"], 1)
            b = a["billing"]
            self.assertTrue(b["applicable"])
            self.assertFalse(b["complete"])
            joined = " | ".join(b["incomplete_reasons"])
            self.assertIn("1 context round(s) report no token counts", joined)
            self.assertIn("retried", joined)
            self.assertIn("1 round(s) ended without a reply", joined)
            # Opponent: no model, so accounting is n/a rather than "incomplete".
            bseat = ta["seats"]["Identity-B"]
            self.assertEqual(bseat["controller"], "random")
            self.assertFalse(bseat["billing"]["applicable"])
            self.assertFalse(bseat["billing"]["complete"])
            self.assertEqual(bseat["model_rounds"]["total"], 0)
            self.assertIn(aggregate.TOOL_ACCOUNTING_CAVEAT, ta["caveat"])
            # The same accounting hangs off the (single) experiment.
            x = s["experiments"]["by_id"][s["experiments"]["order"][0]]
            self.assertEqual(x["tool_accounting"]["seats"]["Identity-A"]["tokens"],
                             a["tokens"])

    def test_complete_accounting_when_every_round_is_costed(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [
                call(1, reply="MEMORY SEARCH 0 x"),
                tool(1, "MEMORY SEARCH 0 x", "Matches=0 next=END\n"),
                call(2, pt=120, ct=8),
                decision(attempts=2, pt=120, ct=8),
                decision(attempts=1, pt=30, ct=3, tier="fast"),
                game(),
            ])
            _, ta = account(d)
            a = ta["seats"]["Identity-A"]
            self.assertTrue(a["billing"]["complete"])
            self.assertEqual(a["billing"]["incomplete_reasons"], [])
            self.assertEqual(a["tokens"]["reported_prompt"], 100 + 120 + 30)
            self.assertEqual(a["tokens"]["rounds_uncosted"], 0)

    def test_retry_without_memory_tools_is_reported_incomplete(self):
        """Plain (non-memory) game: decision rows are the only source. A
        decision with attempts=3 recorded one attempt's tokens; two attempts
        have no record and the seat is not 'complete'."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [
                decision(attempts=3, pt=80, ct=9),
                decision(attempts=1, pt=None, ct=None),   # backend reported no usage
                game(memory_tools=False),
            ])
            _, ta = account(d)
            self.assertEqual(ta["memory_tools_games"], 0)
            self.assertEqual(ta["n_context_calls"], 0)
            a = ta["seats"]["Identity-A"]
            self.assertEqual(a["model_rounds"]["total"], 0)
            self.assertEqual(a["tokens"]["decision_rows"]["rows_known"], 1)
            self.assertEqual(a["tokens"]["decision_rows"]["rows_unknown"], 1)
            self.assertEqual(a["decision_rows"]["attempts_without_token_record"], 2)
            self.assertEqual(a["tokens"]["reported_prompt"], 80)
            self.assertEqual(a["tokens"]["rounds_uncosted"], 3)
            self.assertFalse(a["billing"]["complete"])
            joined = " | ".join(a["billing"]["incomplete_reasons"])
            self.assertIn("2 backend attempt(s) across 1 decision(s)", joined)
            self.assertIn("1 decision row(s) with a model call report no token counts", joined)

    def test_unattached_rounds_and_legacy_decision_rows(self):
        with tempfile.TemporaryDirectory() as d:
            # Legacy decision rows (no attempts/token fields) followed by a
            # context round the crash never closed with a decision row.
            legacy = {"t": "decision", "seat": "Identity-A", "turn": 1, "phase": "MAIN1",
                      "type": "play", "n_options": 2, "choice": 0, "tier": "smart",
                      "latency_ms": 900, "fallback": None}
            write(d, "game-1-1000.jsonl", [legacy, call(1, reply="MEMORY SEARCH 0 y"),
                                           tool(1, "MEMORY SEARCH 0 y", "Matches=0 next=END\n")])
            _, ta = account(d)
            a = ta["seats"]["Identity-A"]
            self.assertEqual(a["controller"], "unknown")   # crashed: no game line to map
            self.assertEqual(a["model_rounds"]["unattached"], 1)
            self.assertEqual(a["decision_rows"]["legacy_without_attempts"], 1)
            self.assertEqual(a["tokens"]["decision_rows"]["rows_unknown"], 1)
            self.assertFalse(a["billing"]["complete"])
            joined = " | ".join(a["billing"]["incomplete_reasons"])
            self.assertIn("never followed by a decision row", joined)
            self.assertIn("predate the attempts field", joined)

    def test_context_rows_attach_per_seat_in_order(self):
        rows = [call(1, seat="A"), call(1, seat="B"), decision(seat="B", attempts=1),
                call(2, seat="A"), decision(seat="A", attempts=2)]
        attached, unattached = aggregate.attach_context_calls(rows)
        self.assertEqual(unattached, [])
        self.assertEqual([r["call"] for r in attached[id(rows[2])]], [1])
        self.assertEqual([r["call"] for r in attached[id(rows[4])]], [1, 2])

    def test_accounting_is_per_experiment(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [call(1), decision(attempts=1), game()])
            write(d, "game-1-2000.jsonl", [decision(attempts=1, pt=7, ct=1),
                                           game(memory_tools=False, context_profile="v1-compat")])
            s, ta = account(d)
            self.assertEqual(s["experiments"]["n"], 2)
            # Directory-wide accounting pools cost (not win rates)...
            self.assertEqual(ta["seats"]["Identity-A"]["tokens"]["reported_prompt"], 107)
            # ...while each experiment reports only its own rows.
            per = {x["condition"]["memory_tools"]: x["tool_accounting"]
                   for x in s["experiments"]["by_id"].values()}
            self.assertEqual(per[True]["seats"]["Identity-A"]["tokens"]["reported_prompt"], 100)
            self.assertEqual(per[True]["seats"]["Identity-A"]["model_rounds"]["total"], 1)
            self.assertEqual(per[False]["seats"]["Identity-A"]["tokens"]["reported_prompt"], 7)
            self.assertEqual(per[False]["seats"]["Identity-A"]["model_rounds"]["total"], 0)

    def test_interleaved_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "game-1-1000.jsonl", [game(1, False), game(1, True)])
            _, ta = account(d)
            self.assertEqual(ta["files_with_multiple_games"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
