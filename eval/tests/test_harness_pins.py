#!/usr/bin/env python3
"""eval/harness_acceptance.sh: how a canary's pins are looked up and labelled (issue #113), and the
environment the javatests JVM runs in.

The script's `want` and `pinset` functions are cut out of it and run in bash against a temporary
expectations file, so this checks the shipped functions, not a copy. It cannot see how the canary
stage uses the label; that is one `case` line beside the seed check. The javatests stage's `testng`
function is run the same way, with a stub in place of java that prints its environment.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import benchmark_version  # noqa: E402

SCRIPT = os.path.join(str(evalpath.EVAL), "harness_acceptance.sh")


def functions():
    """The script text from `want() {` through the end of `pinset() {...}`."""
    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("want() {"))
    begin = next(i for i, l in enumerate(lines) if l.startswith("pinset() {"))
    end = next(i for i in range(begin, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def run(expected_text, call, platform="linux"):
    with tempfile.NamedTemporaryFile("w", suffix=".expected", delete=False) as f:
        f.write(expected_text)
    try:
        script = 'PLATFORM=%s\nEXPECTED="$1"\n%s\n%s\n' % (platform, functions(), call)
        return subprocess.run(["bash", "-c", script, "x", f.name], check=True, stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()
    finally:
        os.unlink(f.name)


PLAIN = "random.seed=42\nrandom.winner=B\nrandom.turns=70\nrandom.decisions=438\n"


class PinsetTest(unittest.TestCase):
    def test_a_full_os_pin_set_is_labelled_with_the_os(self):
        full = PLAIN + "linux.random.winner=A\nlinux.random.turns=45\nlinux.random.decisions=368\n"
        self.assertEqual(run(full, "pinset random"), "linux pins")
        self.assertEqual(run(full, "want random.turns"), "45")

    def test_no_os_pins_is_labelled_plain(self):
        self.assertEqual(run(PLAIN, "pinset random"), "plain (Mac) pins")
        self.assertEqual(run(PLAIN, "want random.turns"), "70")

    def test_a_partial_os_pin_set_is_not_labelled_as_the_os(self):
        # linux.random.turns missing: turns would be checked against the plain (Mac) 70 while the
        # message said "linux pins".
        partial = PLAIN + "linux.random.winner=A\nlinux.random.decisions=368\n"
        label = run(partial, "pinset random")
        self.assertTrue(label.startswith("PARTIAL"), label)
        self.assertIn("turns", label.split("missing:")[1], label)

    def test_another_arms_os_pins_do_not_count(self):
        other = PLAIN + "linux.floor.winner=A\nlinux.floor.turns=108\nlinux.floor.decisions=1081\n"
        self.assertEqual(run(other, "pinset random"), "plain (Mac) pins")


def testng_function():
    """The javatests stage's `testng() {...}` function, as the script defines it."""
    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("testng() {"))
    indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == indent + "}")
    return "\n".join(lines[start:end + 1])


class JavatestsEnvironmentTest(unittest.TestCase):
    def test_the_javatests_jvm_runs_without_eval_out(self):
        # LlmSpikePuzzleTest asserts EVAL_OUT is unset (else it appends to a real puzzle-results.jsonl),
        # so a shell that exports EVAL_OUT must not reach the JVM and fail acceptance.
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "forge-gui-desktop"))
            java = os.path.join(d, "java")
            with open(java, "w") as f:
                f.write("#!/bin/sh\necho \"EVAL_OUT=${EVAL_OUT-unset} MEMORY_TOOLS=${MEMORY_TOOLS-unset} KEEP_ME=${KEEP_ME-unset}\"\n")
            os.chmod(java, 0o755)
            cp = os.path.join(d, "cp.txt")
            with open(cp, "w") as f:
                f.write("x.jar")
            script = ('FORGE="$1"; OUT="$1"; JAVA="$1/java"; CP_FILE="$1/cp.txt"; CLASSES=c; LEDGER=l; REPO=r\n'
                      '%s\ntestng main SomeTest\ncat "$OUT/javatests-main.log"\n' % testng_function())
            env = dict(os.environ, EVAL_OUT=os.path.join(d, "real-run"), KEEP_ME="1")
            out = subprocess.run(["bash", "-c", script, "x", d], check=True, stdout=subprocess.PIPE, env=env,
                                 universal_newlines=True).stdout
        self.assertIn("MEMORY_TOOLS=1", out, "the stub ran")
        self.assertIn("EVAL_OUT=unset", out)
        self.assertIn("KEEP_ME=1", out, "every other variable still reaches the JVM")


