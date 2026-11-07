#!/usr/bin/env python3
"""Tests for eval/rl/rollout.py: trajectory lines, the GRPO and SFT readers, the policies. The environment is
mtg_env's stub JVM (test_mtg_env.py), so no JVM and no model is involved.

    python3 eval/tests/test_rollout.py
"""
import argparse
import contextlib
import io
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "rl"))
sys.path.insert(0, str(ROOT / "eval" / "tests"))
import rollout  # noqa: E402
from test_mtg_env import Base, stub_launcher  # noqa: E402
import fake_model_server  # noqa: E402
import mtg_env  # noqa: E402
import unittest  # noqa: E402


class RolloutTest(Base):
    def test_group_file_and_readers(self):
        env = self.env(STUB_N=2)
        path = Path(self.root) / "traj" / "r.jsonl"
        path.parent.mkdir()
        resets = [{"puzzle": "a/p1.pzl", "seed": s} for s in (1, 2, 3)]
        with open(path, "w") as out:
            recs = rollout.run_group(env, "first", "p1", resets, out)
        self.assertEqual([r["reward"] for r in recs], [0.0, 0.0, 0.0])   # "first" picks option 1, never 2
        eps = rollout.load_episodes(path)
        self.assertEqual([len(e["steps"]) for e in eps], [2, 2, 2])
        s = eps[0]["steps"][0]
        self.assertEqual((s["type"], s["step"], s["action"], s["args"]), ("step", 1, {"option": 1}, {"option": 1}))
        self.assertEqual(s["legal"]["field"], "option")
        self.assertEqual(eps[0]["group"], "p1")

    def test_grpo_and_sft(self):
        eps = [{"group": "g", "episode_id": "g#%d" % i, "reward": r, "valid": v, "steps": [
            {"step": 1, "messages": [{"role": "user", "content": "q"}], "args": {"option": 2}}]}
            for i, (r, v) in enumerate([(1.0, True), (0.0, True), (0.0, True), (1.0, False)])]
        g = rollout.grpo_groups(eps)["g"]
        self.assertEqual(len(g), 3)                      # the invalid episode is not in the group
        self.assertGreater(g[0]["advantage"], 0)
        self.assertLess(g[1]["advantage"], 0)
        flat = rollout.grpo_groups([dict(e, reward=0.0) for e in eps])["g"]
        self.assertEqual({x["advantage"] for x in flat}, {0.0})   # an all-fail group carries no gradient
        sft = rollout.sft_examples(eps)
        self.assertEqual(len(sft), 1)                    # one valid solved episode, one decision
        last = sft[0]["messages"][-1]
        self.assertEqual(json.loads(last["tool_calls"][0]["function"]["arguments"]), {"option": 2})

    def test_random_policy_is_legal_and_replays(self):
        obs = {"legal": {"field": "options", "min": 1, "max": 2, "options": [{"n": n, "text": ""} for n in (1, 2, 3, 4)]}}
        a = [rollout.make_random_policy("s")(obs) for _ in range(1)]
        b = [rollout.make_random_policy("s")(obs) for _ in range(1)]
        self.assertEqual(a, b)
        for i in range(50):
            picks = rollout.make_random_policy(i)(obs)["options"]
            self.assertTrue(1 <= len(picks) <= 2 and set(picks) <= {1, 2, 3, 4})
        num = {"legal": {"field": "number", "min": 2, "max": 4}}
        self.assertIn(rollout.make_random_policy(1)(num)["number"], (2, 3, 4))
        amt = {"legal": {"field": "amounts", "count": 3, "total": 5}}
        self.assertEqual(sum(rollout.make_random_policy(1)(amt)["amounts"]), 5)

    def test_max_steps_cuts_a_runaway(self):
        env = self.env(STUB_N=50)
        with open(Path(self.root) / "r.jsonl", "w") as out:
            rec = rollout.run_episode(env, rollout.first_policy, "g", "g#0", out, {"puzzle": "x.pzl"}, max_steps=3)
        self.assertEqual((rec["valid"], rec["n_steps"], rec["info"]["error"]), (False, 3, "max_steps"))


