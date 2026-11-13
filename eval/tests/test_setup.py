"""setup.sh: one command from a fresh clone to a playable install. Run against stub git / java / mvn that log their
arguments, in a scratch copy of the repo, so no network, build or real Forge checkout is touched."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import textwrap
import unittest
from pathlib import Path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

REPO = evalpath.REPO
PIN = (REPO / "FORGE_COMMIT").read_text().strip()

GIT_STUB = textwrap.dedent("""\
    #!/bin/bash
    echo "git $*" >> "$STUB_LOG"
    dir=.; if [ "$1" = -C ]; then dir="$2"; shift 2; fi
    case "$1" in
      init) mkdir -p "${@: -1}/.git" ;;
      fetch) if [ -n "$STUB_FAIL_FETCH_ONCE" ] && [ -e "$STUB_FAIL_FETCH_ONCE" ]; then
               rm "$STUB_FAIL_FETCH_ONCE"; echo "fatal: the remote end hung up" >&2; exit 128; fi ;;
      checkout) mkdir -p "$dir/forge-gui-desktop"; echo "$STUB_PIN" > "$dir/.git/HEAD_SHA" ;;
      rev-parse) cat "$dir/.git/HEAD_SHA" 2>/dev/null || echo unknown ;;
    esac
    exit 0
""")
JAVA_STUB = textwrap.dedent("""\
    #!/bin/bash
    if [ "$STUB_JAVA" = macos-stub ]; then       # macOS's /usr/bin/java with no JDK installed
      echo "The operation couldn't be completed. Unable to locate a Java Runtime." >&2; exit 1; fi
    echo "Picked up JAVA_TOOL_OPTIONS: -Dx=1" >&2
    echo "openjdk version \\"$STUB_JAVA\\" 2026-08-18" >&2
""")
MVN_STUB = textwrap.dedent("""\
    #!/bin/bash
    if [ "$1" = -v ]; then
      echo "Apache Maven ${STUB_MVN:-3.9.11} (abc)"; echo "Java version: ${STUB_MVN_JAVA:-21.0.2}, vendor: x"; exit 0; fi
    echo "mvn $* (in $PWD) java=$(command -v java) JAVA_HOME=$JAVA_HOME" >> "$STUB_LOG"
