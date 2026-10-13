"""fake_model_server: the loopback stub the acceptance stages drive. Its native-tools policy decides
what the `nativetool` stage can prove, so the policy itself is pinned here."""
import argparse
import json
import unittest

import fake_model_server as fms

TOOLS = [{"type": "function", "function": {"name": n}} for n in ("inspect", "memory_search", "memory_read", "simulate")]


def policy(**kw):
    a = dict(policy="pass", log=None, stall_call=0, stall_ms=0, memory_call=0, native_calls=0, rules_call=0)
    a.update(kw)
    return fms.Policy(argparse.Namespace(**a))


def body(tools=TOOLS, choice="auto", extra=()):
    return {"tools": tools, "tool_choice": choice, "messages": [{"role": "user", "content": "x @41"}] + list(extra)}


class FakeModelServerTest(unittest.TestCase):
    def test_native_calls_cycle_search_inspect_and_one_malformed_call_then_stop(self):
        p = policy(native_calls=3)
        calls = [p.tool_calls_for(body(tools=TOOLS[:3]), "state @41 here")[0]["function"] for _ in range(3)]
        self.assertEqual([c["name"] for c in calls], ["memory_search", "inspect", "memory_read"])
        self.assertEqual(json.loads(calls[1]["arguments"]), {"target": "@41"})
        with self.assertRaises(ValueError):
            json.loads(calls[2]["arguments"])                       # deliberately not JSON
        self.assertIsNone(p.tool_calls_for(body(tools=TOOLS[:3]), "state"))   # the allowance is spent

    def test_no_call_without_tools_when_forbidden_or_after_a_tool_result(self):
        p = policy(native_calls=9)
        self.assertIsNone(p.tool_calls_for({"messages": []}, "x"))
        self.assertIsNone(p.tool_calls_for(body(choice="none"), "x"))
        self.assertIsNone(p.tool_calls_for(body(extra=[{"role": "tool", "content": "r"}]), "x"))

    def test_simulate_policy_previews_whenever_the_tool_is_offered(self):
        p = policy(policy="simulate")
        c = p.tool_calls_for(body(), "x")[0]["function"]
        self.assertEqual((c["name"], json.loads(c["arguments"])), ("simulate", {"option": 1}))
        self.assertIsNone(p.tool_calls_for(body(tools=TOOLS[:3]), "x"))       # not offered, no allowance

    def test_text_replies_are_always_a_legal_format(self):
        p = policy()
        self.assertEqual(p.reply_for("Keep? KEEP or MULLIGAN", 1), "KEEP")
        self.assertEqual(p.reply_for("Choose:\n  1) A\n  2) B\n  3) PASS\nCHOICE: <number>", 1), "CHOICE: 3")
        self.assertEqual(policy(policy="first").reply_for("  1) A\n  2) B\nCHOICE: <n>", 1), "CHOICE: 1")
        self.assertEqual(p.reply_for("PICKS: <numbers>", 1), "PICKS: none")


if __name__ == "__main__":
    unittest.main()
