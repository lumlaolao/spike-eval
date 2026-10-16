#!/usr/bin/env python3
"""The gate scripts parse under the repo's required bash (owner, 2026-10-16).

The remaining-bug class after issue #219: a stray backtick (or an unterminated
quote) inside a `$( ... <<'EOF' )` verdict heredoc corrupts bash 3.2's
quote-scan, the file dies at parse time, and every stage list runs at most
preflight + classpath while the rest silently never executes — a broken gate
that still prints PASS lines.

bash -n is the cheapest rung that reaches that class, on the bash that runs it: the hazard is
bash 3.2's (macOS), and bash 4+ (Linux) parses such a file cleanly, so this test guards it only
where it runs under bash 3.2. It cannot see run-time
behaviour: a script that parses but dies mid-stage, or a stage list naming a
stage the gate never runs. The scanned set is the top-level eval/ and scripts/
gate scripts; root install.sh and the run-llm-*.sh launchers are outside it.

Script paths come from this file's own location, so the test runs unchanged
from any checkout or worktree.
"""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Every bash entry point the repo treats as a gate or hook. bash -n: parse only.
GATE_SCRIPTS = sorted({*REPO.glob("eval/*.sh"), *REPO.glob("scripts/*.sh")})


class GateParses(unittest.TestCase):
    def test_gate_scripts_have_balanced_bash_syntax(self):
        for path in GATE_SCRIPTS:
            with self.subTest(script=path.name):
                proc = subprocess.run(
                    ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
                )
                self.assertEqual(
                    proc.returncode, 0,
                    "%s does not parse under bash:\n%s" % (path.name, proc.stderr.strip()),
                )


if __name__ == "__main__":
    unittest.main()