def episode(i, reward, valid=True, group="g", steps=1, **kw):
    return dict({"group": group, "episode_id": "%s#%d" % (group, i), "reward": reward, "valid": valid, "info": {},
                 "steps": [{"step": k + 1, "messages": [{"role": "user", "content": "q"}], "args": {"option": 2}}
                           for k in range(steps)]}, **kw)


class ReaderTest(unittest.TestCase):
    def test_grpo_advantages_are_exact_for_four_of_forty_and_unnormalized_is_centred(self):
        eps = [episode(i, 1.0 if i < 4 else 0.0) for i in range(40)]
        adv = [x["advantage"] for x in rollout.grpo_groups(eps)["g"]]
        self.assertAlmostEqual(adv[0], 3.0, places=4)          # (1 - 0.1) / sqrt(0.1 * 0.9)
        self.assertAlmostEqual(adv[-1], -1 / 3, places=4)      # (0 - 0.1) / 0.3
        raw = [x["advantage"] for x in rollout.grpo_groups(eps, normalize=False)["g"]]
        self.assertEqual((round(raw[0], 9), round(raw[-1], 9)), (0.9, -0.1))
        self.assertAlmostEqual(statistics.fmean(raw), 0.0, places=9)

    def test_one_valid_episode_in_a_group_has_advantage_zero(self):
        eps = [episode(0, 1.0), episode(1, 0.0, valid=False), episode(2, 0.0, valid=False)]
        self.assertEqual([x["advantage"] for x in rollout.grpo_groups(eps)["g"]], [0.0])

    def test_refused_and_lost_and_eval_only_episodes_are_not_trained_on(self):
        good, lost = episode(0, 1.0, steps=2), episode(1, 1.0, steps=2)
        lost["info"] = {"lost_decisions": 1}
        evalonly = episode(2, 1.0, eval_only=["heldout puzzle x"])
        good["steps"][0]["accepted"] = False
        sft = rollout.sft_examples([good, lost, evalonly])
        self.assertEqual([(e["episode_id"], e["step"]) for e in sft], [("g#0", 2)])   # the refused step 1 is out
        groups = rollout.grpo_groups([good, lost, evalonly, episode(3, 0.0)])["g"]
        self.assertEqual([x["episode_id"] for x in groups], ["g#0", "g#3"])

    def test_a_crashed_episodes_orphan_steps_do_not_join_the_next_one(self):
        path = Path(self.id() + ".jsonl")
        step = lambda eid, n: json.dumps({"type": "step", "episode_id": eid, "group": "g", "step": n})  # noqa: E731
        ep = json.dumps({"type": "episode", "episode_id": "r2:g#0", "group": "g", "reward": 0.0, "valid": True})
        lines = [step("r1:g#0", 1), step("r1:g#0", 2), step("r2:g#0", 1), step("r2:g#0", 2), ep]
        path.write_text("\n".join(lines) + "\n")
        self.addCleanup(path.unlink)
        self.assertEqual([s["episode_id"] for s in rollout.load_episodes(path)[0]["steps"]], ["r2:g#0"] * 2)
        # the same ids: the old orphan steps 1-2 must not stay in front of the new run's 1-2
        path.write_text("\n".join([step("g#0", 1), step("g#0", 2), step("g#0", 1), step("g#0", 2), ep.replace("r2:", "")]) + "\n")
        self.assertEqual([s["step"] for s in rollout.load_episodes(path)[0]["steps"]], [1, 2])