""")


class SetupTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        for f in ("setup.sh", "install.sh", "FORGE_COMMIT"):
            shutil.copy(REPO / f, self.repo / f)
        shutil.copytree(REPO / "forge-overlay", self.repo / "forge-overlay")
        shutil.copytree(REPO / "engine-patches", self.repo / "engine-patches")
        (self.repo / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "platform.sh", self.repo / "scripts" / "platform.sh")
        (self.repo / "eval").mkdir()
        shutil.copy(REPO / "eval" / "engine_patches.py", self.repo / "eval" / "engine_patches.py")
        self.stubs = root / "stubs"
        self.stubs.mkdir()
        for name, body in (("git", GIT_STUB), ("java", JAVA_STUB), ("mvn", MVN_STUB), ("python3", None)):
            p = self.stubs / name
            if body is None:
                p.symlink_to(shutil.which("python3"))
                continue
            p.write_text(body)
            p.chmod(0o755)
        # The system's own tools, minus the ones stubbed or taken away (this machine has a real mvn in /usr/bin).
        self.sysbin = root / "sysbin"
        self.sysbin.mkdir()
        for d in ("/usr/bin", "/bin"):
            for f in os.listdir(d):
                if f not in ("git", "java", "mvn", "python3") and not os.path.lexists(self.sysbin / f):
                    (self.sysbin / f).symlink_to(os.path.join(d, f))
        self.forge = root / "forge"
        self.log = root / "stub.log"

    def tearDown(self):
        self._tmp.cleanup()

    def setup(self, answers="sk-or-test-key\ny\n", java="21.0.2", **env):
        # SETUP_JDK_DIRS: no Homebrew JDK unless a test names one (a Mac that has one would use it, not the stub).
        base = {"PATH": "%s:%s" % (self.stubs, self.sysbin), "HOME": self._tmp.name, "STUB_LOG": str(self.log),
                "STUB_PIN": PIN, "STUB_JAVA": java, "SETUP_JDK_DIRS": str(Path(self._tmp.name) / "no-jdk")}
        base.update(env)
        return subprocess.run(["bash", str(self.repo / "setup.sh"), "--forge-dir", str(self.forge)], input=answers,
                              capture_output=True, text=True, env=base, cwd=self._tmp.name, timeout=120)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def secrets(self):
        return (self.repo / "secrets.env").read_text()

    def test_a_fresh_machine_gets_forge_at_the_pin_the_overlay_a_build_and_the_key(self):
        p = self.setup()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.calls()
        self.assertIn("fetch --depth 1 origin %s" % PIN, calls)
        # -DskipLaunch4j: why is at the mvn line in setup.sh.
        self.assertIn("mvn -q -pl forge-gui-desktop -am install -DskipTests -DskipLaunch4j (in %s)" % self.forge, calls)
        # core.eol, not core.autocrlf alone: Forge's own .gitattributes (text=auto) checks out CRLF on Windows.
        self.assertIn("config core.eol lf", calls, "Forge's files keep the pin's line endings on Windows")
        # With -am, as run-llm-spike.sh's own hint has it: without it Maven resolves the sibling modules from the
        # repository, whose installed poms name an unresolvable ${revision} parent (a real fresh setup, 2026-10-25).
        self.assertIn("mvn -q -pl forge-gui-desktop -am dependency:build-classpath", calls)
        self.assertLess(calls.index("fetch"), calls.index("install -DskipTests"))
        self.assertTrue((self.forge / "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java").exists(),
                        "the overlay is laid on the checkout")
        # The engine patches are applied to the originals at the pin (the stub git hands back an empty file).
        player = "forge-game/src/main/java/forge/game/player/Player.java"
        self.assertIn("show %s:%s" % (PIN, player), calls)
        self.assertTrue((self.forge / player).exists(), "the patched engine file is written into the checkout")
        self.assertEqual((self.repo / ".forge-home").read_text().strip(), str(self.forge))
        self.assertIn("OPENROUTER_API_KEY=sk-or-test-key\n", self.secrets())
        self.assertIn("\nOPENROUTER_ALLOW_EGRESS=1\n", self.secrets())
        self.assertEqual(stat.S_IMODE((self.repo / "secrets.env").stat().st_mode), 0o600)
        self.assertNotIn("sk-or-test-key", p.stdout + p.stderr, "the key is never echoed")
        self.assertIn("play-vs-llm.sh", p.stdout)

    def test_declining_consent_keeps_the_key_and_writes_no_consent(self):
        self.assertEqual(self.setup(answers="sk-or-test-key\nn\n").returncode, 0)
        self.assertIn("OPENROUTER_API_KEY=sk-or-test-key\n", self.secrets())
        self.assertNotIn("\nOPENROUTER_ALLOW_EGRESS=1", self.secrets())
        self.assertIn("# OPENROUTER_ALLOW_EGRESS=1", self.secrets())

    def test_a_key_in_the_shell_is_saved_only_on_a_yes(self):
        p = self.setup(answers="y\ny\n", OPENROUTER_API_KEY="sk-or-from-shell", SETUP_ASSUME_TTY="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("is set in your shell", p.stdout)
        self.assertIn("OPENROUTER_API_KEY=sk-or-from-shell\n", self.secrets())
        self.assertNotIn("sk-or-from-shell", p.stdout + p.stderr, "the key is never echoed")

    def test_enter_at_the_shell_key_question_means_no_and_the_typed_key_is_saved(self):
        p = self.setup(answers="\nsk-or-typed\ny\n", OPENROUTER_API_KEY="sk-or-from-shell", SETUP_ASSUME_TTY="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("OPENROUTER_API_KEY=sk-or-typed\n", self.secrets())
        self.assertNotIn("sk-or-from-shell", self.secrets())

    def test_a_key_in_the_shell_is_not_saved_when_nobody_is_at_the_terminal(self):
        # A piped "y" is not a person: the question is never asked, so the first line is read as the typed key.
        p = self.setup(answers="y\ny\n", OPENROUTER_API_KEY="sk-or-from-shell")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("sk-or-from-shell", self.secrets() + p.stdout + p.stderr)
        self.assertNotIn("is set in your shell. Use it", p.stdout)
        self.assertIn("OPENROUTER_API_KEY=y\n", self.secrets())
        self.assertIn("was not saved", p.stderr)

    def test_an_existing_key_is_kept_and_not_asked_for_again(self):
        (self.repo / "secrets.env").write_text("OPENROUTER_API_KEY=old\nOPENROUTER_ALLOW_EGRESS=1\n")
        p = self.setup(answers="")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.secrets(), "OPENROUTER_API_KEY=old\nOPENROUTER_ALLOW_EGRESS=1\n")

    def test_a_java_older_than_17_is_refused_before_anything_is_fetched(self):
        p = self.setup(java="11.0.20")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Java 17", p.stderr)
        self.assertNotIn("fetch", self.calls())

    def test_a_missing_tool_names_how_to_install_it(self):
        (self.stubs / "mvn").unlink()
        p = self.setup()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("mvn", p.stderr)
        self.assertRegex(p.stderr, "brew install|apt")

    def test_a_fetch_that_fails_midway_is_finished_by_the_next_run(self):
        flag = Path(self._tmp.name) / "fail-once"
        flag.write_text("")
        p = self.setup(STUB_FAIL_FETCH_ONCE=str(flag))
        self.assertNotEqual(p.returncode, 0, "the dropped fetch is reported")
        self.assertNotIn("install -DskipTests", self.calls())
        p = self.setup(STUB_FAIL_FETCH_ONCE=str(flag))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("install -DskipTests", self.calls())

    def test_macos_without_a_jdk_names_the_install_instead_of_stopping_silently(self):
        p = self.setup(java="macos-stub")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("Java 17", p.stderr)
        self.assertNotIn("fetch", self.calls())

    def test_a_homebrew_jdk_is_used_before_the_java_on_path(self):
        jdk = Path(self._tmp.name) / "brew-openjdk@17"
        (jdk / "bin").mkdir(parents=True)
        (jdk / "libexec/openjdk.jdk/Contents/Home").mkdir(parents=True)
        (jdk / "bin/java").write_text(JAVA_STUB.replace("$STUB_JAVA", "17.0.12"))
        (jdk / "bin/java").chmod(0o755)
        p = self.setup(java="macos-stub", SETUP_JDK_DIRS=str(jdk))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("java=%s/bin/java" % jdk, self.calls())
        # Homebrew's mvn builds on $JAVA_HOME, not on the java on PATH.
        self.assertIn("JAVA_HOME=%s/libexec/openjdk.jdk/Contents/Home" % jdk, self.calls(), "the build runs on it")

    def test_the_game_launcher_looks_for_homebrew_s_jdk_where_setup_does(self):
        # An Intel Mac's Homebrew lives in /usr/local: a launcher that missed it would play on macOS's java stub.
        prefixes = re.compile(r"(/[\w/]*/opt/openjdk@17)\b")
        setup_line = next(ln for ln in (REPO / "setup.sh").read_text().splitlines() if "SETUP_JDK_DIRS:-" in ln)
        for launcher in ("run-llm-spike.sh", "run-llm-headless.sh"):   # the GUI game, and every batch game
            line = next((ln for ln in (REPO / "forge-overlay" / launcher).read_text().splitlines() if ln.startswith("for BREW")), "")
            self.assertEqual(prefixes.findall(line), prefixes.findall(setup_line), launcher)
        self.assertEqual(len(prefixes.findall(setup_line)), 2)
        # The harness-env grader's javac, which on macOS would otherwise be /usr/bin/javac, a stub.
        grader = (REPO / "eval" / "harness_env" / "harness_env_task_grade.py").read_text().splitlines()
        line = next((ln for ln in grader if ln.startswith("JDK_DIRS = ")), "")
        self.assertEqual(prefixes.findall(line), prefixes.findall(setup_line), "harness_env_task_grade.py")

    def test_a_maven_older_than_the_forge_build_requires_is_refused_before_the_fetch(self):
        p = self.setup(STUB_MVN="3.6.3")
        self.assertEqual(p.returncode, 3)
        self.assertIn("Maven 3.8.1", p.stderr)
        self.assertNotIn("fetch", self.calls())

    def test_a_maven_whose_version_cannot_be_read_or_that_runs_an_old_jdk_is_refused(self):
        p = self.setup(STUB_MVN="3.8")                   # three words, no patch number: not read as a version
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertNotIn("integer expression", p.stderr)
        p = self.setup(STUB_MVN_JAVA="11.0.20")          # java 17 on PATH, but Maven runs on an exported old JDK
        self.assertEqual(p.returncode, 3)
        self.assertIn("JAVA_HOME", p.stderr)
        self.assertNotIn("fetch", self.calls())

    def test_a_kept_secrets_file_says_whether_it_holds_the_consent(self):
        (self.repo / "secrets.env").write_text("OPENROUTER_API_KEY=someone-elses\nOPENROUTER_ALLOW_EGRESS=1\n")
        p = self.setup(answers="")
        self.assertIn("already allows", p.stdout)
        self.assertNotIn("someone-elses", p.stdout + p.stderr)

    def test_a_yes_typed_with_a_windows_line_ending_is_a_yes(self):
        self.assertEqual(self.setup(answers="sk-or-test-key\r\ny\r\n").returncode, 0)
        self.assertIn("\nOPENROUTER_ALLOW_EGRESS=1\n", self.secrets())
        self.assertIn("OPENROUTER_API_KEY=sk-or-test-key\n", self.secrets(), "no CR kept in the key")

    def test_a_forge_checkout_it_did_not_make_is_never_moved(self):
        (self.forge / ".git").mkdir(parents=True)
        (self.forge / ".git/HEAD_SHA").write_text("0" * 40)
        p = self.setup()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(PIN, p.stderr)
        self.assertNotIn("checkout", self.calls())
        self.assertNotIn("mvn", self.calls())

    def test_its_own_checkout_is_moved_to_a_new_pin(self):
        self.assertEqual(self.setup().returncode, 0)
        (self.forge / ".git/HEAD_SHA").write_text("0" * 40)      # the repo's pin moved since the last setup
        self.log.unlink()
        p = self.setup(answers="")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("fetch --depth 1 origin %s" % PIN, self.calls())
        self.assertIn("checkout -q -f FETCH_HEAD", self.calls())

    def test_a_second_run_at_the_pin_rebuilds_without_fetching(self):
        self.assertEqual(self.setup().returncode, 0)
        self.log.unlink()
        self.assertEqual(self.setup(answers="").returncode, 0)
        self.assertNotIn("fetch", self.calls())
        self.assertIn("install -DskipTests", self.calls())

    # ---- Windows (Git Bash): uname and cygpath are stubbed, so these run on any system ----

    def windows(self):
        for name, body in (("uname", '#!/bin/bash\necho MINGW64_NT-10.0-22631\n'),
                           ("cygpath", '#!/bin/bash\necho "C:/msys64${@: -1}"\n')):
            (self.stubs / name).write_text(body)
            (self.stubs / name).chmod(0o755)

    def test_on_windows_maven_is_given_a_windows_path_for_the_classpath_file(self):
        # Maven's JVM is a native program: /tmp/forge_cp.txt means nothing to it (or C:\tmp), while bash reads the
        # file by its own name for it.
        self.windows()
        cp = "%s/forge_cp.txt" % self._tmp.name
        p = self.setup(FORGE_CP_FILE=cp)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("-Dmdep.outputFile=C:/msys64%s " % cp, self.calls())

    def test_off_windows_the_classpath_file_path_is_passed_as_it_is(self):
        cp = "%s/forge_cp.txt" % self._tmp.name
        self.assertEqual(self.setup(FORGE_CP_FILE=cp).returncode, 0)
        self.assertIn("-Dmdep.outputFile=%s " % cp, self.calls())

    def test_a_python_3_named_python_is_used_when_there_is_no_python3(self):
        # python.org's Windows installer names it python; Windows's own python3 is a stub that opens the Store.
        (self.stubs / "python3").unlink()
        (self.stubs / "python3").write_text("#!/bin/bash\necho 'Python was not found; run without arguments to install' >&2\nexit 9\n")
        (self.stubs / "python3").chmod(0o755)
        # A wrapper, not a symlink: macOS's /usr/bin/python3 picks its tool by the name it was run under.
        (self.stubs / "python").write_text('#!/bin/bash\nexec "%s" "$@"\n' % sys.executable)
        (self.stubs / "python").chmod(0o755)
        p = self.setup()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        player = "forge-game/src/main/java/forge/game/player/Player.java"
        self.assertTrue((self.forge / player).exists(), "install.sh's python3 calls ran on it too")

    def test_no_working_python_is_a_missing_tool(self):
        (self.stubs / "python3").unlink()
        for name in ("python", "py"):       # a Linux image may have /usr/bin/python, which setup would rightly use
            if os.path.lexists(self.sysbin / name):
                (self.sysbin / name).unlink()
        p = self.setup()
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("python3", p.stderr)

    def test_on_windows_a_missing_tool_names_the_windows_install(self):
        self.windows()
        (self.stubs / "mvn").unlink()
        p = self.setup()
        self.assertEqual(p.returncode, 3)
        self.assertIn("winget", p.stderr)
        self.assertNotIn("apt-get", p.stderr)

    def test_the_game_launchers_join_the_classpath_as_maven_wrote_it(self):
        # Maven's classpath file uses the system's separator (; on Windows, : elsewhere) and java wants the same
        # one. A launcher that always wrote : gave a Windows java one unusable entry.
        java = self.stubs / "java"
        java.write_text('#!/bin/bash\nprev=""; for a in "$@"; do [ "$prev" = -cp ] && echo "cp=$a" >> "$STUB_LOG"; prev="$a"; done\n')
        for launcher in ("run-llm-spike.sh", "run-llm-headless.sh"):
            for written, want in (("C:\\m2\\a.jar;C:\\m2\\b.jar", "target/classes;C:\\m2\\a.jar;C:\\m2\\b.jar"),
                                  ("/m2/a.jar:/m2/b.jar", "target/classes:/m2/a.jar:/m2/b.jar")):
                with self.subTest(launcher=launcher, written=written):
                    forge = Path(self._tmp.name) / "launch"
                    shutil.rmtree(forge, ignore_errors=True)
                    (forge / "forge-gui-desktop").mkdir(parents=True)
                    shutil.copy(REPO / "forge-overlay" / launcher, forge / launcher)
                    shutil.copy(REPO / "forge-overlay" / "harness-classes-check.sh", forge)
                    (forge / "cp.txt").write_text(written)
                    if self.log.exists():
                        self.log.unlink()
                    env = {"PATH": "%s:%s" % (self.stubs, self.sysbin), "HOME": self._tmp.name,
                           "STUB_LOG": str(self.log), "FORGE_CP_FILE": str(forge / "cp.txt"), "LAUNCH_JDK_DIRS": ""}
                    p = subprocess.run(["bash", str(forge / launcher)], capture_output=True, text=True, env=env,
                                       timeout=60)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(self.calls().strip(), "cp=" + want)

    def install(self, head, **env):
        """install.sh alone into self.forge, a stub checkout whose HEAD is `head`."""
        (self.forge / ".git").mkdir(parents=True, exist_ok=True)
        (self.forge / ".git/HEAD_SHA").write_text(head)
        (self.forge / "forge-gui-desktop").mkdir(exist_ok=True)
        base = {"PATH": "%s:%s" % (self.stubs, self.sysbin), "HOME": self._tmp.name, "STUB_LOG": str(self.log),
                "FORGE_HOME": str(self.forge)}
        base.update(env)
        return subprocess.run(["bash", str(self.repo / "install.sh")], capture_output=True, text=True, env=base,
                              timeout=120)

    def test_install_removes_a_seat_file_the_overlay_no_longer_has(self):
        spike = self.forge / "forge-gui-desktop/src/main/java/forge/spike"
        spike.mkdir(parents=True)
        (spike / "Renamed.java").write_text("class Renamed {}")
        kept = sorted(p.name for p in (REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike").glob("*.java"))
        (spike / kept[0]).write_text("old")
        p = self.install(PIN)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((spike / "Renamed.java").exists(), "a file the overlay no longer has is removed")
        self.assertNotEqual((spike / kept[0]).read_text(), "old", "the overlay's own file is installed")

    def test_install_on_another_engine_commit_writes_nothing_unless_allowed(self):
        p = self.install("f" * 40)
        self.assertEqual(p.returncode, 1)
        self.assertIn("ALLOW_PIN_MISMATCH=1", p.stderr)
        self.assertFalse((self.forge / "run-llm-headless.sh").exists(), "nothing written")
        p = self.install("f" * 40, ALLOW_PIN_MISMATCH="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("WARNING", p.stderr)
        self.assertTrue((self.forge / "run-llm-headless.sh").exists())

    def test_a_game_is_refused_when_its_harness_classes_are_older_than_their_installed_sources(self):
        # install.sh writes sources; only a build makes the classes a game loads: the seat's from
        # forge-gui-desktop/target/classes, an engine patch's from its module jar in ~/.m2 (first on the classpath).
        java = self.stubs / "java"
        java.write_text('#!/bin/bash\necho ran >> "$STUB_LOG"\n')
        old, new = time.time() - 3600, time.time()
        for launcher in ("run-llm-spike.sh", "run-llm-headless.sh"):
            forge = Path(self._tmp.name) / "launch"
            shutil.rmtree(forge, ignore_errors=True)
            seat_src = forge / "forge-gui-desktop/src/main/java/forge/spike/Seat.java"
            seat_cls = forge / "forge-gui-desktop/target/classes/forge/spike/Seat.class"
            patch_src = forge / "forge-ai/src/main/java/forge/ai/Mana.java"
            jar = forge / "m2/forge/forge-ai/2.0/forge-ai-2.0.jar"
            for f, text in ((seat_src, "class Seat {}"), (seat_cls, "x"), (patch_src, "// HARNESS PATCH\n"), (jar, "j")):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
            for src in (REPO / "forge-overlay" / launcher, REPO / "forge-overlay" / "harness-classes-check.sh"):
                shutil.copy(src, forge)
            (forge / "cp.txt").write_text("%s:/m2/other.jar" % jar)
            def launch():
                if self.log.exists():
                    self.log.unlink()
                env = {"PATH": "%s:%s" % (self.stubs, self.sysbin), "HOME": self._tmp.name, "STUB_LOG": str(self.log),
                       "FORGE_CP_FILE": str(forge / "cp.txt"), "LAUNCH_JDK_DIRS": ""}
                return subprocess.run(["bash", str(forge / launcher)], capture_output=True, text=True, env=env,
                                      timeout=60)
            for built, stale in (((seat_cls, jar), []), ((jar,), ["forge-gui-desktop/src/main/java/forge/spike/Seat.java"]),
                                 ((seat_cls,), ["forge-ai/src/main/java/forge/ai/Mana.java"])):
                with self.subTest(launcher=launcher, stale=stale):
                    for f in (seat_src, patch_src, seat_cls, jar):
                        os.utime(f, (old, old))
                    for f in (seat_src, patch_src):
                        os.utime(f, (old + 60, old + 60))
                    for f in built:
                        os.utime(f, (new, new))
                    p = launch()
                    if not stale:
                        self.assertEqual((p.returncode, self.log.exists()), (0, True), p.stderr)
                    else:
                        self.assertEqual(p.returncode, 4, p.stderr)
                        self.assertFalse(self.log.exists(), "no JVM started")
                        for rel in stale:
                            self.assertIn(rel, p.stderr)
                        self.assertIn("mvn -q -pl forge-gui-desktop -am install", p.stderr)

    def test_a_clone_keeps_every_file_s_bytes_on_every_system(self):
        self.assertIn("* -text", (REPO / ".gitattributes").read_text().splitlines())   # why: see .gitattributes
        # With git's conversion off, a script saved with CRLF by a Windows editor would be committed as it is.
        tracked = subprocess.run(["git", "ls-files", "-z", "--", "*.sh", "*.py"], cwd=str(REPO), capture_output=True,
                                 check=True).stdout.decode().split("\0")
        self.assertGreater(len(tracked), 100)
        self.assertEqual([f for f in tracked if f and b"\r" in (REPO / f).read_bytes()], [])


if __name__ == "__main__":
    unittest.main()
