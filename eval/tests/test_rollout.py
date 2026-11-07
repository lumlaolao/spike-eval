#!/usr/bin/env python3
"""Tests for eval/rl/rollout.py: trajectory lines, the GRPO and SFT readers, the policies. The environment is
mtg_env's stub JVM (test_mtg_env.py), so no JVM and no model is involved.

    python3 eval/tests/test_rollout.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "eval" / "rl"))
sys.path.insert(0, str(ROOT / "eval" / "tests"))
import rollout  # noqa: E402
from test_mtg_env import Base  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
