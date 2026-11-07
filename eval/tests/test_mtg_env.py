#!/usr/bin/env python3
"""Tests for eval/rl/mtg_env.py with a stub JVM: a python process that sends the
requests a game would and writes the result row, so no JVM and no model is involved.

    python3 eval/tests/test_mtg_env.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "rl"))
import mtg_env  # noqa: E402

STUB = r'''
import json, os, socket, sys, time, urllib.request
base, out = os.environ["OPENROUTER_BASE_URL"], os.environ["EVAL_OUT"]
n = int(os.environ.get("STUB_N", "3"))
json.dump({k: os.environ.get(k) for k in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY", "MEMORY_TOOLS",
           "NATIVE_TOOLS", "ANSWER_TOOL", "OPENROUTER_BASE_URL", "SMART_MODEL_A", "PUZZLE", "PZL_SEEDS",
           "GAME_SEED", "CONTROLLER_A")}, open(os.path.join(out, "env.json"), "w"))
def ask(i, timeout=30):
    kinds = {"2": ("number", "`number`: an integer from 0 through 5"),
             "3": ("options", "`options`: the numbers you pick, between 1 and 2")}
    field, bounds = kinds.get(os.environ.get("STUB_FIELD_AT_%d" % i, ""), ("option", ""))
    text = "Decision %d\n  1) PASS\n  2) Cast Bolt\n  3) Attack\ncall `answer` with `%s`. %s" % (i, field, bounds)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": text}]
    body = {"model": "x", "messages": msgs, "tool_choice": "required",
            "tools": [{"type": "function", "function": {"name": "answer", "parameters": {"type": "object"}}}]}
    req = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    return json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
answers = []
for i in range(1, n + 1):
    if i == 2:
        time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    if i == 1 and os.environ.get("STUB_RETRY"):
        try:
            ask(i, timeout=0.3)
        except (socket.timeout, TimeoutError, OSError):
            pass
    answers.append(ask(i))
solved = all(a.get("option") == 2 for a in answers)
if os.environ.get("STUB_NOROW"):
    sys.exit(0)
if os.environ.get("STUB_MODE") == "game":
    open(os.path.join(out, "game-1-0.jsonl"), "w").write(json.dumps({"t": "game", "winner": os.environ.get("STUB_WINNER", "A" if solved else "B"), "turns": 7}) + "\n")
else:
    row = {"schema": "pzl-result-v1", "status": os.environ.get("STUB_STATUS") or ("solved" if solved else "failed"), "seed": 1, "solver_actions": n}
    if os.environ.get("STUB_LOST"):
        row["bridge_down_decisions"] = 1
    open(os.path.join(out, "puzzle-results.jsonl"), "w").write(json.dumps(row) + "\n")
'''


def stub_launcher(**stub_env):
    def launch(env, out_dir):
        env = dict(env, **{k: str(v) for k, v in stub_env.items()})
        return subprocess.Popen([sys.executable, "-c", STUB], env=env, start_new_session=True)
    return launch


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-rl-env-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def env(self, **stub_env):
        e = mtg_env.MtgEnv(launcher=stub_launcher(**stub_env), out_root=self.root, step_timeout_s=20, first_timeout_s=20)
        self.addCleanup(e.close)
        return e

    def play(self, env, answer, **reset):
        obs, steps = env.reset(**reset), 0
        while True:
            obs, reward, done, info = env.step(answer)
            steps += 1
            if done:
                return steps, reward, info


class ParseTest(unittest.TestCase):
    def ask(self, text):
        return mtg_env.parse_ask([{"role": "user", "content": text}])

    def test_option(self):
        lg = self.ask("Pick\n  1) PASS\n  3) Cast Bolt\ncall `answer` with `option`.")
        self.assertEqual(lg, {"field": "option", "options": [{"n": 1, "text": "PASS"}, {"n": 3, "text": "Cast Bolt"}]})

    def test_options_bounds(self):
        lg = self.ask("  1) a\n  2) b\n  3) c\ncall `answer` with `options`. `options`: the numbers you pick, between 1 and 2")
        self.assertEqual((lg["field"], lg["min"], lg["max"], len(lg["options"])), ("options", 1, 2, 3))

    def test_number_and_none(self):
        lg = self.ask("call `answer` with `number`. `number`: an integer from 0 through 5")
        self.assertEqual((lg["field"], lg["min"], lg["max"]), ("number", 0, 5))
        self.assertEqual(self.ask("no question here"), {})

    def test_as_arguments(self):
        self.assertEqual(mtg_env.as_arguments(3), {"option": 3})
        self.assertEqual(mtg_env.as_arguments([1, 2]), {"options": [1, 2]})
        self.assertEqual(mtg_env.as_arguments([1, 2], {"field": "amounts"}), {"amounts": [1, 2]})
        self.assertEqual(mtg_env.as_arguments(True), {"keep": True})
        self.assertEqual(mtg_env.as_arguments({"blocks": []}), {"blocks": []})


class EnvTest(Base):
    def test_solved_episode_rewards_one(self):
        env = self.env(STUB_N=3)
        steps, reward, info = self.play(env, 2, puzzle="eval/puzzles/x.pzl", seed=1)
        self.assertEqual((steps, reward), (3, 1.0))
        self.assertTrue(info["valid"])
        self.assertEqual((info["status"], info["decisions"], info["puzzle"]), ("solved", 3, "x"))

    def test_failed_episode_rewards_zero(self):
        steps, reward, info = self.play(self.env(STUB_N=2), 1, puzzle="x.pzl")
        self.assertEqual((steps, reward, info["status"], info["valid"]), (2, 0.0, "failed", True))

    def test_observation_has_messages_legal_schema(self):
        env = self.env(STUB_N=1)
        obs = env.reset(puzzle="x.pzl")
        self.assertEqual((obs["step"], obs["kind"], obs["legal"]["field"]), (1, "decision", "option"))
        self.assertEqual([o["n"] for o in obs["legal"]["options"]], [1, 2, 3])
        self.assertEqual(obs["messages"][1]["role"], "user")
        self.assertEqual(obs["schema"], {"type": "object"})
        env.step({"option": 1})

    def test_retried_request_is_one_decision(self):
        env = self.env(STUB_N=2, STUB_RETRY=1)
        env.reset(puzzle="x.pzl")
        time.sleep(1.0)   # the stub's first request times out and is re-sent with the same messages
        done = False
        while not done:
            obs, reward, done, info = env.step(2)
        self.assertEqual((info["decisions"], info["deduped"], reward), (2, 1, 1.0))
        self.assertEqual(info["requests"], 3)

    def test_other_field_kinds_reach_the_policy(self):
        env = self.env(STUB_N=3, STUB_FIELD_AT_2="2", STUB_FIELD_AT_3="3")
        obs = env.reset(puzzle="x.pzl")
        fields = [obs["legal"]["field"]]
        done = False
        while not done:
            obs, _, done, _ = env.step({"option": 2} if obs["legal"]["field"] == "option" else 1)
            if obs:
                fields.append(obs["legal"]["field"])
        self.assertEqual(fields, ["option", "number", "options"])

    def test_no_result_row_is_invalid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_NOROW=1), 2, puzzle="x.pzl")
        self.assertEqual((reward, info["valid"], info["error"]), (0.0, False, "no puzzle result row"))

    def test_unscored_status_is_invalid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_STATUS="invalid"), 2, puzzle="x.pzl")
        self.assertEqual((reward, info["valid"]), (0.0, False))

    def test_lost_decisions_are_invalid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_LOST=1), 2, puzzle="x.pzl")
        self.assertEqual((reward, info["valid"]), (0.0, False))

    def test_game_win_and_loss(self):
        _, reward, info = self.play(self.env(STUB_N=2, STUB_MODE="game"), 2, seed=5)
        self.assertEqual((reward, info["winner"], info["mode"]), (1.0, "A", "game"))
        _, reward, info = self.play(self.env(STUB_N=1, STUB_MODE="game", STUB_WINNER="B"), 2, seed=5)
        self.assertEqual((reward, info["valid"]), (0.0, True))
        _, reward, info = self.play(self.env(STUB_N=1, STUB_MODE="game", STUB_WINNER="draw"), 2, seed=5)
        self.assertEqual((reward, info["valid"]), (0.0, False))

    def test_condition_and_no_egress(self):
        os.environ["OPENROUTER_ALLOW_EGRESS"] = "1"
        os.environ["OPENROUTER_API_KEY"] = "k"
        self.addCleanup(os.environ.pop, "OPENROUTER_ALLOW_EGRESS", None)
        self.addCleanup(os.environ.pop, "OPENROUTER_API_KEY", None)
        env = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=1), out_root=self.root, keep_out=True, step_timeout_s=20)
        self.addCleanup(env.close)
        env.reset(puzzle="/some/where/p.pzl", seed=4)
        out_dir = env.out_dir
        env.step(2)
        seen = json.loads((out_dir / "env.json").read_text())
        self.assertIsNone(seen["OPENROUTER_ALLOW_EGRESS"])
        self.assertIsNone(seen["OPENROUTER_API_KEY"])
        self.assertTrue(seen["OPENROUTER_BASE_URL"].startswith("http://127.0.0.1:"))
        self.assertEqual((seen["MEMORY_TOOLS"], seen["NATIVE_TOOLS"], seen["ANSWER_TOOL"]), ("0", "1", "1"))
        self.assertEqual((seen["PZL_SEEDS"], seen["PUZZLE"], seen["CONTROLLER_A"]), ("4", "/some/where/p.pzl", "llm"))
        self.assertTrue(seen["SMART_MODEL_A"].startswith("openrouter:rl/"))

    def test_close_kills_the_process_and_closes_the_port(self):
        env = self.env(STUB_N=5)
        env.reset(puzzle="x.pzl")
        proc, port = env.proc, env.server.server_address[1]
        env.close()
        self.assertIsNotNone(proc.poll())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2)
        with self.assertRaises(mtg_env.EnvError):
            env.step(1)

    def test_reset_twice_leaves_one_server(self):
        env = self.env(STUB_N=2)
        env.reset(puzzle="x.pzl")
        first, port = env.proc, env.server.server_address[1]
        env.reset(puzzle="x.pzl")
        self.assertIsNotNone(first.poll())
        self.assertNotEqual(port, env.server.server_address[1])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2)

    def test_step_timeout_ends_invalid(self):
        env = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=3, STUB_SLEEP=30), out_root=self.root, step_timeout_s=1)
        self.addCleanup(env.close)
        env.reset(puzzle="x.pzl")
        obs, reward, done, info = env.step(2)
        self.assertEqual((obs, reward, done, info["valid"], info["error"]), (None, 0.0, True, False, "step_timeout"))

    def test_no_first_decision_raises(self):
        e = mtg_env.MtgEnv(launcher=lambda env, out: subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"]),
                           out_root=self.root, first_timeout_s=10)
        with self.assertRaises(mtg_env.EnvError):
            e.reset(puzzle="x.pzl")

    def test_scratch_is_deleted(self):
        env = self.env(STUB_N=1)
        self.play(env, 2, puzzle="x.pzl")
        self.assertEqual(list(Path(self.root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
