#!/usr/bin/env python3
"""eval/harness_acceptance.sh: how a canary's pins are looked up and labelled (issue #113), and the
environment the javatests JVM runs in.

The script's `want` and `pinset` functions are cut out of it and run in bash against a temporary
expectations file, so this checks the shipped functions, not a copy. It cannot see how the canary
stage uses the label; that is one `case` line beside the seed check. The javatests stage's `testng`
function is run the same way, with a stub in place of java that prints its environment.
"""
import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_acceptance.sh")


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
                f.write("#!/bin/sh\necho \"EVAL_OUT=${EVAL_OUT-unset} MEMORY_TOOLS=${MEMORY_TOOLS-unset}\"\n")
            os.chmod(java, 0o755)
            cp = os.path.join(d, "cp.txt")
            with open(cp, "w") as f:
                f.write("x.jar")
            script = ('FORGE="$1"; OUT="$1"; JAVA="$1/java"; CP_FILE="$1/cp.txt"; CLASSES=c; LEDGER=l; REPO=r\n'
                      '%s\ntestng main SomeTest\ncat "$OUT/javatests-main.log"\n' % testng_function())
            env = dict(os.environ, EVAL_OUT=os.path.join(d, "real-run"))
            out = subprocess.run(["bash", "-c", script, "x", d], check=True, stdout=subprocess.PIPE, env=env,
                                 universal_newlines=True).stdout
        self.assertIn("MEMORY_TOOLS=1", out, "the stub ran")
        self.assertIn("EVAL_OUT=unset", out)


if __name__ == "__main__":
    unittest.main()
