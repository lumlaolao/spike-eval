#!/usr/bin/env python3
"""The gate scripts parse under the repo's required bash (owner, 2026-10-16).

The remaining-bug class after issue #219: a stray backtick (or an unterminated quote)
inside a `$( ... <<'EOF' )` verdict heredoc corrupts bash 3.2's quote-scan and the file
dies at parse time. Every stage list then runs at most preflight + classpath and the
rest silently never executes — a broken gate that still prints PASS lines.

bash -n over each gate script is the cheapest rung that reaches it. The paths are
relative to this file's repo, so it works from any checkout or worktree.
"""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Every Bash entry point the repo treats as a gate or hook. bash -n is parse-only.
GATE_SCRIPTS = sorted(
    set(REPO.glob("eval/*.sh"))
    | set(REPO.glob("scripts/*.sh"))
)


class GateParses(unittest.TestCase):
    def test_gate_scripts_have_balanced_bash_syntax(self):
        problems = []
        for rel in GATE_SCRIPTS:
            path = REPO / rel
            if not path.exists():
                problems.append("%s missing" % rel)
                continue
            # bash -n: parse only, never execute. Bash 3.2 is the platform target.
            proc = subprocess.run(
                ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                problems.append("%s does not parse under bash:\n%s" % (rel, proc.stderr.strip()))
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
