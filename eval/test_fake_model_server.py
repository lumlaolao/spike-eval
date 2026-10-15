"""fake_model_server: the loopback stub the acceptance stages drive. Its native-tools policy decides
what the `nativetool` stage can prove, so the policy itself is pinned here."""
import argparse
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import fake_model_server as fms

TOOLS = [{"type": "function", "function": {"name": n}} for n in ("inspect", "memory_search", "memory_read", "simulate")]


def policy(**kw):
    a = dict(policy="pass", log=None, stall_call=0, stall_ms=0, memory_call=0, native_calls=0, rules_call=0,
             refuse_tools=False)
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
        second = p.tool_calls_for(body(), "x")[0]["function"]
        self.assertEqual(json.loads(second["arguments"]), {"option": 1, "then": [2]})      # every other one is a line
        # ... and every third one asks for PASS, whose menu number is read off the prompt, so the
        # acceptance game exercises the PASS preview (served, not refused).
        third = p.tool_calls_for(body(), "Choose ONE action:\n  1) CAST X\n  2) PLAY LAND\n  3) PASS (do nothing)")[0]["function"]
        self.assertEqual(json.loads(third["arguments"]), {"option": 3})
        self.assertIsNone(p.tool_calls_for(body(tools=TOOLS[:3]), "x"))       # not offered, no allowance

    def test_text_replies_are_always_a_legal_format(self):
        p = policy()
        self.assertEqual(p.reply_for("Keep? KEEP or MULLIGAN", 1), "KEEP")
        self.assertEqual(p.reply_for("Choose:\n  1) A\n  2) B\n  3) PASS\nCHOICE: <number>", 1), "CHOICE: 3")
        self.assertEqual(policy(policy="first").reply_for("  1) A\n  2) B\nCHOICE: <n>", 1), "CHOICE: 1")
        self.assertEqual(policy(policy="simulate").reply_for("  1) PLAY LAND\n  2) PASS\nCHOICE: <n>", 1), "CHOICE: 1")
        self.assertEqual(p.reply_for("PICKS: <numbers>", 1), "PICKS: none")

    def test_the_gaming_policies_never_answer(self):
        self.assertEqual(policy(policy="empty").reply_for("  1) A\n  2) B\nCHOICE: <n>", 1), "")
        g = policy(policy="garbage").reply_for("  1) A\n  2) B\nCHOICE: <n>", 1)
        self.assertNotRegex(g, r"CHOICE:\s*\d|PICKS:\s*(\d|none)|BLOCKS:\s*none|\bKEEP\b")


class RefuseToolsTest(unittest.TestCase):
    """--refuse-tools over real HTTP: the 404 wording is the one OpenRouterBridge.refusesTools reads."""

    def post(self, port, body):
        req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_request_with_tools_is_refused_and_one_without_is_answered(self):
        fms.Handler.policy = policy(refuse_tools=True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), fms.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            status, out = self.post(port, body())
            self.assertEqual(status, 404)
            self.assertIn("support tool use", out["error"]["message"])
            status, out = self.post(port, {"messages": [{"role": "user", "content": "  1) A\n  2) PASS\nCHOICE: <n>"}]})
            self.assertEqual((status, out["choices"][0]["message"]["content"]), (200, "CHOICE: 2"))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
