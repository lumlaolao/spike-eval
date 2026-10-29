"""tool_audit: the fixtures are request/result pairs recorded in runs/lines/ctl (text grammar) and
the shapes the native path writes."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
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
        # The native path's form refusals count as REFUSED, so --fail-on-refused sees them.
        self.assertEqual(ta.classify("INSPECT @3", "ERROR: a tool request must be a function call; no tool executed."), "REFUSED")
        self.assertEqual(ta.classify("x", "ERROR: `offset` must be an integer."), "REFUSED")
        self.assertEqual(ta.classify("x", "ERROR: rules_lookup needs `action`: one of [search, read]."), "REFUSED")
        # Recorded (runs/lines/ctl/native-lookahead): a preview that threw is a failed tool, not a served one.
        self.assertEqual(ta.classify("TOOL CALL simulate {\"option\":1}", "LOOKAHEAD (option 1): (preview failed: RuntimeException)"), "FAILED")
        self.assertEqual(ta.classify("x", "LOOKAHEAD (option 2): (lookahead v1 supports spell options only)"), "empty")
        # Recorded (GAUNTLET_TOOLS=1, seed t4, arm 3 on epoch 46 - e46-random-t4-tools-20261017 g-kw04:284): the
        # preview guard on a decision that is neither a priority menu nor an aim is the tool's documented
        # `empty`, not a refused form: the model gets its corrective hint and no lookup was spent.
        self.assertEqual(
            ta.classify("TOOL CALL simulate {\"option\": 1}",
                        "ERROR: `simulate` previews an option of your priority menu, or a target while you aim a play you are making; "
                        "this decision is neither, so nothing was previewed. Answer the decision."),
            "empty")
        self.assertEqual(ta.classify("x", "ERROR: `option` must be the number of a SPELL option (1..2)."), "bad-args")
        # Recorded (runs/loop/g03 line 211): a well-formed call naming a menu option the preview does
        # not cover. It is a bad argument, not a form refusal, even though the message opens with a
        # backticked argument name like the "`offset` must be an integer" case above.
        self.assertEqual(ta.classify("TOOL CALL simulate {\"option\": 3}",
            "ERROR: `option` must be a menu number of a CAST, ACTIVATE or PLAY LAND option (1..2); PASS cannot be previewed."), "bad-args")
        self.assertEqual(ta.classify("x", "LOOKAHEAD (option 1): if you cast Guide of Souls: you board 1 -> 2; Rival no change"), "served")
        # A served read that QUOTES an error phrase is still served.
        self.assertEqual(ta.classify("x", "M3 (observation) chars 0..90/90 next=END\nERROR: tool budget exhausted; found no visible object"), "served")

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
