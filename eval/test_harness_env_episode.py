"""harness_env_episode.py: an episode ends when the agent says so (or at the cap), and is graded outside the sandbox.

The agents here are small scripts; grading is a stub that records what it was handed, so these tests need no JVM.
What would make them fail: an episode that ends before DONE, one that runs past its cap, a submission taken from
anywhere but the sandbox's overlay, a checkpoint graded while the agent still runs, a sandbox with history or a leak
handed to the agent, or a dev slice copied in that the spec did not ask for.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

import harness_env_episode as ep

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "eval", "harness_env", "episode-v0.json")


def agent(code):
    return [sys.executable, "-c", code]


class Stub:
    """A grader that records each call and when it came."""

    def __init__(self):
        self.calls = []

    def __call__(self, submission, out):
        files = sorted(os.path.relpath(os.path.join(r, f), submission) for r, _, fs in os.walk(submission) for f in fs)
        self.calls.append({"submission": submission, "files": files, "at": time.time()})
        return {"benchmark": 0.5 if files else 0.0, "dense": 0.5 if files else 0.0}


class EpisodeTest(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out)

    def run_ep(self, code, **kw):
        stub = Stub()
        kw.setdefault("cap_s", 20)
        report = ep.run_episode(SPEC, REPO, agent(code), self.out, grade_fn=stub, poll_s=0.05, **kw)
        return report, stub

    def test_an_agent_that_writes_done_ends_the_episode_and_its_overlay_is_graded(self):
        report, stub = self.run_ep(
            "import os\nos.makedirs('overlay/forge-gui-desktop/src/main/java/a', exist_ok=True)\n"
            "open('overlay/forge-gui-desktop/src/main/java/a/Seat.java','w').write('class Seat {}')\n"
            "open('DONE','w').write('submit HEAD')\n"
            "import time\ntime.sleep(30)\n")
        self.assertEqual(report["ended_by"], "agent")
        self.assertEqual(report["done"], "submit HEAD")
        self.assertLess(report["wall_s"], 15, "the episode ended at DONE, not at the cap")
        self.assertEqual(stub.calls[0]["files"], ["forge-gui-desktop/src/main/java/a/Seat.java"])
        self.assertTrue(stub.calls[0]["submission"].startswith(self.out))
        self.assertNotIn("sandbox", os.path.relpath(stub.calls[0]["submission"], self.out).split(os.sep)[0])
        self.assertEqual(report["score"]["benchmark"], 0.5)
        with open(os.path.join(self.out, "report.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ended_by"], "agent")

    def test_an_agent_that_never_finishes_is_stopped_at_the_cap_and_its_last_state_graded(self):
        report, stub = self.run_ep("import time\nopen('overlay/x.txt','w').write('x')\nwhile True: time.sleep(0.1)\n", cap_s=1.5)
        self.assertEqual(report["ended_by"], "cap")
        self.assertLess(report["wall_s"], 10)
        self.assertEqual(stub.calls[0]["files"], ["x.txt"])

    def test_an_agent_that_exits_without_done_is_graded_and_says_so(self):
        report, stub = self.run_ep("pass\n")
        self.assertEqual(report["ended_by"], "exit")
        self.assertEqual(report["score"]["benchmark"], 0.0)

    def test_checkpoints_are_taken_while_the_agent_works_and_graded_only_after_it_ends(self):
        report, stub = self.run_ep(
            "import time, os\nfor i in range(4):\n    open('overlay/f%d.txt' % i,'w').write('x')\n    time.sleep(0.4)\n"
            "open('DONE','w').write('done')\n", checkpoint_s=0.3, grade_checkpoints=True)
        self.assertGreaterEqual(len(report["checkpoints"]), 2)
        ended = report["ended_at"]
        self.assertTrue(all(c["at"] >= ended for c in stub.calls), "a checkpoint was graded while the agent ran")
        sizes = [len(c["files"]) for c in stub.calls[1:]]
        self.assertEqual(sizes, sorted(sizes), "checkpoints are in time order and only grow here")
        self.assertTrue(all("score" in c for c in report["checkpoints"]))

    def test_the_agent_starts_in_a_fresh_repository_with_one_commit_and_no_remote(self):
        self.run_ep("import subprocess\nn = subprocess.run(['git','rev-list','--count','--all'],capture_output=True,text=True).stdout\n"
                    "r = subprocess.run(['git','remote'],capture_output=True,text=True).stdout\n"
                    "open('notes/git.txt','w').write(n.strip() + '|' + r.strip())\nopen('DONE','w').write('d')\n")
        with open(os.path.join(self.out, "sandbox", "notes", "git.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "1|")

    def test_a_sandbox_that_leaks_is_never_handed_to_an_agent(self):
        stub = Stub()
        report = ep.run_episode(SPEC, REPO, agent("open('DONE','w').write('d')\n"), self.out, grade_fn=stub, poll_s=0.05,
                                leak_check=lambda *a: ["TASK.md holds the forbidden token 'PlayerControllerLLM'"])
        self.assertEqual(report["ended_by"], "refused")
        self.assertIn("leaks", report)
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sandbox", "DONE")), "the agent was started")

    def test_the_dev_slice_is_copied_in_only_when_the_spec_asks_and_only_a_dev_split(self):
        dev = os.path.join(self.out, "dev.json")
        with open(dev, "w", encoding="utf-8") as f:
            json.dump({"split": "dev", "instances": []}, f)
        self.run_ep("open('DONE','w').write('d')\n", dev_set=dev)
        self.assertFalse(os.path.exists(os.path.join(self.out, "sandbox", "dev", "instances.json")), "the spec's dev_slice is off")
        shutil.rmtree(self.out)
        os.makedirs(self.out)
        with open(dev, "w", encoding="utf-8") as f:
            json.dump({"split": "heldout", "instances": []}, f)
        with self.assertRaises(ValueError):
            ep.run_episode(SPEC, REPO, agent("pass\n"), self.out, grade_fn=Stub(), dev_set=dev, dev_slice=True)


if __name__ == "__main__":
    unittest.main()
