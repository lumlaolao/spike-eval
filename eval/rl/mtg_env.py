#!/usr/bin/env python3
"""MtgEnv: the Forge harness as a reset/step environment for RL (stdlib only; docs in eval/rl/README.md).

A loopback OpenAI-compatible server parks every chat-completions request of the game JVM; the request is the
observation, the `answer` tool call written back is the action, and the JVM's exit is the end of the episode.
The reward is the engine's own verdict: a puzzle result row (solved = 1) or the game line (identity A wins = 1).

    env = MtgEnv()
    obs = env.reset(puzzle="eval/puzzles/hard/gen/drain_08bba3eb8d53.pzl", seed=1)
    done = False
    while not done:
        obs_next, reward, done, info = env.step(policy(obs))
        obs = obs_next
    env.close()

No model is called and nothing leaves the machine: the server binds 127.0.0.1, the JVM is pointed at it, and
OPENROUTER_ALLOW_EGRESS / OPENROUTER_API_KEY are removed from the JVM's environment.
"""
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # eval/
import evalpath  # noqa: E402,F401
import fake_model_server  # noqa: E402  (its parser of the legal options is the one the env uses)

MODEL_SPEC = "openrouter:rl/policy"  # loopback: exempt from the egress gate, never sent a key
OPTION = re.compile(r"(?m)^\s*(\d+)\) (.*)$")
ASK_MARK = "call `answer` with `"
# Settings that make every request a decision: no memory or rules lookups, no lookahead, no heuristics. NATIVE_TOOLS
# stays 1: it carries the `answer` function (0 is the text lane, which this env cannot answer); the env only ever
# replies with `answer`, so the lookup functions it also offers are never called.
DECISION_ONLY = {"CONTEXT_PROFILE": "v4-faithful", "MEMORY_TOOLS": "0", "RULES_TOOL": "0", "NATIVE_TOOLS": "1",
                 "ANSWER_TOOL": "1", "LOOKAHEAD": "0", "MULLIGAN_HEURISTIC": "0", "SKIP_HEURISTICS": "0"}
ADD_OPENS = [
    "java.desktop/java.beans", "java.desktop/javax.swing.border", "java.desktop/javax.swing.event",
    "java.desktop/sun.swing", "java.desktop/java.awt.image", "java.desktop/java.awt.color",
    "java.desktop/sun.awt.image", "java.desktop/javax.swing", "java.desktop/java.awt",
    "java.desktop/java.awt.font", "java.base/java.util", "java.base/java.lang",
    "java.base/java.lang.reflect", "java.base/java.text", "java.base/jdk.internal.misc",
    "java.base/sun.nio.ch", "java.base/java.nio", "java.base/java.math",
    "java.base/java.util.concurrent", "java.base/java.net",
]


class EnvError(RuntimeError):
    pass


def messages_key(messages):
    """The identity of a request: a retried ask repeats the same messages array byte for byte."""
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()


def parse_ask(messages):
    """The legal answer set of a request, read with the fake server's parser: the field the question wants,
    its numbered options (number and text) and its bounds. {} for a request that asks nothing."""
    text = next((str(m.get("content", "")) for m in reversed(messages)
                 if m.get("role") == "user" and ASK_MARK in str(m.get("content", ""))), "")
    if not text:
        return {}
    props = fake_model_server.Policy.decision_props(messages)
    field = next(iter(props))
    spec = props[field]
    legal = {"field": field}
    texts = {int(n): t.strip() for n, t in OPTION.findall(text)}
    if field in ("option", "options"):
        enum = spec["enum"] if field == "option" else spec["items"]["enum"]
        legal["options"] = [{"n": n, "text": texts.get(n, "")} for n in enum]
    if field == "options":
        legal["min"], legal["max"] = spec.get("minItems", 0), spec.get("maxItems", len(legal["options"]))
    elif field == "number":
        legal["min"], legal["max"] = spec.get("minimum", 0), spec.get("maximum")
    elif field == "amounts":
        legal["count"] = spec.get("minItems", 1)
        legal["total"] = int(re.search(r"summing to (-?\d+)", spec["description"]).group(1))
    return legal


def answer_schema(tools):
    """The `answer` function's parameter schema as the harness sent it, or None."""
    for t in tools or []:
        fn = t.get("function", {})
        if fn.get("name") == "answer":
            return fn.get("parameters")
    return None


