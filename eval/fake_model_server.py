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
  simulate
         takes the first CHOICE (so it plays lands and casts spells) and otherwise declines like
         pass; on a play prompt that advertises `SIMULATE: <n>` (LOOKAHEAD=1), or offers the
         `simulate` tool, it first asks for a lookahead preview, so the real preview round-trip is
         exercised end to end.
  random takes a uniformly random legal answer on every answer-tool ask (needs --answer-tool): any option,
         any number of picks within the bounds, a random split, a random number in range, keep or not,
         random blocks (any pair the engine refuses is corrected once to no blocks). Seeded by --seed, so a
         game replays. The keyword gauntlet's second pass: the answers `first` never gives.
  empty  answers every request with an empty message.
  garbage
         answers every request with text that holds no answer line in any format the harness asks
         for. With `empty`, the harness-gaming check: a seat that never answers must not score
         above the floor seat (docs/eval/BENCHMARK-RESEARCH.md, point 10).

Fault injection:
  --stall-call N --stall-ms M   request number N (1-based, across all calls) is answered only
                                after M ms; set M above OPENROUTER_TIMEOUT_MS to force the
                                bridge's wall-clock deadline and the controller's fallback.
  --memory-call K               request K answers `MEMORY SEARCH 0 turn`, and request K+2
                                answers `MEMORY NOTE 1 ...`, so a MEMORY_TOOLS=1 game records
                                real context_tool rows. Ignored when the prompt shows no tools.
  --rules-call K                when nonzero, the first tool prompt that advertises the RULES
                                grammar is answered `RULES SEARCH 0 declare blockers`, so a real
                                RULES_TOOL=1 lookup round trip is recorded.

  --native-calls N              NATIVE_TOOLS=1 games: the first N requests that offer `tools` and may
                                call one are answered with a function call instead of text, cycling
                                memory_search, inspect (the first @handle in the prompt), and one
                                deliberately malformed call. With --policy simulate, a request that
                                offers the `simulate` tool is always answered by calling it once.

  --answer-tool                 a request that offers the `answer` function (AnswerTool, the default decision
                                protocol on a backend with function calling) is answered by calling it with
                                values read from the schema the harness sent: the policy picks among the
                                legal ones. A lookup (--native-calls, simulate) may come first in the same
                                conversation; `set_plan` rides along on the first three answers.
  --answer-invalid K            with --answer-tool: the K-th answer call is out of range once, so the
                                harness's same-conversation correction (one more round) is exercised.

  --refuse-tools                a provider with no function calling: any request carrying `tools` is
                                answered 404 "No endpoints found that support tool use." and logged
                                with refused_tools=true. Requests without tools are answered as usual.

