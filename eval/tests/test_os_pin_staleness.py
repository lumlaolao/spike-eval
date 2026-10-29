"""harness_acceptance.expected: what a sibling OS left stale by a re-pin must show, and why.

What it measures (docs/STANDARDS.md §5): when a plain (reference Mac) canary pin carries a
dated re-pin, every other OS that pins the same family either carries a pin dated at or after
that re-pin, or some comment line names that OS STALE together with the movers (an issue
ref, an epoch, or a date on the line), or some line names that OS CONFIRMED with a date at or
after the re-pin (measured there and unchanged: the pin is current, not stale). Fails loudly
when a re-pin silently leaves a sibling OS behind.
What it cannot see: the PR-side half of that naming (the STANDARDS rule wants the same line
in the PR body, which is not this file), whether a date is true history, or which commit
actually moved a number; the check reads text shape only.
"""
from pathlib import Path
import re
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

HERE = evalpath.EVAL
EXPECT = HERE / "harness_acceptance.expected"

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MOVER = re.compile(r"#\d+|epoch", re.I)


def parse(text):
    """Pins with the comment block attached above each key; writes the shape errors out."""
    pins = []
    block = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            block = []
            continue
        if line.startswith("#"):
            block.append(line.lstrip("#").strip())
            continue
        key, sep, value = line.partition("=")
        if not (sep and key.strip() and value.strip()):
            raise ValueError("unparsed line in an expectations file: %r" % raw[:120])
        segments = key.split(".")
        if len(segments) < 2:
            raise ValueError("a pin key needs family.pin segments or os.family.pin: %r" % key)
        pins.append({"os": ".".join(segments[:-2]), "family": segments[-2],
                     "pin": segments[-1], "value": value.strip(), "block": block})
        block = []
    return pins


def stale_problems(pins):
    """The naming failures the §5 rule demands be fixed, in file order; [] when it holds."""
    groups = {}
    for p in pins:
        groups.setdefault((p["os"], p["family"]), []).append(p)
    families = sorted({family for _, family in groups})
    problems = []
    for family in families:
        plain = groups.get(("", family), [])
        if not plain:
            continue  # OS-only family: nothing for the plain reference to leave behind
        dates = [d for p in plain for c in p["block"] for d in DATE.findall(c)]
        if not dates:
            continue  # no plain re-pin in sight, so nothing was left behind either
        latest_plain = max(dates)
        for (os, os_family), os_pins in sorted(groups.items()):
            if os == "" or os_family != family:
                continue
            os_dates = [d for p in os_pins for c in p["block"] for d in DATE.findall(c)]
            if os_dates and max(os_dates) >= latest_plain:
                continue  # the sibling was re-pinned at or after the plain re-pin
            confirmed = any(
                "CONFIRMED" in c.upper() and os in c and any(d >= latest_plain for d in DATE.findall(c))
                for p in plain + os_pins for c in p["block"])
            if confirmed:
                continue  # measured on that OS at or after the plain re-pin, and unchanged
            named = any(
                "STALE" in c.upper() and os in c and (MOVER.search(c) or DATE.search(c))
                for p in plain + os_pins for c in p["block"])
            if not named:
                problems.append(
                    "UNNAMED STALE: %s.%s pins predate the plain %s re-pin %s and no line names "
                    "%s STALE with the movers (issue ref, epoch, or dated line); re-pin %s.%s "
                    "there or add the naming" % (os, family, family, latest_plain, os, os, family))
    return problems


class OsPinStalenessTest(unittest.TestCase):
    def run_on(self, text):
        return stale_problems(parse(text))

    RED = (
        "# 2026-10-16: epoch 33 enlarged the seat's windows; floor re-pinned 1068 -> 1804.\n"
        "floor.decisions=1804\n"
        "floor.turns=108\n"
        "# the other OS's older pins, unmentioned by the re-pin above\n"
        "linux.floor.decisions=1828\n"
        "linux.floor.turns=108\n")

    def test_a_plain_repin_leaving_a_sibling_unnamed_fails_the_check(self):
        problems = self.run_on(self.RED)
        self.assertTrue(problems)
        self.assertTrue(problems[0].startswith("UNNAMED STALE"))
        self.assertIn("linux.floor", problems[0])

    def test_a_sibling_named_stale_with_movers_passes(self):
        self.assertEqual(self.run_on(self.RED.replace(
            "# the other OS's older pins, unmentioned by the re-pin above",
            "# 2026-10-16: the re-pin above moved the plain floor; "
            "linux.floor.* below are STALE for #216/#218 until measured there.")), [])

    def test_a_sibling_repin_dated_at_or_after_the_plain_repin_passes(self):
        self.assertEqual(self.run_on(self.RED.replace(
            "# the other OS's older pins, unmentioned by the re-pin above",
            "# 2026-10-17: this OS re-measured the new floor at this build (twice).")), [])

    def test_a_sibling_confirmed_unchanged_on_or_after_the_repin_passes(self):
        self.assertEqual(self.run_on(self.RED.replace(
            "# the other OS's older pins, unmentioned by the re-pin above",
            "# 2026-10-16: CONFIRMED on linux: the canary ran at #216 and did not move.")), [])

    def test_a_confirmed_line_without_a_current_date_does_not_count(self):
        for line in ("# CONFIRMED on linux.", "# 2026-10-15: CONFIRMED on linux before the re-pin."):
            problems = self.run_on(self.RED.replace("# the other OS's older pins, unmentioned by the re-pin above", line))
            self.assertTrue(problems and problems[0].startswith("UNNAMED STALE"), line)

    def test_a_stale_line_without_movers_does_not_count_as_the_naming(self):
        problems = self.run_on(self.RED.replace(
            "# the other OS's older pins, unmentioned by the re-pin above",
            "# linux.floor.* STALE."))
        self.assertTrue(problems and problems[0].startswith("UNNAMED STALE"))

    def test_the_real_file_holds_the_rule(self):
        pins = parse(EXPECT.read_text())
        os_names = {p["os"] for p in pins if p["os"]}
        self.assertEqual(os_names, {"linux"})  # the check actually ran, not trivially green
        plain = [p for p in pins if not p["os"]]
        self.assertTrue(any(DATE.search(c) for p in plain for c in p["block"]))
        self.assertEqual(stale_problems(pins), [])

    def test_the_real_file_parses_with_family_pin_segments(self):
        pins = parse(EXPECT.read_text())
        self.assertTrue(pins)
        for p in pins:
            self.assertTrue(p["family"] and p["pin"] and p["value"])

    def test_an_unparsed_line_says_what_and_where(self):
        with self.assertRaises(ValueError):
            parse("floordecisions 1804\n")


if __name__ == "__main__":
    unittest.main()
