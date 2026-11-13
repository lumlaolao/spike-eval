"""harness_env_episode.py: an episode ends when the agent says so (or at the cap), and is graded outside the sandbox.

The agents here are small scripts; grading is a stub that records what it was handed, so these tests need no JVM.
What would make them fail: an episode that ends before DONE, one that runs past its cap, a submission taken from
anywhere but the sandbox's overlay, a checkpoint graded while the agent still runs, a sandbox with history or a leak
handed to the agent, a dev slice copied in that the spec did not ask for, a refused episode that leaves no record of
what was checked, or a grader setting (the engine checkout, the held-out set) found wrong only after the agent ran.
"""
import collections
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import fetch_comp_rules
import harness_env_aicount as ac
import harness_env_episode as ep
import harness_env_generate as hgen
import harness_env_grade as hg
import harness_env_task_grade as htg

REPO = str(evalpath.REPO)
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


@unittest.skipUnless(fetch_comp_rules.present(), fetch_comp_rules.MISSING)
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

    def test_done_ends_the_episode_only_once_its_line_is_written(self):
        # Creating DONE and writing its line are two steps; under load the poll can land between them.
        # Ending there killed the agent and recorded "" (acceptance @ 725bbd5, OUT=/tmp/acc-pw-725bbd5, part2/pytests.log).
        report, _ = self.run_ep(
            "import time\nopen('DONE','w').close()\ntime.sleep(1)\n"
            "open('DONE','w').write('submit HEAD')\ntime.sleep(30)\n")
        self.assertEqual(report["ended_by"], "agent")
        self.assertEqual(report["done"], "submit HEAD")
        self.assertLess(report["wall_s"], 15)

    def test_a_done_line_written_in_two_parts_is_recorded_whole(self):
        report, _ = self.run_ep(
            "import time\nf = open('DONE','w')\nf.write('submit '); f.flush()\ntime.sleep(1)\n"
            "f.write('HEAD\\n'); f.close()\ntime.sleep(30)\n")
        self.assertEqual(report["done"], "submit HEAD")

    def test_a_done_line_ending_in_a_newline_ends_the_episode_at_once(self):
        report, _ = self.run_ep("import time\nopen('DONE','w').write('submit HEAD\\n')\ntime.sleep(30)\n",
                                done_grace_s=30)
        self.assertEqual((report["ended_by"], report["done"]), ("agent", "submit HEAD"))
        self.assertLess(report["wall_s"], 15, "ended on the newline, not after the grace")

    def test_a_done_that_is_not_a_regular_file_never_blocks_the_cap(self):
        report, _ = self.run_ep("import os, time\nos.mkfifo('DONE')\ntime.sleep(30)\n", cap_s=2)
        self.assertEqual(report["ended_by"], "cap")

    def test_an_empty_done_left_unchanged_still_ends_the_episode(self):
        # A finished agent that only touches DONE and stays alive is the agent's end, not the cap's.
        report, _ = self.run_ep("import time\nopen('DONE','w').close()\ntime.sleep(30)\n", done_grace_s=1)
        self.assertEqual(report["ended_by"], "agent")
        self.assertEqual(report["done"], "")
        self.assertLess(report["wall_s"], 10)

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
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            self.assertIn("TASK.md", json.load(f)["files"], "a refused episode records what was checked")

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

    def test_the_dev_slice_is_released_when_the_spec_asks_and_the_sandbox_is_checked_again(self):
        dev = os.path.join(self.out, "dev.json")
        with open(dev, "w", encoding="utf-8") as f:
            json.dump({"split": "dev", "instances": [{"card": "Grizzly Bears"}]}, f)
        report, _ = self.run_ep("open('DONE','w').write('d')\n", dev_set=dev, dev_slice=True)
        self.assertEqual(report["ended_by"], "agent", report.get("leaks"))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "sandbox", "dev", "instances.json")))
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            self.assertIn("dev/instances.json", json.load(f)["files"], "the manifest written is the one checked")

    def test_an_episode_cannot_be_run_without_a_grader(self):
        self.assertIs(inspect.signature(ep.run_episode).parameters["grade_fn"].default, inspect.Parameter.empty)

    def test_the_sandbox_is_checked_again_after_the_repository_is_made_and_a_leak_then_refuses(self):
        seen = []

        def check(spec, repo, sandbox, manifest):
            seen.append(os.path.isdir(os.path.join(sandbox, ".git")))
            return ["late leak"] if len(seen) == 2 else []

        stub = Stub()
        report = ep.run_episode(SPEC, REPO, agent("open('DONE','w').write('d')\n"), self.out, grade_fn=stub,
                                poll_s=0.05, leak_check=check)
        self.assertEqual(seen, [False, True])
        self.assertEqual(report["ended_by"], "refused")
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sandbox", "DONE")), "the agent was started")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def git_checkout(root, files):
    """A one-commit git repository holding `files` ({relative path: text})."""
    for rel, text in files.items():
        write(root, rel, text)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "pin"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True)
    return root


