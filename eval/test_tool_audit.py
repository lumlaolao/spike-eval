"""tool_audit: the fixtures are request/result pairs recorded in runs/lines/ctl (text grammar) and
the shapes the native path writes."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import tool_audit as ta

ROWS = [
    {"t": "context_call", "tier": "smart"},
    {"t": "context_tool", "request": "Let me confirm Ajani's 0-ability damage before committing.\n\nINSPECT @87",
     "result": "ERROR: use exactly one MEMORY/INSPECT/RULES command line, at most 1000 characters."},
    {"t": "context_tool", "request": "MEMORY SEARCH 0 reveal\n<｜｜DSML｜｜ invoke name=\"memory_search\">0 reveal</｜｜DSML｜｜ invoke>",
     "result": "ERROR: use exactly one MEMORY/INSPECT/RULES command line, at most 1000 characters."},
    {"t": "context_tool", "request": "INSPECT @87", "result": "@87 (current): Ajani, Nacatl Avenger [loyalty 3]"},
    {"t": "context_tool", "request": "MEMORY SEARCH 0 M220", "result": "Matches=0 next=END\n"},
    {"t": "context_tool", "request": "MEMORY READ 999 0", "result": "ERROR: unknown evidence id."},
    {"t": "context_call", "tier": "smart", "protocol": "native"},
    {"t": "context_tool", "request": "TOOL CALL inspect {\"target\":\"@41\"}", "result": "@41 (current): Sentinel"},
    {"t": "context_tool", "request": "TOOL CALL memory_read {not json", "result": "ERROR: tool arguments are not a JSON object."},
    {"t": "context_tool", "request": "TOOL CALL inspect {\"target\":\"@9\"}",
     "result": "ERROR: tool budget exhausted; no tool executed. Answer the original decision."},
    {"t": "game", "winner": "A"},
]


class ToolAuditTest(unittest.TestCase):
    def audit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "game-1.jsonl")
            with open(p, "w") as f:
                for r in ROWS:
                    f.write(json.dumps(r) + "\n")
            return ta.audit([p])

    def test_every_request_is_sorted_by_what_happened_to_it(self):
        a = self.audit()
        self.assertEqual((a["games"], a["model_calls"], a["requests"]), (1, 2, 8))
        self.assertEqual(dict(a["by_outcome"]), {"REFUSED": 3, "served": 2, "empty": 1, "bad-args": 1, "exhausted": 1})
        self.assertEqual(dict(a["protocols"]), {"text": 1, "native": 1})
        self.assertEqual(a["native_syntax_in_text"], 1)

    def test_a_refusal_is_about_form_not_about_what_the_tool_found(self):
        self.assertEqual(ta.classify("x", "ERROR: use exactly one MEMORY/INSPECT/RULES command line, at most 1000 characters."), "REFUSED")
        self.assertEqual(ta.classify("x", "ERROR: memory_read needs an integer `evidence_id`."), "REFUSED")
        self.assertEqual(ta.classify("x", "ERROR: INSPECT found no visible object or public card matching `@9`."), "empty")
        self.assertEqual(ta.classify("x", "ERROR: ids and offsets must be integers."), "bad-args")
        self.assertEqual(ta.classify("x", "Matches=2 next=END\nM1"), "served")

    def test_tool_names_are_read_from_both_protocols(self):
        self.assertEqual(ta.tool_name("Thinking first.\n\nINSPECT @87"), "inspect")
        self.assertEqual(ta.tool_name("MEMORY READ 80 0"), "memory_read")
        self.assertEqual(ta.tool_name("TOOL CALL memory_search {\"query\":\"x\"}"), "memory_search")

    def test_fail_on_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "game-1.jsonl"), "w") as f:
                for r in ROWS:
                    f.write(json.dumps(r) + "\n")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ta.main([d, "--fail-on-refused", "--json"]), 1)
            self.assertEqual(json.loads(out.getvalue())["total"]["by_outcome"]["REFUSED"], 3)


if __name__ == "__main__":
    unittest.main()
