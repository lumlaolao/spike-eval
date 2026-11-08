"""fake_model_server: the loopback stub the acceptance stages drive. Its native-tools policy decides
what the `nativetool` stage can prove, so the policy itself is pinned here."""
import argparse
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import fake_model_server as fms

TOOLS = [{"type": "function", "function": {"name": n}} for n in ("inspect", "memory_search", "memory_read", "simulate")]


def policy(**kw):
    a = dict(policy="pass", log=None, stall_call=0, stall_ms=0, memory_call=0, native_calls=0, rules_call=0,
             refuse_tools=False, http_error=0)
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

    def test_simulate_policy_previews_the_options_of_a_play_decision(self):
        """`simulate` is declared on every ask of a lookahead seat (the tool list opens the cache prefix),
        so the fake model previews only where a model would: a play decision, whose menu ends with PASS."""
        menu = "Choose ONE action:\n  1) CAST X\n  2) PLAY LAND\n  3) PASS (do nothing)"
        play = {"tools": TOOLS, "tool_choice": "auto", "messages": [{"role": "user", "content": menu}]}
        p = policy(policy="simulate")
        c = p.tool_calls_for(play, menu)[0]["function"]
        self.assertEqual((c["name"], json.loads(c["arguments"])), ("simulate", {"option": 1}))
        second = p.tool_calls_for(play, menu)[0]["function"]
        self.assertEqual(json.loads(second["arguments"]), {"option": 1, "then": [2]})      # every other one is a line
        # ... and every third one asks for PASS, whose menu number is read off the prompt, so the
        # acceptance game exercises the PASS preview (served, not refused).
        third = p.tool_calls_for(play, menu)[0]["function"]
        self.assertEqual(json.loads(third["arguments"]), {"option": 3})
        self.assertIsNone(p.tool_calls_for(body(), "x"))                      # no play menu: no preview
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


    def test_the_random_policy_answers_within_every_bound_and_replays_by_seed(self):
        def ask(p, content):
            msgs = [{"role": "user", "content": content}]
            props = fms.Policy.decision_props(msgs)
            return json.loads(p.random_answer(msgs, props, set(), lambda n, a: {"name": n, "arguments": json.dumps(a)})[0]["arguments"])
        menu = "call `answer` with `option`.\n  1) CAST X\n  2) CAST Y\n  3) PASS"
        picks = "call `answer` with `options`.\n`options`: the numbers you pick, between 1 and 2\n  1) a\n  2) b\n  3) c"
        split = "call `answer` with `amounts`.\n`amounts`: 3 numbers in the order listed, summing to 5"
        num = "call `answer` with `number`.\n`number`: an integer from 2 through 6"
        blocks = "call `answer` with `blocks`.\nATTACKERS: 1 = Bear; 2 = Elf\nYOUR BLOCKERS: 1 = Wall; 2 = Ape; 3 = Rat"
        p = policy(policy="random", seed="kw03")
        seen = set()
        for _ in range(40):
            seen.add(ask(p, menu)["option"])
            o = ask(p, picks)["options"]
            self.assertTrue(1 <= len(o) <= 2 and set(o) <= {1, 2, 3} and len(set(o)) == len(o))
            a = ask(p, split)["amounts"]
            self.assertEqual((len(a), sum(a), min(a) >= 0), (3, 5, True))
            self.assertTrue(2 <= ask(p, num)["number"] <= 6)
            for b in ask(p, blocks)["blocks"]:
                self.assertTrue(1 <= b["blocker"] <= 3 and 1 <= b["attacker"] <= 2)
        self.assertEqual(seen, {1, 2, 3}, "every option is drawn")
        run = lambda seed: [ask(q, menu)["option"] for q in [policy(policy="random", seed=seed)] for _ in range(12)]  # noqa: E731
        self.assertEqual(run("kw03"), run("kw03"), "a seed replays")
        self.assertNotEqual(run("kw03"), run("kw04"))


    def test_the_random_policy_looks_up_now_and_then_with_legal_arguments(self):
        menu = "Choose ONE action:\n  1) CAST X @12\n  2) PLAY LAND Y @13\n  3) PASS (do nothing)"
        play = {"tools": TOOLS + [{"type": "function", "function": {"name": "answer"}}], "tool_choice": "auto",
                "messages": [{"role": "user", "content": menu}]}
        p = policy(policy="random", seed="kw01")
        seen = set()
        for _ in range(60):
            out = p.lookup_calls(play, menu, ["inspect", "memory_search", "simulate"], lambda n, a: {"name": n, "arguments": a})
            if out:
                c = out[0]
                seen.add(c["name"])
                if c["name"] == "simulate":
                    self.assertIn(c["arguments"]["option"], (1, 2, 3))
                if c["name"] == "inspect":
                    self.assertIn(c["arguments"]["target"], ("@12", "@13"))
        self.assertTrue({"simulate", "inspect"} <= seen, seen)
        # A target choice of a play is previewed too, by a target's number.
        ask = "Choose the TARGET for Lightning Bolt:\n  1) Rival\n  2) Grizzly Bears @9\n  3) CANCEL: go back"
        tgt = {"tools": play["tools"], "tool_choice": "auto", "messages": [{"role": "user", "content": ask}]}
        targets = [c[0]["arguments"]["option"] for c in
                   (p.lookup_calls(tgt, ask, ["simulate"], lambda n, a: {"name": n, "arguments": a}) for _ in range(40)) if c]
        self.assertTrue(targets and set(targets) <= {1, 2, 3}, targets)


def post(port, body):
    """POST one chat completion to the stub; (status, parsed body), an HTTP error included."""
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def serve(p):
    """The stub on a free port with policy `p`; the caller shuts it down."""
    fms.Handler.policy = p
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fms.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class RefuseToolsTest(unittest.TestCase):
    """--refuse-tools over real HTTP: the 404 wording is the one OpenRouterBridge.refusesTools reads."""

    def test_a_request_with_tools_is_refused_and_one_without_is_answered(self):
        srv = serve(policy(refuse_tools=True))
        try:
            port = srv.server_address[1]
            status, out = post(port, body())
            self.assertEqual(status, 404)
            self.assertIn("support tool use", out["error"]["message"])
            status, out = post(port, {"messages": [{"role": "user", "content": "  1) A\n  2) PASS\nCHOICE: <n>"}]})
            self.assertEqual((status, out["choices"][0]["message"]["content"]), (200, "CHOICE: 2"))
        finally:
            srv.shutdown()
            srv.server_close()


class HttpErrorTest(unittest.TestCase):
    """--http-error 402: the provider refuses every call the way OpenRouter does when the credits run out
    (issue #369), so a zero-cost game shows what a dead bridge leaves on the game line."""

    def test_an_out_of_credits_provider_refuses_every_request_with_402(self):
        srv = serve(policy(http_error=402))
        try:
            port = srv.server_address[1]
            for b in (body(), {"messages": [{"role": "user", "content": "  1) A\n  2) PASS\nCHOICE: <n>"}]}):
                status, out = post(port, b)
                self.assertEqual(status, 402)
                self.assertIn("exceed your available credits", out["error"]["message"])
        finally:
            srv.shutdown()
            srv.server_close()

if __name__ == "__main__":
    unittest.main()
