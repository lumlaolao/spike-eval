"""demo_compare: the older harness gets the audience feed by a port that is idempotent and refuses a tree it cannot
place it in; both arms play the same conditions; a run never starts without a budget. No game is played here."""
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

import demo_compare as dc

OBS = "class SeatObservation {\n" + dc.OBS_ANCHOR + "}\n"
GUI = "class LlmSpikeGui {\n" + dc.CTRL_ANCHOR + dc.WRITE_ANCHOR + "}\n"


class PortTest(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp()
        self.spike = os.path.join(self.tree, dc.SPIKE)
        os.makedirs(self.spike)
        for name, text in (("SeatObservation.java", OBS), ("LlmSpikeGui.java", GUI)):
            with open(os.path.join(self.spike, name), "w") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.tree)

    def read(self, name):
        with open(os.path.join(self.spike, name)) as f:
            return f.read()

    def test_the_feed_and_its_hooks_are_ported_once(self):
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java", "SeatObservation.java", "LlmSpikeGui.java", "LlmSpikeGui.java"])
        self.assertIn("Map<Integer, Integer> handleMap()", self.read("SeatObservation.java"))
        self.assertIn("DemoFeed.attach(game, seat, observer);", self.read("LlmSpikeGui.java"))
        self.assertTrue(os.path.isfile(os.path.join(self.spike, "DemoFeed.java")))
        self.assertEqual(dc.port_feed(self.tree), ["DemoFeed.java"])  # a second port adds nothing
        self.assertEqual(self.read("LlmSpikeGui.java").count("DemoFeed.attach"), 1)
        self.assertEqual(self.read("LlmSpikeGui.java").count("DemoFeed.beforeDecisionRow"), 1)

    def test_the_ported_decision_hook_is_the_current_one(self):
        with open(os.path.join(dc.REPO, dc.SPIKE, "LlmSpikeGui.java")) as f:
            self.assertIn(dc.WRITE_HOOK, f.read())

    def test_the_ported_handle_map_is_the_current_one(self):
        with open(os.path.join(dc.REPO, dc.SPIKE, "SeatObservation.java")) as f:
            current = f.read()
        sig = "Map<Integer, Integer> handleMap() {"
        self.assertIn(sig + dc.OBS_HOOK.split(sig, 1)[1], current)

    def test_the_current_price_list_is_ported_so_the_older_run_batch_accepts_a_budget(self):
        os.makedirs(os.path.join(self.tree, "eval", "pricing"))
        self.assertEqual(dc.port_prices(self.tree), "prices.json")
        with open(os.path.join(self.tree, "eval", "pricing", "prices.json")) as f:
            self.assertIn("openai/gpt-6-luna", f.read())

    def test_a_tree_without_the_anchor_is_refused_not_guessed(self):
        with open(os.path.join(self.spike, "LlmSpikeGui.java"), "w") as f:
            f.write("class LlmSpikeGui {}\n")
        with self.assertRaises(SystemExit):
            dc.port_feed(self.tree)


