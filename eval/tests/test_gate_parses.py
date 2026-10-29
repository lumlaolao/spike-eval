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
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

REPO = evalpath.REPO
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


def _close(text, i, stop):
    """Where bash 3.2 ends a `$(` opened just before `i`: it scans for the matching paren before it reads any
    heredoc, so a quote in a heredoc body counts as a quote. A '...' pair is literal, "...", `...` and $'...' honour
    backslash escapes, and parens nest. Returns (close, crossed): the closing paren (None when a quote never
    closes, and bash then fails to parse the script), and whether a quote opened before `stop` ends at or after it."""
    depth, n, crossed = 1, len(text), False
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"`":
            ansi = c == "'" and i > 0 and text[i - 1] == "$"
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" and (c != "'" or ansi) else 1
            if i < stop <= j:
                crossed = True
            if j >= n:
                return None, crossed
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i, crossed
        i += 1
    return None, crossed


HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([^\s'\"();&|<>]+)\2")


def heredoc_quotes_in_substitution(text):
    """Heredocs opened inside `$( ... )` that bash 3.2 cannot parse, as (line, tag): ones where, reading the
    substitution as bash 3.2 does (_close), a quote runs across the heredoc's terminator line, or the substitution
    ends before it, or never (issue #219: a lone apostrophe or backtick in the body). Checked by reading, so it
    fails on Linux too, where bash -n accepts such a script."""
    found = []
    for m in re.finditer(r"\$\((?!\()", text):
        close, _ = _close(text, m.end(), len(text))
        opener = HEREDOC.search(text, m.end(), close if close is not None else len(text))
        if not opener:
            continue
        dash, tag = opener.group(1), opener.group(3)
        body = text.find("\n", opener.end())
        term = re.compile(r"^%s%s$" % ("\t*" if dash else "", re.escape(tag)), re.M).search(text, body + 1)
        end = term.end() if term else len(text)
        close, crossed = _close(text, m.end(), end)
        if close is None or close < end or crossed:
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

    def test_a_quote_paired_only_after_the_heredoc_ends_is_still_flagged(self):
        # The body's apostrophe pairs with one in a later comment, and the `$(` then closes on a case pattern's `)`.
        text = "x=$(cat <<EOF\nit's\nEOF\n)\n# it's a case\ncase \"$1\" in a) echo a ;; esac\n"
        self.assertEqual(self.flags(text), ["EOF"])

    def test_arithmetic_and_ansi_c_quotes_are_not_heredocs_or_plain_quotes(self):
        self.assertEqual(self.flags("x=$((1<<k))\n"), [])
        self.assertEqual(self.flags("x=$(f $'it\\'s' <<'PY'\nfine\nPY\n)\n"), [])

    def test_no_script_has_an_unpaired_quote_in_a_heredoc_inside_a_substitution(self):
        for path in GATE_SCRIPTS:
            with self.subTest(script=path.name):
                self.assertEqual(heredoc_quotes_in_substitution(path.read_text()), [],
                                 "%s breaks bash 3.2's parse (STANDARDS section 4)" % path.name)


if __name__ == "__main__":
    unittest.main()
