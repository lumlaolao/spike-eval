#!/usr/bin/env python3
"""The gate scripts parse under the repo's required bash (owner, 2026-10-16).

The remaining-bug class after issue #219: a stray backtick (or an unterminated
quote) inside a `$( ... <<'EOF' )` verdict heredoc corrupts bash 3.2's
quote-scan, the file dies at parse time, and every stage list runs at most
preflight + classpath while the rest silently never executes — a broken gate
that still prints PASS lines.

bash -n parses each script on the bash that runs it, which catches the hazard only under bash 3.2
(macOS): bash 4+ (Linux) parses such a file cleanly. heredoc_quotes_in_substitution reads each
script for the same hazard, so it fails on Linux too. Neither can see run-time
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


def _close(text, i):
    """Where bash 3.2 ends a `$(` opened just before `i`: it scans for the matching paren before it reads any
    heredoc, so a quote in a heredoc body counts as a quote. A '...' pair is literal, "..." and `...` honour
    backslash escapes, and parens nest. None when a quote never closes (bash then fails to parse the script)."""
    depth, n = 1, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            i = j + 1
            continue
        if c in "\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return None
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([^\s'\"();&|<>]+)\2")


def heredoc_quotes_in_substitution(text):
    """Heredocs opened inside `$( ... )` that bash 3.2 cannot parse, as (line, tag): ones whose substitution, read
    as bash 3.2 reads it (_close), ends before the heredoc's own terminator line, or never ends (issue #219: a lone
    apostrophe or backtick in the body). Checked by reading, so it fails on Linux too, where bash -n accepts it."""
    found = []
    for m in re.finditer(r"\$\(", text):
        close = _close(text, m.end())
        opener = HEREDOC.search(text, m.end(), close if close is not None else len(text))
        if not opener:
            continue
        dash, tag = opener.group(1), opener.group(3)
        body = text.find("\n", opener.end())
        term = re.compile(r"^%s%s$" % ("\t*" if dash else "", re.escape(tag)), re.M).search(text, body + 1)
        end = term.end() if term else len(text)
        if close is None or close < end:
            found.append((text.count("\n", 0, m.start()) + 1, tag))
    return found


class HeredocInSubstitution(unittest.TestCase):
    def flags(self, text):
        return [tag for _, tag in heredoc_quotes_in_substitution(text)]

    def test_an_unpaired_quote_in_the_body_is_flagged(self):
        self.assertEqual(self.flags("x=\"$(python3 - <<'PY'\n# the batch's keys\nPY\n)\"\n"), ["PY"])
        self.assertEqual(self.flags("x=$(cat <<EOF\nan ` alone\nEOF\n)\n"), ["EOF"])
        self.assertEqual(self.flags('x=$(cat <<EOF\na " alone\nEOF\n)\n'), ["EOF"])

    def test_quotes_bash_reads_in_order_are_judged_in_order(self):
        # Two apostrophes, but the first sits inside double quotes: the second opens a quote that never closes.
        self.assertEqual(self.flags("x=$(python3 - <<'PY'\nprint(\"don't\")\n# can't\nPY\n)\n"), ["PY"])
        # What bash 3.2 accepts: a pair, an apostrophe inside double quotes, a backtick inside single quotes.
        for body in ("m = re.search(r'a', s)", 'print("don\'t")', "s = '`'"):
            self.assertEqual(self.flags("x=$(python3 - <<'PY'\n%s\nPY\n)\n" % body), [], body)

    def test_the_tag_terminator_and_layout_forms_are_read(self):
        self.assertEqual(self.flags("x=$(cat <<'END-PY'\nit's\nEND-PY\n)\n"), ["END-PY"])
        self.assertEqual(self.flags("x=$(f $(g) <<'PY'\nit's\nPY\n)\n"), ["PY"])
        self.assertEqual(self.flags("x=\"$(\n  python3 - <<'PY'\nit's\nPY\n)\"\n"), ["PY"])
        # An indented tag line does not end a << heredoc; only <<- allows leading tabs.
        self.assertEqual(self.flags("x=$(cat <<'PY'\n  PY\nit's\nPY\n)\n"), ["PY"])
        self.assertEqual(self.flags("x=$(cat <<-'PY'\n\tfine\n\tPY\n)\n"), [])
        self.assertEqual(self.flags("cat <<'EOF'\nit's fine outside $( )\nEOF\n"), [])
        self.assertEqual(self.flags("n=$(cat <<<\"$s\" | wc -l)\n"), [])

    def test_no_script_has_an_unpaired_quote_in_a_heredoc_inside_a_substitution(self):
        for path in GATE_SCRIPTS:
            with self.subTest(script=path.name):
                self.assertEqual(heredoc_quotes_in_substitution(path.read_text()), [],
                                 "%s breaks bash 3.2's parse (STANDARDS section 4)" % path.name)


if __name__ == "__main__":
    unittest.main()