class EnginePinTest(unittest.TestCase):
    """Which engine each arm runs: its own overlay copy of every patched engine file, else the pinned stock source."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def tree(self, name, files):
        root = os.path.join(self.d, name)
        for rel, text in files.items():
            self.write(os.path.join(root, "forge-overlay", rel), text)
        return root

    def test_engine_patches_are_main_sources_outside_the_harness_package(self):
        t = self.tree("t", {"forge-game/src/main/java/x/A.java": "", dc.SPIKE.split("forge-overlay/")[1] + "/S.java": "",
                            "forge-gui-desktop/src/test/java/x/T.java": "", "forge-gui/res/a.png": ""})
        self.assertEqual(dc.engine_patches(t), ["forge-game/src/main/java/x/A.java"])

    A, B = "forge-game/src/main/java/x/A.java", "forge-ai/src/main/java/x/B.java"

    def forge_with_stock_b(self):
        forge = os.path.join(self.d, "forge")
        self.write(os.path.join(forge, self.B), "class B { /* stock */ }")
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s"]):
            subprocess.run(["git", "-C", forge] + cmd, check=True)
        return forge

    def pin(self, tree, build, forge, paths, compiled=None):
        compiled = [] if compiled is None else compiled
        real = dc.subprocess.run

        def fake_run(cmd, **kw):  # javac stubbed: record what it would compile
            if os.path.basename(cmd[0]) == "javac":
                compiled.extend(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real(cmd, **kw)
        self.write(os.path.join(build, "classpath.txt"), "/cp")
        with mock.patch.object(dc.subprocess, "run", fake_run):
            dc.pin_engine(tree, build, forge, paths)

    def test_each_file_is_the_trees_overlay_copy_or_the_stock_source_and_a_missing_stock_is_refused(self):
        tree, forge = self.tree("then", {self.A: "class A { /* then */ }"}), self.forge_with_stock_b()
        build, compiled = os.path.join(self.d, "build"), []
        self.pin(tree, build, forge, [self.A, self.B], compiled)
        with open(os.path.join(build, "engine-patches.txt")) as f:
            lines = [line.split("\t") for line in f.read().splitlines()]
        self.assertEqual([(o, p) for o, _, p in lines], [("stock", self.B), ("overlay", self.A)])
        with open(os.path.join(build, "engine-src", self.B)) as f:
            self.assertIn("stock", f.read())
        with open(os.path.join(build, "engine-src", self.A)) as f:
            self.assertIn("then", f.read())
        self.assertIn("--release", compiled)
        self.assertIn(os.path.join(build, "classes"), compiled)
        with self.assertRaises(SystemExit):  # neither an overlay copy nor a stock source: refused, never guessed
            dc.pin_engine(tree, build, forge, ["forge-game/src/main/java/x/Gone.java"])

    def test_run_refuses_a_build_whose_engine_files_changed_or_were_never_recorded(self):
        tree, forge = self.tree("then", {self.A: "class A { /* then */ }"}), self.forge_with_stock_b()
        build = os.path.join(self.d, "build")
        with mock.patch.object(dc, "REPO", self.tree("now", {})):  # the current tree patches nothing here
            with self.assertRaises(SystemExit):  # no engine-patches.txt yet
                dc.check_pins({"then": (tree, build)}, forge, tree)
            self.pin(tree, build, forge, dc.pinned_paths(tree))
            dc.check_pins({"then": (tree, build)}, forge, tree)  # as built: accepted
            self.write(os.path.join(tree, "forge-overlay", self.A), "class A { /* edited after the build */ }")
            with self.assertRaises(SystemExit) as cm:
                dc.check_pins({"then": (tree, build)}, forge, tree)
        self.assertIn("run build-then again", str(cm.exception))


class ArmTest(unittest.TestCase):
    def test_an_arm_plays_the_llm_seat_against_forge_ai_with_the_feed_and_a_budget(self):
        cmd = dc.arm_command("/r", "/home", 4201, "/a.txt", "/b.txt", "openrouter:x/y", 0.4, "/out")
        flat = " ".join(cmd)
        for part in ("--seeds 4201", "--budget-usd 0.4", "--forge-home /home", "--controller-a llm",
                     "--controller-b ai", "--context-profile v4-faithful", "--smart-a openrouter:x/y", "--fast-a openrouter:x/y", "--env DEMO_FEED=1",
                     "--exploratory", "--out /out"):
            self.assertIn(part, flat)

    def test_the_loopback_launch_pins_the_same_conditions(self):
        env = dc.direct_env(4201, "/d/vintage-esper-control.txt", "/d/pioneer-red-deck-wins.txt", "m", "/out", "u")
        self.assertEqual((env["CONTROLLER_B"], env["DEMO_FEED"], env["MEMORY_TOOLS"], env["LOOKAHEAD"]),
                         ("ai", "1", "1", "1"))
        self.assertEqual(env["DECK_A_NAME"], "vintage-esper-control")
        self.assertNotIn("OPENROUTER_ALLOW_EGRESS", env)

    def test_a_run_without_a_budget_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            dc.main(["run", "--seed", "1", "--deck-a", "a", "--deck-b", "b", "--out", "/tmp/x"])
        self.assertIn("--budget-usd", str(cm.exception))

    def test_a_deck_outside_the_fixed_sets_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            dc.main(["run", "--seed", "1", "--deck-a", "burn", "--deck-b", "burn", "--budget-usd", "0.1",
                     "--out", "/tmp/x"])
        self.assertIn("vintage-esper-control", str(cm.exception))  # burn is a Forge AI deck, not the seat's

    def test_every_deck_in_the_fixed_sets_exists_and_the_sets_do_not_overlap(self):
        agent, ai = dc.deck_sets()
        self.assertFalse(set(agent) & set(ai))
        for name in list(agent) + list(ai):
            self.assertTrue(os.path.isfile(dc.pick_deck(name, [name], "x")), name)

    def test_the_stand_in_home_runs_the_isolated_classes_from_the_real_checkout_even_with_spaces(self):
        d = tempfile.mkdtemp(prefix="Sean Thomas ")  # a macOS home folder may hold a space
        try:
            forge, b = os.path.join(d, "my forge"), os.path.join(d, "build dir")
            os.makedirs(os.path.join(forge, ".git"))
            os.makedirs(os.path.join(forge, "forge-gui-desktop"))
            os.makedirs(b)
            with open(os.path.join(b, "classpath.txt"), "w") as f:
                f.write("/m2/a.jar:/m2/b.jar\n")
            java = os.path.join(d, "fake java")  # prints where it ran and the classpath it was given
            with open(java, "w") as f:
                f.write('#!/bin/bash\npwd\nwhile [ $# -gt 0 ] && [ "$1" != "-cp" ]; do shift; done\necho "$2"\necho "$3"\n')
            os.chmod(java, 0o755)
            script = dc.standin_home(os.path.join(d, "home"), os.path.join(b, "classes"),
                                     os.path.join(b, "classpath.txt"), forge, java)
            self.assertTrue(os.access(script, os.X_OK))
            out = subprocess.run([script], capture_output=True, text=True, check=True, timeout=30).stdout.splitlines()
            self.assertEqual(os.path.realpath(out[0]), os.path.realpath(os.path.join(forge, "forge-gui-desktop")))
            self.assertEqual(out[1], os.path.join(b, "classes") + ":/m2/a.jar:/m2/b.jar")
            self.assertEqual(out[2], "forge.spike.LlmSpikeHeadless")
        finally:
            shutil.rmtree(d)

    def test_the_build_hands_acceptance_the_jdk_it_found(self):
        seen = {}

        def fake_run(cmd, **kw):  # acceptance stubbed: record its env, compile nothing
            seen.update(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(dc.subprocess, "run", fake_run), mock.patch.object(dc, "jdk", lambda: "/brew/jdk"):
            with self.assertRaises(SystemExit):  # nothing compiled: refused after acceptance ran
                dc.compile_arm(dc.REPO, tempfile.mkdtemp(), "/forge", dc.REPO)
        self.assertEqual((seen.get("JAVA_HOME"), seen.get("FORGE_HOME")), ("/brew/jdk", "/forge"))

    def run_dry(self, arm):
        d = tempfile.mkdtemp()
        builds = {}
        for name in ("now", "then"):
            home = os.path.join(d, name, "forge-home")
            os.makedirs(home)
            open(os.path.join(home, "run-llm-headless.sh"), "w").close()
            builds[name] = os.path.join(d, name)
        checked = []
        out = io.StringIO()
        with mock.patch.object(dc, "check_pins", lambda arms, forge, wt: checked.append(sorted(arms))), \
                contextlib.redirect_stdout(out):
            dc.main(["run", "--seed", "1", "--deck-a", "vintage-esper-control", "--deck-b", "burn", "--budget-usd",
                     "0.4", "--out", os.path.join(d, "out"), "--dry-run", "--arm", arm, "--now-build", builds["now"],
                     "--build", builds["then"], "--worktree", d])
        shutil.rmtree(d)
        return [line for line in out.getvalue().splitlines() if line.startswith("[")], checked

    def test_a_pair_splits_the_budget_and_one_arm_gets_all_of_it(self):
        lines, checked = self.run_dry("both")
        self.assertEqual([l.split()[0] for l in lines], ["[now]", "[then]"])
        self.assertTrue(all("--budget-usd 0.2 " in l for l in lines))
        lines, checked2 = self.run_dry("then")
        self.assertEqual([l.split()[0] for l in lines], ["[then]"])
        self.assertIn("--budget-usd 0.4 ", lines[0])
        self.assertEqual(checked + checked2, [["now", "then"], ["now", "then"]])  # both pins checked either way

    def test_a_commit_without_a_price_list_is_refused_before_any_worktree_is_made(self):
        self.assertTrue(dc.has_price_list("HEAD"))
        d = tempfile.mkdtemp()
        try:
            open(os.path.join(d, ".git"), "w").close()  # passes the checkout guard
            calls = []

            def git(cmd, **kw):  # the engine pins agree, so only the price check can stop the build
                calls.append(cmd)
                if "rev-parse" in cmd or (len(cmd) > 3 and cmd[3] == "show"):
                    return subprocess.CompletedProcess(cmd, 0, "34a8662\n", "")
                raise AssertionError("unexpected call: %s" % cmd)
            with mock.patch.object(dc, "has_price_list", lambda c: False), \
                    mock.patch.object(dc.subprocess, "run", git):
                with self.assertRaises(SystemExit) as cm:
                    dc.main(["build-then", "--commit", "e1324cfd", "--forge", d, "--worktree", os.path.join(d, "w")])
            self.assertIn("too old for a budgeted run", str(cm.exception))
            self.assertFalse(any("worktree" in c for c in calls))
            # and with a price list the same fixture does reach the worktree: the check is what stopped it
            with mock.patch.object(dc, "has_price_list", lambda c: True), \
                    mock.patch.object(dc.subprocess, "run", git):
                with self.assertRaises(AssertionError) as cm:
                    dc.main(["build-then", "--commit", "e1324cfd", "--forge", d, "--worktree", os.path.join(d, "w")])
            self.assertIn("worktree", str(cm.exception))
        finally:
            shutil.rmtree(d)

    def test_parallel_starts_both_arms_before_waiting_and_sequential_waits_between(self):
        for parallel, want in ((True, ["start now", "start then", "wait", "wait"]),
                               (False, ["start now", "wait", "start then", "wait", "wait", "wait"])):
            events = []

            class Proc:
                def __init__(self, cmd, **kw):
                    events.append("start " + ("then" if "harness-then" in " ".join(cmd) or "/then/" in " ".join(cmd) else "now"))

                def wait(self):
                    events.append("wait")

                def poll(self):
                    return 0
            d = tempfile.mkdtemp()
            try:
                for name in ("now", "then"):
                    os.makedirs(os.path.join(d, name, "forge-home"))
                    open(os.path.join(d, name, "forge-home", "run-llm-headless.sh"), "w").close()
                args = ["run", "--seed", "1", "--deck-a", "vintage-esper-control", "--deck-b", "burn", "--budget-usd",
                        "0.4", "--out", os.path.join(d, "out"), "--now-build", os.path.join(d, "now"), "--build",
                        os.path.join(d, "then"), "--worktree", os.path.join(d, "harness-then")]
                out = io.StringIO()
                with mock.patch.object(dc, "check_pins", lambda *a: None), \
                        mock.patch.object(dc, "arm_record", lambda repo, name: {"arm": name, "commit": "c0ffee"}), \
                        mock.patch.object(dc.subprocess, "Popen", Proc), contextlib.redirect_stdout(out):
                    dc.main(args + (["--parallel"] if parallel else []))
                with open(os.path.join(d, "out", "then", "arm.json")) as f:  # the viewer names the arm from this
                    self.assertEqual(json.load(f), {"arm": "then", "commit": "c0ffee"})
            finally:
                shutil.rmtree(d)
            self.assertEqual(events[:len(want)], want)
            printed = out.getvalue()
            if parallel:  # the live line comes before the games start, while there is something to watch
                self.assertLess(printed.index("--live"), printed.index("[now]"))
            else:
                self.assertNotIn("--live", printed)

    def test_a_stop_while_the_arms_play_stops_every_arms_process_group(self):
        killed = []

        class Proc:
            n = 0

            def __init__(self, cmd, **kw):
                Proc.n += 1
                self.pid = 1000 + Proc.n

            def wait(self):
                raise SystemExit(143)  # a SIGTERM arriving while the arms play

            def poll(self):
                return None
        d = tempfile.mkdtemp()
        try:
            for name in ("now", "then"):
                os.makedirs(os.path.join(d, name, "forge-home"))
                open(os.path.join(d, name, "forge-home", "run-llm-headless.sh"), "w").close()
            args = ["run", "--seed", "1", "--deck-a", "vintage-esper-control", "--deck-b", "burn", "--budget-usd", "0.4",
                    "--out", os.path.join(d, "out"), "--now-build", os.path.join(d, "now"), "--build",
                    os.path.join(d, "then"), "--worktree", os.path.join(d, "harness-then"), "--parallel"]
            before = signal.getsignal(signal.SIGINT)
            with mock.patch.object(dc, "check_pins", lambda *a: None), \
                    mock.patch.object(dc, "arm_record", lambda repo, name: {"arm": name}), \
                    mock.patch.object(dc.subprocess, "Popen", Proc), \
                    mock.patch.object(dc.os, "killpg", lambda pid, sig: killed.append(pid)), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                dc.main(args)
            self.assertEqual(signal.getsignal(signal.SIGINT), before)  # the handlers are put back
        finally:
            shutil.rmtree(d)
        self.assertEqual(sorted(killed), [1001, 1002])

    def test_an_arm_records_the_commit_it_played(self):
        rec = dc.arm_record(dc.REPO, "now")
        self.assertEqual((rec["arm"], len(rec["commit"]), len(rec["date"])), ("now", 40, 10))

    def test_a_forge_checkout_that_is_a_git_worktree_passes_the_guard(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, ".git"), "w") as f:
                f.write("gitdir: /elsewhere/.git/worktrees/x\n")
            with self.assertRaises(SystemExit) as cm:  # stops later, at the pin check: not "no Forge checkout"
                dc.main(["build-then", "--commit", "e1324cfd", "--forge", d])
            self.assertNotIn("no Forge checkout", str(cm.exception))
        finally:
            shutil.rmtree(d)

    def test_an_explicit_forge_home_wins_else_the_checkout_every_tool_finds(self):
        env = {k: v for k, v in os.environ.items() if k != "FORGE_HOME"}
        found = lambda: "/somewhere/code/forge"  # a checkout forge_home would fall back to, on any machine
        with mock.patch.object(dc.puzzle_format, "forge_home", found):
            with mock.patch.dict(os.environ, {"FORGE_HOME": "$HOME/typo-forge"}):
                self.assertEqual(dc.default_forge(), os.path.expanduser("~/typo-forge"))  # never the fallback
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(dc.default_forge(), "/somewhere/code/forge")
        with self.assertRaises(SystemExit) as cm:
            dc.main(["build-then", "--commit", "e1324cfd", "--forge", os.path.expanduser("~/typo-forge")])
        self.assertIn("no Forge checkout", str(cm.exception))

    def test_the_jdk_is_java_home_else_homebrews_else_the_javac_on_path(self):
        d = tempfile.mkdtemp()
        try:
            home = os.path.join(d, "jdk")
            os.makedirs(os.path.join(home, "bin"))
            open(os.path.join(home, "bin", "javac"), "w").close()
            brew = os.path.join(d, "brew")
            os.makedirs(brew)
            with mock.patch.dict(os.environ, {"JAVA_HOME": home}), mock.patch.object(dc, "BREW_JDKS", ("/nowhere", brew)):
                self.assertEqual(dc.jdk_tool("javac"), os.path.join(home, "bin", "javac"))  # JAVA_HOME first
            with mock.patch.dict(os.environ, {"JAVA_HOME": os.path.join(d, "none")}), \
                    mock.patch.object(dc, "BREW_JDKS", ("/nowhere", brew)):
                self.assertEqual(dc.jdk(), brew)  # the Mac default (here the Intel prefix)
            with mock.patch.dict(os.environ, {"JAVA_HOME": ""}), mock.patch.object(dc, "BREW_JDKS", ("/nowhere",)), \
                    mock.patch.object(dc.shutil, "which", lambda n: os.path.join(home, "bin", "javac")):
                self.assertEqual(dc.jdk(), os.path.realpath(home))  # Linux: the javac on PATH
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
