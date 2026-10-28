"""Provenance + integrity guard for the downloaded Comprehensive Rules text.

Runs as part of the acceptance `pytests` stage (unittest discovery over eval/). The corpus is the
exact official Wizards .txt at data/rules/MagicCompRules.txt, which the repository does not carry:
eval/fetch_comp_rules.py downloads it (setup.sh and the acceptance preflight run it). It is read directly by the Java
RulesTool (no derived index), so this file is the only gate that the text is the one we pinned.

A changed sha256 / effective date / counts is a PROVENANCE EVENT, not a test to relax: it means a
different rules text is in play, and runs made with it are not comparable to earlier runs. Update
the pins here AND data/rules/README.md deliberately. Stdlib only.
"""

import hashlib
import os
import re
import unittest

import fetch_comp_rules

_HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(_HERE, os.pardir, "data", "rules", "MagicCompRules.txt")

# --- pinned provenance (see data/rules/README.md) -------------------------------------------
EFFECTIVE = "September 25, 2026"
RULE_COUNT = 3165
MIN_GLOSSARY = 500

NB = "\u00a0"  # the CR's non-breaking-space "blank" filler
RULE_RE = re.compile(r"^(\d+\.\d+[a-z]?)\.?\s+\S")


def _text():
    with open(CORPUS, encoding="utf-8") as fh:
        return fh.read()


def _rules(lines):
    out = []
    for line in lines:
        m = RULE_RE.match(line)
        if m:
            out.append((m.group(1), line))
    return out


class RulesCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CORPUS):
            raise unittest.SkipTest("no rules text at %s: run python3 eval/fetch_comp_rules.py" % CORPUS)
        cls.text = _text()
        cls.lines = cls.text.split("\n")

    def test_is_the_exact_official_text_we_pinned(self):
        got = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        self.assertEqual(got, fetch_comp_rules.SHA256,
                         "rules text changed (got %s) — this is a provenance event: re-pin the sha256 in "
                         "eval/fetch_comp_rules.py, EFFECTIVE/counts here and data/rules/README.md deliberately" % got)

    def test_effective_date_is_stated_in_the_text(self):
        self.assertIn("These rules are effective as of %s." % EFFECTIVE, self.text)

    def test_rule_count_matches_the_pin(self):
        rules = _rules(self.lines)
        self.assertEqual(len(rules), RULE_COUNT)
        # Every id must be well formed (NNN.N optionally followed by one lowercase letter).
        bad = [rid for rid, _ in rules if not re.fullmatch(r"\d{3}\.\d+[a-z]?", rid)]
        self.assertFalse(bad, "malformed rule ids: %s" % bad[:5])

    def test_spot_rules_are_the_real_ones(self):
        by_id = {rid: line for rid, line in _rules(self.lines)}
        self.assertTrue(by_id["100.1"].startswith("100.1. These Magic rules apply"), by_id["100.1"])
        self.assertTrue(by_id["509.1"].startswith("509.1. First, the defending player declares blockers"),
                        by_id["509.1"])
        self.assertIn("The defending player chooses which creatures they control", by_id["509.1a"])
        self.assertTrue(by_id["702.4"].startswith("702.4. Double Strike"), by_id["702.4"])

    def test_glossary_is_present_and_substantial(self):
        gl = [i for i, l in enumerate(self.lines) if l.strip() == "Glossary"]
        self.assertTrue(gl, "no Glossary section")
        terms, term = 0, None
        for line in self.lines[gl[-1] + 1:]:
            s = line.replace(NB, " ").strip()
            if not s:
                if term:
                    terms += 1
                term = None
                continue
            if s == "Credits":
                break
            if term is None:
                term = s
        if term:
            terms += 1
        self.assertGreaterEqual(terms, MIN_GLOSSARY, "glossary shrank to %d terms" % terms)

    def test_no_filler_characters_leak_into_rule_text(self):
        # The tool normalises NB; the file itself must at least contain no control garbage.
        self.assertNotIn("\r", self.text)
        self.assertNotIn("\x00", self.text)


if __name__ == "__main__":
    unittest.main()
