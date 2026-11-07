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
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "rl"))
import mtg_env  # noqa: E402

NO_GUARD = lambda **kw: []  # noqa: E731  (these tests play made-up puzzles; the guard has test_eval_only.py)

STUB = r'''
import json, os, socket, sys, time, urllib.request
base, out = os.environ["OPENROUTER_BASE_URL"], os.environ["EVAL_OUT"]
n = int(os.environ.get("STUB_N", "3"))
json.dump({k: os.environ.get(k) for k in ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY", "MEMORY_TOOLS",
           "NATIVE_TOOLS", "ANSWER_TOOL", "OPENROUTER_BASE_URL", "SMART_MODEL_A", "PUZZLE", "PZL_SEEDS",
           "GAME_SEED", "CONTROLLER_A", "MY_FLAG")}, open(os.path.join(out, "env.json"), "w"))
def ask(i, timeout=30, text_i=None):
    kinds = {"2": ("number", "`number`: an integer from 0 through 5"),
             "3": ("options", "`options`: the numbers you pick, between 1 and 2")}
    field, bounds = kinds.get(os.environ.get("STUB_FIELD_AT_%d" % i, ""), ("option", ""))
    text = "Decision %d\n  1) PASS\n  2) Cast Bolt\n  3) Attack\ncall `answer` with `%s`. %s" % (i if text_i is None else text_i, field, bounds)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": text}]
    if str(i) in os.environ.get("STUB_ERR_AT", "").split(","):   # the engine refused the previous answer
        msgs += [{"role": "assistant", "content": None}, {"role": "tool", "content": "ERROR: not a legal option."}]
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
    if i == 1 and os.environ.get("STUB_REPEAT"):   # a second, different decision with byte-identical messages
        answers.append(ask(i))
solved = all(a.get("option") == 2 for a in answers)
if os.environ.get("STUB_NOROW"):
    sys.exit(0)
extra = json.loads(os.environ.get("STUB_EXTRA", "{}"))
if os.environ.get("STUB_LOSTROW"):   # the decision row of a decision the harness took with its fixed option
    open(os.path.join(out, "game-1-0.jsonl"), "w").write(json.dumps({"t": "decision", "answer_via": "lost", "answer_invalid": 2}) + "\n")
if os.environ.get("STUB_MODE") == "game":
    open(os.path.join(out, "game-1-0.jsonl"), "a").write(json.dumps(dict({"t": "game", "winner": os.environ.get("STUB_WINNER", "A" if solved else "B"), "turns": 7}, **extra)) + "\n")
else:
    row = {"schema": "pzl-result-v1", "status": os.environ.get("STUB_STATUS") or ("solved" if solved else "failed"), "seed": 1, "solver_actions": n}
    if os.environ.get("STUB_LOST"):
        row["bridge_down_decisions"] = 1
    row.update(extra)
    open(os.path.join(out, "puzzle-results.jsonl"), "w").write(json.dumps(row) + "\n")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
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
        e = mtg_env.MtgEnv(launcher=stub_launcher(**stub_env), out_root=self.root, step_timeout_s=20, first_timeout_s=20,
                           guard=NO_GUARD)
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
        deadline = time.time() + 20   # the stub's first request times out and is re-sent with the same messages
        while env.server.requests < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(env.server.requests, 2)
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
        env = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=1), out_root=self.root, keep_out=True, step_timeout_s=20,
                           guard=NO_GUARD)
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

    def test_extra_env_applies_but_cannot_carry_the_egress_gate_or_a_key(self):
        extra = {"MY_FLAG": "yes", "OPENROUTER_ALLOW_EGRESS": "1", "OPENROUTER_API_KEY": "k"}
        env = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=1), out_root=self.root, keep_out=True, step_timeout_s=20,
                             guard=NO_GUARD, extra_env=extra)
        self.addCleanup(env.close)
        env.reset(puzzle="x.pzl")
        out_dir = env.out_dir
        env.step(2)
        seen = json.loads((out_dir / "env.json").read_text())
        self.assertEqual(seen["MY_FLAG"], "yes")
        self.assertIsNone(seen["OPENROUTER_ALLOW_EGRESS"])
        self.assertIsNone(seen["OPENROUTER_API_KEY"])

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
        env = mtg_env.MtgEnv(launcher=stub_launcher(STUB_N=3, STUB_SLEEP=30), out_root=self.root, step_timeout_s=1,
                           guard=NO_GUARD)
        self.addCleanup(env.close)
        env.reset(puzzle="x.pzl")
        obs, reward, done, info = env.step(2)
        self.assertEqual((obs, reward, done, info["valid"], info["error"]), (None, 0.0, True, False, "step_timeout"))

    def test_no_first_decision_raises(self):
        e = mtg_env.MtgEnv(launcher=lambda env, out: subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"]),
                           out_root=self.root, first_timeout_s=10, guard=NO_GUARD)
        with self.assertRaises(mtg_env.EnvError):
            e.reset(puzzle="x.pzl")

    def test_scratch_is_deleted(self):
        env = self.env(STUB_N=1)
        self.play(env, 2, puzzle="x.pzl")
        self.assertEqual(list(Path(self.root).iterdir()), [])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.srv = mtg_env._Server()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def post(self, headers=None):
        req = urllib.request.Request(self.srv.base_url + "/chat/completions", b'{"messages": []}',
                                     dict({"Content-Type": "application/json"}, **(headers or {})))
        return urllib.request.urlopen(req, timeout=3)

    def test_the_server_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")   # 0.0.0.0 would take the request from the network
        self.assertTrue(self.srv.base_url.startswith("http://127.0.0.1:"))

    def test_a_request_with_an_authorization_header_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post({"Authorization": "Bearer sk-live"})
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(self.srv.requests, 0)   # refused before it counted as a decision

    def test_a_malformed_body_is_a_400(self):
        req = urllib.request.Request(self.srv.base_url + "/x", b"{not json", {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(cm.exception.code, 400)


class ScrubTest(unittest.TestCase):
    def test_every_scrubbed_prefix_is_removed_and_the_rest_kept(self):
        names = ["OPENROUTER_X", "PZL_X", "FORK_X", "CLAUDE_X", "KEEP_ME_RL_TEST"]
        for n in names:
            os.environ[n] = "1"
            self.addCleanup(os.environ.pop, n, None)
        env = mtg_env.scrubbed_environ()
        self.assertEqual([n for n in names if n in env], ["KEEP_ME_RL_TEST"])

    def test_the_pinned_condition_names_are_removed(self):
        import run_batch
        name = run_batch.PINNED_ENV_KEYS[0]
        os.environ[name] = "x"
        self.addCleanup(os.environ.pop, name, None)
        self.assertNotIn(name, mtg_env.scrubbed_environ())

    def test_an_import_failure_of_run_batch_raises(self):
        saved = sys.modules.get("run_batch")
        sys.modules["run_batch"] = None   # `import run_batch` now raises ImportError
        try:
            with self.assertRaises(ImportError):
                mtg_env.scrubbed_environ()
        finally:
            sys.modules["run_batch"] = saved


class ConstantsTest(unittest.TestCase):
    def test_decision_only_is_the_puzzle_runners_condition_with_the_lookups_off(self):
        import argparse
        import puzzle_solve
        cond = puzzle_solve.condition(argparse.Namespace(context_profile="v4-faithful", memory_tools=False,
                                                         rules_tool=False, model="openrouter:rl/policy"))
        self.assertEqual(mtg_env.DECISION_ONLY, cond)

    def test_add_opens_is_the_puzzle_runners_list(self):
        import puzzle_baselines
        self.assertIs(mtg_env.ADD_OPENS, puzzle_baselines.ADD_OPENS)


class VerdictTest(Base):
    def test_a_correction_observation_has_its_kind_and_marks_the_step_before_refused(self):
        env = self.env(STUB_N=2, STUB_ERR_AT="2")
        obs = env.reset(puzzle="x.pzl")
        self.assertEqual(obs["kind"], "decision")
        obs, _, _, info = env.step(1)
        self.assertEqual((obs["kind"], obs["step"], info["accepted"]), ("correction", 2, False))
        _, _, done, info = env.step(2)
        self.assertEqual((done, info["corrections"]), (True, 1))

    def test_an_accepted_step_says_so(self):
        env = self.env(STUB_N=2)
        env.reset(puzzle="x.pzl")
        _, _, _, info = env.step(2)
        self.assertTrue(info["accepted"])

    def test_a_solve_tainted_by_a_deck_out_is_not_valid_and_not_rewarded(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_EXTRA='{"tainted_by_deckout": true}'), 2, puzzle="x.pzl")
        self.assertEqual((info["status"], reward, info["valid"]), ("solved", 0.0, False))

    def test_a_row_that_lost_decisions_to_routing_is_not_valid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_EXTRA='{"routing_refusals": 2}'), 2, puzzle="x.pzl")
        self.assertEqual((info["status"], reward, info["valid"]), ("solved", 0.0, False))

    def test_a_decision_taken_by_the_fixed_option_after_two_refusals_is_lost_and_not_valid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_LOSTROW=1), 2, puzzle="x.pzl")
        self.assertEqual((info["status"], info["lost_decisions"], info["valid"], reward), ("solved", 1, False, 0.0))

    def test_the_rows_stamps_are_in_the_info(self):
        extra = '{"harness_epoch": "82", "pzl_sha256": "ab", "other": 1}'
        _, _, info = self.play(self.env(STUB_N=1, STUB_EXTRA=extra), 2, puzzle="x.pzl")
        self.assertEqual(info["stamps"], {"harness_epoch": "82", "pzl_sha256": "ab"})
        extra = '{"engine_sha": "e1", "overlay_sha": "o1", "harness_epoch": "82", "observation_version": "v9"}'
        _, _, info = self.play(self.env(STUB_N=1, STUB_MODE="game", STUB_EXTRA=extra), 2, seed=5)
        self.assertEqual(info["stamps"], {"engine_sha": "e1", "overlay_sha": "o1", "harness_epoch": "82",
                                          "observation_version": "v9"})

    def test_a_clean_episode_has_no_lost_decision(self):
        _, _, info = self.play(self.env(STUB_N=1), 2, puzzle="x.pzl")
        self.assertEqual((info["lost_decisions"], info["corrections"], info["valid"]), (0, 0, True))

    def test_a_jvm_that_did_not_exit_zero_is_not_valid(self):
        _, reward, info = self.play(self.env(STUB_N=1, STUB_EXIT=3), 2, puzzle="x.pzl")
        self.assertEqual((info["jvm_rc"], info["valid"], reward, info["error"]), (3, False, 0.0, "jvm_rc 3"))

    def test_a_game_that_lost_decisions_to_a_dead_bridge_is_not_valid(self):
        _, reward, info = self.play(self.env(STUB_N=2, STUB_MODE="game", STUB_EXTRA='{"bridge_down_decisions_a": 1}'),
                                    2, seed=5)
        self.assertEqual((info["winner"], reward, info["valid"], info["bridge_down_decisions_a"]), ("A", 0.0, False, 1))

    def test_a_game_with_a_lost_decision_or_a_bad_exit_is_not_valid(self):
        _, reward, info = self.play(self.env(STUB_N=2, STUB_MODE="game", STUB_LOSTROW=1), 2, seed=5)
        self.assertEqual((reward, info["valid"], info["lost_decisions"]), (0.0, False, 1))
        _, reward, info = self.play(self.env(STUB_N=2, STUB_MODE="game", STUB_EXIT=1), 2, seed=5)
        self.assertEqual((reward, info["valid"]), (0.0, False))


class DedupeTest(Base):
    def test_an_identical_request_after_the_answer_is_replied_with_it_and_counted_apart(self):
        # Two genuinely different decisions with byte-identical messages cannot be told from a retry whose reply
        # was lost: the second is answered with the first action and never reaches the policy. It is counted.
        env = self.env(STUB_N=2, STUB_REPEAT=1)
        env.reset(puzzle="x.pzl")
        seen, done = [], False
        while not done:
            obs, reward, done, info = env.step(2)
            if obs:
                seen.append(obs["step"])
        self.assertEqual(seen, [2])   # decisions 1 and its twin reached the policy as one; then decision 2
        self.assertEqual((info["valid"], reward), (False, 0.0))   # the twin was not the policy's choice
        self.assertEqual((info["deduped_after_answer"], info["deduped"], info["decisions"]), (1, 0, 2))
        self.assertEqual(info["requests"], 3)


class WordingTest(unittest.TestCase):
    """parse_ask reads the question as AnswerTool.instruction and the menu builder in LlmSpikeGui word it. The
    wording is Java's, so this pins the Java source: a reworded ask fails here, not as a silent empty legal set."""
    JAVA = ROOT / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"

    def test_the_ask_the_options_and_the_bounds_are_worded_as_the_parser_reads_them(self):
        answer_tool = (self.JAVA / "AnswerTool.java").read_text(encoding="utf-8")
        menu = (self.JAVA / "LlmSpikeGui.java").read_text(encoding="utf-8")
        for fragment in ('"ANSWER: call `answer` with " + how', 'how = "`option`: one number from the list"',
                         '"`options`: the numbers you pick, " + bounds(s)', '"exactly " + s.min', '"between " + s.min + " and " + s.max',
                         '"`number`: an integer from " + s.min + " through " + s.max',
                         '" numbers in the order listed, summing to " + s.max'):
            self.assertIn(fragment, answer_tool)
        self.assertIn('menu.append("  ").append(n++).append(") ")', menu)   # "  N) text", what OPTION reads
        self.assertTrue(mtg_env.ASK_MARK.startswith("call `answer` with `"))

    def test_a_request_worded_as_java_words_it_parses(self):
        text = ("Options:\n  1) PASS\n  2) Cast Bolt\nANSWER: call `answer` with `amounts`: 2 numbers in the order "
                "listed, summing to 7. Lookups do not count against the answer.\n")
        lg = mtg_env.parse_ask([{"role": "user", "content": text}])
        self.assertEqual((lg["field"], lg["count"], lg["total"]), ("amounts", 2, 7))


class ShutdownTest(Base):
    def test_a_process_that_exits_without_close_leaves_no_child(self):
        code = ("import subprocess, sys; sys.path[:0] = [%r, %r]\n"
                "import mtg_env\n"
                "e = mtg_env.MtgEnv(out_root=%r)\n"
                "e.proc = subprocess.Popen(['sleep', '120'], start_new_session=True)\n"
                "print(e.proc.pid, flush=True)\n") % (str(ROOT / "eval"), str(ROOT / "eval" / "rl"), self.root)
        out = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
        pid = int(out.stdout.split()[0])
        self.addCleanup(lambda: os.path.exists("/proc/%d" % pid) and os.kill(pid, 9))
        def alive():
            try:
                return Path("/proc/%d/stat" % pid).read_text().split()[2] != "Z"
            except OSError:
                return False
        deadline = time.time() + 10
        while alive() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(), "the child outlived its parent")


if __name__ == "__main__":
    unittest.main()