def compile_stage():
    """The script's compile stage, `if has_stage compile; then` through its closing `fi`."""
    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("if has_stage compile; then"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1])


class CompileRecordsItsSourcesTest(unittest.TestCase):
    """The compile stage writes overlay-sha.txt beside classes/, the hash run_batch.py stamps as overlay_sha, so
    puzzle_baselines.Build can refuse a build that is not this checkout's (puzzle-build-staleness). A failed
    compile leaves none, and an old one does not survive a recompile. javac is a stub and the repository a
    temporary one-file overlay (so a source can change mid-compile); the hash and the import check are the real
    scripts."""

    def compile(self, javac_rc, edit_during_compile=False, broken_patch=False):
        """(stage output, the recorded hash or None, the overlay's hash before the compile)."""
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(os.path.join(repo, "eval"))
            if broken_patch:  # an engine patch whose original is not in the (missing) Forge checkout
                os.makedirs(os.path.join(repo, "engine-patches/forge-game"))
                with open(os.path.join(repo, "engine-patches/forge-game/G.java.patch"), "w") as f:
                    f.write("--- a/forge-game/G.java\n+++ b/forge-game/G.java\n@@ -1 +1 @@\n-a\n+b\n")
                with open(os.path.join(repo, "FORGE_COMMIT"), "w") as f:
                    f.write("e" * 40 + "\n")
            for name in ("benchmark_version.py", "java_imports.py", "engine_patches.py"):
                shutil.copy(os.path.join(os.path.dirname(SCRIPT), name), os.path.join(repo, "eval"))
            src = os.path.join(repo, "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/A.java")
            os.makedirs(os.path.dirname(src))
            with open(src, "w") as f:
                f.write("package forge.spike;\nclass A {}\n")
            before = benchmark_version.overlay_sha_of_checkout(repo, forge=os.path.join(d, "no-forge"))
            javac = os.path.join(d, "javac")
            with open(javac, "w") as f:
                f.write('#!/bin/sh\n%swhile [ "$1" != -d ]; do shift; done\nmkdir -p "$2/forge/spike"\n'
                        'touch "$2/forge/spike/LlmSpikeGui.class"\nexit %d\n'
                        % ('echo "// edited" >> "%s"\n' % src if edit_during_compile else "", javac_rc))
            os.chmod(javac, 0o755)
            out = os.path.join(d, "out")
            os.makedirs(out)
            with open(os.path.join(out, "cp.txt"), "w") as f:
                f.write("x.jar")
            with open(os.path.join(out, "overlay-sha.txt"), "w") as f:
                f.write("0ld0ld0ld0ld\n")
            script = ('REPO="$1"; OUT="$2"; JAVAC="$3"; FORGE="$4"; CP_FILE="$2/cp.txt"; CLASSES="$2/classes"\n'
                      'OVERLAY="$REPO/forge-overlay/forge-gui-desktop/src"\n'
                      'has_stage() { true; }; pass() { echo "PASS $*"; }; fail() { echo "FAIL $*"; }\n%s\n'
                      % compile_stage())
            res = subprocess.run(["bash", "-c", script, "x", repo, out, javac, os.path.join(d, "no-forge")], check=True,
                                 stdout=subprocess.PIPE, universal_newlines=True).stdout
            sha = os.path.join(out, "overlay-sha.txt")
            return res, (open(sha).read().strip() if os.path.exists(sha) else None), before

    def test_a_compile_records_the_overlay_it_compiled(self):
        res, sha, before = self.compile(0)
        self.assertIn("PASS compile", res)
        self.assertEqual(sha, before)
        self.assertNotEqual(sha, "unknown")

    def test_a_failed_compile_records_nothing(self):
        res, sha, _ = self.compile(1)
        self.assertIn("FAIL compile", res)
        self.assertIsNone(sha)

    def test_an_engine_patch_that_does_not_apply_fails_the_stage_naming_it_and_records_nothing(self):
        res, sha, _ = self.compile(0, broken_patch=True)
        self.assertIn("FAIL compile engine patches do not apply", res)
        self.assertIn("forge-game/G.java", res)
        self.assertIsNone(sha)

    def test_an_overlay_edited_during_the_compile_fails_the_stage_and_records_nothing(self):
        # pr-review of #319: the classes may hold either version, so neither hash names them.
        res, sha, _ = self.compile(0, edit_during_compile=True)
        self.assertIn("FAIL compile", res)
        self.assertIn("changed during the compile", res)
        self.assertIsNone(sha)


