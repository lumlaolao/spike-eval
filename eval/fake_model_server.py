#!/usr/bin/env python3
"""Loopback OpenAI-compatible stub model for harness acceptance runs. Stdlib only.

Speaks just enough of /v1/chat/completions for OpenRouterBridge: it reads the last user
message, recognises which decision format the controller asked for, and answers with a
deterministic, always-legal line. It exists to prove the REAL model transport, parsing,
memory-tool and timeout-recovery paths end to end without a paid call or any egress.
The bridge exempts loopback endpoints from the egress-consent gate and sends no API key
to them, and this server asserts both (it refuses any request carrying Authorization).

Policies (--policy):
  pass   decline everything: KEEP, no attackers/blocks, the highest-numbered CHOICE (PASS /
         decline is always rendered last), PICKS: none. The game runs to decking like the
         floor arm, so every priority window is a model round trip.
  first  take the first option everywhere (casts spells, attacks, picks first-N).

Fault injection:
  --stall-call N --stall-ms M   request number N (1-based, across all calls) is answered only
                                after M ms; set M above OPENROUTER_TIMEOUT_MS to force the
                                bridge's wall-clock deadline and the controller's fallback.
  --memory-call K               request K answers `MEMORY SEARCH 0 turn`, and request K+2
                                answers `MEMORY NOTE 1 ...`, so a MEMORY_TOOLS=1 game records
                                real context_tool rows. Ignored when the prompt shows no tools.

Every request is appended to --log as one JSON line: call number, wall time, whether an
Authorization header was present (never its value), the model requested, prompt length,
the reply, and whether it was stalled. Prints `LISTENING <port>` on stdout once bound.
"""
import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVED_MODEL = "fake/harness-model"
OPTION_LINE = re.compile(r"^\s*(\d+)[.)]\s", re.M)


class Policy:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.calls = 0
        self.log = open(args.log, "a") if args.log else None

    def next_call(self):
        with self.lock:
            self.calls += 1
            return self.calls

    def reply_for(self, prompt, call):
        a = self.args
        tools_offered = "MEMORY TOOLS" in prompt and "Tools available this ask: 0" not in prompt
        if a.memory_call and tools_offered:
            if call == a.memory_call:
                return "MEMORY SEARCH 0 turn"
            if call == a.memory_call + 2:
                return "MEMORY NOTE 1 fake-model hypothesis: opponent is on a slow deck (cites M1)."
        if "KEEP or MULLIGAN" in prompt or ("KEEP" in prompt and "MULLIGAN" in prompt and "CHOICE:" not in prompt):
            return "KEEP"
        if "BLOCKS:" in prompt:
            return "BLOCKS: none"
        if "PICKS:" in prompt:
            if a.policy == "first":
                return "PICKS: 1"
            return "PICKS: none"
        if "CHOICE:" in prompt or "CHOICE" in prompt:
            nums = [int(m.group(1)) for m in OPTION_LINE.finditer(prompt)]
            if not nums:
                return "CHOICE: 1"
            return "CHOICE: %d" % (min(nums) if a.policy == "first" else max(nums))
        if "PLAN:" in prompt:
            return "PLAN: pass and see what happens\nCHOICE: 1"
        return "CHOICE: 1"

    def record(self, entry):
        if self.log:
            with self.lock:
                self.log.write(json.dumps(entry, sort_keys=True) + "\n")
                self.log.flush()


class Handler(BaseHTTPRequestHandler):
    policy = None  # set by main()

    def log_message(self, fmt, *args):  # quiet
        pass

    def do_POST(self):
        p = self.policy
        call = p.next_call()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        has_auth = self.headers.get("Authorization") is not None
        entry = {"call": call, "t": time.time(), "path": self.path, "has_authorization": has_auth}
        if has_auth:
            # A loopback endpoint must never receive the operator's key. Refuse loudly.
            entry["reply"] = None
            entry["error"] = "authorization header present on loopback request"
            p.record(entry)
            self._send(401, {"error": {"message": "loopback stub refuses Authorization headers"}})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            msgs = body.get("messages") or []
            prompt = "\n".join(str(m.get("content", "")) for m in msgs if m.get("role") != "system")
            entry["model"] = body.get("model")
            entry["prompt_chars"] = len(prompt)
            entry["max_tokens"] = body.get("max_tokens")
        except Exception as e:  # noqa: BLE001
            entry["error"] = "bad request: %s" % e
            p.record(entry)
            self._send(400, {"error": {"message": str(e)}})
            return
        stalled = p.args.stall_call and call == p.args.stall_call
        if stalled:
            time.sleep(p.args.stall_ms / 1000.0)
        reply = p.reply_for(prompt, call)
        entry["reply"] = reply
        entry["stalled"] = bool(stalled)
        p.record(entry)
        usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(reply) // 4)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        self._send(200, {
            "id": "fake-%d" % call,
            "object": "chat.completion",
            "model": SERVED_MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": usage,
        })

    def _send(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the bridge gave up (deadline); that is the scenario under test


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port (printed as LISTENING <port>)")
    ap.add_argument("--policy", choices=["pass", "first"], default="pass")
    ap.add_argument("--log", help="JSONL request log")
    ap.add_argument("--stall-call", type=int, default=0)
    ap.add_argument("--stall-ms", type=int, default=0)
    ap.add_argument("--memory-call", type=int, default=0)
    args = ap.parse_args()
    Handler.policy = Policy(args)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print("LISTENING %d" % srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
