"""setup.sh: one command from a fresh clone to a playable install. Run against stub git / java / mvn that log their
arguments, in a scratch copy of the repo, so no network, build or real Forge checkout is touched."""
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN = (REPO / "FORGE_COMMIT").read_text().strip()

GIT_STUB = textwrap.dedent("""\
    #!/bin/bash
    echo "git $*" >> "$STUB_LOG"
    dir=.; if [ "$1" = -C ]; then dir="$2"; shift 2; fi
    case "$1" in
      init) mkdir -p "${@: -1}/.git" ;;
      checkout) mkdir -p "$dir/forge-gui-desktop"; echo "$STUB_PIN" > "$dir/.git/HEAD_SHA" ;;
      rev-parse) cat "$dir/.git/HEAD_SHA" 2>/dev/null || echo unknown ;;
      status) cat "$dir/.git/DIRTY" 2>/dev/null ;;
    esac
    exit 0
""")
JAVA_STUB = textwrap.dedent("""\
    #!/bin/bash
    echo "Picked up JAVA_TOOL_OPTIONS: -Dx=1" >&2
    echo "openjdk version \\"$STUB_JAVA\\" 2026-08-18" >&2
""")
MVN_STUB = textwrap.dedent("""\
    #!/bin/bash
    echo "mvn $* (in $PWD)" >> "$STUB_LOG"
""")


class SetupTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        for f in ("setup.sh", "install.sh", "FORGE_COMMIT", "run-llm-spike.sh", "run-llm-headless.sh"):
            shutil.copy(REPO / f, self.repo / f)
        shutil.copytree(REPO / "forge-overlay", self.repo / "forge-overlay")
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
                if f not in ("git", "java", "mvn", "python3") and not (self.sysbin / f).exists():
                    (self.sysbin / f).symlink_to(os.path.join(d, f))
        self.forge = root / "forge"
        self.log = root / "stub.log"

    def tearDown(self):
        self._tmp.cleanup()

    def setup(self, answers="sk-or-test-key\ny\n", java="21.0.2", **env):
        base = {"PATH": "%s:%s" % (self.stubs, self.sysbin), "HOME": self._tmp.name, "STUB_LOG": str(self.log),
                "STUB_PIN": PIN, "STUB_JAVA": java}
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
        self.assertIn("mvn -q -pl forge-gui-desktop -am install -DskipTests (in %s)" % self.forge, calls)
        self.assertIn("dependency:build-classpath", calls)
        self.assertLess(calls.index("fetch"), calls.index("install -DskipTests"))
        self.assertTrue((self.forge / "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java").exists(),
                        "the overlay is laid on the checkout")
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


if __name__ == "__main__":
    unittest.main()