class PolicyTest(unittest.TestCase):
    FIELDS = {
        "option": "Pick\n  1) PASS\n  3) Cast\n  4) Attack\ncall `answer` with `option`.",
        "options": "  1) a\n  2) b\n  3) c\ncall `answer` with `options`. `options`: the numbers you pick, between 2 and 3",
        "options_exact": "  1) a\n  2) b\ncall `answer` with `options`. `options`: the numbers you pick, exactly 1",
        "number": "call `answer` with `number`. `number`: an integer from 2 through 5",
        "amounts": "call `answer` with `amounts`. `amounts`: 3 numbers in the order listed, summing to 7",
        "keep": "call `answer` with `keep`.",
    }

    def obs(self, text):
        msgs = [{"role": "user", "content": text}]
        return {"messages": msgs, "legal": mtg_env.parse_ask(msgs)}

    def fake(self, policy, obs):
        a = argparse.Namespace(policy=policy, log=None, answer_invalid=0, seed="s")
        props = fake_model_server.Policy.decision_props(obs["messages"])
        call = lambda name, args: {"function": {"arguments": json.dumps(args)}}  # noqa: E731
        p = fake_model_server.Policy(a)
        out = p.random_answer(obs["messages"], props, [], call) if policy == "random" else \
            p.answer_calls({"tools": [{"function": {"name": "answer", "parameters": {"properties": {
                "option": {}, "options": {}, "keep": {}}}}}]}, obs["messages"], ["answer"], call)
        return json.loads(out[0]["function"]["arguments"]), props

    def test_first_policy_agrees_with_the_fake_servers_first_policy(self):
        for name, text in self.FIELDS.items():
            o = self.obs(text)
            self.assertEqual(rollout.first_policy(o), self.fake("first", o)[0], name)

    def test_random_policy_answers_stay_inside_the_fake_servers_legal_set(self):
        for name, text in self.FIELDS.items():
            o = self.obs(text)
            for seed in range(30):
                got = rollout.make_random_policy(seed)(o)
                props = self.fake("random", o)[1]
                if "option" in got:
                    self.assertIn(got["option"], props["option"]["enum"], name)
                if "options" in got:
                    arr = props["options"]
                    self.assertTrue(set(got["options"]) <= set(arr["items"]["enum"]), name)
                    self.assertTrue(arr["minItems"] <= len(got["options"]) <= arr["maxItems"], name)
                if "number" in got:
                    self.assertTrue(props["number"]["minimum"] <= got["number"] <= props["number"]["maximum"], name)
                if "amounts" in got:
                    self.assertEqual((len(got["amounts"]), sum(got["amounts"])), (3, 7), name)

    def test_first_policy_picks_at_least_one_option_when_the_minimum_is_zero(self):
        o = {"legal": {"field": "options", "min": 0, "max": 2, "options": [{"n": 4, "text": ""}, {"n": 2, "text": ""}]}}
        self.assertEqual(rollout.first_policy(o), {"options": [2]})
        self.assertEqual(rollout.first_policy({"legal": {"field": "option", "options": []}}), {"option": 1})


class RunEpisodeTest(Base):
    def lines(self, path):
        return [json.loads(l) for l in Path(path).read_text().splitlines()]

    def test_a_step_the_engine_refused_is_written_not_accepted(self):
        env = self.env(STUB_N=3, STUB_ERR_AT="2")
        path = Path(self.root) / "r.jsonl"
        with open(path, "w") as out:
            rec = rollout.run_episode(env, rollout.first_policy, "g", "r:g#0", out, {"puzzle": "x.pzl"}, run_id="r")
        steps = [l for l in self.lines(path) if l["type"] == "step"]
        self.assertEqual([(s["step"], s["kind"], s["accepted"]) for s in steps],
                         [(1, "decision", False), (2, "correction", True), (3, "decision", True)])
        self.assertEqual((rec["run_id"], rec["info"]["corrections"]), ("r", 1))

    def test_a_lost_decision_makes_the_episode_invalid_in_the_file_and_untrainable(self):
        env = self.env(STUB_N=2, STUB_LOSTROW=1)
        path = Path(self.root) / "r.jsonl"
        with open(path, "w") as out:
            rec = rollout.run_episode(env, lambda o: {"option": 2}, "g", "r:g#0", out, {"puzzle": "x.pzl"})
        self.assertEqual((rec["valid"], rec["reward"], rec["info"]["lost_decisions"]), (False, 0.0, 1))
        self.assertEqual(rollout.sft_examples(rollout.load_episodes(path)), [])

    def test_a_raising_policy_leaves_no_live_child_and_no_server(self):
        env = self.env(STUB_N=5)
        seen = {}

        def policy(obs):
            seen["proc"], seen["port"] = env.proc, env.server.server_address[1]
            raise RuntimeError("policy blew up")
        with open(Path(self.root) / "r.jsonl", "w") as out, self.assertRaises(RuntimeError):
            rollout.run_episode(env, policy, "g", "r:g#0", out, {"puzzle": "x.pzl"})
        self.assertIsNotNone(seen["proc"].poll())
        self.assertIsNone(env.proc)

    def test_group_episode_ids_carry_the_run_id(self):
        env = self.env(STUB_N=1)
        with open(Path(self.root) / "r.jsonl", "w") as out:
            a = rollout.run_group(env, "first", "g", [{"puzzle": "x.pzl", "seed": 1}], out, run_id="aaaa")
            b = rollout.run_group(env, "first", "g", [{"puzzle": "x.pzl", "seed": 1}], out)
        self.assertEqual(a[0]["episode_id"], "aaaa:g#0")
        self.assertNotEqual(b[0]["episode_id"], "g#0")
        self.assertNotEqual(b[0]["episode_id"], a[0]["episode_id"])