def as_arguments(action, legal=None):
    """An action as the `answer` call's arguments: a dict as is, an int as {option} (or {number}), a list as
    {options} (or {amounts}), a bool as {keep}, a str as {card}."""
    field = (legal or {}).get("field")
    if isinstance(action, dict):
        return dict(action)
    if isinstance(action, bool):
        return {"keep": action}
    if isinstance(action, int):
        return {"number": action} if field == "number" else {"option": action}
    if isinstance(action, (list, tuple)):
        return {"amounts": list(action)} if field == "amounts" else {"options": list(action)}
    if isinstance(action, str):
        return {"card": action}
    raise EnvError("unsupported action %r" % (action,))


class _Pending:
    """One parked decision: the request, and the handlers (original plus retries) waiting on its answer."""

    def __init__(self, key, body, index):
        self.key, self.body, self.index = key, body, index
        self.reply = None
        self.event = threading.Event()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.lock = threading.Lock()
        self.events = queue.Queue()  # ("obs", _Pending) | ("exit", rc)
        self.current = None          # the unanswered _Pending
        self.last = None             # the answered _Pending (a retry after the answer gets it again)
        self.count = 0               # distinct decisions seen
        self.requests = 0            # every request, retries included
        self.deduped = 0
        self.closing = False

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        srv = self.server
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Authorization") is not None:  # a loopback endpoint is never sent a key
            return self._send(401, {"error": {"message": "loopback env refuses Authorization headers"}})
        try:
            body = json.loads(raw.decode("utf-8"))
            key = messages_key(body.get("messages") or [])
        except (ValueError, UnicodeDecodeError) as e:
            return self._send(400, {"error": {"message": "bad request: %s" % e}})
        with srv.lock:
            srv.requests += 1
            if srv.closing:
                return self._send(503, {"error": {"message": "env closed"}})
            if srv.current is not None and srv.current.key == key:   # the bridge re-asked an unanswered ask
                srv.deduped += 1
                pending = srv.current
            elif srv.last is not None and srv.last.key == key:       # re-asked after the answer was written
                srv.deduped += 1
                pending = srv.last
            else:
                srv.count += 1
                pending = srv.current = _Pending(key, body, srv.count)
                srv.events.put(("obs", pending))
        pending.event.wait()
        if pending.reply is None:
            return self._send(503, {"error": {"message": "env closed"}})
        self._send(200, pending.reply)

    def _send(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a superseded connection: the bridge's retry carries the answer


def chat_reply(arguments, index):
    call = {"id": "call_answer_%d" % index, "type": "function",
            "function": {"name": "answer", "arguments": json.dumps(arguments)}}
    return {"id": "rl-%d" % index, "object": "chat.completion", "model": "rl/policy",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class JvmLauncher:
    """Starts LlmSpikeHeadless from a built Forge checkout (nothing is built or installed)."""

    def __init__(self, forge_home=None, classpath_file=None, java=None, xmx="1536m"):
        self.forge = Path(os.path.expandvars(forge_home or os.environ.get("FORGE_HOME") or "/root/forge")).expanduser()
        self.cp_file = Path(classpath_file or os.environ.get("FORGE_CP_FILE") or "/tmp/forge_cp.txt")
        self.java = java or os.environ.get("JAVA", "java")
        self.xmx = xmx

    def __call__(self, env, out_dir):
        classes = self.forge / "forge-gui-desktop/target/classes"
        if not (classes / "forge/spike/LlmSpikeHeadless.class").is_file():
            raise EnvError("no compiled overlay in %s (this env builds nothing)" % classes)
        if not self.cp_file.is_file():
            raise EnvError("no classpath file %s" % self.cp_file)
        args = []
        for o in ADD_OPENS:
            args += ["--add-opens", o + "=ALL-UNNAMED"]
        args += ["-Xmx" + self.xmx, "-Dfile.encoding=UTF-8", "-Djava.util.Arrays.useLegacyMergeSort=true",
                 "-Dapple.awt.UIElement=true", "-Djava.awt.headless=true",
                 "-cp", "%s:%s" % (classes, self.cp_file.read_text().strip())]
        argfile = Path(out_dir) / "java.args"
        argfile.write_text("\n".join(args) + "\n")
        log = open(Path(out_dir) / "jvm.log", "w")
        return subprocess.Popen([self.java, "@%s" % argfile, "forge.spike.LlmSpikeHeadless"],
                                cwd=str(self.forge / "forge-gui-desktop"), env=env, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)


def scrubbed_environ():
    """The shell's environment without anything that could change a condition or reach a paid endpoint."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENROUTER", "PZL_", "FORK_", "CLAUDE_"))}
    try:
        import run_batch
        keys = run_batch.PINNED_ENV_KEYS + run_batch.RUN_DEFINING_KEYS
    except Exception:  # noqa: BLE001  (the prefix list above is the floor)
        keys = ()
    for k in keys:
        env.pop(k, None)
    return env


class MtgEnv:
    """reset(puzzle=<.pzl> [, seed]) | reset(seed=<n> [, deck=<file>]) -> obs; step(action) -> (obs, reward, done, info).

    obs is {"episode", "step", "messages", "legal", "schema", "kind"} (kind "decision", or "correction" when the
    engine refused the previous answer and re-asks); on done step returns obs None. The reward is 1.0 for a
    solved puzzle / a won game, else 0.0; info["valid"] is False for an episode that must not be trained on (a
    capped game, a missing row, a step timeout). `launcher(env, out_dir) -> Popen` starts the game process."""

    def __init__(self, launcher=None, out_root=None, keep_out=False, step_timeout_s=300.0, first_timeout_s=240.0,
                 call_timeout_ms=900000, game_cap_ms=1_800_000, model=MODEL_SPEC, extra_env=None):
        self.launcher = launcher or JvmLauncher()
        self.out_root = Path(out_root) if out_root else None
        self.keep_out, self.step_timeout_s, self.first_timeout_s = keep_out, step_timeout_s, first_timeout_s
        self.call_timeout_ms, self.game_cap_ms, self.model = call_timeout_ms, game_cap_ms, model
        self.extra_env = dict(extra_env or {})
        self.server = self.proc = self.out_dir = self.pending = None
        self.mode = self.puzzle_id = self.seed = None
        self.episode = self.steps = 0
        self.t_reset = self.startup_s = 0.0
        self.step_ms = []   # wall ms from the answer written to the next observation or the end
        self.finished = True
        self._scratch = None

    # ---------------------------------------------------------------- lifecycle
    def reset(self, puzzle=None, seed=None, deck=None):
        self.close()
        if puzzle is None and seed is None:
            raise EnvError("reset needs puzzle=<.pzl> or seed=<n>")
        self.episode += 1
        self.steps, self.step_ms, self.finished = 0, [], False
        self.mode = "puzzle" if puzzle is not None else "game"
        self.seed = seed
        root = self.out_root or Path(tempfile.mkdtemp(prefix="mtg-env-"))
        root.mkdir(parents=True, exist_ok=True)
        self._scratch = None if self.out_root else root
        self.out_dir = Path(tempfile.mkdtemp(prefix="ep%d-" % self.episode, dir=str(root)))
        self.server = _Server()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        env = scrubbed_environ()
        env.update(DECISION_ONLY)
        env.update({"CONTROLLER_A": "llm", "CONTROLLER_B": "ai", "SMART_MODEL_A": self.model,
                    "FAST_MODEL_A": self.model, "OPENROUTER_BASE_URL": self.server.base_url,
                    "OPENROUTER_TIMEOUT_MS": str(self.call_timeout_ms), "OPENROUTER_IDLE_MS": str(self.call_timeout_ms),
                    "EVAL_OUT": str(self.out_dir), "GAME_CAP_MS": str(self.game_cap_ms)})
        if self.mode == "puzzle":
            self.puzzle_id = Path(puzzle).stem
            env.update({"PUZZLE": str(Path(puzzle).resolve()), "PZL_SEEDS": str(1 if seed is None else int(seed))})
        else:
            self.puzzle_id = None
            env["GAME_SEED"] = str(int(seed))
            if deck:
                env["DECK_A"] = env["DECK_B"] = str(Path(deck).resolve())
        env.update(self.extra_env)
        for k in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY"):
            env.pop(k, None)
        self.t_reset = time.time()
        try:
            self.proc = self.launcher(env, self.out_dir)
        except Exception:
            self.close()
            raise
        threading.Thread(target=self._watch, args=(self.proc, self.server), daemon=True).start()
        kind, payload = self._next(self.first_timeout_s)
        if kind != "obs":
            tail = self._jvm_tail()
            self.close()
            raise EnvError("no first decision (%s, rc=%s): %s" % (kind, payload, tail))
        self.startup_s = time.time() - self.t_reset
        return self._observe(payload)

    @staticmethod
    def _watch(proc, server):
        server.events.put(("exit", proc.wait()))

    def _next(self, timeout):
        try:
            return self.server.events.get(timeout=timeout)
        except queue.Empty:
            return "timeout", None

    def _observe(self, pending):
        self.pending = pending
        self.steps = pending.index
        msgs = pending.body.get("messages") or []
        correction = any(m.get("role") == "tool" and str(m.get("content", "")).startswith("ERROR:") for m in msgs[-3:])
        return {"episode": self.episode, "step": pending.index, "messages": msgs, "legal": parse_ask(msgs),
                "schema": answer_schema(pending.body.get("tools")),
                "kind": "correction" if correction else "decision"}

    def step(self, action):
        if self.finished or self.pending is None:
            raise EnvError("step on a finished or unstarted episode: call reset")
        pending, self.pending = self.pending, None
        t0 = time.time()
        args = as_arguments(action, parse_ask(pending.body.get("messages") or []))
        with self.server.lock:
            pending.reply = chat_reply(args, pending.index)
            self.server.last, self.server.current = pending, None
        pending.event.set()
        kind, payload = self._next(self.step_timeout_s)
        self.step_ms.append(round((time.time() - t0) * 1000.0, 1))
        if kind == "obs":
            return self._observe(payload), 0.0, False, {"step": payload.index, "args": args}
        if kind == "exit":
            reward, info = self._result(payload)
        else:
            reward, info = 0.0, {"valid": False, "error": "step_timeout", "step_timeout_s": self.step_timeout_s}
        info.update(self._stats())
        self.finished = True
        self.close()
        return None, reward, True, info

    def _stats(self):
        s, ms = self.server, sorted(self.step_ms)
        return {"episode": self.episode, "mode": self.mode, "puzzle": self.puzzle_id, "seed": self.seed,
                "decisions": s.count, "requests": s.requests, "deduped": s.deduped,
                "step_ms_median": ms[len(ms) // 2] if ms else None, "step_ms_max": ms[-1] if ms else None,
                "startup_s": round(self.startup_s, 2), "episode_s": round(time.time() - self.t_reset, 2)}

    # ---------------------------------------------------------------- reward
    def _result(self, rc):
        info = {"jvm_rc": rc, "valid": True}
        if self.mode == "puzzle":
            rows = _jsonl(self.out_dir / "puzzle-results.jsonl")
            if not rows:
                info.update(valid=False, error="no puzzle result row", jvm_tail=self._jvm_tail())
                return 0.0, info
            row = rows[-1]
            for k in ("status", "solver_actions", "model_calls", "win_condition", "solver_loss_reason",
                      "log_sha256", "tainted_by_deckout", "acceptance", "bridge_down_decisions", "routing_refusals"):
                if k in row:
                    info[k] = row[k]
            # puzzle_score.py counts only solved/failed, not a solve tainted by a deck-out, and refuses a row that
            # lost decisions to a dead bridge (a reply that came too late is the env's fault, not the policy's)
            info["valid"] = (row.get("status") in ("solved", "failed") and not row.get("tainted_by_deckout")
                             and not row.get("bridge_down_decisions") and not row.get("routing_refusals"))
            return (1.0 if row.get("status") == "solved" and info["valid"] else 0.0), info
        games = [g for f in sorted(self.out_dir.glob("game-*.jsonl")) for g in _jsonl(f) if g.get("t") == "game"]
        if not games:
            info.update(valid=False, error="no game line", jvm_tail=self._jvm_tail())
            return 0.0, info
        g = games[-1]
        info.update({k: g[k] for k in ("winner", "win_condition", "turns") if k in g})
        info["valid"] = g.get("winner") in ("A", "B")  # a capped game is invalid, never a draw
        return (1.0 if g.get("winner") == "A" else 0.0), info

    def _jvm_tail(self, n=600):
        try:
            return (self.out_dir / "jvm.log").read_text(errors="replace")[-n:]
        except (OSError, AttributeError):
            return ""

    # ---------------------------------------------------------------- shutdown
    def close(self):
        """Kill the JVM, release parked handlers, close the port, delete the scratch directory."""
        proc, srv = self.proc, self.server
        self.proc = self.server = self.pending = None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
        if srv is not None:
            with srv.lock:
                srv.closing = True
                for p in (srv.current, srv.last):
                    if p is not None:
                        p.event.set()
            srv.shutdown()
            srv.server_close()
        if self.out_dir is not None and not self.keep_out:
            shutil.rmtree(self.out_dir, ignore_errors=True)
            if self._scratch is not None:
                shutil.rmtree(self._scratch, ignore_errors=True)
        self.finished = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _jsonl(path):
    try:
        return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []
