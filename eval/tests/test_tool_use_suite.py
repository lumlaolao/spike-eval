"""tool_use_suite: the text arm must be the grammar the harness ships, every scenario must be
solvable through every protocol, and the deciding fact must really be behind a tool."""
import io
import json
import os
import re
import unittest
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import tool_use_suite as tu

SPIKE = evalpath.REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
# One real reply from runs/lines/ctl/lookahead: a sentence, then a valid command. The shipped loop
# rejects it and charges the seat a tool.
PROSE_THEN_COMMAND = "Let me confirm the text before committing.\n\nINSPECT @41"


def java_literals(name):
    """Java source with adjacent string literals joined, so a multi-line literal reads as one."""
    src = (SPIKE / name).read_text()
    return re.sub(r'"\s*\+\s*"', "", src).replace('\\n', "\n")


def scripted(replies):
    it = iter(replies)
    return lambda messages, tools, meta: next(it)


class ToolUseSuiteTest(unittest.TestCase):
    def setUp(self):
        self.tools, self.scen = tu.load_suite()
        self.by_id = {s["id"]: s for s in self.scen["scenarios"]}

    def run_policy(self, policy):
        rows = tu.run_all(tu.policy_model(policy), self.tools, self.scen, tu.ARMS, ("smart", "fast"), 1, 1)
        return tu.summarise(rows, self.scen)["arms"]

    def test_text_arm_is_the_shipped_grammar(self):
        memory = java_literals("ContextMemory.java")
        self.assertIn(tu.MEMORY_INSTRUCTIONS.split("evidence-tools-v4", 1)[1], memory)
        self.assertIn('VERSION = "evidence-tools-v4"', memory)
        self.assertIn(tu.NATIVE_INSTRUCTIONS, java_literals("NativeTools.java"))
        self.assertIn('VERSION = "%s"' % self.tools["version"], java_literals("NativeTools.java"))
        self.assertIn(tu.INSPECT_INSTRUCTIONS, java_literals("SeatObservation.java"))
        self.assertIn(tu.ONE_LINE_ERROR, memory)
        self.assertIn("TOOLS_PER_ASK = %d;" % tu.TOOLS_PER_ASK, memory)
        self.assertIn("PAGE_CHARS = %d;" % tu.PAGE_CHARS, memory)
        self.assertIn("SEARCH_PAGE = %d;" % tu.SEARCH_PAGE, memory)
        self.assertIn("REQUEST_CHARS = NOTE_CHARS + 100;", memory)
        self.assertIn("NOTE_CHARS = %d;" % (tu.REQUEST_CHARS - 100), memory)
        self.assertIn("Math.min(text.length(), %d)" % tu.SNIPPET, memory)
        self.assertIn(tu.BUDGET_ERROR, memory)
        gui = java_literals("LlmSpikeGui.java")
        for tier in ("smart", "fast"):
            self.assertIn(tu.FORMAT[tier], gui)

    def test_every_tool_maps_onto_a_command_the_executor_implements(self):
        for t in self.tools["tools"]:
            props = t["function"]["parameters"]["properties"]
            self.assertEqual(set(re.findall(r"\{(\w+)\}", t["text_command"])), set(props), t["function"]["name"])
        cmd, err = tu.text_command(self.tools, "memory_read", {"evidence_id": 3})
        self.assertEqual((cmd, err), ("MEMORY READ 3 0", None))
        cmd, err = tu.text_command(self.tools, "memory_search", {"query": "Rival\nhand"})
        self.assertEqual(cmd, "MEMORY SEARCH 0 Rival hand")            # one line, whatever the argument holds
        self.assertEqual(tu.call_of(cmd), ("memory_search", "Rival hand"))
        self.assertIsNone(tu.text_command(self.tools, "inspect", {})[0])
        self.assertIsNone(tu.text_command(self.tools, "inspect", {"target": "  "})[0])
        # Wrongly typed arguments are refused before the executor, as NativeTools.command does.
        self.assertEqual(tu.text_command(self.tools, "memory_read", {"evidence_id": "three"}),
                         (None, "ERROR: `evidence_id` must be an integer."))
        self.assertEqual(tu.text_command(self.tools, "memory_read", {"evidence_id": "80"})[0], "MEMORY READ 80 0")
        self.assertIsNone(tu.text_command(self.tools, "memory_read", {"evidence_id": None})[0])
        self.assertIsNone(tu.text_command(self.tools, "memory_read", {"evidence_id": 0})[0])
        self.assertIsNone(tu.text_command(self.tools, "memory_read", {"evidence_id": 1.5})[0])
        self.assertEqual(tu.text_command(self.tools, "memory_read", {"evidence_id": 3.0})[0], "MEMORY READ 3 0")
        self.assertIsNone(tu.text_command(self.tools, "open_library", {"x": 1})[0])

    def test_the_deciding_fact_is_behind_a_tool_and_shown_only_in_the_twin(self):
        for s in self.scen["scenarios"]:
            if s["cls"] == "plain":
                self.assertNotIn("{FACT}", s["state"])
                continue
            need = tu.decision_text(s, "need")
            twin = tu.decision_text(s, "twin")
            seen = need.split("DECISION:")[0] + tu.Seat(s, need).brief()
            self.assertTrue(s["hidden"], s["id"])
            for h in s["hidden"]:
                self.assertNotIn(h.lower(), seen.lower(), "%s leaks %r" % (s["id"], h))
                self.assertIn(h.lower(), twin.lower(), "%s twin lacks %r" % (s["id"], h))
            self.assertTrue(all(1 <= a <= s["n_options"] for a in s["answer"]))
            self.assertEqual(len(re.findall(r"(?m)^  \d+\) ", s["decision"])), s["n_options"])

    def test_oracle_solves_every_scenario_through_every_protocol(self):
        for a in self.run_policy("oracle"):
            self.assertEqual((a["reach"], a["need_correct"], a["restraint"], a["rejected_protocol"]),
                             (a["need_n"], a["need_n"], a["restraint_n"], 0), a["arm"])

    def test_trivial_policies_score_the_stated_baselines(self):
        for a in self.run_policy("never"):
            self.assertEqual((a["reach"], a["restraint"]), (0, a["restraint_n"]))
            self.assertLess(a["need_correct"], a["need_n"])
        for a in self.run_policy("always"):
            self.assertEqual((a["reach"], a["restraint"]), (0, 0))

    def test_strict_text_rejects_prose_before_a_command_and_lenient_executes_it(self):
        s = self.by_id["inspect-attack"]
        replies = [{"content": PROSE_THEN_COMMAND}, {"content": "CHOICE: 2"}]
        meta = {"tier": "smart"}
        strict = tu.run_text(s, "need", "smart", scripted(replies), False, meta)
        self.assertEqual(strict["calls"][0]["result"], tu.ONE_LINE_ERROR)
        self.assertEqual(tu.score(s, "need", strict)["rejected_protocol"], 1)
        self.assertFalse(tu.score(s, "need", strict)["reached"])
        lenient = tu.run_text(s, "need", "smart", scripted(replies), True, meta)
        self.assertIn("First strike, deathtouch", lenient["calls"][0]["result"])
        self.assertTrue(tu.score(s, "need", lenient)["reached"])
        self.assertEqual(tu.tool_line("`INSPECT @3` to see what it does"), "INSPECT @3")       # as ContextMemory.toolLine
        self.assertIsNone(tu.tool_line("I will INSPECT @3 next.\nCHOICE: 2"))

    def test_reach_means_the_fact_came_back_not_that_a_tool_was_called(self):
        s = self.by_id["search-old-hand"]
        seat = tu.Seat(s, tu.decision_text(s, "need"))
        miss = [{"ok": True, "command": "MEMORY READ 6 0", "result": seat.execute("MEMORY READ 6 0")}]
        hit = [{"ok": True, "command": "MEMORY SEARCH 0 Rival's hand", "result": seat.execute("MEMORY SEARCH 0 Rival's hand")}]
        self.assertTrue(tu.needs_met(s, miss))                 # it looked in the archive ...
        self.assertFalse(tu.fact_returned(s, miss))            # ... and did not get the hand back
        self.assertTrue(tu.fact_returned(s, hit))              # a query quoting the decision finds the reveal
        self.assertNotIn("M%d (observation): TURN 8" % seat.observation_id, hit[0]["result"])

    def test_a_legal_answer_after_an_incomplete_one_in_one_reply_is_the_answer(self):
        # As AnswerTool.converse (answer-tool-v12): GLM 5.3 sent `lines` alone, then the option (frontier5, hard-elf2).
        s = self.by_id["inspect-attack"]

        def answers(*args):
            return {"content": None, "tool_calls": [{"id": "a%d" % i, "type": "function",
                                                     "function": {"name": "answer", "arguments": a}}
                                                    for i, a in enumerate(args)]}
        res = tu.run_native_answer(s, "need", "smart", scripted([answers('{"lines": "x"}', '{"option": 2}')]),
                                   self.tools, {"tier": "smart"})
        self.assertEqual((res["choice"], res["answer_via"], res["answer_invalid"]), (2, "tool", 0))
        # Every `answer` call in a reply illegal is one invalid reply: the next reply is still heard.
        res = tu.run_native_answer(s, "need", "smart", scripted([answers('{"option": 99}', '{"lines": "x"}'),
                                                                answers('{"option": 2}')]),
                                   self.tools, {"tier": "smart"})
        self.assertEqual((res["choice"], res["answer_invalid"]), (2, 1))

    def test_native_last_round_forbids_calls_and_call_markup_is_not_an_answer(self):
        s = self.by_id["inspect-attack"]
        seen = []

        def model(messages, tools, meta):
            seen.append(meta["tool_choice"])
            return {"content": None, "tool_calls": [{"id": "x", "type": "function",
                                                     "function": {"name": "inspect", "arguments": "{oops"}}]}
        tu.run_native(s, "need", "smart", model, self.tools, {"tier": "smart"})
        # A call on the forbidden last round is refused and the model is told once; a second ends the ask.
        self.assertEqual(seen, ["auto"] * tu.TOOLS_PER_ASK + ["none", "none"])
        markup = {"content": '<x invoke name="inspect">'}
        res = tu.run_native(s, "need", "smart", scripted([markup, markup]), self.tools, {"tier": "smart"})
        self.assertIsNone(res["choice"])
        self.assertEqual([c["rejected"] for c in res["calls"]], ["protocol", "protocol"])
        # Told once that its tools are spent, a model that then answers is heard (as ContextMemory.askNative).
        res = tu.run_native(s, "need", "smart", scripted([markup, {"content": "CHOICE: 2"}]), self.tools, {"tier": "smart"})
        self.assertEqual(res["choice"], 2)
        # As ContextMemory.askNative: a call made on a round that forbids one does not run, and the
        # answer after the reminder is heard.
        call = {"content": None, "tool_calls": [{"id": "c", "type": "function",
                                                 "function": {"name": "inspect", "arguments": "{\"target\": \"@1\"}"}}]}
        bad = {"content": None, "tool_calls": [{"id": "b", "type": "function",
                                                "function": {"name": "inspect", "arguments": "{oops"}}]}
        # The malformed call spends nothing, so the ask's calls run out on its last round.
        res = tu.run_native(s, "need", "smart", scripted([bad] + [call] * tu.TOOLS_PER_ASK + [{"content": "CHOICE: 2"}]),
                            self.tools, {"tier": "smart"})
        self.assertEqual(res["choice"], 2)
        self.assertEqual(res["calls"][-1]["rejected"], "budget")
        self.assertIn(tu.PRESS, java_literals("ContextMemory.java"))

    def test_text_tool_request_at_zero_budget_yields_no_answer(self):
        s = self.by_id["inspect-attack"]
        res = tu.run_text(s, "need", "smart", scripted([{"content": "INSPECT @41"}] * (tu.TOOLS_PER_ASK + 1)), False,
                          {"tier": "smart"})
        self.assertIsNone(res["choice"])
        self.assertEqual(res["calls"][-1]["rejected"], "budget")

    def test_native_malformed_arguments_are_a_protocol_rejection_not_a_spent_tool(self):
        s = self.by_id["inspect-attack"]
        call = lambda args: {"content": None, "tool_calls": [{"id": "x", "type": "function",
                                                              "function": {"name": "inspect", "arguments": args}}]}
        res = tu.run_native(s, "need", "smart", scripted([call("{not json"), call('{"target": "@41"}'),
                                                           {"content": "CHOICE: 2"}]), self.tools, {"tier": "smart"})
        self.assertEqual([c["rejected"] for c in res["calls"]], ["protocol", None])
        self.assertEqual(res["choice"], 2)
        self.assertTrue(tu.score(s, "need", res)["reached"])

    def test_executor_mirrors_context_memory(self):
        s = self.by_id["read-truncated-hand"]
        seat = tu.Seat(s, tu.decision_text(s, "need"))
        self.assertIn("... [READ for full text]", seat.brief())
        self.assertIn("Halcyon Verdict", seat.execute("MEMORY READ 3 0"))
        self.assertTrue(seat.execute("MEMORY SEARCH 0 halcyon").startswith("Matches=1 next=END\nM3 (historical reveal)"))   # the decision itself is skipped
        self.assertEqual(seat.execute("MEMORY READ 99 0"), "ERROR: unknown evidence id.")
        self.assertEqual(seat.execute("MEMORY READ x 0"), "ERROR: ids and offsets must be integers.")
        self.assertEqual(seat.execute("MEMORY SEARCH 0"), "ERROR: malformed MEMORY command.")
        self.assertTrue(seat.execute("INSPECT @64").startswith("@64: visible in the current state"))
        self.assertTrue(seat.execute("INSPECT Black Lotus").startswith("ERROR: INSPECT found no"))
        self.assertTrue(seat.execute("MEMORY NOTE 3 Rival holds a sweeper").startswith("Saved hypothesis M5: [source M3]"))
        self.assertEqual(seat.execute("MEMORY NOTE 5 again"), "ERROR: cite an observation or reveal, not a hypothesis.")
        old = tu.Seat(self.by_id["search-old-hand"], "obs")
        self.assertTrue(old.execute("MEMORY SEARCH 0 Rival's hand").startswith(
            "Matches=2 next=END (no literal match; entries holding every word)\n"))
        self.assertEqual(old.execute("MEMORY SEARCH 0 Rival's library"), "Matches=0 next=END\n")
        older = old.brief()
        self.assertNotIn("Pale Lantern", older)                          # pushed out of the 4-reveal brief


class MandatoryReasoningFloorTest(unittest.TestCase):
    """A model whose reasoning cannot be turned off refuses reasoning:{enabled:false}; the suite sent it on the fast
    tier and the truncation retry to every model, so those calls failed where the seat (OpenRouterBridge) asks
    its lowest effort instead. The suite sends the seat's reasoning field wherever "none" is sent, and its floored
    retry keeps its budget; its budgets themselves are its own, not the seat's."""

    def test_the_floor_table_is_the_seats(self):
        src = (SPIKE / "LlmSpikeGui.java").read_text()
        m = re.search(r"MANDATORY_REASONING_FLOOR\s*=\s*Map\.ofEntries\((.*?)\);", src, re.S)
        self.assertIsNotNone(m, "MANDATORY_REASONING_FLOOR not found in LlmSpikeGui.java")
        java = dict(re.findall(r'Map\.entry\("([^"]+)",\s*"([^"]*)"\)', m.group(1)))
        self.assertTrue(java)
        self.assertEqual(tu.MANDATORY_REASONING_FLOOR, java)

    def test_reasoning_off_becomes_the_lowest_effort_for_a_mandatory_reasoner(self):
        self.assertEqual(tu.request_body("deepseek/deepseek-v4.1-flash", "fast")["reasoning"], {"enabled": False})
        self.assertEqual(tu.request_body("x-ai/grok-4.7", "fast")["reasoning"], {"effort": "low"})
        self.assertEqual(tu.request_body("meta/muse-wonder", "fast")["reasoning"], {"effort": "minimal"})
        self.assertNotIn("reasoning", tu.request_body("x-ai/grok-build-0.1", "fast"), "lists no effort: no field")
        self.assertEqual(tu.request_body("x-ai/grok-4.7", "smart")["reasoning"], {"effort": "medium"})
        self.assertNotIn("reasoning", tu.request_body("x-ai/grok-build-0.1", "smart"), "no effort it does not list")

    def replies_to(self, model_id, replies, tier="smart"):
        sent = []

        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode()))
            resp = mock.MagicMock()
            resp.__enter__.return_value = io.BytesIO(json.dumps(replies[len(sent) - 1]).encode())
            return resp
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                mock.patch.object(tu.urllib.request, "urlopen", urlopen), mock.patch.object(tu.time, "sleep"):
            call = tu.openrouter_model(model_id, "http://127.0.0.1:9")
            out = call([{"role": "user", "content": "x"}], None, {"tier": tier})
        return sent, out

    def test_the_truncation_retry_keeps_a_mandatory_reasoner_at_its_floor_and_its_budget(self):
        empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        ok = {"choices": [{"message": {"content": "CHOICE: 1"}, "finish_reason": "stop"}]}
        # A budget above the 4096 clamp, or the floored budget and the clamped one are the same number.
        with mock.patch.dict(tu.TIER_CFG["smart"], max_tokens=8000):
            sent, out = self.replies_to("openai/gpt-6.1-sol", [empty, ok])
            self.assertEqual(out["content"], "CHOICE: 1")
            self.assertEqual([b["reasoning"] for b in sent], [{"effort": "medium"}, {"effort": "low"}])
            self.assertEqual([b["max_tokens"] for b in sent], [8000, 8000], "it still reasons: the whole budget")
            sent, _ = self.replies_to("deepseek/deepseek-v4.1-flash", [empty, ok])
            self.assertEqual(sent[1]["reasoning"], {"enabled": False})
            self.assertEqual(sent[1]["max_tokens"], 4096, "reasoning off: room for the answer only")
        sent, _ = self.replies_to("openai/gpt-6.1-sol", [empty, empty, ok])
        self.assertEqual(len(sent), 2, "retried once, not until the floor answers")

    def test_the_fast_tier_is_never_retried(self):
        # Its reasoning is already off (or at the floor), so a retry would resend the same request.
        empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        ok = {"choices": [{"message": {"content": "CHOICE: 1"}, "finish_reason": "stop"}]}
        for model_id in ("deepseek/deepseek-v4.1-flash", "openai/gpt-6.1-sol"):
            sent, out = self.replies_to(model_id, [empty, ok], tier="fast")
            self.assertEqual(len(sent), 1, model_id)
            self.assertFalse(out["content"], model_id)


if __name__ == "__main__":
    unittest.main()