HEAD = "seat\tinstance\tfamily\thook\tlegal\treached\tmin_requests\tasked\toffered\tcarried\tshown\tnoparse\tpass\tai\tai_payment\tnote\n"


class JvmGraderTest(unittest.TestCase):
    """The episode's grade path, with javac and java stubbed: what it refuses, what it withholds, what it hands on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sub = os.path.join(self.tmp, "sub")
        write(self.sub, "harness-env.properties", "seat.factory=a.b.MySeat\n")
        write(self.sub, "forge-gui-desktop/src/main/java/a/b/MySeat.java", "package a.b;\nclass MySeat {}\n")
        self.set = write(self.tmp, "set.json", json.dumps({"split": "heldout", "set_sha": "abc"}))
        self.cp = write(self.tmp, "cp.txt", "/x.jar")
        self.forge = git_checkout(os.path.join(self.tmp, "forge"), {
            ac.ENGINE_PATH: "class PlayerControllerAi {}\n",
            "forge-game/src/main/java/forge/game/zone/MagicStack.java": "class MagicStack {}\n"})
        self.calls, self.ai = [], "0"
        self.real = hg.subprocess.run
        runner = mock.patch.object(hg.subprocess, "run", self.fake)
        runner.start()
        self.addCleanup(runner.stop)
        patcher = mock.patch.object(hg.harness_env_aicount, "write", lambda forge, ledger, out, source=None: (
            write(out, "PlayerControllerAi.java", "x"), collections.Counter()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake(self, cmd, **kw):
        if cmd[0] == "git":
            return self.real(cmd, **kw)
        self.calls.append((cmd, kw))
        report = [a.split("=", 1)[1] for a in cmd if a.startswith("-Dharness.envgrade.report=")]
        if report:
            with open(report[0], "w", encoding="utf-8") as f:
                f.write(HEAD + "\t".join(["candidate", "i", "combat", "declareBlockers", "2", "2", "1", "1", "1", "1",
                                           "1", "1", "1", self.ai, self.ai, ""]) + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    def grade(self, **kw):
        kw.setdefault("set_file", self.set)
        return ep.jvm_grader(self.cp, self.forge, "/jdk", **kw)(self.sub, os.path.join(self.tmp, "g"))

    @contextlib.contextmanager
    def brew_jdk(self):
        """No JAVA_HOME, and Homebrew's JDK (a javac that exits 0) where the task grader looks; yields its home."""
        home = os.path.join(self.tmp, "brew", "libexec/openjdk.jdk/Contents/Home")
        os.chmod(write(home, "bin/javac", "#!/bin/sh\nexit 0\n"), 0o755)
        env = {k: v for k, v in os.environ.items() if k != "JAVA_HOME"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(htg, "JDK_DIRS", (os.path.join(self.tmp, "brew"),)):
            yield home

    def test_with_no_java_home_the_grader_compiles_with_the_jdk_the_task_grader_finds_never_usr_bin(self):
        with self.brew_jdk() as home:
            ep.jvm_grader(self.cp, self.forge, set_file=self.set)(self.sub, os.path.join(self.tmp, "g"))
        self.assertEqual(self.calls[0][0][0], os.path.join(home, "bin", "javac"))
        self.assertEqual(self.calls[0][1]["env"]["JAVA_HOME"], home)

    def test_with_no_jdk_at_all_the_grader_is_refused_before_any_candidate_is_scored(self):
        env = {k: v for k, v in os.environ.items() if k != "JAVA_HOME"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(htg, "JDK_DIRS", ()), \
                mock.patch.object(htg.shutil, "which", return_value=None):
            with self.assertRaises(htg.SetupError):
                ep.jvm_grader(self.cp, self.forge, set_file=self.set)
        self.assertEqual(self.calls, [])

    def test_an_unmarked_engine_patch_scores_zero_with_the_reason_and_builds_nothing(self):
        write(self.sub, "forge-game/src/main/java/forge/game/zone/MagicStack.java", "class MagicStack {}\n")
        s = self.grade(trust_candidate=True)
        self.assertEqual((s["benchmark"], s["dense"]), (0.0, 0.0))
        self.assertIn("unmarked engine patch", s["reason"])
        self.assertEqual(self.calls, [])

    def test_a_class_in_the_grader_package_scores_zero(self):
        write(self.sub, "forge-gui-desktop/src/main/java/forge/harnessenv/grader/AiCounter.java",
              "package forge.harnessenv.grader;\npublic final class AiCounter {}\n")
        s = self.grade(trust_candidate=True)
        self.assertEqual(s["benchmark"], 0.0)
        self.assertIn("forge.harnessenv", s["reason"])

    def test_without_trust_the_candidate_is_built_but_never_run_and_gets_no_score(self):
        s = self.grade()
        self.assertIsNone(s["benchmark"])
        self.assertIsNone(s["dense"])
        self.assertTrue(s["status"].startswith("unscored: in-JVM grader"), s)
        self.assertFalse(s["trust_candidate"])
        self.assertEqual(len(self.calls), 1, "javac only")
        self.assertTrue(self.calls[0][0][0].endswith("javac"))

    def test_with_trust_it_is_scored_and_the_trust_is_recorded(self):
        s = self.grade(trust_candidate=True)
        self.assertEqual(s["benchmark"], 1.0)
        self.assertTrue(s["trust_candidate"])
        self.assertEqual(s["passed"], 1)

    def test_a_missing_counter_scores_zero_not_ungated(self):
        self.ai = "-1"
        s = self.grade(trust_candidate=True)
        self.assertEqual((s["benchmark"], s["dense"]), (0.0, 0.0))
        self.assertIn("counter off", s["reason"])

    def test_javac_and_java_get_a_scrubbed_environment(self):
        with mock.patch.dict(os.environ, HARNESS_ENV_SEED="s3cret", OPENROUTER_API_KEY="k", OPENROUTER_ALLOW_EGRESS="1"):
            self.grade(trust_candidate=True)
        self.assertEqual(len(self.calls), 2)
        for cmd, kw in self.calls:
            env = kw.get("env")
            self.assertIsNotNone(env, cmd[0])
            self.assertEqual(env["JAVA_HOME"], "/jdk")
            self.assertFalse([k for k in env if k.startswith("OPENROUTER") or k == "HARNESS_ENV_SEED"], env)

    def test_a_held_out_set_is_drawn_at_grade_time_from_the_secret_seed(self):
        cards = os.path.join(self.tmp, "cards")
        for i in range(12):
            write(cards, "c/card%d.txt" % i, "Name:Card %d\nManaCost:1 G\nTypes:Creature Bear\nPT:2/2\nK:Flying\n" % i)
        with mock.patch.dict(os.environ, HARNESS_ENV_SEED="s3cret"):
            s = self.grade(set_file=None, cards=cards, trust_candidate=True)
        drawn = os.path.join(self.tmp, "g", "set.json")
        with open(drawn, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["split"], "heldout")
        self.assertEqual(st["seed_sha"], hgen.sha("s3cret")[:16])
        self.assertTrue(st["instances"])
        self.assertEqual(s["set_sha"], st["set_sha"])
        java = self.calls[-1][0]
        self.assertIn("-Dharness.envgrade.set=" + drawn, java)
        self.assertNotIn("s3cret", json.dumps([c for c, _ in self.calls]))

    def test_a_set_that_is_not_held_out_is_refused(self):
        dev = write(self.tmp, "dev.json", json.dumps({"split": "dev"}))
        with self.assertRaises(ValueError):
            ep.jvm_grader(self.cp, self.forge, "/jdk", set_file=dev)

    def test_the_classpath_and_engine_are_required_before_the_agent_runs(self):
        for name in ("classpath_file", "forge"):
            self.assertIs(inspect.signature(ep.jvm_grader).parameters[name].default, inspect.Parameter.empty, name)
        for cp, forge in ((None, self.forge), ("/nope/cp.txt", self.forge), (self.cp, None), (self.cp, "/nope/forge")):
            with self.assertRaises(ValueError, msg=(cp, forge)):
                ep.jvm_grader(cp, forge, "/jdk", set_file=self.set)
        out = os.path.join(self.tmp, "o")
        self.assertEqual(ep.main(["ep", SPEC, out, "--set", self.set, "--forge", self.forge, "--", "true"]), 2)
        self.assertFalse(os.path.exists(os.path.join(out, "sandbox")), "the agent's sandbox was built")

    def test_the_engine_must_be_a_checkout_whose_head_has_the_ai_controller(self):
        not_git = os.path.join(self.tmp, "plain")
        write(not_git, ac.ENGINE_PATH, "class PlayerControllerAi {}\n")
        without = git_checkout(os.path.join(self.tmp, "other"), {"README": "x\n"})
        for forge in (not_git, without):
            with self.assertRaises(ValueError, msg=forge):
                ep.jvm_grader(self.cp, forge, "/jdk", set_file=self.set)
            out = os.path.join(self.tmp, "o-" + os.path.basename(forge))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                rc = ep.main(["ep", SPEC, out, "--classpath", self.cp, "--set", self.set, "--forge", forge, "--", "true"])
            self.assertEqual(rc, 2)
            self.assertIn("PlayerControllerAi", printed.getvalue())
            self.assertFalse(os.path.exists(os.path.join(out, "sandbox")), "the agent's sandbox was built")
        # git's English message, and no walk up into a work tree that holds the temp dir
        quiet = {"LC_ALL": "C", "GIT_CEILING_DIRECTORIES": self.tmp}
        with self.assertRaises(ValueError) as cm, mock.patch.dict(os.environ, quiet):
            ep.jvm_grader(self.cp, not_git, "/jdk", set_file=self.set)
        self.assertIn("not a git repository", str(cm.exception), "git's own reason")

    def test_the_engine_refusal_says_what_git_said(self):
        # A checkout another user owns fails `git show` with "dubious ownership"; reported as "not a checkout"
        # it sends the operator looking for the wrong fault.
        said = "fatal: detected dubious ownership in repository at '%s'" % self.forge
        failed = subprocess.CalledProcessError(128, ["git", "show"], output="", stderr=said + "\n")
        with mock.patch.object(ep.harness_env_aicount, "engine_source", side_effect=failed), \
                self.assertRaises(ValueError) as cm:
            ep.jvm_grader(self.cp, self.forge, "/jdk", set_file=self.set)
        self.assertIn(said, str(cm.exception))
        self.assertIn(self.forge, str(cm.exception))
        # git that writes nothing to stderr still leaves a reason: the exit status, not an empty "(git: )".
        silent = subprocess.CalledProcessError(128, ["git", "show"], output="", stderr="")
        with mock.patch.object(ep.harness_env_aicount, "engine_source", side_effect=silent), \
                self.assertRaises(ValueError) as cm:
            ep.jvm_grader(self.cp, self.forge, "/jdk", set_file=self.set)
        self.assertIn("exit status", str(cm.exception))

    def test_a_missing_or_malformed_set_file_exits_cleanly_before_the_agent_runs(self):
        bad = write(self.tmp, "bad.json", "{not json")
        listed = write(self.tmp, "list.json", "[1, 2]")
        for set_file in (os.path.join(self.tmp, "nope.json"), bad, listed):
            out = os.path.join(self.tmp, "o-" + os.path.basename(set_file))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                rc = ep.main(["ep", SPEC, out, "--classpath", self.cp, "--set", set_file, "--forge", self.forge,
                              "--", "true"])
            self.assertEqual(rc, 2, set_file)
            self.assertIn(set_file, printed.getvalue())
            self.assertFalse(os.path.exists(os.path.join(out, "sandbox")), "the agent's sandbox was built")

    def test_a_counter_that_misses_a_hook_in_the_submissions_copy_scores_zero(self):
        real = hg.build_classes
        hg.build_classes = lambda *a, **k: (None, "/c", ["declareBlockers"], ["chooseSpellAbilityToPlay"])
        try:
            s = self.grade(trust_candidate=True)
        finally:
            hg.build_classes = real
        self.assertEqual((s["benchmark"], s["dense"]), (0.0, 0.0))
        self.assertEqual(s["reason"], "counter incomplete: chooseSpellAbilityToPlay")

    def test_an_episode_grade_needs_a_generated_set(self):
        with self.assertRaises(ValueError):
            ep.jvm_grader(self.cp, self.forge, "/jdk")
        self.assertEqual(ep.main(["ep", SPEC, os.path.join(self.tmp, "o"), "--", "true"]), 2)
        with mock.patch.dict(os.environ):
            os.environ.pop("HARNESS_ENV_SEED", None)
            with self.assertRaises(ValueError):
                ep.jvm_grader(self.cp, self.forge, "/jdk", cards=self.tmp)


if __name__ == "__main__":
    unittest.main()