Every request is appended to --log as one JSON line: call number, wall time, whether an
Authorization header was present (never its value), the model requested, prompt length,
the reply, and whether it was stalled. Prints `LISTENING <port>` on stdout once bound.
"""
import argparse
import json
import random
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
        self.rules_done = False
        self.native_sent = 0
        self.log = open(args.log, "a") if args.log else None

    def next_call(self):
        with self.lock:
            self.calls += 1
            return self.calls

    def tool_calls_for(self, body, prompt):
        """Function calls to answer a native-tools request with, or None to answer in text. One tool
        turn per ask: once the list holds a tool result, the answer is text."""
        offered = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        if not offered or body.get("tool_choice") == "none":
            return None

        def call(name, arguments):
            return {"id": "call_%s_%d" % (name, self.calls), "type": "function",
                    "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}
        msgs = body.get("messages") or []
        if getattr(self.args, "answer_tool", False) and "answer" in offered:
            # A lookup first while the ask still has none (as below), then the decision as a call.
            looked = any(m.get("role") == "tool" for m in msgs)
            if not looked:
                lookup = self.lookup_calls(body, prompt, offered, call)
                if lookup:
                    return lookup
            return self.answer_calls(body, msgs, offered, call)
        if any(m.get("role") == "tool" for m in msgs):
            return None
        return self.lookup_calls(body, prompt, offered, call)

    def answer_calls(self, body, msgs, offered, call):
        """The decision answered by calling `answer`, built from the schema the harness sent: the legal
        values only (the policy picks among them). With --answer-invalid K the K-th answer is out of range
        once, so the same-conversation correction runs; `set_plan` rides along on the first answers."""
        fn = next(t["function"] for t in body["tools"] if t.get("function", {}).get("name") == "answer")
        props = fn["parameters"]["properties"]
        props = {k: (v["anyOf"][0] if "anyOf" in v else v) for k, v in props.items()}   # strict: nullable optionals
        if {"option", "options", "keep"} <= set(props):   # the fixed schema names every field
            props = self.decision_props(msgs)              # so the legal values come off the question
        first = self.args.policy in ("first", "simulate")
        if self.args.policy == "random":
            return self.random_answer(msgs, props, offered, call)
        correcting = any(m.get("role") == "tool" and str(m.get("content", "")).startswith("ERROR:") for m in msgs[-3:])
        with self.lock:
            self.answers = getattr(self, "answers", 0) + 1
            n = self.answers
        args = {}
        if "option" in props:
            e = props["option"]["enum"]
            args["option"] = min(e) if first else max(e)
        elif "options" in props:
            arr = props["options"]
            e, lo = arr["items"]["enum"], arr.get("minItems", 0)
            hi = arr.get("maxItems", len(e))
            want = max(lo, 1 if first else 0)
            args["options"] = e[:min(want, hi, len(e))]
        elif "keep" in props:
            args["keep"] = True
        elif "blocks" in props:
            args["blocks"] = []
        elif "number" in props:
            args["number"] = props["number"].get("minimum", 0)
        elif "amounts" in props:
            k = props["amounts"].get("minItems", 1)
            total = int(re.search(r"summing to (-?\d+)", props["amounts"].get("description", "")).group(1))
            args["amounts"] = [total] + [0] * (k - 1)
        elif "card" in props:
            args["card"] = "Lightning Bolt"
        if "profile" in props:
            args["profile"] = "fake read: an unknown deck"
        if "lines" in props:
            args["lines"] = "fake lines: none known"
        with self.lock:
            invalid = self.args.answer_invalid and n == self.args.answer_invalid and not correcting
        if invalid:
            if "option" in args:
                args["option"] = 999
            elif "options" in args:
                args["options"] = [999]
            else:
                args = {"unknown_field": 1}
        out = [call("answer", args)]
        if "set_plan" in offered and n <= 3 and not correcting:
            out.insert(0, call("set_plan", {"goal": "fake goal %d" % n, "next": "play a land, then pass"}))
        return out

    def random_lookup(self, last_user, prompt, offered, play_menu, call):
        """--policy random: now and then a lookup before the answer, with random arguments: a preview of any menu
        option (sometimes a line of two) or of any target of a play, an inspect of any handle on the state, a memory search. None otherwise."""
        with self.lock:
            self.lookups = getattr(self, "lookups", 0) + 1
            n = self.lookups
        rng = random.Random("%s/lookup/%d" % (self.args.seed, n))
        if "simulate" in offered and play_menu and rng.random() < 0.35:
            nums = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\) ", last_user)]
            if nums:
                args = {"option": rng.choice(nums)}
                rest = [x for x in nums if x != args["option"]]
                if rest and rng.random() < 0.3:
                    args["then"] = [rng.choice(rest)]
                return [call("simulate", args)]
        # A target choice of a play is previewed too (lookahead-preview-v10): any target, or the CANCEL line.
        if "simulate" in offered and "Choose the TARGET" in last_user and rng.random() < 0.35:
            nums = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\) ", last_user)]
            if nums:
                return [call("simulate", {"option": rng.choice(nums)})]
        handles = re.findall(r"@\d+", prompt)
        if "inspect" in offered and handles and rng.random() < 0.1:
            return [call("inspect", {"target": rng.choice(handles)})]
        if "memory_search" in offered and rng.random() < 0.05:
            return [call("memory_search", {"query": rng.choice(["attack", "graveyard", "counter", "life", "draw"])})]
        return None

    def random_answer(self, msgs, props, offered, call):
        """A uniformly random legal answer (--policy random). The draw is keyed on the seed and the answer
        count, so a game replays; a correction round answers with the least that is always legal."""
        with self.lock:
            self.answers = getattr(self, "answers", 0) + 1
            n = self.answers
        rng = random.Random("%s/%d" % (self.args.seed, n))
        correcting = any(m.get("role") == "tool" and str(m.get("content", "")).startswith("ERROR:") for m in msgs[-3:])
        args = {}
        if "option" in props:
            args["option"] = rng.choice(props["option"]["enum"])
        elif "options" in props:
            arr = props["options"]
            e, lo = arr["items"]["enum"], arr.get("minItems", 0)
            hi = min(arr.get("maxItems", len(e)), len(e))
            k = lo if correcting else rng.randint(lo, max(lo, hi))
            args["options"] = rng.sample(e, k) if k <= len(e) else [rng.choice(e) for _ in range(k)]
        elif "keep" in props:
            args["keep"] = True if correcting else rng.random() < 0.7
        elif "blocks" in props:
            text = next((str(m.get("content", "")) for m in reversed(msgs) if m.get("role") == "user"), "")
            count = lambda tag: len(re.findall(r"(?:^|; )\d+ = ", (re.search(r"(?m)^%s: (.*)$" % tag, text) or [None, ""])[1]))  # noqa: E731
            atk, blk = count("ATTACKERS"), count("YOUR BLOCKERS")
            args["blocks"] = [] if correcting or not atk else [
                {"blocker": b, "attacker": rng.randint(1, atk)} for b in range(1, blk + 1) if rng.random() < 0.5]
        elif "number" in props:
            lo, hi = props["number"].get("minimum", 0), props["number"].get("maximum")
            args["number"] = lo if correcting or hi is None else rng.randint(lo, hi)
        elif "amounts" in props:
            k = props["amounts"].get("minItems", 1)
            total = int(re.search(r"summing to (-?\d+)", props["amounts"].get("description", "")).group(1))
            if total > 0 and k > 1 and not correcting:
                cuts = sorted(rng.randint(0, total) for _ in range(k - 1))
                args["amounts"] = [b - a for a, b in zip([0] + cuts, cuts + [total])]
            else:
                args["amounts"] = [total] + [0] * (k - 1)
        elif "card" in props:
            args["card"] = rng.choice(["Lightning Bolt", "Force of Will", "Island", "Tarmogoyf"])
        out = [call("answer", args)]
        if "set_plan" in offered and n <= 3 and not correcting:
            out.insert(0, call("set_plan", {"goal": "fake goal %d" % n, "next": "anything"}))
        return out

    @staticmethod
    def decision_props(msgs):
        """Under the fixed `answer` schema the legal values are in the question, as for a real model: the
        newest user message that says which field answers it, its numbered options and its bounds. Returned
        in the per-decision schema's shape so one picker serves both."""
        text = ""
        for m in reversed(msgs):
            c = str(m.get("content", "")) if m.get("role") == "user" else ""
            if "call `answer` with `" in c:
                text = c
                break
        field = re.search(r"call `answer` with `(\w+)`", text)
        field = field.group(1) if field else "option"
        nums = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\) ", text)] or [1]
        e = sorted(set(nums))
        if field == "option":
            return {"option": {"enum": e}}
        if field == "options":
            b = re.search(r"`options`: the numbers you pick, (?:exactly (\d+)|between (\d+) and (\d+))", text)
            lo = int(b.group(1) or b.group(2)) if b else 0
            hi = int(b.group(1) or b.group(3)) if b else len(e)
            return {"options": {"items": {"enum": e}, "minItems": lo, "maxItems": hi}}
        if field == "amounts":
            b = re.search(r"`amounts`: (\d+) numbers in the order listed, summing to (-?\d+)", text)
            k, total = (int(b.group(1)), b.group(2)) if b else (1, "0")
            return {"amounts": {"minItems": k, "description": "summing to %s" % total}}
        if field == "number":
            b = re.search(r"`number`: an integer from (-?\d+)(?: through (-?\d+))?", text)
            out = {"minimum": int(b.group(1)) if b else 0}
            if b and b.group(2):
                out["maximum"] = int(b.group(2))
            return {"number": out}
        return {field: {}}

    def lookup_calls(self, body, prompt, offered, call):
        # `simulate` is declared on every ask of a lookahead seat (the tool list is the cache prefix), so
        # preview only what a model would: an option of a play decision, whose menu ends with PASS.
        last_user = next((str(m.get("content", "")) for m in reversed(body.get("messages") or [])
                          if m.get("role") == "user"), "")
        play_menu = re.search(r"(?m)^\s*\d+\) PASS\b", last_user) is not None
        if self.args.policy == "random":
            return self.random_lookup(last_user, prompt, offered, play_menu, call)
        if self.args.policy == "simulate" and "simulate" in offered and play_menu:
            with self.lock:
                self.previews = getattr(self, "previews", 0) + 1
                n = self.previews
            # Every third preview asks for the PASS option (the last menu line), so the PASS path runs
            # in the acceptance game: it must come back "if you pass", not refused. Every other preview
            # asks for a LINE (option 1, then option 2), so the line path runs in the acceptance game:
            # both plays when there is mana for both, "could not be paid for" when there is not, and an
            # error when the decision has only one such option.
            if n % 3 == 0:
                m = re.search(r"(?m)^\s*(\d+)\) PASS\b", prompt)
                if m:
                    return [call("simulate", {"option": int(m.group(1))})]
            return [call("simulate", {"option": 1, "then": [2]} if n % 2 == 0 else {"option": 1})]
        if "memory_search" not in offered:
            return None
        with self.lock:
            if self.native_sent >= self.args.native_calls:
                return None
            k = self.native_sent
            self.native_sent += 1
        if k % 3 == 0:
            return [call("memory_search", {"query": "turn"})]
        if k % 3 == 1 and "inspect" in offered:
            handle = re.search(r"@\d+", prompt)
            return [call("inspect", {"target": handle.group(0) if handle else "Mountain"})]
        return [call("memory_read", "{not json")]                # refused by the harness, costs no budget

    GARBAGE = "Purple elephants dance at dawn; the answer is banana. CHOICE: banana\nPICKS: everything\nBLOCKS: the moon"

    def reply_for(self, prompt, call):
        a = self.args
        if a.policy == "empty":
            return ""
        if a.policy == "garbage":
            return self.GARBAGE
        # LOOKAHEAD validation: on a play prompt that advertises SIMULATE and has not yet been given a
        # preview, request one. The re-ask carries "LOOKAHEAD (option ...", so this fires once per
        # decision and then falls through to a normal CHOICE.
        if a.policy == "simulate" and "SIMULATE:" in prompt and "LOOKAHEAD (option" not in prompt:
            return "SIMULATE: 1"
        tools_offered = "MEMORY TOOLS" in prompt and "Tools available this ask: 0" not in prompt
        if a.memory_call and tools_offered:
            if call == a.memory_call:
                return "MEMORY SEARCH 0 turn"
            if call == a.memory_call + 2:
                return "MEMORY NOTE 1 fake-model hypothesis: opponent is on a slow deck (cites M1)."
        # RULES tool: fire exactly once, on the first tool prompt that advertises the grammar, so the
        # real RulesTool lookup round trip is exercised (robust to call numbering).
        if a.rules_call and tools_offered and "RULES TOOL" in prompt:
            with self.lock:
                if not self.rules_done:
                    self.rules_done = True
                    fire = True
                else:
                    fire = False
            if fire:
                return "RULES SEARCH 0 declare blockers"
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
            # simulate develops its board (first option: a land, then a spell) so that there are
            # options to preview. It used to pass everything and only ever played a land because the
            # harness's retry prompt had no menu in it, which made the stub answer "CHOICE: 1".
            return "CHOICE: %d" % (min(nums) if a.policy in ("first", "simulate") else max(nums))
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
            # Tool results are quoted data: they must not be mistaken for the decision's format.
            prompt = "\n".join(str(m.get("content", "")) for m in msgs if m.get("role") not in ("system", "tool", "assistant"))
            entry["tools_offered"] = [t.get("function", {}).get("name") for t in body.get("tools") or []]
            entry["tool_choice"] = body.get("tool_choice")
            entry["tool_results"] = sum(1 for m in msgs if m.get("role") == "tool")
            # Is the list a valid tool-call sequence (every call answered by its result before anything
            # else), and how much earlier conversation does it carry? (TURN_CHAT / ONE_CHAT checks)
            pending, seq = set(), []
            for m in msgs:
                role = m.get("role")
                if role == "tool":
                    pending.discard(m.get("tool_call_id"))
                    continue
                if pending:
                    seq.append("%s message before the results of %s" % (role, sorted(pending)))
                    pending = set()
                if role == "assistant":
                    pending = {tc.get("id") for tc in m.get("tool_calls") or []}
            if pending:
                seq.append("no result for %s" % sorted(pending))
            entry["sequence_problems"] = seq
            entry["assistant_messages"] = sum(1 for m in msgs if m.get("role") == "assistant")
            entry["assistant_text_only"] = sum(1 for m in msgs if m.get("role") == "assistant" and not m.get("tool_calls"))
            entry["earlier_turn_note"] = any("your own decisions earlier this turn" in str(m.get("content", "")) for m in msgs)
            entry["model"] = body.get("model")
            entry["prompt_chars"] = len(prompt)
            entry["max_tokens"] = body.get("max_tokens")
        except Exception as e:  # noqa: BLE001
            entry["error"] = "bad request: %s" % e
            p.record(entry)
            self._send(400, {"error": {"message": str(e)}})
            return
        if getattr(p.args, "refuse_tools", False) and body.get("tools"):
            entry["refused_tools"] = True
            entry["reply"] = None
            p.record(entry)
            self._send(404, {"error": {"message": "No endpoints found that support tool use.", "code": 404}})
            return
        stalled = p.args.stall_call and call == p.args.stall_call
        if stalled:
            time.sleep(p.args.stall_ms / 1000.0)
        tool_calls = p.tool_calls_for(body, prompt)
        reply = None if tool_calls else p.reply_for(prompt, call)
        entry["reply"] = reply
        entry["tool_calls"] = [c["function"]["name"] for c in tool_calls or []]
        entry["stalled"] = bool(stalled)
        p.record(entry)
        if tool_calls:
            self._send(200, {
                "id": "fake-%d" % call, "object": "chat.completion", "model": SERVED_MODEL,
                "choices": [{"index": 0, "finish_reason": "tool_calls",
                             "message": {"role": "assistant", "content": None, "tool_calls": tool_calls}}],
                "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": 8, "total_tokens": max(1, len(prompt) // 4) + 8},
            })
            return
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
            # Said, not implied: the server closes after each reply, and a client that reused the socket got "header
            # parser received no bytes" on the next request (13 times in the keyword gauntlet with lookups on).
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the bridge gave up (deadline); that is the scenario under test


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port (printed as LISTENING <port>)")
    ap.add_argument("--policy", choices=["pass", "first", "simulate", "random", "empty", "garbage"], default="pass")
    ap.add_argument("--log", help="JSONL request log")
    ap.add_argument("--seed", default="0", help="--policy random: the seed its draws are keyed on")
    ap.add_argument("--stall-call", type=int, default=0)
    ap.add_argument("--stall-ms", type=int, default=0)
    ap.add_argument("--memory-call", type=int, default=0)
    ap.add_argument("--native-calls", type=int, default=0,
                    help="answer the first N tool-offering requests with a function call (NATIVE_TOOLS=1 games)")
    ap.add_argument("--answer-tool", action="store_true",
                    help="answer every request that offers the `answer` function by calling it (ANSWER_TOOL games)")
    ap.add_argument("--answer-invalid", type=int, default=0,
                    help="with --answer-tool: the K-th answer call is out of range once, so the harness's "
                         "same-conversation correction runs")
    ap.add_argument("--refuse-tools", action="store_true",
                    help="answer 404 to any request that carries `tools` (a provider without function calling)")
    ap.add_argument("--rules-call", type=int, default=0,
                    help="when nonzero, the first tool prompt advertising the RULES grammar is answered "
                         "with `RULES SEARCH 0 declare blockers`, so a real rules lookup is exercised")
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