def pytests_stage():
    """The script's pytests stage, `if has_stage pytests; then` through its closing `fi`."""
    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("if has_stage pytests; then"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1])


class PytestsSkipAllowanceTest(unittest.TestCase):
    """The pytests stage counts skips like javatests: a test that lost its prerequisite under acceptance (a broken
    resolver, a moved rules file) fails the stage once the skips exceed pytests.skipped_max. The stage runs, as the
    script has it, on a temporary eval/tests holding two skipping tests and one that passes; `want` is the script's
    own. PYTEST_SHARDS names the shards here, as the stage allows."""

    SUITE = ("import unittest\nclass T(unittest.TestCase):\n"
             "    def test_a(self):\n        self.skipTest('no rules text')\n"
             "    def test_b(self):\n        self.skipTest('no rules text')\n"
             "    def test_c(self):\n        pass\n")

    def stage(self, expected_text, suite=SUITE, env=None, files=None, shards="test_one.py|REST"):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "eval", "tests"))
            for name, text in (files or {"test_one.py": suite}).items():
                with open(os.path.join(d, "eval", "tests", name), "w") as f:
                    f.write(text)
            expected = os.path.join(d, "x.expected")
            with open(expected, "w") as f:
                f.write(expected_text)
            script = ('REPO="$1"; OUT="$1"; FORGE="$1"; EXPECTED="$2"; PLATFORM=linux\n'
                      'has_stage() { true; }; pass() { echo "PASS $*"; }; fail() { echo "FAIL $*"; }\n%s\n%s\n'
                      % (functions(), pytests_stage()))
            env = dict(env if env is not None else os.environ, PYTEST_SHARDS=shards)
            return subprocess.run(["bash", "-c", script, "x", d, expected], check=True, stdout=subprocess.PIPE,
                                  universal_newlines=True, env=env).stdout

    def test_skips_over_the_allowance_fail_the_stage_and_name_the_reason(self):
        out = self.stage("pytests.skipped_max=1\n")
        self.assertIn("FAIL pytests", out)
        self.assertIn("2 skipped, more than the 1 allowed", out)
        self.assertIn("no rules text", out)

    def test_skips_within_the_allowance_pass_and_are_counted_in_the_line(self):
        out = self.stage("pytests.skipped_max=2\n")
        self.assertIn("PASS pytests", out)
        self.assertIn("Ran 3 tests OK, 2 skipped (at most 2 allowed", out)

    def test_a_tests_own_ok_line_flushed_after_the_verdict_does_not_hide_the_skips(self):
        # Buffered stdout lands after unittest's summary (stderr): the last line starting "OK" was a test's own.
        suite = self.SUITE + "    def test_d(self):\n        print('OK 3 rows read')\n"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        out = self.stage("pytests.skipped_max=1\n", suite=suite, env=env)
        self.assertIn("FAIL pytests", out)
        self.assertIn("2 skipped, more than the 1 allowed", out)

    def test_a_skip_reason_in_double_quotes_is_named_too(self):
        suite = self.SUITE.replace("'no rules text'", "\"the rules text isn't here\"")
        out = self.stage("pytests.skipped_max=1\n", suite=suite)
        self.assertIn("the rules text isn't here", out)

    ONE_SKIP = "import unittest\nclass U(unittest.TestCase):\n    def test_x(self):\n        self.skipTest('no jar')\n"
    PASSES = "import unittest\nclass V(unittest.TestCase):\n    def test_y(self):\n        pass\n"
    FAILS = "import unittest\nclass W(unittest.TestCase):\n    def test_z(self):\n        self.fail('broken')\n"

    def test_the_shards_tests_and_skips_are_summed_into_one_line(self):
        files = {"test_one.py": self.SUITE, "test_two.py": self.ONE_SKIP, "test_three.py": self.PASSES}
        out = self.stage("pytests.skipped_max=3\n", files=files, shards="test_one.py|test_two.py|REST")
        self.assertIn("PASS pytests Ran 5 tests OK, 3 skipped (at most 3 allowed; 3 shards;", out)
        out = self.stage("pytests.skipped_max=2\n", files=files, shards="test_one.py|test_two.py|REST")
        self.assertIn("3 skipped, more than the 2 allowed", out)

    def test_a_failing_shard_fails_the_stage(self):
        files = {"test_one.py": self.SUITE, "test_two.py": self.FAILS}
        out = self.stage("pytests.skipped_max=5\n", files=files, shards="test_one.py|REST")
        self.assertIn("FAIL pytests", out)
        self.assertIn("shard 2 (rc=1", out)

    def test_a_test_file_in_no_shard_or_a_named_file_that_is_gone_fails_the_stage(self):
        files = {"test_one.py": self.SUITE, "test_two.py": self.PASSES}
        out = self.stage("pytests.skipped_max=5\n", files=files, shards="test_one.py")
        self.assertIn("FAIL pytests", out)
        self.assertIn("> test_two.py", out)
        out = self.stage("pytests.skipped_max=5\n", files=files, shards="test_one.py|test_gone.py|REST")
        self.assertIn("test_gone.py(no such test file)", out)
        out = self.stage("pytests.skipped_max=5\n", files=files, shards="test_one.py|test_one.py test_two.py")
        self.assertIn("< test_one.py", out, "a file in two shards")

    def test_a_missing_allowance_fails_rather_than_allowing_any_number(self):
        self.assertIn("FAIL pytests", self.stage("floor.seed=42\n"))