FORGE_PZL = "/root/forge/forge-gui/res/puzzle/MTGP_08.pzl"   # listed in the builtin suite as forge:MTGP_08.pzl, heldout


class MainTest(Base):
    def main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(err):
            try:
                rc = rollout.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def patched(self):
        real = mtg_env.MtgEnv
        mtg_env.MtgEnv = lambda **kw: real(launcher=stub_launcher(STUB_N=2), out_root=self.root, step_timeout_s=20,
                                           first_timeout_s=20)
        self.addCleanup(setattr, mtg_env, "MtgEnv", real)

    def test_main_plays_a_group_and_writes_a_file(self):
        self.patched()
        path = Path(self.root) / "o" / "t.jsonl"
        rc, out, _ = self.main("--puzzle", "eval/puzzles/rules-pilot/RP_017.pzl", "--group-size", "2",
                               "--policy", "first", "--out", str(path))
        self.assertEqual(rc, 0)
        eps = rollout.load_episodes(path)
        self.assertEqual([e["n_steps"] for e in eps], [2, 2])
        self.assertEqual(len({e["run_id"] for e in eps}), 1)
        self.assertEqual(len(out.strip().splitlines()), 2)

    def test_main_refuses_a_non_empty_out_unless_append(self):
        self.patched()
        path = Path(self.root) / "t.jsonl"
        path.write_text("{}\n")
        rc, _, err = self.main("--puzzle", "eval/puzzles/rules-pilot/RP_017.pzl", "--out", str(path))
        self.assertEqual(rc, 2)
        self.assertIn("--append", err)
        self.assertEqual(path.read_text(), "{}\n")
        rc, _, _ = self.main("--puzzle", "eval/puzzles/rules-pilot/RP_017.pzl", "--out", str(path), "--append")
        self.assertEqual(rc, 0)

    def test_main_refuses_reserved_seeds_and_heldout_puzzles_unless_allowed(self):
        self.patched()
        path = Path(self.root) / "t.jsonl"
        rc, _, err = self.main("--game-seed", "30050", "--out", str(path))
        self.assertEqual(rc, 2)
        self.assertIn("certification-holdout", err)
        rc, _, err = self.main("--game-seed", "499", "--group-size", "3", "--out", str(path))   # 499, 500, 501
        self.assertEqual(rc, 2)
        self.assertIn("frozen-legacy", err)
        rc, _, err = self.main("--puzzle", FORGE_PZL, "--out", str(path))    # heldout in the builtin suite
        self.assertEqual((rc, "heldout puzzle" in err), (2, True))
        self.assertFalse(path.exists())
        rc, _, _ = self.main("--puzzle", FORGE_PZL, "--out", str(path), "--allow-eval-only")
        self.assertEqual(rc, 0)
        eps = rollout.load_episodes(path)
        self.assertTrue(all(e["eval_only"] for e in eps))
        self.assertEqual(rollout.sft_examples(eps), [])

    def test_an_open_puzzle_and_a_clean_seed_are_accepted(self):
        self.assertEqual(rollout.puzzle_split("eval/puzzles/rules-pilot/RP_017.pzl"), "open")
        self.assertEqual(rollout.puzzle_split("eval/puzzles/hard/gen/drain_0026d851c85f.pzl"), "heldout")
        self.assertEqual(rollout.eval_only_reasons(["eval/puzzles/rules-pilot/RP_017.pzl"], [1000], [1, 2]), [])


if __name__ == "__main__":
    unittest.main()
