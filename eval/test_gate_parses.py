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
gate scripts and the root launchers (install.sh, setup.sh, run-llm-*.sh, play-vs-llm.sh).

Script paths come from this file's own location, so the test runs unchanged
from any checkout or worktree.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Every bash entry point the repo treats as a gate, hook or launcher. bash -n: parse only.
GATE_SCRIPTS = sorted({*REPO.glob("eval/*.sh"), *REPO.glob("scripts/*.sh"), *REPO.glob("*.sh")})


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


def heredoc_quotes_in_substitution(text):
    """Heredocs opened inside `$( ... )` whose apostrophes or backticks do not pair up, as (opening line, tag): bash
    3.2 scans for the closing paren before it parses the heredoc, so an unpaired quote swallows the rest of the
    script (issue #219; a pair only quotes the text between). Checked by reading, so it fails on Linux too, where
    bash -n parses such a file cleanly."""
    found, tag, start, body = [], None, 0, []
    for n, line in enumerate(text.splitlines(), 1):
        if tag is None:
            m = re.search(r"\$\([^)]*<<-?\s*['\"]?(\w+)['\"]?", line)
            if m:
                tag, start, body = m.group(1), n, []
        elif line.strip() == tag:
            joined = "\n".join(body)
            if joined.count("'") % 2 or joined.count("`") % 2:
                found.append((start, tag))
            tag = None
        else:
            body.append(line)
    return found


class HeredocInSubstitution(unittest.TestCase):
    def test_the_check_sees_a_quote_inside_and_ignores_one_outside(self):
        bad = "x=\"$(python3 - <<'PY'\n# the batch's keys\nprint(1)\nPY\n)\"\necho 'fine'\n"
        self.assertEqual(heredoc_quotes_in_substitution(bad), [(1, "PY")])
        paired = "x=\"$(python3 - <<'PY'\nm = re.search(r'a', s)\nPY\n)\"\n"
        self.assertEqual(heredoc_quotes_in_substitution(paired), [])
        self.assertEqual(heredoc_quotes_in_substitution("cat <<'EOF'\nit's fine outside $( )\nEOF\n"), [])

    def test_no_script_has_an_unpaired_quote_in_a_heredoc_inside_a_substitution(self):
        for path in GATE_SCRIPTS:
            with self.subTest(script=path.name):
                self.assertEqual(heredoc_quotes_in_substitution(path.read_text()), [],
                                 "%s breaks bash 3.2's parse (STANDARDS section 4)" % path.name)


if __name__ == "__main__":
    unittest.main()