def cut(start_text, end_text, after=None):
    """The script's lines from the first starting with start_text (after the line holding `after`, if given) through
    the next line holding end_text."""
    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    first = next(i for i, l in enumerate(lines) if after in l) if after else 0
    start = next(i for i in range(first, len(lines)) if lines[i].strip().startswith(start_text))
    end = next(i for i in range(start, len(lines)) if end_text in lines[i])
    return "\n".join(lines[start:end + 1])


class GateBranchTest(unittest.TestCase):
    """Two gates added to stages that need a JVM: each branch is cut out of the script and run on a stub."""

    def run_cut(self, body, d, *args):
        script = 'pass() { echo "PASS $*"; }; fail() { echo "FAIL $*"; }\n%s\n' % body
        return subprocess.run(["bash", "-c", script, "x", d, *args], check=True, stdout=subprocess.PIPE,
                              universal_newlines=True).stdout

    def test_a_missing_ownership_pin_fails_javatests_instead_of_turning_the_check_off(self):
        body = cut('OWN="$(grep', "else pass javatests")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "javatests.log"), "w") as f:
                f.write("[OWNERSHIP] silent strategic: 0\n] decisions-bridge-v3 ONE -> answer\n"
                        "] claude-cli-bridge-v2 -> TOOL CALL answer\n[TRANSCRIPT] transcript-v1 byte-equal\n")
            pre = 'OUT="$1"; EXPECTED="$2"; T=5; F=0; S=0; DECLARED=5; RES=r\n'
            outs = {}
            for name, text in (("absent", "floor.seed=42\n"), ("pinned", "ownership.silent_ai_strategic_max=0\n")):
                exp = os.path.join(d, name + ".expected")
                with open(exp, "w") as f:
                    f.write(text)
                outs[name] = self.run_cut(pre + body, d, exp)
        self.assertIn("FAIL javatests", outs["absent"])
        self.assertIn("no ownership.silent_ai_strategic_max pin", outs["absent"])
        self.assertIn("PASS javatests", outs["pinned"])

    def test_turnchat_fails_when_the_defect_analyzer_fails(self):
        body = cut('VERDICT="$(python3 -', "pass turnchat", after="if has_stage turnchat")
        with tempfile.TemporaryDirectory() as d:
            sdir = os.path.join(d, "server")
            os.makedirs(sdir)
            rows = [{"t": "decision", "tier": "smart", "answer_via": "tool"}] * 10 + [
                {"t": "game", "turn_chat": True, "turn_chat_version": "turn-chat-v1", "one_chat_version": "one-chat-v3"}]
            with open(os.path.join(d, "game-42-1.jsonl"), "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            with open(os.path.join(sdir, "fake_requests.jsonl"), "w") as f:
                f.write(json.dumps({"earlier_turn_note": True, "assistant_messages": 2}) + "\n")
            with open(os.path.join(d, "game.log"), "w") as f:
                f.write("a clean game\n")
            pre = 'dir="$1"; sdir="$2"; ARC="$3"\n'
            clean = self.run_cut(pre + body, d, sdir, "0")
            failed = self.run_cut(pre + body, d, sdir, "1")
        self.assertIn("PASS turnchat", clean)
        self.assertIn("FAIL turnchat", failed)
        self.assertIn("defect_analyzer rc=1", failed)

if __name__ == "__main__":
    unittest.main()
